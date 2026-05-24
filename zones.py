"""Zone data model + JSON persistence."""
from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from window_ops import Monitor, get_monitors

# Config lives next to the code (NOT in %APPDATA%) so the app is fully
# self-contained and immune to AppData virtualization / redirection.
CONFIG_DIR = Path(__file__).resolve().parent
CONFIG_PATH = CONFIG_DIR / "zones.json"


@dataclass
class Zone:
    x: int          # monitor-relative pixels
    y: int
    w: int
    h: int
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])

    @property
    def cx(self) -> int:
        return self.x + self.w // 2

    @property
    def cy(self) -> int:
        return self.y + self.h // 2

    def contains(self, px: int, py: int) -> bool:
        return self.x <= px < self.x + self.w and self.y <= py < self.y + self.h

    def to_absolute(self, monitor: Monitor) -> tuple[int, int, int, int]:
        """Convert monitor-relative to virtual-screen coordinates."""
        return (monitor.x + self.x, monitor.y + self.y, self.w, self.h)


@dataclass
class MonitorLayout:
    monitor_id: str
    width: int
    height: int
    zones: list[Zone] = field(default_factory=list)

    def scaled_to(self, m: Monitor) -> "MonitorLayout":
        """Return a copy rescaled if the monitor resolution changed since save."""
        if m.w == self.width and m.h == self.height:
            return self
        sx, sy = m.w / self.width, m.h / self.height
        return MonitorLayout(
            monitor_id=self.monitor_id,
            width=m.w, height=m.h,
            zones=[Zone(
                x=int(z.x * sx), y=int(z.y * sy),
                w=int(z.w * sx), h=int(z.h * sy),
                id=z.id,
            ) for z in self.zones],
        )


@dataclass
class Layout:
    monitors: dict[str, MonitorLayout] = field(default_factory=dict)

    def for_monitor(self, m: Monitor) -> MonitorLayout:
        ml = self.monitors.get(m.id)
        if ml is None:
            ml = default_layout_for(m)
            self.monitors[m.id] = ml
            return ml
        return ml.scaled_to(m)


def default_layout_for(m: Monitor) -> MonitorLayout:
    """Three equal columns — sensible default for ultrawide."""
    col_w = m.w // 3
    zones = [
        Zone(x=0, y=0, w=col_w, h=m.h),
        Zone(x=col_w, y=0, w=col_w, h=m.h),
        Zone(x=2 * col_w, y=0, w=m.w - 2 * col_w, h=m.h),
    ]
    return MonitorLayout(monitor_id=m.id, width=m.w, height=m.h, zones=zones)


def load() -> Layout:
    if not CONFIG_PATH.exists():
        layout = Layout()
        for m in get_monitors():
            layout.monitors[m.id] = default_layout_for(m)
        save(layout)
        return layout
    raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    monitors: dict[str, MonitorLayout] = {}
    for mid, data in raw.get("monitors", {}).items():
        monitors[mid] = MonitorLayout(
            monitor_id=mid,
            width=data["width"],
            height=data["height"],
            zones=[Zone(**z) for z in data["zones"]],
        )
    return Layout(monitors=monitors)


def save(layout: Layout) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    data = {"monitors": {mid: asdict(ml) for mid, ml in layout.monitors.items()}}
    CONFIG_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
