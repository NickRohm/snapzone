"""Directional snap logic + global hotkey registration."""
from __future__ import annotations

import math
from typing import Callable

import keyboard

from window_ops import (
    get_active_hwnd,
    get_visible_rect,
    monitor_from_hwnd,
    move_window,
)
from zones import Layout, Zone


Direction = str  # "left" | "right" | "up" | "down"

_DIR_VECTORS = {
    "left":  (-1, 0),
    "right": (1, 0),
    "up":    (0, -1),
    "down":  (0, 1),
}


def _current_zone(zones: list[Zone], cx: int, cy: int) -> Zone | None:
    """Zone containing point, else nearest by center distance."""
    for z in zones:
        if z.contains(cx, cy):
            return z
    if not zones:
        return None
    return min(zones, key=lambda z: (z.cx - cx) ** 2 + (z.cy - cy) ** 2)


def pick_neighbor(zones: list[Zone], current: Zone, direction: Direction) -> Zone | None:
    """Pick zone in given direction from current. Uses signed projection
    onto direction vector, penalizing perpendicular drift."""
    dx, dy = _DIR_VECTORS[direction]
    candidates = []
    for z in zones:
        if z.id == current.id:
            continue
        ox = z.cx - current.cx
        oy = z.cy - current.cy
        forward = ox * dx + oy * dy
        if forward <= 0:
            continue
        perp = abs(ox * dy - oy * dx)  # cross magnitude
        # Score: want forward distance small, perpendicular drift heavily penalized
        score = forward + perp * 3
        candidates.append((score, z))
    if not candidates:
        return None
    candidates.sort(key=lambda c: c[0])
    return candidates[0][1]


def snap_active(layout: Layout, direction: Direction,
                on_snapped: Callable[[str, int], None] | None = None) -> bool:
    hwnd = get_active_hwnd()
    if not hwnd:
        return False
    mon = monitor_from_hwnd(hwnd)
    if mon is None:
        return False
    ml = layout.for_monitor(mon)
    if not ml.zones:
        return False

    vx, vy, vw, vh = get_visible_rect(hwnd)
    # Convert window center to monitor-relative coords
    rel_cx = vx + vw // 2 - mon.x
    rel_cy = vy + vh // 2 - mon.y

    current = _current_zone(ml.zones, rel_cx, rel_cy)
    if current is None:
        return False
    target = pick_neighbor(ml.zones, current, direction) or current
    ax, ay, aw, ah = target.to_absolute(mon)
    ok = move_window(hwnd, ax, ay, aw, ah)
    if ok and on_snapped is not None:
        try:
            on_snapped(target.id, hwnd)
        except Exception:
            pass
    return ok


def register(
    get_layout: Callable[[], Layout],
    on_edit: Callable[[], None],
    on_reload: Callable[[], None],
    on_snapped: Callable[[str, int], None] | None = None,
    on_restore: Callable[[], None] | None = None,
) -> None:
    """Register global hotkeys. Callbacks are invoked on the keyboard thread;
    GUI work must be marshalled onto the main thread by the caller."""
    keyboard.add_hotkey("ctrl+shift+left",  lambda: snap_active(get_layout(), "left", on_snapped))
    keyboard.add_hotkey("ctrl+shift+right", lambda: snap_active(get_layout(), "right", on_snapped))
    keyboard.add_hotkey("ctrl+shift+up",    lambda: snap_active(get_layout(), "up", on_snapped))
    keyboard.add_hotkey("ctrl+shift+down",  lambda: snap_active(get_layout(), "down", on_snapped))
    keyboard.add_hotkey("ctrl+shift+e", on_edit)
    keyboard.add_hotkey("ctrl+shift+r", on_reload)
    if on_restore is not None:
        keyboard.add_hotkey("ctrl+shift+space", on_restore)


def unregister_all() -> None:
    keyboard.unhook_all()
