"""
cursor.py — Mouse lock logic using Win32 SendInput.

Modes:
  Normal  — cursor visible, moves on screen. Proportional movement toward target.
  FPS     — mouse controls camera. Low-gain proportional (factor=0.12*speed) with
            no velocity prediction, preventing oscillation from camera feedback.

Hotkeys:
  lock_toggle (F7) — toggle lock on/off
  lock_hold   (F4) — hold to lock, release to unlock

Target selection:
  Picks character closest to cursor within FOV radius.
  Supports target_class filter: set to a class_id int to lock only that class
  (e.g. class 1 = "roblox avatar" in rivals.pt, ignoring class 0 = "friendly").
  Sticky: once locked, stays on that track. Grace period before abandoning lost track.
"""

import ctypes
import logging
import math
import threading
import time
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Win32 helpers
# ---------------------------------------------------------------------------

class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx",          ctypes.c_long),
        ("dy",          ctypes.c_long),
        ("mouseData",   ctypes.c_ulong),
        ("dwFlags",     ctypes.c_ulong),
        ("time",        ctypes.c_ulong),
        ("dwExtraInfo", ctypes.c_uint64),
    ]

class _INPUT_UNION(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT)]

class _INPUT(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("_input", _INPUT_UNION)]

_INPUT_MOUSE        = 0
_MOUSEEVENTF_MOVE   = 0x0001
_MOUSEEVENTF_NOCOAL = 0x2000


def _get_cursor_pos() -> Tuple[int, int]:
    pt = _POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


def _send_mouse_move(dx: int, dy: int) -> None:
    if dx == 0 and dy == 0:
        return
    inp = _INPUT(
        type=_INPUT_MOUSE,
        _input=_INPUT_UNION(mi=_MOUSEINPUT(
            dx=dx, dy=dy, mouseData=0,
            dwFlags=_MOUSEEVENTF_MOVE | _MOUSEEVENTF_NOCOAL,
            time=0, dwExtraInfo=0,
        )),
    )
    ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))


# ---------------------------------------------------------------------------
# CursorFollower
# ---------------------------------------------------------------------------

_LOCK_GRACE_FRAMES = 8   # frames to keep lock after track disappears before re-selecting


class CursorFollower:
    def __init__(self, config: dict, get_tracks_fn, screen_size: Tuple[int, int]):
        self._cfg = config["cursor_follow"]
        self._hotkeys_cfg = config.get("hotkeys", {})
        self._get_tracks = get_tracks_fn
        self._screen_w, self._screen_h = screen_size

        self._toggle_enabled = threading.Event()
        self._hold_active    = threading.Event()
        self._stop_event     = threading.Event()
        self._thread: Optional[threading.Thread] = None

        self._locked_target_id: Optional[int] = None
        self._lock_lost_frames: int = 0
        self._hold_was_pressed: bool = False

        self._toggle_handler      = None
        self._hold_press_handler  = None
        self._hold_release_handler = None
        self._registered_toggle_key: Optional[str] = None
        self._registered_hold_key:   Optional[str] = None

        self.active_target_id: Optional[int] = None
        self.follow_active: bool = False
        self.frame_time_ms: float = 16.67

        if self._cfg.get("enabled", False):
            self._toggle_enabled.set()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        self._install_hotkeys()
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, name="CursorFollowThread", daemon=True
        )
        self._thread.start()
        logger.info("Cursor follow thread started.")

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2.0)

    def toggle(self) -> None:
        if self._toggle_enabled.is_set():
            self._toggle_enabled.clear()
            self._reset_lock()
            self.follow_active = False
            logger.info("Lock DISABLED (toggle).")
        else:
            self._toggle_enabled.set()
            logger.info("Lock ENABLED (toggle).")

    def set_enabled(self, value: bool) -> None:
        if value:
            self._toggle_enabled.set()
        else:
            self._toggle_enabled.clear()
            self._reset_lock()
            self.follow_active = False

    def update_config(self, key: str, value) -> None:
        self._cfg[key] = value
        if key in ("lock_toggle", "lock_hold"):
            self._hotkeys_cfg[key] = value
            self._reinstall_hotkeys()

    def _reset_lock(self) -> None:
        self._locked_target_id = None
        self._lock_lost_frames = 0

    # ------------------------------------------------------------------
    # Hotkeys
    # ------------------------------------------------------------------

    def _install_hotkeys(self) -> None:
        try:
            import keyboard
            toggle_key = self._hotkeys_cfg.get("lock_toggle", "F7")
            hold_key   = self._hotkeys_cfg.get("lock_hold",   "F4")

            self._toggle_handler = keyboard.add_hotkey(toggle_key, self.toggle)
            self._registered_toggle_key = toggle_key

            def _on_hold_press(e):
                if not self._hold_was_pressed:
                    self._hold_was_pressed = True
                    self._hold_active.set()

            def _on_hold_release(e):
                self._hold_was_pressed = False
                self._hold_active.clear()
                self._reset_lock()

            self._hold_press_handler   = keyboard.on_press_key(hold_key, _on_hold_press)
            self._hold_release_handler = keyboard.on_release_key(hold_key, _on_hold_release)
            self._registered_hold_key  = hold_key

            logger.info("Hotkeys — toggle: %s  hold: %s", toggle_key, hold_key)
        except Exception as exc:
            logger.warning("Could not install hotkeys: %s", exc)

    def _reinstall_hotkeys(self) -> None:
        try:
            import keyboard
            if self._toggle_handler:
                keyboard.remove_hotkey(self._toggle_handler)
            if self._hold_press_handler:
                keyboard.unhook(self._hold_press_handler)
            if self._hold_release_handler:
                keyboard.unhook(self._hold_release_handler)
        except Exception:
            pass
        self._install_hotkeys()

    # ------------------------------------------------------------------
    # Target selection
    # ------------------------------------------------------------------

    def _ref_point(self) -> Tuple[int, int]:
        """Always use actual cursor position — the user hovers the cursor on the target."""
        return _get_cursor_pos()

    def _select_target(self, tracks: List[dict]) -> Optional[dict]:
        # Sticky: try to keep the locked target
        if self._locked_target_id is not None:
            for t in tracks:
                if t["id"] == self._locked_target_id:
                    self._lock_lost_frames = 0
                    return t
            # Track missing this frame — grace period before giving up
            self._lock_lost_frames += 1
            if self._lock_lost_frames < _LOCK_GRACE_FRAMES:
                return None  # pause movement, don't switch target
            # Grace expired — release lock
            self._reset_lock()

        # Pick new target: closest to ref point within FOV radius
        # Optionally filter by class_id (e.g. enemies only in rivals.pt)
        rx, ry = self._ref_point()
        radius       = self._cfg.get("follow_radius", 150)
        target_class = self._cfg.get("target_class")   # None = any class
        best: Optional[dict] = None
        best_dist = float("inf")
        for t in tracks:
            if target_class is not None and t.get("class_id") != target_class:
                continue
            tx, ty = t["center"]
            dist = math.sqrt((tx - rx) ** 2 + (ty - ry) ** 2)
            if dist <= radius and dist < best_dist:
                best_dist = dist
                best = t

        if best is not None:
            self._locked_target_id = best["id"]
            self._lock_lost_frames = 0
        return best

    # ------------------------------------------------------------------
    # Follow point + prediction
    # ------------------------------------------------------------------

    def _compute_follow_point(self, track: dict) -> Tuple[float, float]:
        x1, y1, x2, y2 = track["bbox"]
        h  = y2 - y1
        cx = (x1 + x2) / 2
        pt = self._cfg.get("follow_point", "chest")
        if pt == "head":
            return cx, y1 + h * 0.15
        elif pt == "chest":
            return cx, y1 + h * 0.35
        else:
            return cx, (y1 + y2) / 2

    def _predict_position(
        self, pos: Tuple[float, float], velocity: Tuple[float, float]
    ) -> Tuple[float, float]:
        # FPS mode: the character's screen velocity is dominated by camera rotation,
        # not actual movement — using it as a predictor inverts the delta and causes
        # the cursor to flick away from the target. Skip prediction entirely.
        if self._cfg.get("fps_mode", False):
            return pos
        prediction_ms = self._cfg.get("prediction_ms", 60)
        scale = prediction_ms / max(self.frame_time_ms, 1.0)
        px = pos[0] + velocity[0] * scale
        py = pos[1] + velocity[1] * scale
        px = max(0.0, min(float(self._screen_w), px))
        py = max(0.0, min(float(self._screen_h), py))
        return px, py

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def _run(self) -> None:
        loop_hz  = 120
        interval = 1.0 / loop_hz

        while not self._stop_event.is_set():
            t0 = time.perf_counter()

            active = self._toggle_enabled.is_set() or self._hold_active.is_set()
            if not active:
                self.follow_active    = False
                self.active_target_id = None
                time.sleep(interval)
                continue

            self.follow_active = True
            tracks  = self._get_tracks()
            target  = self._select_target(tracks)

            if target is None:
                self.active_target_id = None
                time.sleep(interval)
                continue

            self.active_target_id = target["id"]

            fp        = self._compute_follow_point(target)
            predicted = self._predict_position(fp, target["velocity"])

            rx, ry = self._ref_point()
            dx = predicted[0] - rx
            dy = predicted[1] - ry
            dist = math.sqrt(dx * dx + dy * dy)

            fps_mode = self._cfg.get("fps_mode", False)
            deadzone = self._cfg.get("deadzone", 5) if fps_mode else 0
            speed    = self._cfg.get("speed", 1.0)

            if dist <= deadzone:
                # Already on target
                elapsed    = time.perf_counter() - t0
                sleep_time = interval - elapsed
                if sleep_time > 0:
                    time.sleep(sleep_time)
                continue

            if fps_mode:
                # Proportional with a fixed base factor scaled by speed.
                # No forced minimum 1px — that was the cause of the oval spinning:
                # near-target 1px corrections at 120Hz get amplified by camera sensitivity
                # into a constant orbit. Instead, let int() truncation naturally zero out
                # sub-pixel corrections. The deadzone catches the final approach.
                # Convergence is guaranteed when (factor * camera_sensitivity) < 1,
                # which holds for any sane in-game sensitivity at factor=0.12.
                factor = 0.12 * speed
                move_x = dx * factor
                move_y = dy * factor
            else:
                # Proportional: move a fraction of the remaining delta each frame
                smoothing = self._cfg.get("smoothing", 0.12)
                factor    = (1.0 - smoothing) * speed
                move_x    = dx * factor
                move_y    = dy * factor
                # Guarantee at least 1px if there is meaningful delta
                if abs(dx) > 0.5 and abs(move_x) < 1.0:
                    move_x = math.copysign(1.0, dx)
                if abs(dy) > 0.5 and abs(move_y) < 1.0:
                    move_y = math.copysign(1.0, dy)

            _send_mouse_move(int(move_x), int(move_y))

            elapsed    = time.perf_counter() - t0
            sleep_time = interval - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)
