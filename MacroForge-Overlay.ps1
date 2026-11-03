<#
  MacroForge overlay  (v2)
  ------------------------
  A small see-through panel that shows what each key and knob does on the page
  the pad is on right now. It follows the pad live: change page and it changes
  with it, and the key you press lights up. It also shows your status light
  (BUSY, AWAY...) and any alerts.

  - Drag it anywhere to move it.
  - Scroll over it (or drag its bottom-right corner) to make it bigger or smaller.
  - Right-click it for options: lock it in place (clicks then pass through it),
    see-through level, auto page switching, reset.
  - Its position and size are remembered.
  - Auto page switching: set "Switch to this page automatically when using" on a
    page in the editor, and the overlay moves the pad to that page when you use
    that app.

  Start it with "Start MacroForge Overlay.bat" (keep both files in the same folder).
  Needs pad firmware 2.2 or newer for auto page switching and key highlights.
#>

# ---------------- Settings ----------------
# These are only the starting values - once you move, resize or change the overlay
# with the mouse, your choices are remembered instead.
$Corner          = 'BottomRight'   # first-run position: TopLeft, TopRight, BottomLeft or BottomRight
$Scale           = 1.0             # 0.8 = smaller, 1.25 = bigger
$Opacity         = 0.92            # 0.3 (faint) up to 1.0 (solid)
$AutoHideSeconds = 0               # 0 = always visible. e.g. 6 = appear for 6 s after a page change, then hide
$ComPort         = ''              # blank = find the pad automatically, or e.g. 'COM6'

# Optional: watch servers / printers / PCs and flash the pad if one stops answering ping.
# Use this INSTEAD of running MacroForge-Alerts.ps1 at the same time (they'd compete for the pad's ports).
$PingTargets     = @()             # e.g. @('dc01', 'print-server', '10.0.0.1')
$PingInterval    = 60              # seconds between checks
# ------------------------------------------

$ErrorActionPreference = 'Stop'
$NeedFirmware = '2.2'
$SettingsPath = Join-Path ([Environment]::GetFolderPath('ApplicationData')) 'MacroForge\overlay.json'

# Symbols are built from character codes so this file stays plain ASCII
# (Windows PowerShell 5.1 misreads non-ASCII characters in scripts saved without a BOM).
$Sym = @{
    Cw    = [string][char]0x21BB
    Ccw   = [string][char]0x21BA
    Dot   = [string][char]0x25CF
    Link  = [string][char]0x2197
    Run   = [string][char]0x25B6
    Admin = [string][char]0x26E8
    Times = [string][char]0x00D7
    Mid   = [string][char]0x00B7
}

$ModNames = @{ LCTRL = 'Ctrl'; LSFT = 'Shift'; LALT = 'Alt'; LWIN = 'Win'; RCTRL = 'RCtrl'; RSFT = 'RShift'; RALT = 'AltGr'; RWIN = 'RWin' }
$KeyNames = @{
    ESCAPE = 'Esc'; BSPACE = 'Bksp'; ENTER = 'Enter'; SPACE = 'Space'; TAB = 'Tab'; DELETE = 'Del'; INSERT = 'Ins'
    HOME = 'Home'; END = 'End'; PGUP = 'PgUp'; PGDOWN = 'PgDn'; UP = 'Up'; DOWN = 'Down'; LEFT = 'Left'; RIGHT = 'Right'
    MINUS = '-'; EQUAL = '='; LBRACKET = '['; RBRACKET = ']'; BSLASH = '\'; SCOLON = ';'; QUOTE = "'"; GRAVE = '`'
    COMMA = ','; DOT = '.'; SLASH = '/'; CAPSLOCK = 'Caps'; PSCREEN = 'PrtSc'; APPLICATION = 'Menu'
    MUTE = 'Mute'; VOLU = 'Volume up'; VOLD = 'Volume down'; MEDIA_PLAY_PAUSE = 'Play/Pause'
    MEDIA_NEXT_TRACK = 'Next track'; MEDIA_PREV_TRACK = 'Prev track'; MEDIA_STOP = 'Stop'
    BRIGHTNESS_UP = 'Screen +'; BRIGHTNESS_DOWN = 'Screen -'
    MS_WH_UP = 'Scroll up'; MS_WH_DOWN = 'Scroll down'; MW_UP = 'Scroll up'; MW_DN = 'Scroll down'; NO = ''
}

# ---------------- State ----------------
$script:Port        = $null
$script:Buf         = ''
$script:Config      = $null
$script:Page        = 0
$script:StatusIndex = $null
$script:AlertText   = $null
$script:AlertColor  = $null
$script:PageChanged = $false
$script:Message     = 'Looking for the pad...'
$script:FwWarn      = ''
$script:Firmware    = ''
$script:NextConnect = [DateTime]::MinValue
$script:Flash       = @{}            # slot -> time it stops being highlighted
$script:LastTarget  = -2             # auto switch: -2 unknown, -1 no rule matched, else page
$script:LastApp     = ''
$script:LastTitle   = ''
$script:PingTasks   = @{}
$script:PingFails   = @{}
$script:Down        = @{}
$script:NextPing    = [DateTime]::MinValue

# ---------------- Remembered overlay settings ----------------
function Read-OverlaySettings {
    $s = [ordered]@{ Left = $null; Top = $null; Scale = [double]$Scale; Opacity = [double]$Opacity; Locked = $false; AutoSwitch = $true }
    try {
        if (Test-Path $SettingsPath) {
            $j = Get-Content -Raw -Path $SettingsPath | ConvertFrom-Json
            foreach ($k in @('Left', 'Top', 'Scale', 'Opacity', 'Locked', 'AutoSwitch')) {
                if ($null -ne $j.$k) { $s[$k] = $j.$k }
            }
        }
    }
    catch { }
    $s.Scale = [Math]::Max(0.5, [Math]::Min(2.5, [double]$s.Scale))
    $s.Opacity = [Math]::Max(0.3, [Math]::Min(1.0, [double]$s.Opacity))
    $s.Locked = [bool]$s.Locked
    $s.AutoSwitch = [bool]$s.AutoSwitch
    return $s
}

function Save-OverlaySettings($s) {
    try {
        $dir = Split-Path $SettingsPath
        if (-not (Test-Path $dir)) { [void](New-Item -ItemType Directory -Path $dir -Force) }
        ([pscustomobject]$s | ConvertTo-Json) | Set-Content -Path $SettingsPath -Encoding UTF8
    }
    catch { }
}

$script:Prefs = Read-OverlaySettings

# ---------------- Describing actions ----------------
function Get-KeyName([string]$k) {
    if ($ModNames.ContainsKey($k)) { return $ModNames[$k] }
    if ($KeyNames.ContainsKey($k)) { return $KeyNames[$k] }
    if ($k -match '^N(\d)$') { return $Matches[1] }
    return $k
}

function Get-StatusName($i) {
    $st = @()
    if ($script:Config -and $script:Config.rgb) { $st = @($script:Config.rgb.statuses) }
    if ($null -ne $i -and [int]$i -ge 0 -and [int]$i -lt $st.Count) { return [string]$st[[int]$i].name }
    return 'status ' + ([int]$i + 1)
}

function Get-ActionText($a) {
    if ($null -eq $a) { return '' }
    switch ([string]$a.type) {
        'combo' {
            $names = @(@($a.keys) | ForEach-Object { Get-KeyName ([string]$_) })
            return ($names -join '+')
        }
        'key' { return (Get-KeyName ([string]$a.key)) }
        'system' {
            if ($a.action -eq 'NEXT_LAYER') { return 'Next page' }
            if ($a.action -eq 'PREV_LAYER') { return 'Previous page' }
            if ($a.action -eq 'GOTO_LAYER') { return 'Page ' + ([int]$a.layer + 1) }
            return [string]$a.action
        }
        'url' {
            $u = ([string]$a.url) -replace '^https?://', ''
            return $Sym.Link + ' ' + ($u -replace '/$', '')
        }
        'run' {
            $icon = $Sym.Run
            if ($a.admin) { $icon = $Sym.Admin }
            return $icon + ' ' + [string]$a.command
        }
        'text' { return 'Types "' + (([string]$a.text) -replace '\r?\n', ' ') + '"' }
        'macro' {
            $n = @($a.steps).Count
            if ($n -eq 1) { return 'Macro (1 step)' }
            return 'Macro (' + $n + ' steps)'
        }
        'status' {
            if ($a.action -eq 'CLEAR') { return 'Status off' }
            if ($a.action -eq 'CYCLE') { return 'Cycle status' }
            if ($a.action -eq 'SET') { return 'Status: ' + (Get-StatusName $a.index) }
            return 'Toggle ' + (Get-StatusName $a.index)
        }
        'timer' {
            if ($a.action -eq 'TYPE') { return 'Type time spent' }
            if ($a.action -eq 'RESET') { return 'Reset timer' }
            return 'Timer start/pause'
        }
    }
    return ''
}

function Get-ButtonInfo($b) {
    $label = [string]$b.label
    if ($b.type -eq 'tapdance') {
        $sub = '1' + $Sym.Times + ' ' + (Get-ActionText $b.single_tap) + '   2' + $Sym.Times + ' ' + (Get-ActionText $b.double_tap) +
               '   3' + $Sym.Times + ' ' + (Get-ActionText $b.triple_tap)
    }
    else {
        $sub = Get-ActionText $b
        if ($b.confirm) { $sub = $sub + '  (press twice)' }
        if ($b.hold) {
            $holdText = 'Hold: ' + (Get-ActionText $b.hold)
            if ($sub) { $sub = $sub + '   ' + $holdText } else { $sub = $holdText }
        }
    }
    if (-not $label) { $label = '-' }
    return [pscustomobject]@{ Label = $label; Sub = $sub }
}

function Get-KnobText($e, [string]$name) {
    if ($null -eq $e) { return '' }
    return $name + '  ' + $Sym.Cw + ' ' + (Get-ActionText $e.cw) + '    ' + $Sym.Ccw + ' ' + (Get-ActionText $e.ccw) +
           '    ' + $Sym.Dot + ' ' + (Get-ActionText $e.click)
}

function Get-PageModel {
    if (-not $script:Config) { return $null }
    $layers = @($script:Config.layers)
    if ($layers.Count -eq 0) { return $null }
    $p = [Math]::Max(0, [Math]::Min([int]$script:Page, $layers.Count - 1))
    $l = $layers[$p]
    $enc = @($l.encoders)
    $accent = @(0, 150, 255)
    if ($script:Config.rgb -and $script:Config.rgb.color) { $accent = @($script:Config.rgb.color) }
    if ($l.led_color) { $accent = @($l.led_color) }
    return [pscustomobject]@{
        Title  = 'Page ' + ($p + 1) + ' of ' + $layers.Count + '   ' + $Sym.Mid + '   ' + [string]$l.name
        Keys   = @(@($l.buttons) | ForEach-Object { Get-ButtonInfo $_ })
        Left   = Get-KnobText $enc[0] 'L'
        Right  = Get-KnobText $enc[1] 'R'
        Accent = $accent
    }
}

# Returns @{ Text; Color } for the chip in the corner (alert beats status), or $null
function Get-Badge {
    if ($script:AlertText) {
        $c = @(255, 0, 0)
        if ($script:AlertColor) { $c = @($script:AlertColor) }
        return @{ Text = '! ' + [string]$script:AlertText; Color = $c }
    }
    if ($null -ne $script:StatusIndex -and $script:Config -and $script:Config.rgb) {
        $st = @($script:Config.rgb.statuses)
        $i = [int]$script:StatusIndex
        if ($i -ge 0 -and $i -lt $st.Count) {
            return @{ Text = ([string]$st[$i].name).ToUpper(); Color = @($st[$i].color) }
        }
    }
    return $null
}

function Compare-Version([string]$a, [string]$b) {
    $pa = @(($a -split '\.') | ForEach-Object { [int]('0' + ($_ -replace '\D', '')) })
    $pb = @(($b -split '\.') | ForEach-Object { [int]('0' + ($_ -replace '\D', '')) })
    for ($i = 0; $i -lt [Math]::Max($pa.Count, $pb.Count); $i++) {
        $x = 0; $y = 0
        if ($i -lt $pa.Count) { $x = $pa[$i] }
        if ($i -lt $pb.Count) { $y = $pb[$i] }
        if ($x -ne $y) { return [Math]::Sign($x - $y) }
    }
    return 0
}

# ---------------- Auto page switching ----------------
# Rule formats (set per page in the editor):  app   app:title words   :title words
function Test-AppRule([string]$rule, [string]$proc, [string]$title) {
    $r = $rule.Trim()
    if (-not $r) { return 0 }
    $i = $r.IndexOf(':')
    if ($i -ge 0) { $rp = $r.Substring(0, $i).Trim(); $rt = $r.Substring($i + 1).Trim() }
    else { $rp = $r; $rt = '' }
    $rp = $rp -replace '\.exe$', ''
    if (-not $rp -and -not $rt) { return 0 }
    if ($rp -and -not [string]::Equals($rp, $proc, [StringComparison]::OrdinalIgnoreCase)) { return 0 }
    if ($rt -and ([string]$title).IndexOf($rt, [StringComparison]::OrdinalIgnoreCase) -lt 0) { return 0 }
    if ($rt) { return 2 }   # rules that also check the title win over app-only rules
    return 1
}

function Get-AutoTarget([string]$proc, [string]$title) {
    if (-not $script:Config) { return -1 }
    $layers = @($script:Config.layers)
    $best = -1; $bestScore = 0
    for ($p = 0; $p -lt $layers.Count; $p++) {
        foreach ($rule in @($layers[$p].apps)) {
            if ($null -eq $rule) { continue }
            $sc = Test-AppRule ([string]$rule) $proc $title
            if ($sc -gt $bestScore) { $best = $p; $bestScore = $sc }
        }
    }
    return $best
}

# Called whenever the app in front changes. Returns a page to switch to, or $null.
# It only acts when the matching rule changes, so changing page by hand still works.
function Step-AutoSwitch([string]$proc, [string]$title) {
    if (-not $script:Prefs.AutoSwitch -or -not $script:Config) { return $null }
    $t = Get-AutoTarget $proc $title
    $prev = $script:LastTarget
    if ($t -eq $prev) { return $null }
    $script:LastTarget = $t
    if ($t -ge 0) {
        if ($t -ne [int]$script:Page) { return $t }
        return $null
    }
    $def = -1
    if ($script:Config.settings -and $null -ne $script:Config.settings.auto_switch_default) { $def = [int]$script:Config.settings.auto_switch_default }
    if ($def -ge 0 -and $prev -ge 0 -and $def -ne [int]$script:Page -and $def -lt @($script:Config.layers).Count) { return $def }
    return $null
}

# ---------------- Talking to the pad ----------------
function Get-DeckPorts {
    if ($ComPort) { return @($ComPort) }
    # CircuitPython boards use USB vendor ID 239A. Try the data port (higher interface number) first.
    return @(Get-CimInstance Win32_PnPEntity |
        Where-Object { $_.DeviceID -match 'VID_239A' -and $_.Name -match '\(COM\d+\)' } |
        Sort-Object { if ($_.DeviceID -match 'MI_(\d+)') { - [int]$Matches[1] } else { 0 } } |
        ForEach-Object { [regex]::Match($_.Name, 'COM\d+').Value })
}

function Send-Deck([hashtable]$Message) {
    if (-not ($script:Port -and $script:Port.IsOpen)) { return }
    try { $script:Port.WriteLine(($Message | ConvertTo-Json -Compress -Depth 5)) }
    catch { Disconnect-Deck }
}

function Disconnect-Deck {
    if ($script:Port) {
        try { $script:Port.Close() } catch { }
        $script:Port.Dispose()
        $script:Port = $null
    }
}

function Connect-Deck {
    foreach ($com in @(Get-DeckPorts)) {
        $p = $null
        try {
            $p = New-Object System.IO.Ports.SerialPort $com, 115200
            $p.DtrEnable = $true
            $p.NewLine = "`n"
            $p.ReadTimeout = 50
            $p.WriteTimeout = 1000
            $p.Open()
            $script:Port = $p
            $script:Buf = ''
            $script:LastTarget = -2
            $script:Message = 'Connected on ' + $com + ' - waiting for the pad...'
            Send-Deck @{ cmd = 'get_config' }
            Send-Deck @{ cmd = 'get_state' }
            Send-Deck @{ cmd = 'info' }
            return $true
        }
        catch {
            if ($p) { $p.Dispose() }   # busy (the editor or another script has it) - try the next port
        }
    }
    $script:Message = 'Pad not found or both ports busy - retrying...'
    return $false
}

# Handles one line from the pad. Returns $true if the display needs redrawing.
function Invoke-DeckLine([string]$line) {
    $line = $line -replace '\x1b\][^\x07\x1b]*(\x07|\x1b\\)', '' -replace '\x1b\[[0-9;?]*[A-Za-z]', ''
    $j = $line.IndexOf('{')
    if ($j -lt 0) { return $false }
    try { $o = $line.Substring($j) | ConvertFrom-Json } catch { return $false }
    if ($null -eq $o) { return $false }
    if ($o.PSObject.Properties['config'] -and $o.config) {
        $script:Config = $o.config
        $script:Message = ''
        $script:LastTarget = -2
        return $true
    }
    if ($o.PSObject.Properties['state'] -and $o.state) {
        $script:Page = [int]$o.state.page
        $script:StatusIndex = $o.state.status
        $script:AlertText = $o.state.alert
        $script:AlertColor = $o.state.alert_color
        return $true
    }
    if ($o.PSObject.Properties['info'] -and $o.info) {
        $script:Firmware = [string]$o.info.firmware
        if ((Compare-Version $script:Firmware $NeedFirmware) -lt 0) {
            $script:FwWarn = 'Pad firmware ' + $script:Firmware + ' - update it from the editor (Pad Firmware > Update pad) for auto page switching and key highlights.'
        }
        else { $script:FwWarn = '' }
        return $true
    }
    switch ([string]$o.event) {
        'page'   { $script:Page = [int]$o.page; $script:PageChanged = $true; return $true }
        'status' { $script:StatusIndex = $o.index; return $true }
        'alert'  { $script:AlertText = $o.text; $script:AlertColor = $o.color; return $true }
        'config' { Send-Deck @{ cmd = 'get_config' }; return $false }
        'key'    { $script:Flash[[string]$o.slot] = (Get-Date).AddMilliseconds(380); return $false }
    }
    if ($o.status -eq 'error' -and ([string]$o.msg) -like '*No config*') {
        $script:Message = 'No layout saved on the pad yet - save one from the editor.'
        return $true
    }
    return $false
}

function Read-Deck {
    if (-not $script:Port) { return $false }
    try { $chunk = $script:Port.ReadExisting() }
    catch {
        Disconnect-Deck
        $script:Message = 'Pad disconnected - retrying...'
        return $true
    }
    if (-not $chunk) { return $false }
    $script:Buf += $chunk
    $changed = $false
    while (($i = $script:Buf.IndexOf("`n")) -ge 0) {
        $line = $script:Buf.Substring(0, $i)
        $script:Buf = $script:Buf.Substring($i + 1)
        if (Invoke-DeckLine $line) { $changed = $true }
    }
    if ($script:Buf.Length -gt 200000) { $script:Buf = '' }
    return $changed
}

# ---------------- Optional ping alerts (non-blocking) ----------------
function Update-Pings {
    if (@($PingTargets).Count -eq 0) { return }
    foreach ($t in @($script:PingTasks.Keys)) {
        $task = $script:PingTasks[$t]
        if ($null -ne $task -and -not $task.IsCompleted) { continue }
        $ok = $false
        if ($null -ne $task -and -not $task.IsFaulted -and -not $task.IsCanceled) {
            $ok = ([string]$task.Result.Status -eq 'Success')
        }
        $script:PingTasks.Remove($t)
        if ($ok) {
            $script:PingFails[$t] = 0
            if ($script:Down.ContainsKey($t)) {
                $script:Down.Remove($t)
                Send-Deck @{ cmd = 'notify'; text = "$t is back up"; color = @(0, 200, 0); effect = 'SOLID'; seconds = 20 }
            }
        }
        else {
            $script:PingFails[$t] = [int]$script:PingFails[$t] + 1
            if ($script:PingFails[$t] -ge 2 -and -not $script:Down.ContainsKey($t)) {
                $script:Down[$t] = $true
                Send-Deck @{ cmd = 'notify'; text = "$t not responding"; color = @(255, 0, 0); effect = 'PULSE'; seconds = 0 }
            }
        }
    }
    if ($script:PingTasks.Count -eq 0 -and (Get-Date) -ge $script:NextPing) {
        # Re-check a failing target sooner, so a real outage is confirmed quickly
        $soon = @($script:PingFails.Values | Where-Object { $_ -eq 1 }).Count -gt 0
        $script:NextPing = (Get-Date).AddSeconds($(if ($soon) { 10 } else { $PingInterval }))
        foreach ($t in $PingTargets) {
            try { $script:PingTasks[$t] = (New-Object System.Net.NetworkInformation.Ping).SendPingAsync($t, 2000) }
            catch { $script:PingTasks[$t] = $null }
        }
    }
}

# Lets the logic above be tested without opening a window
if ($env:MACROFORGE_NO_UI) { return }

# =====================================================================
# Window
# =====================================================================
Add-Type -AssemblyName PresentationFramework, PresentationCore, WindowsBase, System.Windows.Forms, System.Drawing
Add-Type @'
using System;
using System.Text;
using System.Runtime.InteropServices;
public static class MFWin {
    [DllImport("user32.dll")] public static extern int GetWindowLong(IntPtr hWnd, int nIndex);
    [DllImport("user32.dll")] public static extern int SetWindowLong(IntPtr hWnd, int nIndex, int dwNewLong);
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint pid);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] public static extern int GetWindowText(IntPtr hWnd, StringBuilder text, int count);
    public static string Title(IntPtr h) { var sb = new StringBuilder(512); GetWindowText(h, sb, 512); return sb.ToString(); }
}
'@

$created = $false
$mutex = New-Object System.Threading.Mutex($true, 'Local\MacroForgeOverlay', [ref]$created)
if (-not $created) {
    [void][System.Windows.Forms.MessageBox]::Show('The MacroForge overlay is already running - look for its icon in the taskbar tray.', 'MacroForge')
    return
}

[xml]$xaml = @'
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="MacroForge Overlay" WindowStyle="None" AllowsTransparency="True" Background="Transparent"
        Topmost="True" ShowInTaskbar="False" ShowActivated="False"
        SizeToContent="WidthAndHeight" ResizeMode="NoResize" FontFamily="Segoe UI">
  <Grid>
    <Grid.LayoutTransform><ScaleTransform x:Name="Zoom" ScaleX="1" ScaleY="1"/></Grid.LayoutTransform>
    <Border x:Name="Card" CornerRadius="12" Background="#E60B0F15" BorderThickness="1.5" BorderBrush="#AA3AA0FF" Padding="11,9,11,10" Margin="6" Cursor="SizeAll">
      <Grid>
        <StackPanel>
          <DockPanel LastChildFill="True" Margin="2,0,2,7">
            <Border x:Name="Chip" DockPanel.Dock="Right" CornerRadius="6" Padding="7,1,7,2" Margin="10,0,0,0" Visibility="Collapsed" VerticalAlignment="Center">
              <TextBlock x:Name="ChipText" FontSize="10.5" FontWeight="Bold" MaxWidth="170" TextTrimming="CharacterEllipsis"/>
            </Border>
            <TextBlock x:Name="Title" Foreground="#E6EDF3" FontSize="12.5" FontWeight="SemiBold" TextTrimming="CharacterEllipsis" MaxWidth="250" VerticalAlignment="Center"/>
          </DockPanel>
          <UniformGrid x:Name="Keys" Rows="2" Columns="3"/>
          <TextBlock x:Name="LeftKnob" Foreground="#8B98A9" FontSize="10.5" Margin="3,7,0,0" TextTrimming="CharacterEllipsis" MaxWidth="400" FontFamily="Consolas"/>
          <TextBlock x:Name="RightKnob" Foreground="#8B98A9" FontSize="10.5" Margin="3,2,0,0" TextTrimming="CharacterEllipsis" MaxWidth="400" FontFamily="Consolas"/>
          <TextBlock x:Name="Msg" Foreground="#F5B142" FontSize="10.5" Margin="3,6,0,0" TextWrapping="Wrap" MaxWidth="400" Visibility="Collapsed"/>
          <TextBlock x:Name="Hint" Foreground="#5B6676" FontSize="9.5" Margin="3,6,14,0" Visibility="Collapsed"
                     Text="Drag to move  -  scroll or drag the corner to resize  -  right-click for options"/>
        </StackPanel>
        <Thumb x:Name="Grip" Width="14" Height="14" HorizontalAlignment="Right" VerticalAlignment="Bottom" Margin="0,0,-6,-6" Cursor="SizeNWSE">
          <Thumb.Template>
            <ControlTemplate>
              <Canvas Background="Transparent">
                <Line X1="13" Y1="4" X2="4" Y2="13" Stroke="#5B6676" StrokeThickness="1.4"/>
                <Line X1="13" Y1="8" X2="8" Y2="13" Stroke="#5B6676" StrokeThickness="1.4"/>
              </Canvas>
            </ControlTemplate>
          </Thumb.Template>
        </Thumb>
      </Grid>
    </Border>
  </Grid>
</Window>
'@

$win = [Windows.Markup.XamlReader]::Load((New-Object System.Xml.XmlNodeReader $xaml))
$ui = @{}
foreach ($n in 'Card', 'Chip', 'ChipText', 'Title', 'Keys', 'LeftKnob', 'RightKnob', 'Msg', 'Hint', 'Zoom', 'Grip') { $ui[$n] = $win.FindName($n) }

function New-Brush($rgb, [int]$alpha = 255) {
    $c = [System.Windows.Media.Color]::FromArgb([byte]$alpha, [byte]$rgb[0], [byte]$rgb[1], [byte]$rgb[2])
    return New-Object System.Windows.Media.SolidColorBrush $c
}
$BrushKey    = New-Brush @(22, 28, 37) 235
$BrushKeyLn  = New-Brush @(42, 51, 64)
$BrushWhite  = New-Brush @(236, 242, 248)
$BrushMuted  = New-Brush @(139, 152, 169)
$BrushBlack  = New-Brush @(10, 12, 16)
$script:BrushAccent = New-Brush @(58, 160, 255)
$script:Cells = @()

function New-KeyCell($info) {
    $b = New-Object System.Windows.Controls.Border
    $b.CornerRadius = New-Object System.Windows.CornerRadius 8
    $b.Margin = New-Object System.Windows.Thickness 3
    $b.Padding = New-Object System.Windows.Thickness 8, 5, 8, 6
    $b.Width = 132
    $b.Background = $BrushKey
    $b.BorderBrush = $BrushKeyLn
    $b.BorderThickness = New-Object System.Windows.Thickness 1
    $sp = New-Object System.Windows.Controls.StackPanel
    $t1 = New-Object System.Windows.Controls.TextBlock
    $t1.Text = $info.Label
    $t1.Foreground = $BrushWhite
    $t1.FontSize = 12.5
    $t1.FontWeight = [System.Windows.FontWeights]::Bold
    $t1.TextTrimming = [System.Windows.TextTrimming]::CharacterEllipsis
    $t2 = New-Object System.Windows.Controls.TextBlock
    $t2.Text = $info.Sub
    $t2.Foreground = $BrushMuted
    $t2.FontSize = 10
    $t2.Margin = New-Object System.Windows.Thickness 0, 1, 0, 0
    $t2.TextTrimming = [System.Windows.TextTrimming]::CharacterEllipsis
    [void]$sp.Children.Add($t1)
    [void]$sp.Children.Add($t2)
    $b.Child = $sp
    return $b
}

function Update-View {
    $m = Get-PageModel
    $ui.Keys.Children.Clear()
    $script:Cells = @()
    $accent = @(58, 160, 255)
    if ($m) {
        $ui.Title.Text = $m.Title
        foreach ($k in $m.Keys) { $c = New-KeyCell $k; $script:Cells += $c; [void]$ui.Keys.Children.Add($c) }
        $ui.LeftKnob.Text = $m.Left
        $ui.RightKnob.Text = $m.Right
        $ui.LeftKnob.Visibility = 'Visible'
        $ui.RightKnob.Visibility = 'Visible'
        $accent = $m.Accent
    }
    else {
        $ui.Title.Text = 'MacroForge'
        $ui.LeftKnob.Visibility = 'Collapsed'
        $ui.RightKnob.Visibility = 'Collapsed'
    }
    $badge = Get-Badge
    if ($badge) {
        $c = $badge.Color
        $ui.Chip.Background = New-Brush $c 235
        $lum = 0.299 * $c[0] + 0.587 * $c[1] + 0.114 * $c[2]
        if ($lum -gt 160) { $ui.ChipText.Foreground = $BrushBlack } else { $ui.ChipText.Foreground = $BrushWhite }
        $ui.ChipText.Text = $badge.Text
        $ui.Chip.Visibility = 'Visible'
        $accent = $c
    }
    else { $ui.Chip.Visibility = 'Collapsed' }
    $ui.Card.BorderBrush = New-Brush $accent 190
    $script:BrushAccent = New-Brush $accent
    $msg = @($script:Message, $script:FwWarn | Where-Object { $_ }) -join "`n"
    if ($msg) { $ui.Msg.Text = $msg; $ui.Msg.Visibility = 'Visible' }
    else { $ui.Msg.Visibility = 'Collapsed' }
    Update-Flash
}

# Lights up the key / knob that was just used on the pad
function Update-Flash {
    $now = Get-Date
    for ($i = 0; $i -lt $script:Cells.Count; $i++) {
        $on = $script:Flash.ContainsKey("b$i") -and $script:Flash["b$i"] -gt $now
        $cell = $script:Cells[$i]
        if ($on) { $cell.BorderBrush = $script:BrushAccent; $cell.BorderThickness = New-Object System.Windows.Thickness 2 }
        else { $cell.BorderBrush = $BrushKeyLn; $cell.BorderThickness = New-Object System.Windows.Thickness 1 }
    }
    foreach ($k in 0, 1) {
        $tb = if ($k -eq 0) { $ui.LeftKnob } else { $ui.RightKnob }
        $on = $false
        foreach ($s in "c$k", "r${k}cw", "r${k}ccw") { if ($script:Flash.ContainsKey($s) -and $script:Flash[$s] -gt $now) { $on = $true } }
        if ($on) { $tb.Foreground = $BrushWhite } else { $tb.Foreground = $BrushMuted }
    }
    foreach ($s in @($script:Flash.Keys)) { if ($script:Flash[$s] -le $now.AddSeconds(-2)) { $script:Flash.Remove($s) } }
}

# ---------------- Position, size, see-through, lock ----------------
$script:Hwnd = [IntPtr]::Zero
function Set-Zoom([double]$s) {
    $script:Prefs.Scale = [Math]::Round([Math]::Max(0.5, [Math]::Min(2.5, $s)), 2)
    $ui.Zoom.ScaleX = $script:Prefs.Scale
    $ui.Zoom.ScaleY = $script:Prefs.Scale
}
function Set-CornerPosition([string]$corner) {
    $wa = [System.Windows.SystemParameters]::WorkArea
    $w = $win.ActualWidth; $h = $win.ActualHeight
    switch ($corner) {
        'TopLeft'    { $x = $wa.Left + 6;       $y = $wa.Top + 6 }
        'TopRight'   { $x = $wa.Right - $w - 6; $y = $wa.Top + 6 }
        'BottomLeft' { $x = $wa.Left + 6;       $y = $wa.Bottom - $h - 6 }
        default      { $x = $wa.Right - $w - 6; $y = $wa.Bottom - $h - 6 }
    }
    $win.Left = $x; $win.Top = $y
    $script:Prefs.Left = $x; $script:Prefs.Top = $y
}
function Test-OnScreen($x, $y) {
    if ($null -eq $x -or $null -eq $y) { return $false }
    $l = [System.Windows.SystemParameters]::VirtualScreenLeft
    $t = [System.Windows.SystemParameters]::VirtualScreenTop
    $r = $l + [System.Windows.SystemParameters]::VirtualScreenWidth
    $b = $t + [System.Windows.SystemParameters]::VirtualScreenHeight
    return ([double]$x -ge $l - 50 -and [double]$x -le $r - 60 -and [double]$y -ge $t - 20 -and [double]$y -le $b - 40)
}
function Set-Locked([bool]$on) {
    $script:Prefs.Locked = $on
    if ($script:Hwnd -ne [IntPtr]::Zero) {
        $ex = [MFWin]::GetWindowLong($script:Hwnd, -20)
        if ($on) { $ex = $ex -bor 0x20 } else { $ex = $ex -band (-bnot 0x20) }   # WS_EX_TRANSPARENT = clicks pass through
        [void][MFWin]::SetWindowLong($script:Hwnd, -20, $ex)
    }
    if ($on) { $ui.Grip.Visibility = 'Collapsed'; $ui.Card.Cursor = $null; $ui.Hint.Visibility = 'Collapsed' }
    else { $ui.Grip.Visibility = 'Visible'; $ui.Card.Cursor = [System.Windows.Input.Cursors]::SizeAll }
    Update-Menus
    Save-OverlaySettings $script:Prefs
}
function Set-SeeThrough([double]$o) {
    $script:Prefs.Opacity = [Math]::Max(0.3, [Math]::Min(1.0, $o))
    $win.Opacity = $script:Prefs.Opacity
    Save-OverlaySettings $script:Prefs
}

$win.Add_SourceInitialized({
    $script:Hwnd = (New-Object System.Windows.Interop.WindowInteropHelper $win).Handle
    $ex = [MFWin]::GetWindowLong($script:Hwnd, -20)
    # TOOLWINDOW (no Alt+Tab) | LAYERED | NOACTIVATE (never steals focus)
    [void][MFWin]::SetWindowLong($script:Hwnd, -20, ($ex -bor 0x80 -bor 0x80000 -bor 0x08000000))
    Set-Locked $script:Prefs.Locked
})

# Drag to move
$win.Add_MouseLeftButtonDown({
    param($s, $e)
    if ($script:Prefs.Locked) { return }
    if ($e.OriginalSource -is [System.Windows.Controls.Primitives.Thumb]) { return }
    try { $win.DragMove() } catch { }
    $script:Prefs.Left = $win.Left; $script:Prefs.Top = $win.Top
    Save-OverlaySettings $script:Prefs
})
# Scroll to resize
$win.Add_MouseWheel({
    param($s, $e)
    if ($script:Prefs.Locked) { return }
    Set-Zoom ($script:Prefs.Scale + 0.05 * [Math]::Sign($e.Delta))
    Save-OverlaySettings $script:Prefs
})
# Corner grip to resize
$ui.Grip.Add_DragDelta({
    param($s, $e)
    $w = [Math]::Max(150.0, $win.ActualWidth)
    Set-Zoom ($script:Prefs.Scale * (1 + $e.HorizontalChange / $w))
})
$ui.Grip.Add_DragCompleted({ Save-OverlaySettings $script:Prefs })
$win.Add_MouseEnter({ if (-not $script:Prefs.Locked) { $ui.Hint.Visibility = 'Visible' } })
$win.Add_MouseLeave({ $ui.Hint.Visibility = 'Collapsed' })

function Show-Overlay {
    if (-not $script:UserHidden) { $win.Visibility = 'Visible' }
    if ($AutoHideSeconds -gt 0) { $script:HideAt = (Get-Date).AddSeconds($AutoHideSeconds) }
}
function Switch-Overlay {
    $script:UserHidden = -not $script:UserHidden
    if ($script:UserHidden) { $win.Visibility = 'Hidden' } else { $win.Visibility = 'Visible'; Show-Overlay }
    Update-Menus
}
function Reset-Overlay {
    Set-Zoom ([double]$Scale)
    $win.UpdateLayout()
    Set-CornerPosition $Corner
    Set-SeeThrough ([double]$Opacity)
    Set-Locked $false
}

# ---------------- Menus (right-click the overlay, or the tray icon) ----------------
function New-WpfItem([string]$text, [scriptblock]$onClick) {
    $mi = New-Object System.Windows.Controls.MenuItem
    $mi.Header = $text
    if ($onClick) { $mi.Add_Click($onClick) }
    return $mi
}
$cm = New-Object System.Windows.Controls.ContextMenu
$wLock = New-WpfItem 'Lock in place (clicks pass through)' { Set-Locked $true }
$wAuto = New-WpfItem 'Auto-switch pages by app' { $script:Prefs.AutoSwitch = -not $script:Prefs.AutoSwitch; $script:LastTarget = -2; Save-OverlaySettings $script:Prefs; Update-Menus }
$wAuto.IsCheckable = $false
$wSee = New-WpfItem 'See-through' $null
foreach ($pct in 100, 90, 75, 60, 45) {
    $it = New-WpfItem ("$pct%") { param($s, $e) Set-SeeThrough ([double]$s.Tag) }
    $it.Tag = $pct / 100.0
    [void]$wSee.Items.Add($it)
}
$wApp = New-WpfItem 'Current app: -' $null
$wApp.IsEnabled = $false
$wCopy = New-WpfItem 'Copy app name (for page rules)' { if ($script:LastApp) { [System.Windows.Clipboard]::SetText($script:LastApp) } }
$wReset = New-WpfItem 'Reset size and position' { Reset-Overlay }
$wHide = New-WpfItem 'Hide (click the tray icon to show)' { Switch-Overlay }
$wExit = New-WpfItem 'Exit' { Stop-Overlay }
foreach ($i in $wLock, $wAuto, $wSee, (New-Object System.Windows.Controls.Separator), $wApp, $wCopy,
               (New-Object System.Windows.Controls.Separator), $wReset, $wHide, $wExit) { [void]$cm.Items.Add($i) }
$win.ContextMenu = $cm
$cm.Add_Opened({ Update-Menus })

$tray = New-Object System.Windows.Forms.NotifyIcon
$tray.Icon = [System.Drawing.SystemIcons]::Application
$tray.Text = 'MacroForge overlay'
$tray.Visible = $true
$menu = New-Object System.Windows.Forms.ContextMenuStrip
$tShow = $menu.Items.Add('Hide overlay');               $tShow.Add_Click({ Switch-Overlay })
$tLock = $menu.Items.Add('Unlock (move and resize)');   $tLock.Add_Click({ Set-Locked (-not $script:Prefs.Locked) })
$tAuto = $menu.Items.Add('Auto-switch pages by app');   $tAuto.Add_Click({ $script:Prefs.AutoSwitch = -not $script:Prefs.AutoSwitch; $script:LastTarget = -2; Save-OverlaySettings $script:Prefs; Update-Menus })
$tReset = $menu.Items.Add('Reset size and position');   $tReset.Add_Click({ Reset-Overlay })
$tRecon = $menu.Items.Add('Reconnect to pad');          $tRecon.Add_Click({ Disconnect-Deck; $script:NextConnect = [DateTime]::MinValue; $script:Message = 'Reconnecting...'; Update-View })
[void]$menu.Items.Add((New-Object System.Windows.Forms.ToolStripSeparator))
$tExit = $menu.Items.Add('Exit');                       $tExit.Add_Click({ Stop-Overlay })
$tray.ContextMenuStrip = $menu
$tray.Add_MouseClick({ param($s, $e) if ($e.Button -eq [System.Windows.Forms.MouseButtons]::Left) { Switch-Overlay } })

function Update-Menus {
    $tick = [string][char]0x2713 + ' '
    $wAuto.Header = $(if ($script:Prefs.AutoSwitch) { $tick } else { '' }) + 'Auto-switch pages by app'
    $tAuto.Checked = $script:Prefs.AutoSwitch
    $tLock.Text = $(if ($script:Prefs.Locked) { 'Unlock (move and resize)' } else { 'Lock in place (clicks pass through)' })
    $tShow.Text = $(if ($script:UserHidden) { 'Show overlay' } else { 'Hide overlay' })
    $app = $script:LastApp
    if (-not $app) { $app = '-' }
    $wApp.Header = 'Current app: ' + $app
    $wCopy.IsEnabled = [bool]$script:LastApp
}

function Stop-Overlay {
    $timer.Stop()
    Save-OverlaySettings $script:Prefs
    $tray.Visible = $false
    $tray.Dispose()
    Disconnect-Deck
    $win.Close()
    [System.Windows.Application]::Current.Shutdown()
}

# ---------------- Watching which app is in front ----------------
$script:MyPid = $PID
$script:ProcNames = @{}
$script:NextFg = [DateTime]::MinValue
$script:LastFgKey = ''
function Get-ForegroundApp {
    $h = [MFWin]::GetForegroundWindow()
    if ($h -eq [IntPtr]::Zero) { return $null }
    $fpid = [uint32]0
    [void][MFWin]::GetWindowThreadProcessId($h, [ref]$fpid)
    if ($fpid -eq $script:MyPid -or $fpid -eq 0) { return $null }
    if (-not $script:ProcNames.ContainsKey($fpid)) {
        try { $script:ProcNames[$fpid] = [System.Diagnostics.Process]::GetProcessById([int]$fpid).ProcessName } catch { return $null }
        if ($script:ProcNames.Count -gt 300) { $script:ProcNames.Clear() }
    }
    $proc = [string]$script:ProcNames[$fpid]
    $title = [MFWin]::Title($h)
    # Ignore the taskbar, Start menu, search, lock screen and alt-tab switcher
    if (-not $title -or $proc -in @('ShellExperienceHost', 'StartMenuExperienceHost', 'SearchHost', 'SearchApp', 'LockApp', 'ShellHost')) { return $null }
    return @{ Proc = $proc; Title = $title }
}

# ---------------- Main loop ----------------
$timer = New-Object System.Windows.Threading.DispatcherTimer
$timer.Interval = [TimeSpan]::FromMilliseconds(100)
$timer.Add_Tick({
    try {
        if (-not $script:Port) {
            if ((Get-Date) -ge $script:NextConnect) {
                $script:NextConnect = (Get-Date).AddSeconds(5)
                [void](Connect-Deck)
                Update-View
            }
        }
        elseif (Read-Deck) {
            Update-View
            if ($script:PageChanged -or $script:AlertText) { $script:PageChanged = $false; Show-Overlay }
        }
        else { Update-Flash }

        if ((Get-Date) -ge $script:NextFg) {
            $script:NextFg = (Get-Date).AddMilliseconds(400)
            $fg = Get-ForegroundApp
            if ($fg) {
                $key = $fg.Proc + '|' + $fg.Title
                if ($key -ne $script:LastFgKey) {
                    $script:LastFgKey = $key
                    $script:LastApp = $fg.Proc
                    $script:LastTitle = $fg.Title
                    $target = Step-AutoSwitch $fg.Proc $fg.Title
                    if ($null -ne $target -and $script:Port -and -not $script:FwWarn) { Send-Deck @{ cmd = 'goto_page'; page = [int]$target } }
                }
            }
        }

        Update-Pings
        if ($script:HideAt -and (Get-Date) -ge $script:HideAt -and -not $script:AlertText) {
            $script:HideAt = $null
            $win.Visibility = 'Hidden'
        }
    }
    catch {
        $script:Message = 'Overlay error: ' + $_.Exception.Message
    }
})

# ---------------- Start ----------------
$script:UserHidden = $false
$script:HideAt = $null
Set-Zoom $script:Prefs.Scale
$win.Opacity = $script:Prefs.Opacity
$app = New-Object System.Windows.Application
$app.ShutdownMode = [System.Windows.ShutdownMode]::OnExplicitShutdown
Update-View
$win.Show()
$win.UpdateLayout()
if (Test-OnScreen $script:Prefs.Left $script:Prefs.Top) { $win.Left = [double]$script:Prefs.Left; $win.Top = [double]$script:Prefs.Top }
else { Set-CornerPosition $Corner; Save-OverlaySettings $script:Prefs }
Update-Menus
Show-Overlay
$timer.Start()
[void]$app.Run()
$mutex.ReleaseMutex()
