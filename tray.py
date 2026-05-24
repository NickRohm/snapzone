"""System tray icon + menu."""
from __future__ import annotations

from typing import Callable

import pystray
from PIL import Image, ImageDraw

import autostart


def _make_icon() -> Image.Image:
    img = Image.new("RGB", (64, 64), "#1f1f1f")
    d = ImageDraw.Draw(img)
    # stylized 3-pane layout
    d.rectangle((6, 12, 22, 52), fill="#4a90e2", outline="white")
    d.rectangle((24, 12, 40, 52), fill="#4a90e2", outline="white")
    d.rectangle((42, 12, 58, 52), fill="#4a90e2", outline="white")
    return img


def build_icon(
    on_edit: Callable[[], None],
    on_reload: Callable[[], None],
    on_quit: Callable[[], None],
    on_restore: Callable[[], None] | None = None,
) -> pystray.Icon:
    def toggle_autostart(icon, item):
        autostart.toggle()
        icon.update_menu()

    def autostart_checked(_item):
        return autostart.is_enabled()

    menu = pystray.Menu(
        pystray.MenuItem("Edit Zones  (Ctrl+Shift+E)", lambda icon, item: on_edit()),
        pystray.MenuItem("Reload Config  (Ctrl+Shift+R)", lambda icon, item: on_reload()),
        pystray.MenuItem("Restore Windows Now  (Ctrl+Shift+Space)",
                         lambda icon, item: on_restore() if on_restore else None),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Start with Windows", toggle_autostart, checked=autostart_checked),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", lambda icon, item: (on_quit(), icon.stop())),
    )

    return pystray.Icon("SnapZone", _make_icon(), "SnapZone", menu)
