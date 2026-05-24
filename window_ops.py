"""Win32 window operations: DPI-aware move/resize, monitor enumeration."""
from __future__ import annotations

import ctypes
import ntpath
import os
from ctypes import wintypes
from dataclasses import dataclass

import win32api
import win32con
import win32gui
import win32process

user32 = ctypes.windll.user32
dwmapi = ctypes.windll.dwmapi

DWMWA_EXTENDED_FRAME_BOUNDS = 9
SWP_FLAGS = win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE | win32con.SWP_FRAMECHANGED

OWN_PID = os.getpid()  # used by the game watcher to ignore our own windows

_dpi_initialized = False


def ensure_dpi_aware() -> None:
    """Must be called once at process start. Without this, zone pixel coords
    drift on any display with non-100% scaling."""
    global _dpi_initialized
    if _dpi_initialized:
        return
    try:
        # PER_MONITOR_AWARE_V2 = -4 (Win10 1703+)
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)  # fallback
        except Exception:
            user32.SetProcessDPIAware()
    _dpi_initialized = True


@dataclass
class Monitor:
    id: str           # device name, e.g. "\\\\.\\DISPLAY1"
    x: int; y: int    # virtual-screen coords of top-left
    w: int; h: int    # work-area size (excludes taskbar)


def get_monitors() -> list[Monitor]:
    mons: list[Monitor] = []
    for hmon, _hdc, _rect in win32api.EnumDisplayMonitors(None, None):
        info = win32api.GetMonitorInfo(hmon)
        wl, wt, wr, wb = info["Work"]   # work area excludes taskbar
        mons.append(Monitor(
            id=info["Device"],
            x=wl, y=wt,
            w=wr - wl, h=wb - wt,
        ))
    return mons


def get_active_hwnd() -> int:
    return win32gui.GetForegroundWindow()


def _get_frame_offset(hwnd: int) -> tuple[int, int, int, int]:
    """Invisible resize margin on Win10+ (~7px on left/right/bottom, 0 top).
    Returns (left, top, right, bottom) to subtract from target rect so the
    *visible* edge aligns with the zone edge."""
    rect = wintypes.RECT()
    try:
        hr = dwmapi.DwmGetWindowAttribute(
            wintypes.HWND(hwnd),
            wintypes.DWORD(DWMWA_EXTENDED_FRAME_BOUNDS),
            ctypes.byref(rect),
            ctypes.sizeof(rect),
        )
        if hr != 0:
            return (0, 0, 0, 0)
    except Exception:
        return (0, 0, 0, 0)
    wl, wt, wr, wb = win32gui.GetWindowRect(hwnd)
    return (rect.left - wl, rect.top - wt, wr - rect.right, wb - rect.bottom)


def move_window(hwnd: int, x: int, y: int, w: int, h: int) -> bool:
    """Move window so its *visible* bounds match (x, y, w, h)."""
    if not win32gui.IsWindow(hwnd):
        return False
    # Un-maximize first; SetWindowPos on a maximized window is a no-op
    placement = win32gui.GetWindowPlacement(hwnd)
    if placement[1] == win32con.SW_SHOWMAXIMIZED:
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)

    ol, ot, or_, ob = _get_frame_offset(hwnd)
    tx = x - ol
    ty = y - ot
    tw = w + ol + or_
    th = h + ot + ob
    try:
        win32gui.SetWindowPos(hwnd, 0, tx, ty, tw, th, SWP_FLAGS)
        return True
    except Exception:
        return False


def get_visible_rect(hwnd: int) -> tuple[int, int, int, int]:
    """Return window's visible bounds (accounting for invisible frame)."""
    rect = wintypes.RECT()
    try:
        hr = dwmapi.DwmGetWindowAttribute(
            wintypes.HWND(hwnd),
            wintypes.DWORD(DWMWA_EXTENDED_FRAME_BOUNDS),
            ctypes.byref(rect),
            ctypes.sizeof(rect),
        )
        if hr == 0:
            return (rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)
    except Exception:
        pass
    l, t, r, b = win32gui.GetWindowRect(hwnd)
    return (l, t, r - l, b - t)


def monitor_from_point(x: int, y: int) -> Monitor | None:
    mons = get_monitors()
    for m in mons:
        if m.x <= x < m.x + m.w and m.y <= y < m.y + m.h:
            return m
    return mons[0] if mons else None


def monitor_from_hwnd(hwnd: int) -> Monitor | None:
    x, y, w, h = get_visible_rect(hwnd)
    cx, cy = x + w // 2, y + h // 2
    return monitor_from_point(cx, cy)


# ---------------------------------------------------------------------------
# Workspace-restore helpers (minimize game, raise/restore zone windows)
# ---------------------------------------------------------------------------

def is_window(hwnd: int) -> bool:
    return bool(hwnd) and bool(win32gui.IsWindow(hwnd))


def is_minimized(hwnd: int) -> bool:
    try:
        return bool(win32gui.IsIconic(hwnd))
    except Exception:
        return False


def minimize_window(hwnd: int) -> bool:
    try:
        win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)
        return True
    except Exception:
        return False


def force_minimize_window(hwnd: int) -> bool:
    """SW_FORCEMINIMIZE — minimizes even an unresponsive / different-thread
    window (e.g. a game that's slow to honour SW_MINIMIZE)."""
    try:
        win32gui.ShowWindow(hwnd, win32con.SW_FORCEMINIMIZE)
        return True
    except Exception:
        return False


_HWND_TOPMOST = -1
_HWND_NOTOPMOST = -2
_TOPMOST_FLAGS = (win32con.SWP_NOMOVE | win32con.SWP_NOSIZE
                  | win32con.SWP_NOACTIVATE)


def set_topmost(hwnd: int, on: bool) -> bool:
    """Toggle WS_EX_TOPMOST on hwnd via SetWindowPos. Used to briefly hold
    our zone windows above games that auto-restore themselves after we
    minimize them."""
    if not is_window(hwnd):
        return False
    insert_after = _HWND_TOPMOST if on else _HWND_NOTOPMOST
    try:
        win32gui.SetWindowPos(hwnd, insert_after, 0, 0, 0, 0, _TOPMOST_FLAGS)
        return True
    except Exception:
        return False


def restore_window(hwnd: int) -> None:
    """Un-minimize without forcing maximize."""
    try:
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    except Exception:
        pass


def bring_to_top(hwnd: int) -> None:
    """Raise within the Z-order without stealing keyboard focus."""
    try:
        win32gui.SetWindowPos(
            hwnd, win32con.HWND_TOP, 0, 0, 0, 0,
            win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE,
        )
    except Exception:
        pass


def set_foreground(hwnd: int) -> None:
    """Best-effort focus. Reliable in our flow because the blocking
    fullscreen game has already been minimized first."""
    try:
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        pass


def get_window_pid(hwnd: int) -> int:
    try:
        _tid, pid = win32process.GetWindowThreadProcessId(hwnd)
        return int(pid)
    except Exception:
        return 0


def is_own_window(hwnd: int) -> bool:
    return get_window_pid(hwnd) == OWN_PID


_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def get_window_exe(hwnd: int) -> str:
    """Full executable path of the process owning hwnd, lowercased.
    Empty string on failure (e.g. elevated/protected process)."""
    pid = get_window_pid(hwnd)
    if not pid:
        return ""
    try:
        kernel32 = ctypes.windll.kernel32
        hproc = kernel32.OpenProcess(
            _PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not hproc:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(32768)
            size = wintypes.DWORD(len(buf))
            ok = kernel32.QueryFullProcessImageNameW(
                hproc, 0, buf, ctypes.byref(size))
            if not ok:
                return ""
            return buf.value.lower()
        finally:
            kernel32.CloseHandle(hproc)
    except Exception:
        return ""


def find_fullscreen_window_for_pid(pid: int) -> int:
    """Return a currently-visible fullscreen/borderless window owned by `pid`,
    or 0. Used to track a game by its process rather than a single hwnd,
    because some games (The Bazaar) spawn short-lived borderless windows for
    animations/transitions that would otherwise hijack single-hwnd tracking."""
    if not pid:
        return 0
    found: list[int] = []

    def _cb(h, _):
        try:
            if not win32gui.IsWindowVisible(h):
                return True
            if win32gui.IsIconic(h):
                return True
            if get_window_pid(h) != pid:
                return True
            if is_fullscreen_window(h):
                found.append(h)
        except Exception:
            pass
        return True

    try:
        win32gui.EnumWindows(_cb, None)
    except Exception:
        pass
    return found[0] if found else 0


def enum_top_level_windows() -> list[int]:
    """Visible, non-minimized, captioned top-level windows that aren't tool
    windows — i.e. things the user would alt-tab to. Best-effort."""
    out: list[int] = []

    def _cb(h, _):
        try:
            if not win32gui.IsWindowVisible(h):
                return True
            if win32gui.IsIconic(h):
                return True
            if not win32gui.GetWindowText(h):
                return True
            ex = win32gui.GetWindowLong(h, win32con.GWL_EXSTYLE)
            if ex & win32con.WS_EX_TOOLWINDOW:
                return True
            out.append(h)
        except Exception:
            pass
        return True

    try:
        win32gui.EnumWindows(_cb, None)
    except Exception:
        pass
    return out


def fullscreen_debug(hwnd: int) -> dict:
    """Diagnostic snapshot used to tune game detection. Never raises."""
    d: dict = {"hwnd": hwnd}
    try:
        d["title"] = win32gui.GetWindowText(hwnd)
        d["iconic"] = bool(win32gui.IsIconic(hwnd))
        l, t, r, b = win32gui.GetWindowRect(hwnd)
        d["winrect"] = (l, t, r, b)
        hmon = win32api.MonitorFromWindow(hwnd, win32con.MONITOR_DEFAULTTONEAREST)
        info = win32api.GetMonitorInfo(hmon)
        d["monitor"] = tuple(info["Monitor"])
        d["work"] = tuple(info["Work"])
        style = win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE)
        d["has_caption"] = bool(style & win32con.WS_CAPTION)
        d["has_thickframe"] = bool(style & win32con.WS_THICKFRAME)
        d["is_fullscreen"] = is_fullscreen_window(hwnd)
    except Exception as e:
        d["error"] = repr(e)
    return d


# Windows that must NEVER be treated as a "game", even though some of them
# are borderless and cover the screen. The Alt+Tab "Task Switching" overlay
# is the big one — misdetecting it caused SnapZone to minimize it / pin
# windows over it, breaking the task switcher entirely.
_SHELL_EXES = frozenset({
    "explorer.exe", "searchhost.exe", "searchapp.exe",
    "startmenuexperiencehost.exe", "shellexperiencehost.exe",
    "textinputhost.exe", "applicationframehost.exe", "sihost.exe",
    "dwm.exe",
})
_SHELL_CLASSES = frozenset({
    "Windows.UI.Core.CoreWindow", "XamlExplorerHostIslandWindow",
    "MultitaskingViewFrame", "ForegroundStaging", "TaskSwitcherWnd",
    "TaskSwitcherOverlayWnd", "Shell_TrayWnd", "Progman", "WorkerW",
    "Windows.UI.Composition.DesktopWindowContentBridge",
})


def is_shell_window(hwnd: int) -> bool:
    """True for Windows shell / system UI (taskbar, Start menu, Alt+Tab
    switcher, etc.). These must never be classified as a game, and the
    GameWatcher must ignore ticks where one is foreground (e.g. while the
    user is mid-Alt+Tab) so it doesn't fire a restore and hijack focus."""
    try:
        exe = get_window_exe(hwnd)
        if exe and ntpath.basename(exe) in _SHELL_EXES:
            return True
    except Exception:
        pass
    try:
        if win32gui.GetClassName(hwnd) in _SHELL_CLASSES:
            return True
    except Exception:
        pass
    return False


def is_fullscreen_window(hwnd: int) -> bool:
    """Heuristic: is this a borderless/fullscreen game?

    True only when the window covers the ENTIRE monitor bounds (incl. the
    taskbar area) AND is not a shell/system window. Real borderless games
    (The Bazaar, WoW) cover the full monitor; shell overlays like the Alt+Tab
    switcher cover only the work area, so the full-monitor requirement plus
    the shell exclusion keeps them out. A normal maximized app only covers
    the work area and is excluded too."""
    if not is_window(hwnd):
        return False
    if is_shell_window(hwnd):
        return False
    try:
        if win32gui.IsIconic(hwnd):
            return False
        l, t, r, b = win32gui.GetWindowRect(hwnd)
        hmon = win32api.MonitorFromWindow(hwnd, win32con.MONITOR_DEFAULTTONEAREST)
        info = win32api.GetMonitorInfo(hmon)
        ml, mt, mr, mb = info["Monitor"]   # full monitor rect (incl. taskbar)
    except Exception:
        return False

    tol = 4
    return (abs(l - ml) <= tol and abs(t - mt) <= tol
            and abs(r - mr) <= tol and abs(b - mb) <= tol)
