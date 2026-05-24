# SnapZone

A lightweight FancyZones-style window snapper for Windows ultrawide and multi-monitor setups, plus an extra trick: **when you tab out of a borderless-fullscreen application, your snapped windows come back automatically**.

Pure Python, ~1500 lines, no framework. Built because PowerToys FancyZones is great but doesn't restore your layout after a borderless-fullscreen window has been covering the screen.

## Features

- **Custom zones per monitor** — drag-to-resize visual editor (`Ctrl+Shift+E`)
- **Hotkey snapping** — `Ctrl+Shift+←/→/↑/↓` jumps the active window between zones
- **Drag snapping** — hold `Shift` while dragging a window → live overlay of zones, drop to snap
- **Linked-edge resizing** in the editor — moving one zone's edge auto-resizes the neighbour, so layouts stay gap-free
- **Fullscreen-aware auto-restore** — when a borderless-fullscreen application loses focus (alt-tab out), it's minimized and your snapped apps reappear in their zones. Tab back in → the fullscreen app returns instantly, no overlay.
- **Persistent zone↔app bindings** — snap an app to a zone once, and SnapZone remembers it across restarts and reboots (matches by executable name, so updates don't break it)
- **Scheduled-task autostart** — survives Windows Fast Startup (unlike the registry `Run` key, which silently doesn't fire after hybrid hibernation)
- **DPI-aware** — uses `PER_MONITOR_AWARE_V2` and DWM extended frame bounds so windows align pixel-perfect on mixed-scale setups

## Requirements

- Windows 10 (1703+) or Windows 11
- Python 3.11 or later
- One or more monitors (tested on 3440×1440 ultrawide + 1280×800 secondary)

## Install

```powershell
git clone https://github.com/NickRohm/snapzone.git
cd snapzone

# Create a venv so dependencies don't pollute system Python
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Run

```powershell
# Foreground (errors visible in the console):
.venv\Scripts\python.exe main.py --no-delay

# Background (no console window):
.venv\Scripts\pythonw.exe main.py --no-delay
```

A three-blue-panes tray icon appears in the system tray. First launch creates a default 3-equal-columns layout per monitor.

> **Tip:** Windows hides new tray icons in the overflow chevron by default. Click the `^` in the bottom-right of your taskbar to find it, then drag it onto the visible part of the taskbar.

## Shortcuts

| Shortcut | Action |
|---|---|
| `Ctrl+Shift+←/→/↑/↓` | Snap active window to the neighbouring zone |
| `Ctrl+Shift+E` | Open zone editor |
| `Ctrl+Shift+R` | Reload zones from disk |
| Hold `Shift` while dragging | Live zone overlay; drop on a zone to snap |

## Zone editor (`Ctrl+Shift+E`)

A semi-transparent overlay appears on every monitor:

- **Drag the middle of a zone** → move it
- **Drag an edge / corner** → resize (the adjacent zone's matching edge follows in lockstep, so no gaps/overlaps)
- **Click ✕ on a zone** or hover + `Delete` → remove
- **`+ Add Zone` button** (or `+` key) → splits the largest zone in half so the layout stays tiled
- **`Ctrl+S`** → save and close · **`Esc`** → cancel

Edits snap to a 10 px grid. Saved to `zones.json` next to the install.

## Fullscreen application integration

Detection is heuristic: a window is considered a fullscreen application if it's borderless (no title bar) and covers the entire monitor incl. the taskbar area, and isn't a known shell window — the Alt+Tab switcher, Start menu, etc. are explicitly excluded.

When the watcher sees a fullscreen window lose foreground (alt-tab out):

1. Minimizes the fullscreen window (escalates to `SW_FORCEMINIMIZE` if it's slow to respond)
2. Raises your snapped apps to their zones
3. Pins them topmost briefly so the fullscreen window can't pop back over them

When you tab back in, pins drop and the fullscreen window is re-raised — instant, no flicker. Works with any borderless-fullscreen application.

## Autostart at logon

Recommended: use the included one-liner to register a Scheduled Task that fires at user logon (works across Fast Startup, unlike the registry `Run` key):

```powershell
$venvPyw = "$PWD\.venv\Scripts\pythonw.exe"
$main    = "$PWD\main.py"
$action = New-ScheduledTaskAction -Execute $venvPyw -Argument $main
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$trigger.Delay = "PT5S"
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName "SnapZone" -Action $action -Trigger $trigger -Settings $settings -Principal $principal
```

To disable later: `Unregister-ScheduledTask -TaskName SnapZone -Confirm:$false`.

> **Why a Scheduled Task instead of `HKCU\...\Run`?** Windows Fast Startup (default on Win10/11) means "Shut Down" is actually hybrid hibernation — the kernel session resumes from hiberfil.sys and Run-key entries don't reliably fire. Scheduled Tasks with the "At log on" trigger fire on every logon regardless of how Windows got there.

## Files written at runtime

All next to the install — fully self-contained, no `%APPDATA%`:

| File | Purpose |
|---|---|
| `zones.json` | Your zone layout per monitor |
| `workspace.json` | Zone → app (exe basename) bindings |
| `snapzone.log` | Operational log (rotates by append) |
| `boot.log` | Ultra-early startup trace, useful if pythonw dies before logging initialises |

## Architecture (quick)

- `main.py` — entry point, wires everything
- `zones.py` — `Zone` / `MonitorLayout` / `Layout` dataclasses + JSON persistence
- `window_ops.py` — Win32 wrappers (move, minimize, topmost, monitor enum, fullscreen detection, shell-window exclusion)
- `hotkeys.py` — directional snap logic + global hotkey binding (`keyboard` lib)
- `drag_snap.py` — low-level mouse hook (`pynput`) + click-through tkinter overlay
- `editor.py` — fullscreen tkinter editor with linked-edge resize and overlap prevention
- `workspace.py` — zone↔window memory, the fullscreen-watcher thread, restore logic
- `tray.py` — pystray menu
- `autostart.py` — legacy `HKCU\...\Run` helpers (the Scheduled Task is preferred)

## Known limitations

- **`Ctrl+Shift+→` doesn't cross monitors.** Directional snap is within the current monitor only. Workaround: `Win+Shift+→` to move the window across, or Shift-drag.
- **UAC-elevated windows** (Task Manager, regedit) can't be moved by non-elevated SnapZone — `SetWindowPos` silently fails. Run SnapZone elevated if you need this.
- **Monitor device-name shuffling.** Windows occasionally reassigns `\\.\DISPLAY1` ↔ `\\.\DISPLAY2` after replugging cables; zones are bound to those names and may end up on the "wrong" physical monitor. Easy fix: re-edit zones once.
- **`keyboard` library hooks** may need admin rights on some machines.

## License

MIT — see [LICENSE](LICENSE).
