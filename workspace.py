"""Workspace memory + auto-restore when tabbing out of a fullscreen game.

WorkspaceTracker remembers, per zone, which window was snapped into it.
It keeps a live hwnd map for the session AND persists a zone -> executable
mapping to disk, so after an app restart / reboot it can still find the
right windows by matching their process executable. GameWatcher polls the
foreground window and, when a fullscreen/borderless game loses focus,
minimizes the game and re-raises every zone's window.
"""
from __future__ import annotations

import json
import logging
import ntpath
import threading
import time
from pathlib import Path
from typing import Callable

import window_ops as wo
from zones import Layout

log = logging.getLogger("snapzone.workspace")

# Persisted next to the code (same self-contained, non-virtualized dir as
# zones.json / snapzone.log).
STORE_PATH = Path(__file__).resolve().parent / "workspace.json"


def _exe_name(path: str) -> str:
    """Match key for an executable: the filename only, lowercased.

    Full paths are NOT stable: Windows Store apps live under
    `...\\WindowsApps\\claude_<VERSION>_x64__...\\app\\claude.exe`, so the
    path changes on every app update. The basename ("claude.exe") is stable
    and still unique enough for this use case."""
    if not path:
        return ""
    return ntpath.basename(path).lower()


class WorkspaceTracker:
    """Zone -> window memory.

    - `_zone_hwnd`: live hwnd per zone (this session).
    - `_zone_exe`:  persisted executable path per zone (survives restarts).

    On restore, a zone's window is resolved as: the live hwnd if still
    valid, otherwise the first open window whose process exe matches the
    persisted exe for that zone.
    """

    def __init__(self) -> None:
        self._zone_hwnd: dict[str, int] = {}
        self._zone_exe: dict[str, str] = {}
        self._order: list[str] = []          # zone ids, most-recent last
        self._lock = threading.Lock()
        # Windows we've temporarily pinned WS_EX_TOPMOST (to outlast a game's
        # auto-restore pop-back). Tracked so we can reliably un-pin them — via
        # a fallback timer AND immediately when the user re-enters the game.
        self._topmost: set[int] = set()
        self._tm_lock = threading.Lock()
        self._load()

    # ---------- topmost pin/unpin ----------

    def pin_topmost(self, hwnds: list[int]) -> None:
        with self._tm_lock:
            for h in hwnds:
                if wo.set_topmost(h, True):
                    self._topmost.add(h)

    def clear_topmost(self) -> None:
        with self._tm_lock:
            if not self._topmost:
                return
            for h in list(self._topmost):
                wo.set_topmost(h, False)
            self._topmost.clear()

    def is_pinned(self, hwnd: int) -> bool:
        with self._tm_lock:
            return hwnd in self._topmost

    # ---------- persistence ----------

    def _load(self) -> None:
        try:
            if STORE_PATH.exists():
                data = json.loads(STORE_PATH.read_text(encoding="utf-8"))
                self._zone_exe = {str(k): str(v).lower()
                                  for k, v in data.get("zone_exe", {}).items()}
                self._order = [str(z) for z in data.get("order", [])]
                log.info("workspace loaded: %d zone->app mapping(s)",
                         len(self._zone_exe))
        except Exception:
            log.exception("failed to load workspace.json (ignoring)")

    def _save_locked(self) -> None:
        try:
            STORE_PATH.write_text(
                json.dumps({"zone_exe": self._zone_exe,
                            "order": self._order}, indent=2),
                encoding="utf-8",
            )
        except Exception:
            log.exception("failed to save workspace.json")

    # ---------- recording ----------

    def remember(self, zone_id: str, hwnd: int) -> None:
        if not zone_id or not hwnd:
            return
        exe = wo.get_window_exe(hwnd)
        exe_key = _exe_name(exe)
        with self._lock:
            # Keep it 1 window <-> 1 zone: drop this hwnd/exe from other zones.
            for zid, h in list(self._zone_hwnd.items()):
                if h == hwnd and zid != zone_id:
                    del self._zone_hwnd[zid]
            if exe_key:
                for zid, e in list(self._zone_exe.items()):
                    if _exe_name(e) == exe_key and zid != zone_id:
                        del self._zone_exe[zid]
            self._zone_hwnd[zone_id] = hwnd
            if exe:
                self._zone_exe[zone_id] = exe
            if zone_id in self._order:
                self._order.remove(zone_id)
            self._order.append(zone_id)
            self._save_locked()
        log.info("remembered zone %s -> hwnd %s exe %s",
                 zone_id, hwnd, exe or "?")

    # ---------- restore ----------

    def _resolve(self, zone_id: str, used: set[int]) -> int:
        """Best hwnd for a zone: live hwnd if valid, else an open window
        whose exe matches the persisted one. `used` prevents two zones
        grabbing the same window in one restore pass."""
        h = self._zone_hwnd.get(zone_id)
        if h and h not in used and wo.is_window(h):
            return h
        want = _exe_name(self._zone_exe.get(zone_id, ""))
        if not want:
            return 0
        for cand in wo.enum_top_level_windows():
            if cand in used:
                continue
            if wo.is_own_window(cand):
                continue
            if _exe_name(wo.get_window_exe(cand)) == want:
                self._zone_hwnd[zone_id] = cand   # refresh live binding
                return cand
        return 0

    def restore(self, layout: Layout, game_hwnd: int = 0) -> int:
        """Minimize the blocking game and raise every zone's window into its
        zone. `game_hwnd` is the window the GameWatcher saw lose focus; we
        minimize THAT specifically (by the time we run, the foreground is
        already the app the user tabbed to, so checking the current
        foreground would never find the game). Manual triggers pass 0 and
        fall back to "minimize the foreground if it's fullscreen".
        Returns the number of windows restored."""
        with self._lock:
            order = list(self._order)
            zone_ids_known = set(self._zone_hwnd) | set(self._zone_exe)

            # 1. Minimize the blocking borderless/fullscreen game.
            target = 0
            if game_hwnd and wo.is_window(game_hwnd) and not wo.is_minimized(game_hwnd):
                target = game_hwnd
            else:
                fg = wo.get_active_hwnd()
                if (fg and not wo.is_own_window(fg)
                        and wo.is_fullscreen_window(fg)):
                    target = fg
            if target:
                wo.minimize_window(target)
                # Wait until the game is ACTUALLY minimized before raising
                # the zone windows — otherwise a still-on-top borderless
                # game re-covers them (esp. on fast tab-out cycles).
                deadline = time.monotonic() + 0.6
                while time.monotonic() < deadline:
                    if wo.is_minimized(target):
                        break
                    time.sleep(0.03)
                if not wo.is_minimized(target):
                    wo.force_minimize_window(target)
                    time.sleep(0.05)
                    log.info("game hwnd %s slow to minimize -> forced", target)

            # 2. Resolve + re-place each zone's window.
            restored = 0
            raised: list[int] = []
            used: set[int] = set()
            focus_target = 0
            monitors = wo.get_monitors()
            for m in monitors:
                ml = layout.for_monitor(m)
                for z in ml.zones:
                    if z.id not in zone_ids_known:
                        continue
                    hwnd = self._resolve(z.id, used)
                    if not hwnd:
                        continue
                    used.add(hwnd)
                    wo.restore_window(hwnd)
                    ax, ay, aw, ah = z.to_absolute(m)
                    wo.move_window(hwnd, ax, ay, aw, ah)
                    wo.bring_to_top(hwnd)
                    raised.append(hwnd)
                    restored += 1

            # 3. Focus the most-recently snapped zone's window.
            for zid in reversed(order):
                h = self._zone_hwnd.get(zid)
                if h and wo.is_window(h):
                    focus_target = h
                    break

        if focus_target:
            wo.set_foreground(focus_target)

        # Pin the raised windows topmost to outlast the game's auto-restore
        # pop-back. Un-pinned by EITHER: the fallback timer below (covers
        # "tabbed away, didn't go back to the game"), OR the GameWatcher
        # calling clear_topmost() the instant it sees the user re-enter the
        # game (covers "tabbed back into the game" — so our windows can never
        # be left overlaying it). The watcher path is what makes rapid
        # in/out cycling safe; the timer is just a backstop.
        if raised:
            self.pin_topmost(raised)
            # Long safety net only. The real un-pin happens in the watcher:
            # the moment the user is genuinely back in the game, or focuses an
            # unrelated window. We keep windows topmost while the user is on
            # them so a game's delayed auto-pop-back can't creep over them.
            threading.Timer(30.0, self.clear_topmost).start()

        log.info("restore: %d window(s) raised", restored)
        return restored


class GameWatcher:
    """Daemon thread. Fires tracker.restore() exactly once each time a
    fullscreen/borderless game loses foreground focus."""

    POLL_S = 0.25

    def __init__(self, get_layout: Callable[[], Layout],
                 tracker: WorkspaceTracker) -> None:
        self._get_layout = get_layout
        self._tracker = tracker
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run, name="GameWatcher", daemon=True)
        self._thread.start()
        log.info("game watcher thread polling every %.2fs", self.POLL_S)

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        # Process-based state machine. We track the game by its PROCESS, not a
        # single window handle (some games spawn short-lived borderless
        # windows that would hijack single-hwnd tracking). Each tick we ask
        # "does the game process have a fullscreen window up, and where's the
        # user?" Topmost pins on our zone windows are held while the user is
        # on them, and dropped on game re-entry or when the user focuses an
        # unrelated window.
        game_pid = 0
        restored = False
        prev_present = False
        while not self._stop.is_set():
            try:
                fg = wo.get_active_hwnd()

                # While a shell window is foreground (user mid-Alt+Tab, Start
                # menu open, etc.) the foreground hasn't settled — do nothing
                # this tick so we don't fire a restore and steal focus, which
                # would commit the switch out from under the user.
                if fg and wo.is_shell_window(fg):
                    self._stop.wait(self.POLL_S)
                    continue

                fg_own = wo.is_own_window(fg) if fg else True

                if fg and not fg_own and wo.is_fullscreen_window(fg):
                    # The game is in the foreground -> the user is in the game.
                    # Always honour this (drop topmost pins, arm). We do NOT
                    # try to re-minimize "auto pop-backs": timing can't tell a
                    # game's self-restore from the user genuinely tabbing back
                    # in, and guessing wrong locks the user out of their game.
                    self._tracker.clear_topmost()
                    # Clearing topmost places the window at the top of the
                    # non-topmost z-order — which would put Brave/Claude
                    # ABOVE the foreground game. Raise the game explicitly
                    # so it stays on top.
                    wo.bring_to_top(fg)
                    pid = wo.get_window_pid(fg)
                    if pid and pid != game_pid:
                        log.info("game process detected: pid %s (hwnd %s)",
                                 pid, fg)
                    game_pid = pid
                    restored = False
                    prev_present = True
                    self._stop.wait(self.POLL_S)
                    continue

                # Foreground is NOT a game window.
                # If the user focused an unrelated, non-pinned window, drop our
                # topmost pins so we don't float over it.
                if fg and not fg_own and not self._tracker.is_pinned(fg):
                    self._tracker.clear_topmost()

                # Is the game still up (un-minimized fullscreen window)?
                game_hwnd = (wo.find_fullscreen_window_for_pid(game_pid)
                             if game_pid else 0)
                present = bool(game_hwnd)
                if present and not prev_present:
                    if restored:
                        log.info("game (pid %s) back -> re-armed", game_pid)
                    restored = False
                prev_present = present

                # Fire once per episode: game's fullscreen window is up and the
                # user is on some other normal (non-own) window.
                if (present and not restored
                        and fg and not fg_own
                        and not wo.is_minimized(fg)):
                    restored = True
                    log.info("game (pid %s, hwnd %s) lost focus -> restoring",
                             game_pid, game_hwnd)
                    try:
                        self._tracker.restore(self._get_layout(),
                                               game_hwnd=game_hwnd)
                    except Exception:
                        log.exception("restore failed")
            except Exception:
                log.exception("game watcher tick failed")
            self._stop.wait(self.POLL_S)
