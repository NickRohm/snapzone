"""Create / refresh the SnapZone launcher in the user's Downloads folder.

We ship a .cmd batch file (not a .lnk) because Explorer's ShellExecute path
silently refuses to launch the .lnk we emitted via WScript.Shell. The .cmd
is dumb, well-supported, and just works. It also generates the multi-res
.ico used by the tray icon, kept here so this script remains the single
"refresh launcher + icon" entry point.

Re-run any time the snapzone folder moves or the Python install changes.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
ICON_PATH = HERE / "snapzone.ico"
MAIN_SCRIPT = HERE / "main.py"


def _make_icon() -> Path:
    """Render a 256-px icon and save as multi-resolution .ico."""
    sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    base = Image.new("RGBA", (256, 256), (31, 31, 31, 255))
    d = ImageDraw.Draw(base)
    pad = 24
    gap = 8
    pane_w = (256 - pad * 2 - gap * 2) // 3
    top, bottom = pad, 256 - pad
    for i in range(3):
        x = pad + i * (pane_w + gap)
        d.rectangle((x, top, x + pane_w, bottom),
                    fill=(74, 144, 226, 255), outline=(255, 255, 255, 255), width=4)
    base.save(ICON_PATH, format="ICO", sizes=sizes)
    return ICON_PATH


def _downloads_dir() -> Path:
    """User's Downloads. OneDrive doesn't redirect Downloads by default, so
    USERPROFILE\\Downloads is correct for ~99% of installs."""
    return Path(os.environ["USERPROFILE"]) / "Downloads"


def _python_runner() -> Path:
    """Prefer pythonw.exe (no console) sitting next to the active python.exe."""
    py = Path(sys.executable)
    pyw = py.with_name("pythonw.exe")
    return pyw if pyw.exists() else py


def _cleanup_old_launchers(downloads: Path) -> list[Path]:
    """Remove any previously-installed SnapZone.lnk that Explorer wouldn't
    launch. Also clean up a stale .lnk on the Desktop from earlier versions
    (best-effort, ignored if missing)."""
    removed: list[Path] = []
    for candidate in (downloads / "SnapZone.lnk",
                      Path(os.environ.get("USERPROFILE", "")) / "OneDrive" / "Desktop" / "SnapZone.lnk",
                      Path(os.environ.get("USERPROFILE", "")) / "Desktop" / "SnapZone.lnk"):
        if candidate.exists():
            try:
                candidate.unlink()
                removed.append(candidate)
            except OSError:
                pass
    return removed


def main() -> None:
    icon = _make_icon()
    downloads = _downloads_dir()
    downloads.mkdir(parents=True, exist_ok=True)
    cmd_path = downloads / "SnapZone.cmd"

    runner = _python_runner()
    body = (
        "@echo off\r\n"
        "rem SnapZone launcher\r\n"
        f'start "" /B "{runner}" "{MAIN_SCRIPT}" --no-delay\r\n'
    )
    cmd_path.write_text(body, encoding="utf-8")

    print("Launcher created:")
    print(f"  {cmd_path}")
    print(f"  -> {runner} \"{MAIN_SCRIPT}\" --no-delay")
    print(f"Icon: {icon}")

    removed = _cleanup_old_launchers(downloads)
    for r in removed:
        print(f"Removed stale launcher: {r}")


if __name__ == "__main__":
    main()
