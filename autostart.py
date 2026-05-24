"""Windows autostart via HKCU\\...\\Run registry key."""
from __future__ import annotations

import sys
import winreg
from pathlib import Path

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
APP_NAME = "SnapZone"


def _main_script() -> Path:
    return Path(__file__).resolve().parent / "main.py"


def _command() -> str:
    # Use pythonw.exe to suppress console; fall back to python.exe if not found
    py = Path(sys.executable)
    pyw = py.with_name("pythonw.exe")
    runner = pyw if pyw.exists() else py
    return f'"{runner}" "{_main_script()}"'


def is_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as k:
            value, _ = winreg.QueryValueEx(k, APP_NAME)
            return bool(value)
    except FileNotFoundError:
        return False
    except OSError:
        return False


def enable() -> None:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, _command())


def disable() -> None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, APP_NAME)
    except FileNotFoundError:
        pass


def toggle() -> bool:
    if is_enabled():
        disable()
        return False
    enable()
    return True
