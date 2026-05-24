"""Visual drag-to-resize zone editor. Fullscreen tkinter Toplevel per monitor."""
from __future__ import annotations

import tkinter as tk
from dataclasses import replace
from typing import Callable

from window_ops import Monitor, get_monitors
from zones import Layout, Zone, save

HANDLE = 12            # handle hitbox size (px)
SNAP_GRID = 10
MIN_W = 120
MIN_H = 80

FILL = "#4a90e2"
FILL_HOVER = "#6aafff"
OUTLINE = "#ffffff"
BG = "#202020"

TOOLBAR_H = 56
DELETE_ICON_R = 16     # × icon radius (per zone, top-right)


def _snap(v: int) -> int:
    return round(v / SNAP_GRID) * SNAP_GRID


def _intersects(a: Zone, b: Zone) -> bool:
    """Two zones overlap iff their projections on both axes overlap."""
    if a.x + a.w <= b.x or b.x + b.w <= a.x:
        return False
    if a.y + a.h <= b.y or b.y + b.h <= a.y:
        return False
    return True


def _proposed_overlaps(self_zone: Zone, others: list[Zone],
                       nx: int, ny: int, nw: int, nh: int) -> bool:
    """Would moving/resizing self_zone to (nx,ny,nw,nh) overlap any other?"""
    probe = Zone(x=nx, y=ny, w=nw, h=nh)
    for o in others:
        if o is self_zone:
            continue
        if _intersects(probe, o):
            return True
    return False


_EDGE_TOL = 4   # px tolerance for "edges touch"


def _find_linked_for_edge(z: Zone, others: list[Zone], edge: str) -> list[Zone]:
    """Zones whose opposite edge currently coincides with z's `edge`.

    For edge 'e' of z (right side): find zones whose left edge sits at
    z.x + z.w (within tolerance) and which vertically overlap z.
    Symmetric for 'w', 'n', 's'.
    """
    out: list[Zone] = []
    if edge == "e":
        target = z.x + z.w
        for o in others:
            if o is z: continue
            if abs(o.x - target) > _EDGE_TOL: continue
            if o.y + o.h <= z.y or o.y >= z.y + z.h: continue
            out.append(o)
    elif edge == "w":
        target = z.x
        for o in others:
            if o is z: continue
            if abs(o.x + o.w - target) > _EDGE_TOL: continue
            if o.y + o.h <= z.y or o.y >= z.y + z.h: continue
            out.append(o)
    elif edge == "s":
        target = z.y + z.h
        for o in others:
            if o is z: continue
            if abs(o.y - target) > _EDGE_TOL: continue
            if o.x + o.w <= z.x or o.x >= z.x + z.w: continue
            out.append(o)
    elif edge == "n":
        target = z.y
        for o in others:
            if o is z: continue
            if abs(o.y + o.h - target) > _EDGE_TOL: continue
            if o.x + o.w <= z.x or o.x >= z.x + z.w: continue
            out.append(o)
    return out


def _hit_edge(z: Zone, px: int, py: int) -> str | None:
    if not (z.x - HANDLE <= px <= z.x + z.w + HANDLE and
            z.y - HANDLE <= py <= z.y + z.h + HANDLE):
        return None
    left = abs(px - z.x) <= HANDLE
    right = abs(px - (z.x + z.w)) <= HANDLE
    top = abs(py - z.y) <= HANDLE
    bottom = abs(py - (z.y + z.h)) <= HANDLE
    if top and left: return "nw"
    if top and right: return "ne"
    if bottom and left: return "sw"
    if bottom and right: return "se"
    if top: return "n"
    if bottom: return "s"
    if left: return "w"
    if right: return "e"
    if z.contains(px, py):
        return "move"
    return None


class _Button:
    """Simple click-zone with label + colour. Stored per-redraw, hit-tested first."""
    __slots__ = ("x1", "y1", "x2", "y2", "label", "fill", "action")

    def __init__(self, x1, y1, x2, y2, label, fill, action: Callable[[], None]):
        self.x1, self.y1, self.x2, self.y2 = x1, y1, x2, y2
        self.label = label
        self.fill = fill
        self.action = action

    def contains(self, x, y):
        return self.x1 <= x <= self.x2 and self.y1 <= y <= self.y2


class MonitorEditor:
    """Editor overlay for a single monitor."""

    def __init__(self, root: tk.Tk, monitor: Monitor, zones: list[Zone],
                 on_finish: Callable[[bool], None]):
        self.monitor = monitor
        self.zones = [replace(z) for z in zones]
        self.on_finish = on_finish

        self.top = tk.Toplevel(root)
        self.top.overrideredirect(True)
        self.top.geometry(f"{monitor.w}x{monitor.h}+{monitor.x}+{monitor.y}")
        self.top.attributes("-topmost", True)
        self.top.attributes("-alpha", 0.78)
        self.top.configure(bg=BG)

        self.canvas = tk.Canvas(self.top, width=monitor.w, height=monitor.h,
                                bg=BG, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)

        self.drag_zone: Zone | None = None
        self.drag_mode: str | None = None
        self.drag_anchor: tuple[int, int] = (0, 0)
        self.drag_orig: tuple[int, int, int, int] = (0, 0, 0, 0)
        # For each cardinal direction being dragged, list of (linked_zone,
        # original_xywh) — the neighbour whose touching edge moves with us.
        # Captured at on_press, applied during on_drag, so positions can't
        # drift during the gesture.
        self._linked: dict[str, list[tuple[Zone, tuple[int, int, int, int]]]] = {}

        # Built fresh each redraw, hit-tested first in on_press / on_motion
        self._buttons: list[_Button] = []
        self._delete_icons: list[tuple[Zone, int, int, int]] = []  # (zone, cx, cy, r)
        self._hover_zone: Zone | None = None

        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.canvas.bind("<Double-Button-1>", self.on_double)
        self.canvas.bind("<Button-3>", self.on_right_click)
        self.canvas.bind("<Motion>", self.on_motion)
        self.top.bind("<Escape>", lambda e: on_finish(False))
        self.top.bind("<Control-s>", lambda e: on_finish(True))
        self.top.bind("<Key-Delete>", self.on_delete_key)
        self.top.bind("<plus>", lambda e: self._add_default_zone())
        self.top.bind("<KP_Add>", lambda e: self._add_default_zone())

        self.redraw()
        self.top.focus_force()

    # ---------- adding zones ----------

    def _add_default_zone(self) -> None:
        """Add a new zone by splitting the largest existing zone in half so
        the new zone integrates with the layout instead of floating on top.
        Splits along the longer axis (wide zones split vertically, tall zones
        split horizontally). Falls back to filling the work area if no zones
        exist, or doing nothing if every zone is already at minimum size."""
        work_top = TOOLBAR_H + 30
        if not self.zones:
            self.zones.append(Zone(
                x=0, y=work_top,
                w=self.monitor.w,
                h=self.monitor.h - work_top,
            ))
            self.redraw()
            return

        # Pick the largest splittable zone
        candidates = sorted(self.zones, key=lambda z: z.w * z.h, reverse=True)
        for target in candidates:
            if target.w >= target.h:
                half = _snap(target.w // 2)
                if half < MIN_W or target.w - half < MIN_W:
                    continue
                new_zone = Zone(
                    x=target.x + half, y=target.y,
                    w=target.w - half, h=target.h,
                )
                target.w = half
            else:
                half = _snap(target.h // 2)
                if half < MIN_H or target.h - half < MIN_H:
                    continue
                new_zone = Zone(
                    x=target.x, y=target.y + half,
                    w=target.w, h=target.h - half,
                )
                target.h = half
            self.zones.append(new_zone)
            self.redraw()
            return
        # No zone is big enough to split — silently do nothing

    def _delete_zone(self, z: Zone) -> None:
        if z in self.zones:
            self.zones.remove(z)
            if self._hover_zone is z:
                self._hover_zone = None
            self.redraw()

    # ---------- drawing ----------

    def redraw(self) -> None:
        self.canvas.delete("all")
        self._buttons.clear()
        self._delete_icons.clear()

        # Zones first (under toolbar)
        for i, z in enumerate(self.zones):
            fill = FILL_HOVER if z is self._hover_zone else FILL
            self.canvas.create_rectangle(
                z.x, z.y, z.x + z.w, z.y + z.h,
                fill=fill, outline=OUTLINE, width=2,
            )
            self.canvas.create_text(
                z.cx, z.cy,
                text=f"{i+1}\n{z.w}×{z.h}",
                fill="white", font=("Segoe UI", 20, "bold"),
                justify="center",
            )
            # Delete × icon, top-right inside the zone
            r = DELETE_ICON_R
            cx = z.x + z.w - r - 6
            cy = z.y + r + 6
            self.canvas.create_oval(
                cx - r, cy - r, cx + r, cy + r,
                fill="#c0392b", outline="white", width=2,
            )
            self.canvas.create_text(
                cx, cy, text="×",
                fill="white", font=("Segoe UI", 18, "bold"),
            )
            self._delete_icons.append((z, cx, cy, r))

        # Toolbar across the top
        self.canvas.create_rectangle(
            0, 0, self.monitor.w, TOOLBAR_H,
            fill="#181818", outline="",
        )

        btn_w, btn_h = 160, 38
        gap = 12
        total_w = btn_w * 3 + gap * 2
        x0 = (self.monitor.w - total_w) // 2
        y0 = (TOOLBAR_H - btn_h) // 2

        self._add_button(x0, y0, x0 + btn_w, y0 + btn_h,
                         "+  Add Zone", "#27ae60", self._add_default_zone)
        x0 += btn_w + gap
        self._add_button(x0, y0, x0 + btn_w, y0 + btn_h,
                         "✓  Save (Ctrl+S)", "#2980b9",
                         lambda: self.on_finish(True))
        x0 += btn_w + gap
        self._add_button(x0, y0, x0 + btn_w, y0 + btn_h,
                         "✕  Cancel (Esc)", "#7f8c8d",
                         lambda: self.on_finish(False))

        # Help text under toolbar
        self.canvas.create_text(
            self.monitor.w // 2, TOOLBAR_H + 14,
            text=("Drag edges to resize · Drag middle to move · "
                  "Click ×  on a zone or press Delete to remove · + key adds zone"),
            fill="#cccccc", font=("Segoe UI", 10),
        )

    def _add_button(self, x1, y1, x2, y2, label, fill, action) -> None:
        self.canvas.create_rectangle(
            x1, y1, x2, y2, fill=fill, outline="white", width=1,
        )
        self.canvas.create_text(
            (x1 + x2) // 2, (y1 + y2) // 2,
            text=label, fill="white",
            font=("Segoe UI", 12, "bold"),
        )
        self._buttons.append(_Button(x1, y1, x2, y2, label, fill, action))

    # ---------- hit testing ----------

    def _hit_button(self, px: int, py: int) -> _Button | None:
        for b in self._buttons:
            if b.contains(px, py):
                return b
        return None

    def _hit_delete_icon(self, px: int, py: int) -> Zone | None:
        for z, cx, cy, r in self._delete_icons:
            if (px - cx) ** 2 + (py - cy) ** 2 <= r * r:
                return z
        return None

    def _find_zone_hit(self, px: int, py: int) -> tuple[Zone, str] | None:
        # iterate topmost-first
        for z in reversed(self.zones):
            mode = _hit_edge(z, px, py)
            if mode:
                return (z, mode)
        return None

    # ---------- events ----------

    def on_motion(self, ev: tk.Event) -> None:
        # Cursor: pointer over buttons / delete icons, else resize/move/arrow
        if self._hit_button(ev.x, ev.y) or self._hit_delete_icon(ev.x, ev.y):
            self.canvas.configure(cursor="hand2")
            if self._hover_zone is not None:
                self._hover_zone = None
                self.redraw()
            return

        hit = self._find_zone_hit(ev.x, ev.y)
        new_hover = hit[0] if hit else None
        if new_hover is not self._hover_zone:
            self._hover_zone = new_hover
            self.redraw()
        cursor_map = {
            "n": "sb_v_double_arrow", "s": "sb_v_double_arrow",
            "e": "sb_h_double_arrow", "w": "sb_h_double_arrow",
            "nw": "size_nw_se", "se": "size_nw_se",
            "ne": "size_ne_sw", "sw": "size_ne_sw",
            "move": "fleur", None: "arrow",
        }
        self.canvas.configure(cursor=cursor_map.get(hit[1] if hit else None, "arrow"))

    def on_press(self, ev: tk.Event) -> None:
        # 1. Toolbar buttons
        btn = self._hit_button(ev.x, ev.y)
        if btn:
            btn.action()
            return
        # 2. Per-zone delete × icons
        zd = self._hit_delete_icon(ev.x, ev.y)
        if zd:
            self._delete_zone(zd)
            return
        # 3. Don't start drags inside the toolbar strip
        if ev.y < TOOLBAR_H:
            return
        # 4. Zone resize/move
        hit = self._find_zone_hit(ev.x, ev.y)
        if not hit:
            return
        z, mode = hit
        self.drag_zone = z
        self.drag_mode = mode
        self.drag_anchor = (ev.x, ev.y)
        self.drag_orig = (z.x, z.y, z.w, z.h)
        # Capture linked zones once, with their original geometry, for each
        # edge being dragged. Move-mode (mode == "move") gets no linked
        # zones because there's no single edge being pushed.
        self._linked = {}
        if mode != "move":
            for edge in ("n", "e", "s", "w"):
                if edge in mode:
                    neighbors = _find_linked_for_edge(z, self.zones, edge)
                    if neighbors:
                        self._linked[edge] = [
                            (n, (n.x, n.y, n.w, n.h)) for n in neighbors
                        ]

    def on_drag(self, ev: tk.Event) -> None:
        if not self.drag_zone or not self.drag_mode:
            return
        dx = ev.x - self.drag_anchor[0]
        dy = ev.y - self.drag_anchor[1]
        ox, oy, ow, oh = self.drag_orig
        z = self.drag_zone
        mode = self.drag_mode

        if mode == "move":
            nx = _snap(max(0, min(self.monitor.w - ow, ox + dx)))
            ny = _snap(max(0, min(self.monitor.h - oh, oy + dy)))
            if _proposed_overlaps(z, self.zones, nx, ny, ow, oh):
                return
            z.x, z.y = nx, ny
            self.redraw()
            return

        nx, ny, nw, nh = ox, oy, ow, oh
        if "w" in mode:
            nx = _snap(ox + dx); nw = ow - (nx - ox)
        if "e" in mode:
            nw = _snap(ow + dx)
        if "n" in mode:
            ny = _snap(oy + dy); nh = oh - (ny - oy)
        if "s" in mode:
            nh = _snap(oh + dy)
        if nw < MIN_W or nh < MIN_H:
            return
        if nx < 0 or ny < 0 or nx + nw > self.monitor.w or ny + nh > self.monitor.h:
            return

        # Compute linked-neighbour adjustments without mutating until all are
        # known to be valid. Each shared edge moves in lockstep with z so the
        # tiling stays gap-free and overlap-free.
        linked_changes: list[tuple[Zone, int, int, int, int]] = []
        new_right = nx + nw
        new_bottom = ny + nh
        for edge, items in self._linked.items():
            for lz, (lox, loy, low, loh) in items:
                lx, ly, lw, lh = lox, loy, low, loh
                if edge == "e":   # neighbour is right of z; its left edge follows z.right
                    lx = new_right
                    lw = (lox + low) - lx
                elif edge == "w":  # neighbour is left of z; its right edge follows z.left
                    lw = nx - lox
                elif edge == "s":  # neighbour is below z; its top edge follows z.bottom
                    ly = new_bottom
                    lh = (loy + loh) - ly
                elif edge == "n":  # neighbour is above z; its bottom edge follows z.top
                    lh = ny - loy
                if lw < MIN_W or lh < MIN_H:
                    return  # neighbour would collapse — abort the gesture step
                linked_changes.append((lz, lx, ly, lw, lh))

        # Final overlap safety check, ignoring zones we're about to update
        about_to_change = {id(z)} | {id(c[0]) for c in linked_changes}
        check_others = [o for o in self.zones if id(o) not in about_to_change]
        if _proposed_overlaps(z, check_others, nx, ny, nw, nh):
            return
        for lz, lx, ly, lw, lh in linked_changes:
            if _proposed_overlaps(lz, check_others, lx, ly, lw, lh):
                return

        # All checks passed — commit
        z.x, z.y, z.w, z.h = nx, ny, nw, nh
        for lz, lx, ly, lw, lh in linked_changes:
            lz.x, lz.y, lz.w, lz.h = lx, ly, lw, lh
        self.redraw()

    def on_release(self, ev: tk.Event) -> None:
        self.drag_zone = None
        self.drag_mode = None
        self._linked = {}

    def on_double(self, ev: tk.Event) -> None:
        # Empty-space double-click still works as a quick add
        if (ev.y < TOOLBAR_H or self._hit_button(ev.x, ev.y)
                or self._hit_delete_icon(ev.x, ev.y)
                or self._find_zone_hit(ev.x, ev.y)):
            return
        w, h = 400, 300
        x = _snap(max(0, min(self.monitor.w - w, ev.x - w // 2)))
        y = _snap(max(TOOLBAR_H + 10, min(self.monitor.h - h, ev.y - h // 2)))
        self.zones.append(Zone(x=x, y=y, w=w, h=h))
        self.redraw()

    def on_right_click(self, ev: tk.Event) -> None:
        hit = self._find_zone_hit(ev.x, ev.y)
        if hit and hit[1] == "move":
            self._delete_zone(hit[0])

    def on_delete_key(self, ev: tk.Event) -> None:
        if self._hover_zone:
            self._delete_zone(self._hover_zone)

    def destroy(self) -> None:
        try:
            self.top.destroy()
        except tk.TclError:
            pass


def open_editor(layout: Layout) -> bool:
    """Block until user finishes (Ctrl+S, button, or Esc). Returns True if saved."""
    root = tk.Tk()
    root.withdraw()

    state = {"saved": False, "done": False}

    def finish(saved: bool) -> None:
        if state["done"]:
            return
        state["done"] = True
        state["saved"] = saved
        root.quit()

    editors: list[MonitorEditor] = []
    for m in get_monitors():
        ml = layout.for_monitor(m)
        editors.append(MonitorEditor(root, m, ml.zones, finish))

    root.mainloop()

    if state["saved"]:
        for ed in editors:
            ml = layout.for_monitor(ed.monitor)
            ml.zones = ed.zones
            ml.width = ed.monitor.w
            ml.height = ed.monitor.h
        save(layout)

    for ed in editors:
        ed.destroy()
    try:
        root.destroy()
    except tk.TclError:
        pass
    return state["saved"]
