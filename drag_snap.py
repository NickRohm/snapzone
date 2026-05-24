"""Drag-with-Shift snap. Mouse hook + click-through overlay preview.

Architecture:
  - pynput.mouse.Listener fires on_click / on_move on its own thread.
  - pynput.keyboard.Listener tracks Shift state (thread-safe boolean).
  - A tk root running on its own thread polls shared state every 25ms and
    creates / destroys / updates the overlay accordingly. No cross-thread
    tk calls (which are unsafe).
"""
from __future__ import annotations

import ctypes
import logging
import threading
import tkinter as tk
from ctypes import wintypes
from typing import Callable

import win32api
import win32con
import win32gui
from pynput import keyboard as pkeyboard
from pynput import mouse as pmouse

from window_ops import Monitor, get_monitors, move_window
from zones import Layout, Zone

_log_obj = logging.getLogger("snapzone.drag_snap")


def _log(msg: str) -> None:
    _log_obj.info(msg)


user32 = ctypes.windll.user32

# ctypes signatures (avoid wrong calling convention)
user32.WindowFromPoint.argtypes = [wintypes.POINT]
user32.WindowFromPoint.restype = wintypes.HWND
user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetAncestor.restype = wintypes.HWND
user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowLongW.restype = ctypes.c_long
user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
user32.SetWindowLongW.restype = ctypes.c_long
user32.SetWindowPos.argtypes = [
    wintypes.HWND, wintypes.HWND,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.UINT,
]
user32.SetWindowPos.restype = wintypes.BOOL

WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
GWL_EXSTYLE = -20
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020
GA_ROOT = 2


def _make_click_through(hwnd: int) -> None:
    style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    style |= WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
    user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
    # SWP_FRAMECHANGED required to make ext-style change take effect
    user32.SetWindowPos(
        hwnd, 0, 0, 0, 0, 0,
        SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED,
    )


def _top_window_at(x: int, y: int) -> int:
    pt = wintypes.POINT(x, y)
    hwnd = user32.WindowFromPoint(pt)
    if not hwnd:
        return 0
    return user32.GetAncestor(hwnd, GA_ROOT) or hwnd


def _is_draggable(hwnd: int) -> bool:
    if not hwnd or not win32gui.IsWindow(hwnd) or not win32gui.IsWindowVisible(hwnd):
        return False
    style = win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE)
    if not (style & win32con.WS_CAPTION):
        return False
    ex = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
    if ex & win32con.WS_EX_TOOLWINDOW:
        return False
    return True


class DragSnapController:
    POLL_MS = 25
    DRAG_THRESHOLD_PX = 5

    def __init__(self, get_layout: Callable[[], Layout],
                 on_snapped: Callable[[str, int], None] | None = None):
        self.get_layout = get_layout
        self._on_snapped = on_snapped
        self._stop = threading.Event()

        # shared state (atomic reads/writes on simple types)
        self._shift_held = False
        self._mouse_down = False
        self._dragging_hwnd = 0
        self._drag_started = False
        self._drag_anchor = (0, 0)
        self._cursor = (0, 0)
        self._release_pending: tuple[int, int, int] | None = None  # (x, y, hwnd)

        # tk-thread-only
        self._root: tk.Tk | None = None
        self._overlay_windows: list[tuple[tk.Toplevel, tk.Canvas, Monitor]] = []
        self._overlay_zones: dict[str, list[Zone]] = {}
        self._highlighted: Zone | None = None

    def start(self) -> None:
        threading.Thread(target=self._tk_thread, daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    # ===== input listeners (background threads) =====

    def _on_click(self, x, y, button, pressed):
        if button != pmouse.Button.left:
            return
        if pressed:
            hwnd = _top_window_at(x, y)
            if _is_draggable(hwnd):
                self._dragging_hwnd = hwnd
                self._mouse_down = True
                self._drag_started = False
                self._drag_anchor = (x, y)
        else:
            if self._mouse_down and self._drag_started and self._shift_held:
                self._release_pending = (x, y, self._dragging_hwnd)
            self._mouse_down = False
            self._drag_started = False
            self._dragging_hwnd = 0

    def _on_move(self, x, y):
        self._cursor = (x, y)
        if self._mouse_down and not self._drag_started:
            ax, ay = self._drag_anchor
            if abs(x - ax) >= self.DRAG_THRESHOLD_PX or abs(y - ay) >= self.DRAG_THRESHOLD_PX:
                self._drag_started = True

    def _on_key_press(self, key):
        if key in (pkeyboard.Key.shift, pkeyboard.Key.shift_l, pkeyboard.Key.shift_r):
            if not self._shift_held:
                self._shift_held = True

    def _on_key_release(self, key):
        if key in (pkeyboard.Key.shift, pkeyboard.Key.shift_l, pkeyboard.Key.shift_r):
            self._shift_held = False

    # ===== tk thread =====

    def _tk_thread(self) -> None:
        self._root = tk.Tk()
        self._root.withdraw()

        m_listener = pmouse.Listener(on_click=self._on_click, on_move=self._on_move)
        k_listener = pkeyboard.Listener(on_press=self._on_key_press, on_release=self._on_key_release)
        m_listener.start()
        k_listener.start()
        _log("listeners started")

        self._poll()
        try:
            self._root.mainloop()
        finally:
            try: m_listener.stop()
            except Exception: pass
            try: k_listener.stop()
            except Exception: pass

    def _poll(self) -> None:
        if self._stop.is_set():
            self._root.quit()
            return

        # Process release first so we snap before tearing down
        if self._release_pending is not None:
            x, y, hwnd = self._release_pending
            self._release_pending = None
            if self._overlay_windows:
                self._do_snap(hwnd, x, y)
                self._destroy_overlay()

        should_show = self._mouse_down and self._drag_started and self._shift_held
        if should_show and not self._overlay_windows:
            _log("creating overlay")
            self._create_overlay()
        elif not should_show and self._overlay_windows:
            _log("destroying overlay")
            self._destroy_overlay()

        if self._overlay_windows:
            self._update_highlight(*self._cursor)

        self._root.after(self.POLL_MS, self._poll)

    def _create_overlay(self) -> None:
        layout = self.get_layout()
        for m in get_monitors():
            top = tk.Toplevel(self._root)
            top.overrideredirect(True)
            top.geometry(f"{m.w}x{m.h}+{m.x}+{m.y}")
            top.attributes("-topmost", True)
            top.attributes("-alpha", 0.4)
            top.configure(bg="#101820")
            canvas = tk.Canvas(top, width=m.w, height=m.h,
                               bg="#101820", highlightthickness=0)
            canvas.pack(fill="both", expand=True)
            top.update_idletasks()
            try:
                _make_click_through(top.winfo_id())
            except Exception as e:
                _log(f"click-through failed: {e}")
            self._overlay_windows.append((top, canvas, m))
            self._overlay_zones[m.id] = layout.for_monitor(m).zones
        self._highlighted = None
        self._draw_overlay()

    def _destroy_overlay(self) -> None:
        for top, _c, _m in self._overlay_windows:
            try: top.destroy()
            except tk.TclError: pass
        self._overlay_windows.clear()
        self._overlay_zones.clear()
        self._highlighted = None

    def _zone_at(self, sx: int, sy: int) -> tuple[Monitor, Zone] | None:
        for _t, _c, m in self._overlay_windows:
            if m.x <= sx < m.x + m.w and m.y <= sy < m.y + m.h:
                rx, ry = sx - m.x, sy - m.y
                for z in self._overlay_zones.get(m.id, []):
                    if z.contains(rx, ry):
                        return (m, z)
                return None
        return None

    def _update_highlight(self, sx: int, sy: int) -> None:
        hit = self._zone_at(sx, sy)
        new = hit[1] if hit else None
        if new is not self._highlighted:
            self._highlighted = new
            self._draw_overlay()

    def _draw_overlay(self) -> None:
        for _top, canvas, m in self._overlay_windows:
            canvas.delete("all")
            for z in self._overlay_zones.get(m.id, []):
                hl = z is self._highlighted
                canvas.create_rectangle(
                    z.x, z.y, z.x + z.w, z.y + z.h,
                    fill="#6aafff" if hl else "#4a90e2",
                    outline="#ffffff",
                    width=5 if hl else 2,
                )

    def _do_snap(self, hwnd: int, sx: int, sy: int) -> None:
        hit = self._zone_at(sx, sy)
        if not hit:
            return
        mon, zone = hit
        ax, ay, aw, ah = zone.to_absolute(mon)
        ok = move_window(hwnd, ax, ay, aw, ah)
        _log(f"snap to zone ({ax},{ay},{aw},{ah}) ok={ok}")
        if ok and self._on_snapped is not None:
            try:
                self._on_snapped(zone.id, hwnd)
            except Exception:
                pass
