# MacroForge — Stream Deck & IT Macro Pad

A 6-key macro pad with two rotary knobs, a 0.91" OLED and RGB lighting, built on the **Raspberry Pi Pico 2 W** with **CircuitPython** and **KMK**. Everything is configured from a browser editor — no software to install — and changes apply instantly over USB.

Built by a school IT technician, so it's packed with IT tools, but it works just as well as a general stream deck or productivity pad.

**🖨️ Print files:** [MakerWorld — MacroForge](LINK-TO-YOUR-MAKERWORLD-MODEL)
**🌐 Open the editor:** https://YOUR-USERNAME.github.io/macroforge/editor/editor.html *(Chrome or Edge)*

---

## Features

**Hardware**
- 6 programmable keys + 2 rotary encoders with push switches (12 inputs per page)
- 0.91" OLED showing page name, status, timers and alerts
- 13 × WS2812B RGB LEDs for lighting, status lights and notifications
- Unlimited pages, switched with the right knob

**Key actions**
- Shortcuts, single keys, media keys, mouse scroll
- Open any website or app, or run commands (optionally as administrator)
- Type text, with `{clipboard}`, `{date}`, `{time}` and `{timer}` placeholders
- Macros: keys, text, pauses, status lights and page changes in sequence
- TapDance (different actions for 1×, 2× or 3× taps) and hold actions
- "Press again to confirm" for risky actions
- 80+ ready-made actions: Windows tools, admin consoles, Remote Desktop Manager, Freshdesk, Teams and more

**Browser editor**
- Live replica of the pad — click a key and edit it
- Drag and drop app shortcuts, links, `.url` and `.rdp` files onto keys
- Record shortcuts from your keyboard, undo/redo, import/export, printable cheat sheet
- Usage heatmap showing which keys you actually use
- Loads the layout back from the pad, so you can edit it from any PC
- One-click firmware updates over USB

**Lighting & status**
- Solid, breathing, pulse and colour-cycle effects, plus a colour per page
- Status lights (Busy, Away, In a lesson…) on the LEDs and OLED
- Alerts that pulse the LEDs and scroll a message on the OLED

**Smart features**
- On-screen overlay for Windows showing what each key does — movable, resizable, highlights keys as you press them
- Auto page switching depending on the app you're using
- Job timer that can type the time spent into a ticket
- School bell timetable alerts
- Optional Freshdesk new-ticket alerts over Wi-Fi
- Server/printer-down alerts from a PowerShell script
- Sleeps when the PC is off, quiet hours, anti-burn-in screen saver, and the first press only wakes the screen

---

## What's in this repo

```
firmware/   main.py, boot.py              -> copy to the pad (CIRCUITPY drive)
editor/     editor.html                   -> open in Chrome or Edge
pc-tools/   MacroForge-Overlay.ps1        -> on-screen overlay (Windows)
            Start MacroForge Overlay.bat  -> double-click to start the overlay
            MacroForge-Alerts.ps1         -> server/printer-down alerts (Windows)
            Start MacroForge Alerts.bat   -> double-click to start the alerts
```

---

## Components

| Qty | Part | Notes |
|---|---|---|
| 1 | Raspberry Pi Pico 2 W (RP2350) | Plain Pico 2 also works, without the Wi-Fi features |
| 1 | 0.91" SSD1306 OLED, 128×32, I2C | Address 0x3C |
| 2 | EC11 rotary encoder with push switch | |
| 6 | Push buttons / key switches + keycaps | |
| 13 | WS2812B LEDs | Strip or individual |
| — | Hookup wire | |
| 1 | Micro-USB cable | Data cable, not charge-only |

Printed case, print settings and assembly: see the [MakerWorld page](LINK-TO-YOUR-MAKERWORLD-MODEL).

---

## Wiring

Buttons and encoders use the Pico's internal pull-ups, so each one connects its pin to **GND** when pressed. No resistors needed.

| Component | Pin on component | Pico 2 W |
|---|---|---|
| WS2812B LEDs | DIN | **GP0** |
| | 5V / GND | VBUS / GND |
| OLED | SDA / SCL | **GP2** / **GP3** |
| | VCC / GND | 3V3 / GND |
| Left encoder (media) | A / B / Switch | **GP5** / **GP6** / **GP7** |
| | C (middle) + other switch leg | GND |
| Buttons 1–3 (top row) | one leg each | **GP8**, **GP9**, **GP10** |
| Buttons 4–6 (bottom row) | one leg each | **GP11**, **GP12**, **GP13** |
| All buttons | other leg | GND |
| Right encoder (pages) | A / B / Switch | **GP16** / **GP17** / **GP18** |
| | C (middle) + other switch leg | GND |

If a knob turns the wrong way, change `ENCODER_REVERSED = [False, False]` in `main.py` instead of rewiring.

---

## Installing

### 1. CircuitPython
1. Download **CircuitPython 10.x** for the **Raspberry Pi Pico 2 W** from [circuitpython.org](https://circuitpython.org/board/raspberry_pi_pico2_w/).
2. Hold **BOOTSEL** while plugging the Pico in — an **RP2350** drive appears.
3. Drag the `.uf2` onto it. It restarts as a **CIRCUITPY** drive.

### 2. Libraries
1. Download [KMK firmware](https://github.com/KMKfw/kmk_firmware) and copy its `kmk` folder to the root of CIRCUITPY.
2. From the [Adafruit CircuitPython Library Bundle](https://circuitpython.org/libraries) (matching your CircuitPython version), copy into CIRCUITPY's `lib` folder:
   - `adafruit_displayio_ssd1306.mpy`
   - `adafruit_display_text/`
   - `neopixel.mpy`
   - *Optional, for Freshdesk alerts and Wi-Fi clock:* `adafruit_requests.mpy`, `adafruit_connection_manager.mpy`, `adafruit_ntp.mpy`

### 3. MacroForge
1. Copy `firmware/boot.py` and `firmware/main.py` to the root of CIRCUITPY.
2. Unplug and replug. Keys, knobs, screen and LEDs should all work with the default layout.

### 4. Let the editor save to the pad
While the CIRCUITPY drive is visible, the pad can't save layouts from the editor. To switch to editor mode:
1. Open the pad in [Thonny](https://thonny.org/) (interpreter: CircuitPython) and press **STOP**.
2. In the Shell run:
   ```python
   import microcontroller; microcontroller.nvm[0] = 0
   microcontroller.reset()
   ```
The drive is now hidden and the editor can save. To get the drive back, run the same with `nvm[0] = 1`.

From now on, firmware updates are one click: **Pad Firmware → Update pad** in the editor.

---

## Using it

1. Open the [online editor](https://YOUR-USERNAME.github.io/macroforge/editor/editor.html) or `editor/editor.html` in **Chrome or Edge**.
2. Click **Connect** and choose a "CircuitPython CDC" port.
3. Click a key, choose what it does, then **Save to Device**. It works instantly.

**PC tools (Windows, optional)** — keep each `.ps1` and its `.bat` in the same folder:
- **Overlay:** double-click `Start MacroForge Overlay.bat`. Drag to move, scroll to resize, right-click for options.
- **Alerts:** put your server/printer names in `MacroForge-Alerts.ps1` (open with Notepad), then double-click `Start MacroForge Alerts.bat`.
- Use the overlay **or** the alerts script, not both — the overlay has the same ping alerts built in (`$PingTargets`).

> **Windows blocked the file?** Right-click it → **Properties** → tick **Unblock** → **OK**. If you see "Windows protected your PC", click **More info → Run anyway**.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| Save says "Drive is read-only" | Do step 4 of Installing |
| Editor says the port is busy | The overlay, alert script or Thonny is using it — connect again and pick the other CircuitPython port |
| Blue screen: `IntcUSB.sys` DRIVER_IRQL_NOT_LESS_OR_EQUAL | Intel Smart Sound Technology clashes with CircuitPython's USB MIDI. The included `boot.py` turns MIDI off — make sure you're using it, then unplug and replug |
| Websites/apps open with missing letters | Raise **Run box wait** and **Key delay** in Deck Settings, and set the right keyboard layout (UK/US) |
| Opening websites/apps does nothing | Your network may block the Win+R box for some accounts |
| Freshdesk alerts don't work | Needs a normal password Wi-Fi (not WPA2-Enterprise) and the optional libraries above. Use **Check deck** in the editor to see what's wrong |

---

## Privacy

Your layout lives on the pad (`config.json`) and in your browser. Wi-Fi details and the Freshdesk API key are stored only on the pad in `settings.toml` — never in exported layouts. Don't share those two files.

---

## Credits

- [CircuitPython](https://circuitpython.org/) by Adafruit
- [KMK firmware](https://github.com/KMKfw/kmk_firmware) (GPL-3.0)
- `boot.py` based on the [Pog](https://github.com/JanLunge/pog) keyboard configurator
- Adafruit SSD1306, display_text and NeoPixel libraries
This Code is made using AI

## Licence

[Your chosen licence — see LICENSE]
