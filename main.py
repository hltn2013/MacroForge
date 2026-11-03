import board
import busio
import json
import sys
import select
import usb_cdc
import supervisor
import time
import math
import os
import gc
import binascii
import microcontroller
import neopixel
import displayio
import terminalio
from adafruit_display_text import label

# OLED Display Bus Import for CircuitPython 9.x / 10.x
try:
    from i2cdisplaybus import I2CDisplayBus as DisplayBus
except ImportError:
    from displayio import I2CDisplay as DisplayBus

import adafruit_displayio_ssd1306

from kmk.kmk_keyboard import KMKKeyboard
from kmk.keys import KC
try:
    from kmk.keys import make_key, Key
except ImportError:
    from kmk.keys import make_key
    Key = None
from kmk.scanners import DiodeOrientation
from kmk.scanners.keypad import KeysScanner
from kmk.modules import Module
from kmk.modules.encoder import EncoderHandler
from kmk.extensions.media_keys import MediaKeys
from kmk.modules.mouse_keys import MouseKeys
from kmk.modules.layers import Layers
from kmk.modules.tapdance import TapDance

# --- HARDWARE PIN DEFINITIONS ---
PIN_RGB = board.GP0
PIN_OLED_SDA = board.GP2
PIN_OLED_SCL = board.GP3

PIN_ENC1_A = board.GP5
PIN_ENC1_B = board.GP6
PIN_ENC1_BTN = board.GP7

PIN_BUTTONS = [
    board.GP8,  board.GP9,  board.GP10,  # Row 1 (Buttons 1-3)
    board.GP11, board.GP12, board.GP13   # Row 2 (Buttons 4-6)
]

PIN_ENC2_A = board.GP16
PIN_ENC2_B = board.GP17
PIN_ENC2_BTN = board.GP18

# --- LOAD CONFIGURATION ---
def load_config():
    try:
        with open("config.json", "r") as f:
            return json.load(f)
    except Exception:
        return None

config = load_config()

# --- INITIALIZE NEOPIXELS ---
pixels = neopixel.NeoPixel(PIN_RGB, 13, brightness=0.3, auto_write=True)
if config and "rgb" in config:
    c = config["rgb"]["color"]
    pixels.fill((c[0], c[1], c[2]))
else:
    pixels.fill((0, 150, 255))

# Current base colour (used by live updates and the breathing effect)
RGB_STATE = {"color": tuple(config["rgb"]["color"][:3]) if (config and "rgb" in config) else (0, 150, 255)}

# --- INITIALIZE OLED DISPLAY (128x32 RESOLUTION) ---
displayio.release_displays()
try:
    i2c = busio.I2C(scl=PIN_OLED_SCL, sda=PIN_OLED_SDA)
    display_bus = DisplayBus(i2c, device_address=0x3C)
    
    # 0.91" OLEDs require height=32
    display = adafruit_displayio_ssd1306.SSD1306(display_bus, width=128, height=32)
    
    splash = displayio.Group()
    title_text = "MacroForge"
    if config and "layers" in config and len(config["layers"]) > 0:
        title_text = config["layers"][0].get("oled_title", "STREAM DECK")
        
    # Adjusted y-coordinates specifically for 32px height
    text_area = label.Label(terminalio.FONT, text=title_text, color=0xFFFFFF, x=2, y=6)
    sub_area = label.Label(terminalio.FONT, text="Profile 1: Active", color=0xFFFFFF, x=2, y=22)
    
    splash.append(text_area)
    splash.append(sub_area)
    display.root_group = splash
except Exception as e:
    print("OLED Bypass (No pull-up or wiring issue):", e)

# --- CUSTOM SERIAL LISTENER MODULE ---
# Listens for newline-terminated JSON on BOTH USB serial ports without blocking:
#   - data port    (usb_cdc.data, "CircuitPython CDC2 control")
#   - console port (REPL, "CircuitPython CDC control")
# and replies on whichever port the command arrived on.
class _Hooks(Module):
    # No-op KMK hooks so subclasses only implement what they need
    def during_bootup(self, keyboard):
        pass

    def before_matrix_scan(self, keyboard):
        pass

    def after_matrix_scan(self, keyboard):
        pass

    def process_key(self, keyboard, key, is_pressed, int_coord):
        return key

    def before_hid_send(self, keyboard):
        pass

    def after_hid_send(self, keyboard):
        pass

    def on_powersave_enable(self, keyboard):
        pass

    def on_powersave_disable(self, keyboard):
        pass


class WebSerialListener(_Hooks):
    MAX_LINE = 65536

    def __init__(self):
        self._data = usb_cdc.data
        if self._data:
            self._data.timeout = 0
        self._buf = {"data": b"", "console": b""}

    def before_matrix_scan(self, keyboard):
        if self._data and self._data.in_waiting:
            self._feed("data", self._data.read(self._data.in_waiting))
        n = supervisor.runtime.serial_bytes_available
        if n:
            self._feed("console", sys.stdin.read(int(n)))

    def _feed(self, src, chunk):
        if not chunk:
            return
        if isinstance(chunk, str):
            chunk = chunk.encode()
        buf = self._buf[src] + chunk
        while True:
            i = buf.find(b"\n")
            if i < 0:
                break
            line = buf[:i]
            buf = buf[i + 1:]
            try:
                self._handle(line.decode().strip(), src)
            except UnicodeError:
                self._reply(src, "error", "Bad text encoding")
        if len(buf) > self.MAX_LINE:
            buf = b""
        self._buf[src] = buf

    def _handle(self, line, src):
        if not (line.startswith("{") and line.endswith("}")):
            return
        cmd = ""
        try:
            data = json.loads(line)
            cmd = data.get("cmd")
            if cmd == "set_rgb":
                c = (int(data["r"]), int(data["g"]), int(data["b"]))
                if not data.get("preview"):
                    RGB_STATE["color"] = c
                deck_runtime.preview(c)
                self._reply(src, "ok", "RGB Updated")
            elif cmd == "save_config":
                with open("config.json", "w") as f:
                    json.dump(data["config"], f)
                try:
                    apply_config(data["config"])
                    self._reply(src, "ok", "Config Saved & applied")
                except Exception as err:
                    print("Apply failed:", err)
                    self._reply(src, "error", "Saved, but couldn't apply: " + str(err))
            elif cmd == "get_config":
                if config:
                    self._reply(src, "ok", "Config loaded from deck", {"config": config})
                else:
                    self._reply(src, "error", "No config saved on the deck yet")
            elif cmd == "set_time":
                t = [int(v) for v in data["t"]]
                if set_clock(*t[:7]):
                    self._reply(src, "ok", "Clock set")
                else:
                    self._reply(src, "error", "No clock support")
            elif cmd == "notify":
                col = data.get("color", [255, 0, 0])
                show_alert(str(data.get("text", "Alert")), col, str(data.get("effect", "PULSE")).upper(),
                           int(data.get("seconds", 60)))
                self._reply(src, "ok", "Alert shown")
            elif cmd == "clear_alert":
                clear_alert()
                self._reply(src, "ok", "Alert cleared")
            elif cmd == "save_secrets":
                write_secrets(data.get("secrets", {}))
                notifier.reset_network()
                self._reply(src, "ok", "Wi-Fi / Freshdesk details saved on deck")
            elif cmd == "goto_page":
                goto_page("GOTO", int(data.get("page", 0)))
                deck_runtime.activity()
                deck_runtime.refresh()
                self._reply(src, "ok", "Page switched", {"quiet": True})
            elif cmd == "get_stats":
                self._reply(src, "ok", "Usage counts", {"stats": stats.data, "since": stats.since,
                                                        "saved": stats.can_save})
            elif cmd == "reset_stats":
                stats.reset()
                self._reply(src, "ok", "Usage counts reset")
            elif cmd == "file_begin":
                upload_begin(str(data.get("name")), data.get("size", 0), data.get("crc"))
                self._reply(src, "ok", "ready", {"upload": True})
            elif cmd == "file_chunk":
                upload_chunk(data.get("seq", -1), data.get("data", ""))
                self._reply(src, "ok", "chunk", {"upload": True, "seq": data.get("seq")})
            elif cmd == "file_end":
                nm = upload_end()
                self._reply(src, "ok", nm + " updated", {"upload": True})
            elif cmd == "reboot":
                gate.reboot_at = time.monotonic() + 0.6
                self._reply(src, "ok", "Restarting", {"upload": True})
            elif cmd == "get_state":
                self._reply(src, "ok", "State", {"state": {
                    "page": keyboard.active_layers[0] if keyboard.active_layers else 0,
                    "status": STATE["status"],
                    "alert": ALERT["text"] if ALERT["on"] else None,
                    "alert_color": list(ALERT["color"]) if ALERT["on"] else None}})
            elif cmd == "info":
                self._reply(src, "ok", "Deck info", {"info": deck_info()})
        except OSError as err:
            extra = {"upload": True} if str(cmd).startswith("file_") else None
            if err.args and err.args[0] == 30:
                self._reply(src, "error", "Drive is read-only (USB drive mode is on)", extra)
            else:
                self._reply(src, "error", "File error: " + str(err), extra)
        except Exception as err:
            extra = {"upload": True} if str(cmd).startswith("file_") else None
            self._reply(src, "error", str(err), extra)

    def _reply(self, src, status, msg, extra=None):
        d = {"status": status, "msg": msg}
        if extra:
            d.update(extra)
        out = json.dumps(d) + "\r\n"
        if src == "data" and self._data:
            self._data.write(out.encode())
        else:
            print(out, end="")


# --- KMK ENGINE SETUP ---
keyboard = KMKKeyboard()

# Configure direct pin scanning for 6 buttons + 2 encoder click switches
keyboard.matrix = KeysScanner(
    pins=PIN_BUTTONS + [PIN_ENC1_BTN, PIN_ENC2_BTN],
    value_when_pressed=False,
    pull=True,
)

# Enable Extensions & Serial Listener
media = MediaKeys()
mouse = MouseKeys()
serial_listener = WebSerialListener()

keyboard.extensions.append(media)
keyboard.modules.append(mouse)
keyboard.modules.append(serial_listener)

# Encoder Handler Setup
encoder_handler = EncoderHandler()
keyboard.modules.append(encoder_handler)

encoder_handler.pins = (
    (PIN_ENC1_A, PIN_ENC1_B, None),
    (PIN_ENC2_A, PIN_ENC2_B, None),
)

# =====================================================================
# MACROFORGE RUNTIME
# Everything below is driven by config.json (saved from the editor).
# Hardware pins, OLED and NeoPixel setup above are untouched.
# =====================================================================

FIRMWARE_VERSION = "2.2"
FS_ROOT = "/"
ENCODER_REVERSED = [False, False]   # [left, right] - flip if a knob turns the wrong way

SETTINGS = {
    "layout": "UK", "tap_time": 250, "run_delay": 450, "key_delay": 12, "hold_ms": 500,
    "sleep_pc_off": True, "idle_sleep_min": 0, "saver_min": 5,
    "quiet_start": "", "quiet_end": "", "timezone": "UK",
    "freshdesk_domain": "", "freshdesk_poll": 60, "freshdesk_color": [0, 200, 255],
    "rdm_template": "",
    "timetable": [], "timetable_lead": 5, "timetable_color": [255, 200, 0],
}
STATE = {"status": None, "timer_start": None, "timer_acc": 0.0}
ALERT = {"on": False, "text": "", "color": (255, 0, 0), "effect": "PULSE", "until": None}


def emit_event(d):
    # Tell any PC program listening (e.g. the overlay) that something changed.
    # Only sent to ports a program actually has open, so it never blocks.
    line = json.dumps(d) + "\r\n"
    try:
        dp = usb_cdc.data
        if dp and dp.connected:
            dp.write(line.encode())
    except Exception:
        pass
    try:
        if supervisor.runtime.serial_connected:
            print(line, end="")
    except Exception:
        pass

layers_module = Layers()
tapdance = TapDance()
tapdance.tap_time = SETTINGS["tap_time"]
keyboard.modules.insert(0, layers_module)
keyboard.modules.append(tapdance)

# Caps Lock indicator on the OLED (only if this KMK build has LockStatus)
try:
    from kmk.extensions.lock_status import LockStatus
    locks = LockStatus()
    keyboard.extensions.append(locks)
except Exception:
    locks = None

try:
    import rtc
except ImportError:
    rtc = None


# ---------------------------------------------------------------------
# Key lookup
# ---------------------------------------------------------------------
KEY_ALIASES = {
    "BSPACE": "BSPC", "ESCAPE": "ESC", "DELETE": "DEL", "INSERT": "INS",
    "PGDOWN": "PGDN", "CAPSLOCK": "CAPS", "PSCREEN": "PSCR", "SCROLLLOCK": "SLCK",
    "LBRACKET": "LBRC", "RBRACKET": "RBRC", "BSLASH": "BSLS", "SCOLON": "SCLN",
    "QUOTE": "QUOT", "GRAVE": "GRV", "APPLICATION": "APP",
    "MS_WH_UP": "MW_UP", "MS_WH_DOWN": "MW_DN", "MW_DOWN": "MW_DN",
    "MEDIA_PLAY_PAUSE": "MPLY", "MEDIA_NEXT_TRACK": "MNXT", "MEDIA_PREV_TRACK": "MPRV",
    "MEDIA_STOP": "MSTP", "BRIGHTNESS_UP": "BRIU", "BRIGHTNESS_DOWN": "BRID",
    "LSFT": "LSHIFT", "RSFT": "RSHIFT", "LWIN": "LGUI", "RWIN": "RGUI",
    "NUHS": "NONUS_HASH", "NUBS": "NONUS_BSLASH",
}
MOD_NAMES = ("LCTRL", "LSFT", "LALT", "LWIN", "RCTRL", "RSFT", "RALT", "RWIN",
             "LCTL", "LSHIFT", "LGUI", "LCMD", "RCTL", "RSHIFT", "RGUI", "RCMD")


def kc(name):
    name = str(name).upper()
    for n in (name, KEY_ALIASES.get(name)):
        if not n:
            continue
        try:
            k = getattr(KC, n)
            if k is not None:
                return k
        except Exception:
            pass
    print("Unknown key in config:", name)
    return KC.NO


def hhmm(v):
    try:
        h, m = str(v).strip().split(":")
        h, m = int(h), int(m)
        return h * 60 + m if (0 <= h < 24 and 0 <= m < 60) else None
    except Exception:
        return None


def load_settings(cfg):
    s = cfg.get("settings", {}) if isinstance(cfg, dict) else {}
    if not isinstance(s, dict):
        s = {}

    def num(k, lo, hi):
        try:
            SETTINGS[k] = max(lo, min(hi, int(s.get(k, SETTINGS[k]))))
        except Exception:
            pass

    SETTINGS["layout"] = "US" if str(s.get("layout", "UK")).upper() == "US" else "UK"
    num("tap_time", 100, 1000)
    num("run_delay", 100, 3000)
    num("key_delay", 4, 100)
    num("hold_ms", 200, 3000)
    num("idle_sleep_min", 0, 1440)
    num("saver_min", 0, 240)
    num("freshdesk_poll", 0, 3600)
    num("timetable_lead", 0, 60)
    SETTINGS["sleep_pc_off"] = bool(s.get("sleep_pc_off", True))
    for k in ("quiet_start", "quiet_end", "freshdesk_domain", "rdm_template"):
        SETTINGS[k] = str(s.get(k, "") or "").strip()
    SETTINGS["timezone"] = "UTC" if str(s.get("timezone", "UK")).upper() == "UTC" else "UK"
    for k in ("freshdesk_color", "timetable_color"):
        c = s.get(k)
        if isinstance(c, list) and len(c) >= 3:
            SETTINGS[k] = [int(c[0]), int(c[1]), int(c[2])]
    SETTINGS["timetable"] = []
    tt = s.get("timetable", [])
    if isinstance(tt, list):
        for ev in tt[:40]:
            if isinstance(ev, dict):
                m = hhmm(ev.get("time"))
                if m is not None:
                    SETTINGS["timetable"].append((m, str(ev.get("label", "Bell"))[:16]))
    tapdance.tap_time = SETTINGS["tap_time"]
    _char_cache.clear()


# ---------------------------------------------------------------------
# Clock (set by the editor on connect, or by NTP over Wi-Fi)
# ---------------------------------------------------------------------
def clock_valid():
    return time.localtime().tm_year >= 2024


def set_clock(y, mo, d, h, mi, s, wd):
    if rtc is None:
        return False
    rtc.RTC().datetime = time.struct_time((y, mo, d, h, mi, s, wd, -1, -1))
    return True


def fmt_time():
    if not clock_valid():
        return ""
    t = time.localtime()
    return "%02d:%02d" % (t.tm_hour, t.tm_min)


def fmt_date():
    if not clock_valid():
        return ""
    t = time.localtime()
    return "%02d/%02d/%04d" % (t.tm_mday, t.tm_mon, t.tm_year)


def _last_sunday(year, month):
    wd = time.localtime(time.mktime((year, month, 31, 12, 0, 0, 0, -1, -1))).tm_wday
    return 31 - ((wd + 1) % 7)


def uk_offset(epoch_utc):
    # British Summer Time: last Sunday of March 01:00 UTC -> last Sunday of October 01:00 UTC
    y = time.localtime(epoch_utc).tm_year
    start = time.mktime((y, 3, _last_sunday(y, 3), 1, 0, 0, 0, -1, -1))
    end = time.mktime((y, 10, _last_sunday(y, 10), 1, 0, 0, 0, -1, -1))
    return 3600 if start <= epoch_utc < end else 0


def in_quiet_hours():
    a, b = hhmm(SETTINGS["quiet_start"]), hhmm(SETTINGS["quiet_end"])
    if a is None or b is None or a == b or not clock_valid():
        return False
    t = time.localtime()
    m = t.tm_hour * 60 + t.tm_min
    return (a <= m < b) if a < b else (m >= a or m < b)


# ---------------------------------------------------------------------
# Typing text (US or UK Windows keyboard layout)
# ---------------------------------------------------------------------
_US_PLAIN = {" ": "SPACE", "\n": "ENTER", "\t": "TAB", "-": "MINUS", "=": "EQUAL",
             "[": "LBRC", "]": "RBRC", "\\": "BSLS", ";": "SCLN", "'": "QUOT",
             "`": "GRV", ",": "COMM", ".": "DOT", "/": "SLSH"}
_US_SHIFT = {"!": "N1", "@": "N2", "#": "N3", "$": "N4", "%": "N5", "^": "N6",
             "&": "N7", "*": "N8", "(": "N9", ")": "N0", "_": "MINUS", "+": "EQUAL",
             "{": "LBRC", "}": "RBRC", "|": "BSLS", ":": "SCLN", '"': "QUOT",
             "~": "GRV", "<": "COMM", ">": "DOT", "?": "SLSH"}
_UK = {'"': ("N2", True), "@": ("QUOT", True), "#": ("NUHS", False),
       "~": ("NUHS", True), "\\": ("NUBS", False), "|": ("NUBS", True),
       "£": ("N3", True)}
_char_cache = {}


def char_key(ch):
    ck = SETTINGS["layout"] + ch
    if ck in _char_cache:
        return _char_cache[ck]
    name, shift = None, False
    if SETTINGS["layout"] == "UK" and ch in _UK:
        name, shift = _UK[ch]
    elif "a" <= ch <= "z":
        name = ch.upper()
    elif "A" <= ch <= "Z":
        name, shift = ch, True
    elif "0" <= ch <= "9":
        name = "N" + ch
    elif ch in _US_PLAIN:
        name = _US_PLAIN[ch]
    elif ch in _US_SHIFT:
        name, shift = _US_SHIFT[ch], True
    k = None
    if name:
        k = kc(name)
        if shift:
            k = kc("LSFT")(k)
    _char_cache[ck] = k
    return k


# ---------------------------------------------------------------------
# Sequencer: plays taps, text, pauses and deck actions without blocking
# ---------------------------------------------------------------------
class Sequencer(_Hooks):
    def __init__(self):
        self.q = []
        self.next_at = 0
        self.injecting = False

    def run(self, ops):
        if len(self.q) < 4000:
            self.q.extend(ops)

    def before_matrix_scan(self, keyboard):
        if not self.q:
            return
        now = time.monotonic()
        if now < self.next_at:
            return
        op, arg = self.q.pop(0)
        gap = SETTINGS["key_delay"] / 1000
        if op == "down" or op == "up":
            self.injecting = True
            try:
                if op == "down":
                    keyboard.add_key(arg)
                else:
                    keyboard.remove_key(arg)
            finally:
                self.injecting = False
            self.next_at = now + gap
        elif op == "wait":
            self.next_at = now + arg / 1000
        elif op == "call":
            try:
                arg()
            except Exception as err:
                print("Action error:", err)
        elif op == "dyn":
            try:
                self.q[0:0] = arg()
            except Exception as err:
                print("Action error:", err)


sequencer = Sequencer()
keyboard.modules.append(sequencer)


def tap_ops(k):
    return [("down", k), ("up", k)] if k is not None else []


def text_ops(text):
    ops = []
    for ch in str(text):
        if ch != "\r":
            ops += tap_ops(char_key(ch))
    return ops


def expand_static(text):
    dom = SETTINGS["freshdesk_domain"] or "yourschool.freshdesk.com"
    if "." not in dom:
        dom += ".freshdesk.com"
    return str(text).replace("{freshdesk}", dom).replace("{rdm_template}", SETTINGS["rdm_template"])


def expand_dynamic(text):
    # {clipboard} pastes (Ctrl+V); {date} {time} {timer} type live values
    ops = []
    i = 0
    while i < len(text):
        j = text.find("{", i)
        k = text.find("}", j) if j >= 0 else -1
        if j < 0 or k < 0:
            ops += text_ops(text[i:])
            break
        ops += text_ops(text[i:j])
        tok = text[j + 1:k].lower()
        if tok == "clipboard":
            ops += tap_ops(kc("LCTRL")(kc("V")))
            ops.append(("wait", 60))
        elif tok == "date":
            ops += text_ops(fmt_date())
        elif tok == "time":
            ops += text_ops(fmt_time())
        elif tok == "timer":
            ops += text_ops(timer_text())
        else:
            ops += text_ops(text[j:k + 1])
        i = k + 1
    return ops


def tmpl_ops(text):
    text = expand_static(text)
    if "{" not in text:
        return text_ops(text)
    return [("dyn", lambda: expand_dynamic(text))]


def run_ops(command, admin=False):
    # Win+R, wait for the Run box, type, then Enter (or Ctrl+Shift+Enter = run as admin)
    ops = tap_ops(kc("LWIN")(kc("R")))
    ops.append(("wait", SETTINGS["run_delay"]))
    ops += tmpl_ops(command)
    ops.append(("wait", 80))
    if admin:
        ops += tap_ops(kc("LCTRL")(kc("LSFT")(kc("ENTER"))))
    else:
        ops += tap_ops(kc("ENTER"))
    return ops


def goto_page(action, page=0):
    n = len(keyboard.keymap)
    cur = keyboard.active_layers[0] if keyboard.active_layers else 0
    if action == "NEXT":
        target = (cur + 1) % n
    elif action == "PREV":
        target = (cur - 1) % n
    else:
        target = max(0, min(n - 1, int(page)))
    keyboard.active_layers.clear()
    keyboard.active_layers.insert(0, target)


def macro_ops(steps, layer_idx, layer_count):
    ops = []
    for st in steps if isinstance(steps, list) else []:
        if not isinstance(st, dict):
            continue
        t = st.get("t")
        if t == "keys":
            ops += tap_ops(build_basic({"type": "combo", "keys": st.get("keys", [])}, layer_idx, layer_count))
        elif t == "text":
            ops += tmpl_ops(st.get("text", ""))
        elif t == "delay":
            try:
                ops.append(("wait", max(0, min(10000, int(st.get("ms", 0))))))
            except Exception:
                pass
        elif t == "status":
            ops.append(_call_status(st.get("action", "TOGGLE"), st.get("index", 0)))
        elif t == "timer":
            ops.append(_call_timer(st.get("action", "TOGGLE")))
        elif t == "page":
            act = str(st.get("action", "NEXT")).upper()
            pg = st.get("page", 0)
            ops.append(("call", lambda act=act, pg=pg: goto_page(act, pg)))
    return ops


def _call_status(action, index):
    act = str(action).upper()
    try:
        idx = int(index)
    except Exception:
        idx = 0
    return ("call", lambda: status_do(act, idx))


def _call_timer(action):
    act = str(action).upper()
    return ("call", lambda: timer_do(act))


def _custom_key(on_press, on_release=None):
    def _p(*args):
        on_press()
        return args[1] if len(args) > 1 else None

    def _r(*args):
        if on_release:
            on_release()
        return args[1] if len(args) > 1 else None
    try:
        return make_key(names=(), on_press=_p, on_release=_r)
    except TypeError:
        return Key(on_press=_p, on_release=_r)


def action_key(ops):
    return _custom_key(lambda: sequencer.run(ops))


# ---------------------------------------------------------------------
# Hold vs tap
# ---------------------------------------------------------------------
class HoldManager(_Hooks):
    def __init__(self):
        self.pending = None   # [start, tap_ops, hold_ops, fired, label]

    def press(self, tap, hold, label):
        self.pending = [time.monotonic(), tap, hold, False, label]

    def release(self, tap):
        p = self.pending
        if p and p[1] is tap:
            if not p[3]:
                sequencer.run(tap)
            self.pending = None

    def before_matrix_scan(self, keyboard):
        p = self.pending
        if p and not p[3] and time.monotonic() - p[0] >= SETTINGS["hold_ms"] / 1000:
            p[3] = True
            sequencer.run(p[2])
            deck_runtime.flash("Hold: " + p[4])


holds = HoldManager()
keyboard.modules.append(holds)


def hold_key(tap, hold, label):
    return _custom_key(lambda: holds.press(tap, hold, label), lambda: holds.release(tap))


# ---------------------------------------------------------------------
# Status lights, job timer, alerts
# ---------------------------------------------------------------------
def statuses():
    try:
        st = config["rgb"].get("statuses", [])
        return st if isinstance(st, list) else []
    except Exception:
        return []


def status_do(action, idx):
    st = statuses()
    cur = STATE["status"]
    if action == "CLEAR":
        STATE["status"] = None
    elif action == "CYCLE":
        if st:
            STATE["status"] = 0 if cur is None else (None if cur + 1 >= len(st) else cur + 1)
    elif 0 <= idx < len(st):
        STATE["status"] = None if (action == "TOGGLE" and cur == idx) else idx
    deck_runtime.refresh()
    if STATE["status"] != cur:
        emit_event({"event": "status", "index": STATE["status"]})


def timer_elapsed():
    run = 0 if STATE["timer_start"] is None else time.monotonic() - STATE["timer_start"]
    return STATE["timer_acc"] + run


def timer_text():
    m = int(timer_elapsed() // 60)
    return "%d h %02d min" % (m // 60, m % 60) if m >= 60 else "%d min" % max(1, m)


def timer_do(action):
    if action == "TOGGLE":
        if STATE["timer_start"] is None:
            STATE["timer_start"] = time.monotonic()
        else:
            STATE["timer_acc"] += time.monotonic() - STATE["timer_start"]
            STATE["timer_start"] = None
    elif action == "RESET":
        STATE["timer_start"] = None
        STATE["timer_acc"] = 0.0
    elif action == "TYPE":
        sequencer.run(text_ops(timer_text()))
    deck_runtime.refresh()


def show_alert(text, color, effect="PULSE", seconds=60):
    try:
        c = (int(color[0]), int(color[1]), int(color[2]))
    except Exception:
        c = (255, 0, 0)
    ALERT["on"] = True
    ALERT["text"] = str(text)[:60]
    ALERT["color"] = c
    ALERT["effect"] = effect if effect in ("SOLID", "BREATHING", "PULSE", "RAINBOW") else "PULSE"
    ALERT["until"] = (time.monotonic() + seconds) if seconds and seconds > 0 else None
    deck_runtime.activity()
    deck_runtime.refresh()
    emit_event({"event": "alert", "text": ALERT["text"], "color": list(c)})


def clear_alert():
    was = ALERT["on"]
    ALERT["on"] = False
    deck_runtime.refresh()
    if was:
        emit_event({"event": "alert", "text": None})


# ---------------------------------------------------------------------
# Building keys from config
# ---------------------------------------------------------------------
def build_basic(a, layer_idx, layer_count):
    t = a.get("type")
    if t == "key":
        return kc(a.get("key", "NO"))
    if t == "combo":
        keys = [str(k).upper() for k in a.get("keys", [])]
        if not keys:
            return KC.NO
        mods = [k for k in keys if k in MOD_NAMES]
        rest = [k for k in keys if k not in MOD_NAMES]
        key = kc(rest[-1]) if rest else kc(mods.pop())
        for m in reversed(mods):
            key = kc(m)(key)
        return key
    if t == "system":
        act = a.get("action")
        if act == "NEXT_LAYER":
            return KC.TO((layer_idx + 1) % layer_count)
        if act == "PREV_LAYER":
            return KC.TO((layer_idx - 1) % layer_count)
        if act == "GOTO_LAYER":
            try:
                return KC.TO(max(0, min(layer_count - 1, int(a.get("layer", 0)))))
            except Exception:
                return KC.NO
    return KC.NO


def action_ops(a, layer_idx, layer_count):
    if not isinstance(a, dict):
        return []
    t = a.get("type")
    if t in ("key", "combo", "system"):
        k = build_basic(a, layer_idx, layer_count)
        return tap_ops(k) if k is not KC.NO else []
    if t == "url":
        url = expand_static(str(a.get("url", "")).strip())
        if not url:
            return []
        if ":" not in url.split("/")[0]:
            url = "https://" + url
        return run_ops(url)
    if t == "run":
        cmd = str(a.get("command", "")).strip()
        return run_ops(cmd, bool(a.get("admin"))) if cmd else []
    if t == "text":
        ops = tmpl_ops(a.get("text", ""))
        if a.get("enter"):
            ops += tap_ops(kc("ENTER"))
        return ops
    if t == "macro":
        return macro_ops(a.get("steps", []), layer_idx, layer_count)
    if t == "status":
        return [_call_status(a.get("action", "TOGGLE"), a.get("index", 0))]
    if t == "timer":
        return [_call_timer(a.get("action", "TOGGLE"))]
    return []


def build_action(a, layer_idx, layer_count):
    if not isinstance(a, dict):
        return KC.NO
    if a.get("type") in ("key", "combo", "system"):
        return build_basic(a, layer_idx, layer_count)
    ops = action_ops(a, layer_idx, layer_count)
    return action_key(ops) if ops else KC.NO


# ---------------------------------------------------------------------
# "Press again to confirm" keys
# ---------------------------------------------------------------------
CONFIRM = {"coord": None, "until": 0}


def confirm_key(ops, label):
    def fire():
        now = time.monotonic()
        c = STATE.get("last_coord")
        if CONFIRM["coord"] is not None and CONFIRM["coord"] == c and now < CONFIRM["until"]:
            CONFIRM["coord"] = None
            deck_runtime.flash("Running: " + label, 1.2)
            deck_runtime.preview((0, 0, 0), 0)
            sequencer.run(ops)
        else:
            CONFIRM["coord"] = c
            CONFIRM["until"] = now + 3
            deck_runtime.flash("Press again: " + label, 3)
            deck_runtime.preview((255, 140, 0), 3)
    return _custom_key(fire)


def build_button(b, layer_idx, layer_count):
    if not isinstance(b, dict):
        return KC.NO
    if b.get("type") == "tapdance":
        return KC.TD(
            build_action(b.get("single_tap"), layer_idx, layer_count),
            build_action(b.get("double_tap"), layer_idx, layer_count),
            build_action(b.get("triple_tap"), layer_idx, layer_count),
        )
    if isinstance(b.get("hold"), dict):
        tap = action_ops(b, layer_idx, layer_count)
        hold = action_ops(b["hold"], layer_idx, layer_count)
        return hold_key(tap, hold, str(b.get("label", ""))[:14])
    if b.get("confirm"):
        ops = action_ops(b, layer_idx, layer_count)
        if ops:
            return confirm_key(ops, str(b.get("label", ""))[:12])
    return build_action(b, layer_idx, layer_count)


def build_from_config(cfg):
    layers = cfg["layers"]
    n = len(layers)
    keymap, enc_map = [], []
    for i, layer in enumerate(layers):
        buttons = list(layer.get("buttons", []))[:6]
        buttons += [None] * (6 - len(buttons))
        encs = list(layer.get("encoders", []))[:2]
        encs += [{}] * (2 - len(encs))
        row = [build_button(b, i, n) for b in buttons]
        row += [build_action(e.get("click"), i, n) for e in encs]   # GP7, GP18
        keymap.append(row)
        pairs = []
        for j, e in enumerate(encs):
            cw = build_action(e.get("cw"), i, n)
            ccw = build_action(e.get("ccw"), i, n)
            pairs.append((ccw, cw) if ENCODER_REVERSED[j] else (cw, ccw))
        enc_map.append(tuple(pairs))
    return keymap, enc_map


# ---------------------------------------------------------------------
# Display + lighting engine
# Priority: live preview > alert > status light > page colour > global
# ---------------------------------------------------------------------
def _hue(h):
    h = (h % 360) / 60.0
    i = int(h)
    f = h - i
    q = int(255 * (1 - f))
    t = int(255 * f)
    return ((255, t, 0), (q, 255, 0), (0, 255, t), (0, q, 255), (t, 0, 255), (255, 0, q))[i % 6]


def usb_ready():
    try:
        return bool(supervisor.runtime.usb_connected)
    except Exception:
        return True


class DeckRuntime(_Hooks):
    def __init__(self):
        self.mode = "SOLID"
        self._last = None
        self._lines = None
        self._next_frame = 0
        self._next_oled = 0
        self._preview_until = 0
        self._flash = None
        self._flash_until = 0
        self._usb_off_since = None
        self._page_sent = None
        self.last_activity = time.monotonic()
        self.asleep = False
        ta = globals().get("text_area")
        self._home = (ta.x, ta.y) if ta is not None else None
        self._pos = self._home
        self.apply_rgb(config)

    # -- state --
    def apply_rgb(self, cfg):
        rgb = cfg.get("rgb", {}) if isinstance(cfg, dict) else {}
        self.mode = str(rgb.get("mode", "SOLID")).upper()
        try:
            c = rgb.get("color", RGB_STATE["color"])
            RGB_STATE["color"] = (int(c[0]), int(c[1]), int(c[2]))
            pixels.brightness = max(0.0, min(1.0, float(rgb.get("brightness", 0.3))))
        except Exception:
            pass
        self.refresh()

    def refresh(self):
        self._last = None
        self._lines = None
        self._next_oled = 0

    def preview(self, color, secs=3):
        self._preview_until = time.monotonic() + secs
        pixels.fill(color)
        self._last = None

    def flash(self, text, secs=1.5):
        self._flash = str(text)
        self._flash_until = time.monotonic() + secs
        self._next_oled = 0

    def activity(self):
        self.last_activity = time.monotonic()

    def screen_off(self, now):
        # True while the pad is asleep or the screen saver is showing
        if self.asleep:
            return True
        return bool(SETTINGS["saver_min"]) and not ALERT["on"] and \
            now - self.last_activity > SETTINGS["saver_min"] * 60

    def wake(self):
        self.activity()
        self._usb_off_since = None if usb_ready() else self._usb_off_since
        self.refresh()

    # -- sleep --
    def _want_sleep(self, now):
        if SETTINGS["sleep_pc_off"]:
            if not usb_ready():
                if self._usb_off_since is None:
                    self._usb_off_since = now
                if now - self._usb_off_since > 3:
                    return True
            else:
                self._usb_off_since = None
        if ALERT["on"]:
            return False
        idle = now - self.last_activity
        if SETTINGS["idle_sleep_min"] and idle > SETTINGS["idle_sleep_min"] * 60:
            return True
        if in_quiet_hours() and idle > 60:
            return True
        return False

    # -- main loop --
    def before_matrix_scan(self, keyboard):
        now = time.monotonic()
        if ALERT["on"] and ALERT["until"] is not None and now > ALERT["until"]:
            clear_alert()
        sleep = self._want_sleep(now)
        if sleep != self.asleep:
            self.asleep = sleep
            if sleep:
                pixels.fill((0, 0, 0))
                self._last = (0, 0, 0)
                self._move(self._home)
                self._set_lines("", "")
            else:
                self.refresh()
        if self.asleep:
            return
        layer = keyboard.active_layers[0] if keyboard.active_layers else 0
        if layer != self._page_sent:
            self._page_sent = layer
            emit_event({"event": "page", "page": layer})
        if now >= self._next_oled:
            self._next_oled = now + 0.25
            self._update_oled(layer, now)
        if now < self._preview_until:
            return
        color, eff = self._target(layer)
        if eff == "OFF":
            c = (0, 0, 0)
        elif eff in ("BREATHING", "PULSE", "RAINBOW"):
            if now < self._next_frame and self._last is not None:
                return
            self._next_frame = now + 0.033
            if eff == "BREATHING":
                f = 0.12 + 0.88 * (0.5 - 0.5 * math.cos(now * 2 * math.pi / 2.6))
                c = (int(color[0] * f), int(color[1] * f), int(color[2] * f))
            elif eff == "PULSE":
                c = color if int(now * 2) % 2 == 0 else (0, 0, 0)
            else:
                c = _hue(now * 60)
        else:
            c = color
        if c != self._last:
            pixels.fill(c)
            self._last = c

    def _target(self, layer):
        if ALERT["on"]:
            return ALERT["color"], ALERT["effect"]
        st = statuses()
        s = STATE["status"]
        if s is not None and s < len(st):
            e = st[s]
            c = e.get("color", (255, 0, 0))
            return (int(c[0]), int(c[1]), int(c[2])), str(e.get("effect", "SOLID")).upper()
        color = RGB_STATE["color"]
        try:
            lc = config["layers"][layer].get("led_color")
            if lc:
                color = (int(lc[0]), int(lc[1]), int(lc[2]))
        except Exception:
            pass
        return color, self.mode

    # -- OLED --
    def _move(self, pos):
        ta = globals().get("text_area")
        if ta is None or pos is None or pos == self._pos:
            return
        ta.x, ta.y = pos
        self._pos = pos

    def _set_lines(self, l1, l2):
        ta = globals().get("text_area")
        sa = globals().get("sub_area")
        if ta is None or sa is None:
            return
        lines = (l1[:21], l2[:21])
        if lines != self._lines:
            if self._lines is None or lines[0] != self._lines[0]:
                ta.text = lines[0]
            if self._lines is None or lines[1] != self._lines[1]:
                sa.text = lines[1]
            self._lines = lines

    def _update_oled(self, layer, now):
        try:
            info = config["layers"][layer]
        except Exception:
            info = {}
        st = statuses()
        s = STATE["status"]
        status_txt = ("[" + str(st[s].get("name", "STATUS")).upper() + "]") if (s is not None and s < len(st)) else None
        idle = now - self.last_activity

        # Screensaver: a slowly moving clock (or status) so the OLED doesn't burn in
        if SETTINGS["saver_min"] and idle > SETTINGS["saver_min"] * 60 and not ALERT["on"]:
            txt = status_txt or fmt_time()
            minute = int(now // 60)
            span = max(1, 124 - 6 * len(txt))
            pos = (2 + (minute * 37) % span, 6 if (minute // 2) % 2 == 0 else 22)
            self._move(pos)
            self._set_lines(txt, "")
            return
        self._move(self._home)

        if ALERT["on"]:
            txt = "! " + ALERT["text"]
            if len(txt) > 21:
                loop = txt + "   "
                off = int(now * 3) % len(loop)
                txt = (loop + loop)[off:off + 21]
            l1 = txt
        elif status_txt:
            l1 = status_txt
        else:
            l1 = str(info.get("oled_title", "STREAM DECK"))

        if self._flash and now < self._flash_until:
            l2 = self._flash
        elif STATE["timer_start"] is not None or STATE["timer_acc"] > 0:
            secs = int(timer_elapsed())
            l2 = ("Job " if STATE["timer_start"] is not None else "Paused ") + \
                "%02d:%02d:%02d" % (secs // 3600, (secs // 60) % 60, secs % 60)
        elif locks is not None and _caps_on():
            l2 = "CAPS LOCK ON"
        else:
            l2 = "Profile: " + str(info.get("name", "Page %d" % (layer + 1)))
        self._set_lines(l1, l2)


def _caps_on():
    try:
        return bool(locks.get_caps_lock())
    except Exception:
        return False


# ---------------------------------------------------------------------
# Notifications: school timetable, Freshdesk (Wi-Fi), clock sync (NTP)
# ---------------------------------------------------------------------
NET = {"session": None, "pool": None}


class Notifier(_Hooks):
    def __init__(self):
        self._next_tt = 0
        self._fired = set()
        self._day = None
        self._next_fd = time.monotonic() + 20
        self._next_wifi = 0
        self._next_ntp = 0
        self.fd_seen = None
        self.fd_count = None
        self.fd_error = ""
        self.wifi_error = ""

    def reset_network(self):
        NET["session"] = None
        self._next_wifi = 0
        self._next_fd = time.monotonic() + 5
        self.fd_seen = None

    def before_matrix_scan(self, keyboard):
        now = time.monotonic()
        if now >= self._next_tt:
            self._next_tt = now + 15
            self._check_timetable()
        # Network calls briefly pause the keyboard, so only make them while you're idle
        if deck_runtime.asleep or sequencer.q or now - deck_runtime.last_activity < 4:
            return
        if SETTINGS["freshdesk_poll"] and SETTINGS["freshdesk_domain"] and now >= self._next_fd:
            self._next_fd = now + max(30, SETTINGS["freshdesk_poll"])
            self._poll_freshdesk()
        elif not clock_valid() and now >= self._next_ntp and os.getenv("MF_WIFI_SSID"):
            self._next_ntp = now + 3600
            self._ntp()

    def _check_timetable(self):
        tt = SETTINGS["timetable"]
        if not tt or not clock_valid():
            return
        t = time.localtime()
        if t.tm_wday > 4:
            return
        day = (t.tm_year, t.tm_yday)
        if day != self._day:
            self._day = day
            self._fired = set()
        mins = t.tm_hour * 60 + t.tm_min
        lead = SETTINGS["timetable_lead"]
        for i, (m, lbl) in enumerate(tt):
            if mins == m - lead and i not in self._fired:
                self._fired.add(i)
                show_alert(lbl + (" in %d min" % lead if lead else " now"), SETTINGS["timetable_color"], "PULSE", 60)

    def ensure_wifi(self):
        ssid = os.getenv("MF_WIFI_SSID")
        if not ssid:
            self.wifi_error = "Wi-Fi not set up"
            return False
        try:
            import wifi
            import socketpool
            import ssl
            import adafruit_requests
        except ImportError as err:
            self.wifi_error = "Missing library: " + str(err)
            return False
        if wifi.radio.ipv4_address is None:
            if time.monotonic() < self._next_wifi:
                return False
            self._next_wifi = time.monotonic() + 300
            try:
                wifi.radio.connect(ssid, os.getenv("MF_WIFI_PASSWORD") or "", timeout=10)
            except Exception as err:
                self.wifi_error = "Wi-Fi: " + str(err)
                return False
        if NET["session"] is None:
            NET["pool"] = socketpool.SocketPool(wifi.radio)
            NET["session"] = adafruit_requests.Session(NET["pool"], ssl.create_default_context())
        self.wifi_error = ""
        return True

    def _poll_freshdesk(self):
        key = os.getenv("MF_FRESHDESK_KEY")
        if not key:
            self.fd_error = "No API key on deck"
            return
        if not self.ensure_wifi():
            return
        dom = SETTINGS["freshdesk_domain"]
        if "." not in dom:
            dom += ".freshdesk.com"
        auth = binascii.b2a_base64((key + ":X").encode()).strip().decode()
        try:
            r = NET["session"].get("https://%s/api/v2/tickets?filter=new_and_my_open&per_page=20" % dom,
                                   headers={"Authorization": "Basic " + auth,
                                            "Content-Type": "application/json"}, timeout=8)
            code = r.status_code
            data = r.json() if code == 200 else None
            r.close()
        except Exception as err:
            self.fd_error = "Request failed: " + str(err)
            NET["session"] = None
            return
        if code != 200:
            self.fd_error = "Freshdesk replied HTTP %d" % code
            return
        tickets = {}
        for t in data if isinstance(data, list) else []:
            tickets[t.get("id")] = str(t.get("subject", ""))
        self.fd_count = len(tickets)
        self.fd_error = ""
        if self.fd_seen is not None:
            new = [i for i in tickets if i not in self.fd_seen]
            if new:
                head = "New: " if len(new) == 1 else "%d new: " % len(new)
                show_alert(head + tickets[max(new)], SETTINGS["freshdesk_color"], "PULSE", 0)
            self.fd_seen |= set(tickets)
            if len(self.fd_seen) > 500:
                self.fd_seen = set(tickets)
        else:
            self.fd_seen = set(tickets)
        gc.collect()

    def _ntp(self):
        if not self.ensure_wifi():
            return
        try:
            import adafruit_ntp
            ntp = adafruit_ntp.NTP(NET["pool"], tz_offset=0)
            utc = time.mktime(ntp.datetime)
            off = uk_offset(utc) if SETTINGS["timezone"] == "UK" else 0
            t = time.localtime(utc + off)
            set_clock(t.tm_year, t.tm_mon, t.tm_mday, t.tm_hour, t.tm_min, t.tm_sec, t.tm_wday)
        except Exception as err:
            print("NTP failed:", err)


def write_secrets(updates):
    # Stores Wi-Fi / Freshdesk secrets in settings.toml (kept out of config.json)
    names = {"wifi_ssid": "MF_WIFI_SSID", "wifi_password": "MF_WIFI_PASSWORD",
             "freshdesk_key": "MF_FRESHDESK_KEY"}
    upd = {}
    for k, v in updates.items():
        if k in names and v is not None:
            upd[names[k]] = str(v)
    if not upd:
        return
    lines = []
    try:
        with open("settings.toml", "r") as f:
            lines = f.read().split("\n")
    except OSError:
        pass
    while lines and lines[-1].strip() == "":
        lines.pop()
    out, done = [], set()
    for ln in lines:
        k = ln.split("=", 1)[0].strip()
        if k in upd:
            done.add(k)
            if upd[k]:
                out.append('%s = "%s"' % (k, upd[k].replace("\\", "\\\\").replace('"', '\\"')))
        else:
            out.append(ln)
    for k, v in upd.items():
        if k not in done and v:
            out.append('%s = "%s"' % (k, v.replace("\\", "\\\\").replace('"', '\\"')))
    while out and out[-1] == "":
        out.pop()
    with open("settings.toml", "w") as f:
        f.write("\n".join(out) + "\n")


def _midi_on():
    try:
        import usb_midi
        return len(usb_midi.ports) > 0
    except Exception:
        return False


def deck_info():
    wifi_state = "off"
    try:
        import wifi
        ip = wifi.radio.ipv4_address
        wifi_state = ("connected " + str(ip)) if ip else (notifier.wifi_error or "not connected")
    except ImportError:
        wifi_state = "no Wi-Fi support"
    except Exception as err:
        wifi_state = str(err)
    if not os.getenv("MF_WIFI_SSID"):
        wifi_state = "not set up"
    fd = "off"
    if SETTINGS["freshdesk_domain"]:
        fd = notifier.fd_error or ("%s ticket(s) in your queue" % notifier.fd_count
                                   if notifier.fd_count is not None else "waiting for first check")
    return {
        "firmware": FIRMWARE_VERSION,
        "clock": (fmt_date() + " " + fmt_time()) if clock_valid() else "not set",
        "wifi": wifi_state,
        "freshdesk": fd,
        "freshdesk_key": bool(os.getenv("MF_FRESHDESK_KEY")),
        "pages": len(keyboard.keymap),
        "usb": usb_ready(),
        "free_memory": gc.mem_free(),
        "can_update": True,
        "usb_midi": _midi_on(),
        "stats_saved": stats.can_save,
    }


# ---------------------------------------------------------------------
# Boot
# ---------------------------------------------------------------------
try:
    if not (config and config.get("layers")):
        raise ValueError("no layers in config.json")
    load_settings(config)
    keyboard.keymap, encoder_handler.map = build_from_config(config)
    start_layer = config.get("active_layer", 0)
    if not (0 <= start_layer < len(keyboard.keymap)):
        start_layer = 0
    keyboard.active_layers = [start_layer]
    print("Loaded", len(keyboard.keymap), "page(s) from config.json")
except Exception as err:
    print("Config keymap failed, using built-in defaults:", err)
    # Original built-in keymap (6 Main Buttons + 2 Encoder Buttons)
    keyboard.keymap = [
        [
            KC.LCTRL(KC.C),      KC.LCTRL(KC.V),      KC.LCTRL(KC.Z),
            KC.LALT(KC.TAB),     KC.LWIN(KC.D),       KC.MUTE,
            KC.MEDIA_PLAY_PAUSE, KC.MEDIA_NEXT_TRACK
        ]
    ]
    encoder_handler.map = [
        (
            (KC.VOLU, KC.VOLD),
            (KC.MW_UP, KC.MW_DN),
        )
    ]

# ---------------------------------------------------------------------
# Usage counts (how often each key / knob is used, per page)
# ---------------------------------------------------------------------
class Stats:
    PATH = FS_ROOT + "stats.json"

    def __init__(self):
        self.data = {}
        self.since = ""
        self.dirty = False
        self.next_save = time.monotonic() + 600
        self.can_save = True
        try:
            with open(self.PATH, "r") as f:
                d = json.load(f)
            self.data = d.get("counts", {}) if isinstance(d, dict) else {}
            self.since = d.get("since", "") if isinstance(d, dict) else ""
        except (OSError, ValueError):
            pass

    def hit(self, slot):
        try:
            layer = keyboard.active_layers[0] if keyboard.active_layers else 0
            name = str(config["layers"][layer].get("name", "Page %d" % (layer + 1)))
        except Exception:
            name = "Page"
        d = self.data.get(name)
        if d is None:
            if len(self.data) >= 40:
                return
            d = self.data[name] = {}
        d[slot] = d.get(slot, 0) + 1
        if not self.since:
            self.since = fmt_date()
        self.dirty = True

    def reset(self):
        self.data = {}
        self.since = fmt_date()
        self.dirty = True
        self.save()

    def save(self):
        if not (self.dirty and self.can_save):
            return
        try:
            with open(FS_ROOT + "stats.tmp", "w") as f:
                json.dump({"since": self.since, "counts": self.data}, f)
            try:
                os.remove(self.PATH)
            except OSError:
                pass
            os.rename(FS_ROOT + "stats.tmp", self.PATH)
            self.dirty = False
        except OSError:
            self.can_save = False   # drive is read-only (USB drive mode on) - keep counting in memory

    def maybe_save(self, now):
        if self.dirty and now >= self.next_save and now - deck_runtime.last_activity > 5 and not sequencer.q:
            self.next_save = now + 600
            self.save()


stats = Stats()


# ---------------------------------------------------------------------
# Input gate: runs before every other module.
# - If the screen is off (asleep / screen saver) or an alert is showing,
#   the first press or turn only wakes it - nothing is sent to the PC.
# - Counts usage, tells the overlay which key was used, cancels a
#   pending "press again to confirm".
# ---------------------------------------------------------------------
class InputGate(_Hooks):
    def __init__(self):
        self.swallowed = set()
        self.grace_until = 0
        self._last_turn_event = 0
        self.reboot_at = None

    def _wake(self, now, hold_for):
        was_alert = ALERT["on"]
        deck_runtime.wake()
        if was_alert:
            clear_alert()
        self.grace_until = now + hold_for

    def process_key(self, keyboard, key, is_pressed, int_coord):
        if int_coord is None or sequencer.injecting:
            return key
        now = time.monotonic()
        if is_pressed:
            if int_coord in self.swallowed:
                return None
            if now < self.grace_until or ALERT["on"] or deck_runtime.screen_off(now):
                if now >= self.grace_until:
                    self._wake(now, 0.35)
                self.swallowed.add(int_coord)
                return None
            deck_runtime.activity()
            STATE["last_coord"] = int_coord
            if CONFIRM["coord"] is not None and CONFIRM["coord"] != int_coord:
                CONFIRM["coord"] = None
            slot = ("b%d" % int_coord) if int_coord < 6 else ("c%d" % (int_coord - 6))
            stats.hit(slot)
            emit_event({"event": "key", "slot": slot})
            return key
        if int_coord in self.swallowed:
            self.swallowed.discard(int_coord)
            return None
        return key

    def turn(self, encoder_id, direction):
        # Returns True if this knob step should be ignored
        now = time.monotonic()
        if now < self.grace_until:
            self.grace_until = now + 0.3      # keep ignoring until the knob stops
            return True
        if ALERT["on"] or deck_runtime.screen_off(now):
            self._wake(now, 0.4)
            return True
        deck_runtime.activity()
        # KMK runs map[...][0] for direction -1; pairs are stored (cw, ccw) unless reversed
        idx = 0 if direction < 0 else 1
        try:
            if ENCODER_REVERSED[encoder_id]:
                idx ^= 1
        except Exception:
            pass
        slot = "r%d%s" % (encoder_id, "cw" if idx == 0 else "ccw")
        stats.hit(slot)
        if now - self._last_turn_event > 0.12:
            self._last_turn_event = now
            emit_event({"event": "key", "slot": slot})
        return False

    def before_matrix_scan(self, keyboard):
        now = time.monotonic()
        if self.reboot_at is not None and now >= self.reboot_at:
            stats.save()
            microcontroller.reset()
        stats.maybe_save(now)


gate = InputGate()
keyboard.modules.insert(0, gate)

# Knob turns don't pass through the module chain, so hook the encoder handler too
try:
    _orig_on_move = encoder_handler.on_move_do

    def _on_move(keyboard, encoder_id, state):
        try:
            direction = state["direction"] if isinstance(state, dict) else getattr(state, "direction", 0)
        except Exception:
            direction = 0
        if gate.turn(encoder_id, direction):
            return None
        return _orig_on_move(keyboard, encoder_id, state)

    encoder_handler.on_move_do = _on_move
except Exception as err:
    print("Encoder wake hook unavailable:", err)


# ---------------------------------------------------------------------
# Firmware updates from the editor (main.py / boot.py over USB serial)
# ---------------------------------------------------------------------
UPLOAD = {"name": None, "size": 0, "crc": None, "got": 0, "seq": 0}
UPLOAD_NAMES = ("main.py", "boot.py")
_crc_table = None


def crc32_bytes(data, crc=0):
    try:
        return binascii.crc32(data, crc) & 0xFFFFFFFF
    except Exception:
        pass
    global _crc_table
    if _crc_table is None:
        _crc_table = []
        for i in range(256):
            c = i
            for _ in range(8):
                c = (c >> 1) ^ 0xEDB88320 if c & 1 else c >> 1
            _crc_table.append(c)
    crc = crc ^ 0xFFFFFFFF
    for b in data:
        crc = _crc_table[(crc ^ b) & 0xFF] ^ (crc >> 8)
    return crc ^ 0xFFFFFFFF


def upload_begin(name, size, crc):
    if name not in UPLOAD_NAMES:
        raise ValueError("Can only update main.py or boot.py")
    with open(FS_ROOT + "mf_upload.tmp", "wb"):
        pass
    UPLOAD.update({"name": name, "size": int(size), "crc": crc, "got": 0, "seq": 0})


def upload_chunk(seq, data_b64):
    if UPLOAD["name"] is None:
        raise ValueError("No update in progress")
    if int(seq) != UPLOAD["seq"]:
        raise ValueError("Update chunk out of order")
    raw = binascii.a2b_base64(data_b64)
    with open(FS_ROOT + "mf_upload.tmp", "ab") as f:
        f.write(raw)
    UPLOAD["got"] += len(raw)
    UPLOAD["seq"] += 1


def upload_end():
    name = UPLOAD["name"]
    if name is None:
        raise ValueError("No update in progress")
    UPLOAD["name"] = None
    tmp = FS_ROOT + "mf_upload.tmp"
    if UPLOAD["got"] != UPLOAD["size"]:
        os.remove(tmp)
        raise ValueError("Update incomplete (%d of %d bytes)" % (UPLOAD["got"], UPLOAD["size"]))
    if UPLOAD["crc"] is not None:
        crc = 0
        with open(tmp, "rb") as f:
            while True:
                b = f.read(1024)
                if not b:
                    break
                crc = crc32_bytes(b, crc)
        if crc != int(UPLOAD["crc"]):
            os.remove(tmp)
            raise ValueError("Update damaged in transfer - nothing was changed")
    dest = FS_ROOT + name
    bak = FS_ROOT + name.replace(".py", ".bak.py")
    try:
        os.remove(bak)
    except OSError:
        pass
    had_old = True
    try:
        os.rename(dest, bak)
    except OSError:
        had_old = False
    try:
        os.rename(tmp, dest)
    except OSError:
        if had_old:
            os.rename(bak, dest)
        raise
    return name


deck_runtime = DeckRuntime()
keyboard.modules.append(deck_runtime)
notifier = Notifier()
keyboard.modules.append(notifier)


def apply_config(new_cfg):
    # Rebuild everything from a freshly saved config without restarting
    global config
    load_settings(new_cfg)
    keymap, enc_map = build_from_config(new_cfg)
    keyboard.keymap = keymap
    encoder_handler.map = enc_map
    current = keyboard.active_layers[0] if keyboard.active_layers else 0
    if not (0 <= current < len(keymap)):
        keyboard.active_layers = [0]
    config = new_cfg
    if STATE["status"] is not None and STATE["status"] >= len(statuses()):
        STATE["status"] = None
    notifier.reset_network()
    deck_runtime.apply_rgb(new_cfg)
    deck_runtime._page_sent = None
    print("Applied", len(keymap), "page(s) from editor")
    emit_event({"event": "config"})

if __name__ == '__main__':
    keyboard.go()
