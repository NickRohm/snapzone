"""SnapZone entry point. Starts tray, hotkeys, and drag-snap controller."""
from __future__ import annotations

# ----- ULTRA-EARLY BOOT TRACE -----
# This block writes a debug line BEFORE any heavy imports so that if pythonw
# dies during startup (e.g. autostart firing before some service is ready,
# or shortcut launch hitting an env issue) we still have evidence of how
# far we got. Goes to ~\snapzone_boot.log — kept tiny and never rotated.
import os as _os
import sys as _sys
import time as _time
try:
    # Boot trace lives next to this script (non-virtualized path).
    _boot_path = _os.path.join(
        _os.path.dirname(_os.path.abspath(__file__)),
        "boot.log",
    )
    with open(_boot_path, "a", encoding="utf-8") as _bf:
        _bf.write(
            "[{ts}] argv={argv} exe={exe} cwd={cwd} "
            "APPDATA={appdata} USERPROFILE={up}\n".format(
                ts=_time.strftime("%Y-%m-%d %H:%M:%S"),
                argv=_sys.argv,
                exe=_sys.executable,
                cwd=_os.getcwd(),
                appdata=_os.environ.get("APPDATA"),
                up=_os.environ.get("USERPROFILE"),
            )
        )
except Exception:
    pass
# ----- /boot trace -----

import logging
import os
import sys
import threading
import time
import traceback
from pathlib import Path

# Log lives next to the code (NOT in %APPDATA%) — self-contained, immune
# to AppData virtualization, and easy to find alongside the install.
LOG_DIR = Path(__file__).resolve().parent
LOG_PATH = LOG_DIR / "snapzone.log"

logging.basicConfig(
    filename=str(LOG_PATH),
    filemode="a",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("snapzone")


def _redirect_streams() -> None:
    """pythonw.exe has no console — without this, any print() / write to
    stderr raises AttributeError on .write because std streams are None."""
    null = open(os.devnull, "w", encoding="utf-8")
    if sys.stdout is None:
        sys.stdout = null
    if sys.stderr is None:
        # Tee stderr into the log so unhandled exceptions are captured
        class _Tee:
            def write(self, s):
                if s and s.strip():
                    log.error(s.rstrip())
            def flush(self): pass
        sys.stderr = _Tee()


def _excepthook(exc_type, exc, tb) -> None:
    log.error("UNHANDLED: %s", "".join(traceback.format_exception(exc_type, exc, tb)))


_singleton_mutex = None  # kept alive for process lifetime


def _acquire_singleton() -> bool:
    """Named-mutex single-instance check. Returns False if another instance
    already holds the mutex (caller should exit). Held mutex auto-releases
    when this process dies. If pywin32 isn't importable, we log and skip
    the check (better to run two copies than to fail to start)."""
    global _singleton_mutex
    try:
        import win32api
        import win32event
        import winerror
    except Exception as e:
        log.warning("pywin32 unavailable, skipping singleton check: %s", e)
        return True
    _singleton_mutex = win32event.CreateMutex(None, False, "SnapZone-singleton-v1")
    return win32api.GetLastError() != winerror.ERROR_ALREADY_EXISTS


def main() -> None:
    _redirect_streams()
    sys.excepthook = _excepthook
    log.info("=" * 60)
    log.info("SnapZone starting (pid=%s, cwd=%s)", os.getpid(), os.getcwd())
    log.info("Python: %s", sys.executable)

    if not _acquire_singleton():
        log.info("Another instance is already running; this one will exit.")
        sys.exit(0)

    # Heartbeat: confirms main() reached and mutex acquired, with full boot
    # context. Combined with the ultra-early ~/snapzone_boot.log trace, this
    # tells us exactly how far the autostart got after each reboot.
    log.info("boot context: argv=%s appdata=%s userprofile=%s",
             sys.argv, os.environ.get("APPDATA"), os.environ.get("USERPROFILE"))

    # When launched at login the user session may not be fully ready —
    # global keyboard hooks can fail to install. A short delay fixes this.
    if "--no-delay" not in sys.argv:
        time.sleep(3)
        log.info("startup delay done")

    try:
        import hotkeys
        import tray
        import zones
        from drag_snap import DragSnapController
        from editor import open_editor
        from window_ops import ensure_dpi_aware
        from workspace import GameWatcher, WorkspaceTracker

        ensure_dpi_aware()
        log.info("DPI aware set")

        state = {"layout": zones.load()}
        lock = threading.Lock()
        log.info("layout loaded: %d monitor(s)", len(state["layout"].monitors))

        def get_layout():
            with lock:
                return state["layout"]

        def reload_layout() -> None:
            with lock:
                state["layout"] = zones.load()
            log.info("layout reloaded")

        def edit_layout() -> None:
            with lock:
                current = state["layout"]
            try:
                saved = open_editor(current)
            except Exception:
                log.exception("editor failed")
                return
            if saved:
                reload_layout()

        tracker = WorkspaceTracker()

        def restore_workspace() -> None:
            try:
                tracker.restore(get_layout())
            except Exception:
                log.exception("restore_workspace failed")

        hotkeys.register(
            get_layout, edit_layout, reload_layout,
            on_snapped=tracker.remember,
            on_restore=restore_workspace,
        )
        log.info("hotkeys registered")

        drag = DragSnapController(get_layout, on_snapped=tracker.remember)
        drag.start()
        log.info("drag-snap controller started")

        watcher = GameWatcher(get_layout, tracker)
        watcher.start()
        log.info("game watcher started")

        icon = tray.build_icon(
            on_edit=edit_layout,
            on_reload=reload_layout,
            on_restore=restore_workspace,
            on_quit=lambda: (watcher.stop(), drag.stop(), hotkeys.unregister_all()),
        )
        log.info("tray icon built, entering mainloop")
        icon.run()   # blocks on main thread
        log.info("tray exited cleanly")
    except Exception:
        log.exception("startup failed")
        raise


if __name__ == "__main__":
    main()
