"""
cursor.py — Mouse lock logic using Win32 SendInput.

Modes:
  Normal  — cursor visible, moves on screen. PID-controlled movement toward target.
  FPS     — mouse controls camera. Low-gain proportional (factor=0.12*speed) with
            SmartTracker prediction, preventing oscillation from camera feedback.

Features:
  PID control       — smooth, overshoot-free movement via PIDController
  SmartTracker      — velocity prediction with direction-change aware smoothing
  Snap-back         — detects unexpected cursor movement (user input) and pauses lock
  Triggerbot        — auto-clicks when cursor is inside a detected bbox
  Depth preference  — optionally prefer the target with the largest bbox (closest)
  Sticky lock       — keeps locked target across frames with grace period

Hotkeys:
  lock_toggle (F7) — toggle lock on/off
  lock_hold   (F4) — hold to lock, release to unlock
  triggerbot  (F5) — toggle triggerbot on/off
"""

import ctypes
import logging
import math
import random
import threading
import time
from typing import List, Optional, Tuple

from pid_controller import PIDController
from smart_tracker import SmartTracker

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

_INPUT_MOUSE         = 0
_MOUSEEVENTF_MOVE    = 0x0001
_MOUSEEVENTF_NOCOAL  = 0x2000
_MOUSEEVENTF_LEFTDOWN  = 0x0002
_MOUSEEVENTF_LEFTUP    = 0x0004

# VK codes for mouse buttons
_VK_LBUTTON = 0x01
_VK_RBUTTON = 0x02


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


def _send_left_click() -> None:
    """Send a single left mouse button down + up."""
    inp_down = _INPUT(
        type=_INPUT_MOUSE,
        _input=_INPUT_UNION(mi=_MOUSEINPUT(
            dx=0, dy=0, mouseData=0,
            dwFlags=_MOUSEEVENTF_LEFTDOWN,
            time=0, dwExtraInfo=0,
        )),
    )
    inp_up = _INPUT(
        type=_INPUT_MOUSE,
        _input=_INPUT_UNION(mi=_MOUSEINPUT(
            dx=0, dy=0, mouseData=0,
            dwFlags=_MOUSEEVENTF_LEFTUP,
            time=0, dwExtraInfo=0,
        )),
    )
    ctypes.windll.user32.SendInput(1, ctypes.byref(inp_down), ctypes.sizeof(_INPUT))
    ctypes.windll.user32.SendInput(1, ctypes.byref(inp_up), ctypes.sizeof(_INPUT))


def _is_vk_down(vk: int) -> bool:
    return bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000)


# ---------------------------------------------------------------------------
# CursorFollower
# ---------------------------------------------------------------------------

_LOCK_GRACE_FRAMES = 8


class CursorFollower:
    def __init__(self, config: dict, get_tracks_fn, screen_size: Tuple[int, int]):
        self._cfg          = config["cursor_follow"]
        self._tb_cfg       = config.get("triggerbot", {})
        self._hotkeys_cfg  = config.get("hotkeys", {})
        self._get_tracks   = get_tracks_fn
        self._screen_w, self._screen_h = screen_size

        # Lock state
        self._toggle_enabled = threading.Event()
        self._hold_active    = threading.Event()
        self._stop_event     = threading.Event()
        self._thread: Optional[threading.Thread] = None

        self._locked_target_id: Optional[int] = None
        self._lock_lost_frames: int = 0
        self._lock_start_time: float = 0.0
        self._hold_was_pressed: bool = False

        # Hotkey handler references
        self._toggle_handler       = None
        self._hold_press_handler   = None
        self._hold_release_handler = None
        self._tb_hotkey_handler    = None
        self._registered_toggle_key: Optional[str] = None
        self._registered_hold_key:   Optional[str] = None
        self._registered_tb_key:     Optional[str] = None

        # Snap-back state
        self._expected_cursor: Optional[Tuple[int, int]] = None
        self._snapback_until: float = 0.0

        # Triggerbot state
        self._tb_enabled: bool = self._tb_cfg.get("enabled", False)
        self._tb_last_click_t: float = 0.0

        # PID controllers
        pid_cfg = self._cfg.get("pid", {})
        self._pid_x = PIDController(
            Kp=pid_cfg.get("kp", 0.4),
            Ki=pid_cfg.get("ki", 0.0),
            Kd=pid_cfg.get("kd", 0.08),
        )
        self._pid_y = PIDController(
            Kp=pid_cfg.get("kp", 0.4),
            Ki=pid_cfg.get("ki", 0.0),
            Kd=pid_cfg.get("kd", 0.08),
        )

        # SmartTracker
        tracker_cfg = self._cfg.get("tracker", {})
        self._smart_tracker = SmartTracker(
            smoothing_factor=tracker_cfg.get("smoothing_factor", 0.5),
            stop_threshold=tracker_cfg.get("stop_threshold", 20.0),
            position_deadzone=tracker_cfg.get("position_deadzone", 4.0),
        )
        self._last_track_time: float = 0.0
        self._last_locked_target_id: Optional[int] = None

        # Public state
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

    def update_triggerbot(self, key: str, value) -> None:
        self._tb_cfg[key] = value
        if key == "enabled":
            self._tb_enabled = bool(value)
        elif key == "hotkey":
            self._reinstall_hotkeys()

    def _reset_lock(self) -> None:
        self._locked_target_id = None
        self._lock_lost_frames = 0
        self._lock_start_time = 0.0
        self._pid_x.reset()
        self._pid_y.reset()
        self._smart_tracker.reset()
        self._last_locked_target_id = None

    # ------------------------------------------------------------------
    # Hotkeys
    # ------------------------------------------------------------------

    def _install_hotkeys(self) -> None:
        try:
            import keyboard

            toggle_key = self._hotkeys_cfg.get("lock_toggle", "F7")
            hold_key   = self._hotkeys_cfg.get("lock_hold",   "F4")
            tb_key     = self._tb_cfg.get("hotkey", "F5")

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

            def _toggle_tb():
                self._tb_enabled = not self._tb_enabled
                logger.info("Triggerbot %s", "ON" if self._tb_enabled else "OFF")

            self._tb_hotkey_handler   = keyboard.add_hotkey(tb_key, _toggle_tb)
            self._registered_tb_key   = tb_key

            logger.info(
                "Hotkeys — toggle: %s  hold: %s  triggerbot: %s",
                toggle_key, hold_key, tb_key,
            )
        except Exception as exc:
            logger.warning("Could not install hotkeys: %s", exc)

    def _reinstall_hotkeys(self) -> None:
        try:
            import keyboard
            for h in (
                self._toggle_handler,
                self._hold_press_handler,
                self._hold_release_handler,
                self._tb_hotkey_handler,
            ):
                if h is not None:
                    try:
                        keyboard.remove_hotkey(h)
                    except Exception:
                        try:
                            keyboard.unhook(h)
                        except Exception:
                            pass
        except Exception:
            pass
        self._install_hotkeys()

    # ------------------------------------------------------------------
    # Target selection
    # ------------------------------------------------------------------

    def _ref_point(self) -> Tuple[int, int]:
        return _get_cursor_pos()

    def _select_target(self, tracks: List[dict]) -> Optional[dict]:
        # Sticky: keep locked target if still visible
        if self._locked_target_id is not None:
            for t in tracks:
                if t["id"] == self._locked_target_id:
                    self._lock_lost_frames = 0
                    return t
            self._lock_lost_frames += 1
            if self._lock_lost_frames < _LOCK_GRACE_FRAMES:
                return None
            self._reset_lock()

        rx, ry = self._ref_point()
        radius       = self._cfg.get("follow_radius", 150)
        prefer_depth = self._cfg.get("prefer_closest_depth", False)

        candidates: List[Tuple[float, dict]] = []
        for t in tracks:
            tx, ty = t["center"]
            dist = math.sqrt((tx - rx) ** 2 + (ty - ry) ** 2)
            if dist <= radius:
                candidates.append((dist, t))

        if not candidates:
            return None

        if prefer_depth:
            best = max(candidates, key=lambda x: x[1].get("depth_score", 0.0))[1]
        else:
            best = min(candidates, key=lambda x: x[0])[1]

        self._locked_target_id = best["id"]
        self._lock_lost_frames = 0
        self._lock_start_time = time.perf_counter()
        return best

    # ------------------------------------------------------------------
    # Follow point + prediction
    # ------------------------------------------------------------------

    def _compute_follow_point(self, track: dict) -> Tuple[float, float]:
        x1, y1, x2, y2 = track["bbox"]
        h  = y2 - y1
        cx = (x1 + x2) / 2
        pt = self._cfg.get("follow_point", "chest")
        head_ratio = self._cfg.get("head_height_ratio", 0.15)
        if pt == "head":
            return cx, y1 + h * head_ratio
        elif pt == "chest":
            return cx, y1 + h * (head_ratio + 0.20)
        else:
            return cx, (y1 + y2) / 2

    def _predict_position(
        self, target_x: float, target_y: float, target_id: int
    ) -> Tuple[float, float]:
        """Use SmartTracker for position prediction with velocity estimation."""
        now = time.perf_counter()

        # Reset SmartTracker if target changed
        if target_id != self._last_locked_target_id:
            self._smart_tracker.reset()
            self._last_locked_target_id = target_id
            self._last_track_time = now

        dt = now - self._last_track_time if self._last_track_time > 0 else 0.0
        self._last_track_time = now

        self._smart_tracker.update(target_x, target_y, dt)

        if self._cfg.get("fps_mode", False):
            return target_x, target_y

        prediction_ms = self._cfg.get("prediction_ms", 60)
        pred_x, pred_y = self._smart_tracker.get_predicted_position(prediction_ms / 1000.0)

        # Clamp to screen bounds
        pred_x = max(0.0, min(float(self._screen_w), pred_x))
        pred_y = max(0.0, min(float(self._screen_h), pred_y))
        return pred_x, pred_y

    # ------------------------------------------------------------------
    # Movement computation (PID)
    # ------------------------------------------------------------------

    def _compute_movement(
        self,
        dx: float,
        dy: float,
        dist: float,
        fps_mode: bool,
    ) -> Tuple[float, float]:
        speed = self._cfg.get("speed", 1.0)

        if fps_mode:
            factor = 0.12 * speed
            return dx * factor, dy * factor

        move_x = self._pid_x.update(dx) * speed
        move_y = self._pid_y.update(dy) * speed

        # aim_y_reduce: after locking for N seconds, suppress Y correction.
        # Prevents the cursor from drifting down while tracking a stationary target.
        if (self._cfg.get("aim_y_reduce", False)
                and self._lock_start_time > 0):
            delay = self._cfg.get("aim_y_reduce_delay", 0.6)
            if time.perf_counter() - self._lock_start_time > delay:
                move_y = 0.0

        # Guarantee at least 1px movement if there's meaningful delta
        if abs(dx) > 0.5 and abs(move_x) < 1.0:
            move_x = math.copysign(1.0, dx)
        if abs(dy) > 0.5 and abs(move_y) < 1.0:
            move_y = math.copysign(1.0, dy)

        return move_x, move_y

    # ------------------------------------------------------------------
    # Triggerbot
    # ------------------------------------------------------------------

    def _handle_triggerbot(self, tracks: List[dict]) -> None:
        if not self._tb_cfg.get("enabled", False) or not self._tb_enabled:
            return

        delay_min = self._tb_cfg.get("delay_min_ms", 50) / 1000.0
        delay_max = self._tb_cfg.get("delay_max_ms", 120) / 1000.0
        padding   = self._tb_cfg.get("padding", 5)

        now = time.perf_counter()
        if now < self._tb_last_click_t:
            return  # Still in delay window

        rx, ry = _get_cursor_pos()
        for track in tracks:
            x1, y1, x2, y2 = track["bbox"]
            if (x1 - padding <= rx <= x2 + padding and
                    y1 - padding <= ry <= y2 + padding):
                _send_left_click()
                self._tb_last_click_t = now + random.uniform(delay_min, delay_max)
                break

    # ------------------------------------------------------------------
    # Snap-back detection
    # ------------------------------------------------------------------

    def _check_snapback(self, actual_cursor: Tuple[int, int]) -> bool:
        """
        Returns True if an unexpected cursor movement (user input) was detected.
        Only applies in normal mode.
        """
        if self._expected_cursor is None:
            return False

        ex, ey = self._expected_cursor
        diff = math.sqrt(
            (actual_cursor[0] - ex) ** 2 + (actual_cursor[1] - ey) ** 2
        )
        threshold = self._cfg.get("snapback_threshold", 15)
        return diff > threshold

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def _sleep_remaining(self, interval: float, t0: float) -> None:
        elapsed = time.perf_counter() - t0
        sleep_time = interval - elapsed
        if sleep_time > 0:
            time.sleep(sleep_time)

    def _run(self) -> None:
        loop_hz  = 120
        interval = 1.0 / loop_hz

        while not self._stop_event.is_set():
            t0 = time.perf_counter()

            active = self._toggle_enabled.is_set() or self._hold_active.is_set()
            if not active:
                self.follow_active    = False
                self.active_target_id = None
                self._expected_cursor = None
                self._sleep_remaining(interval, t0)
                continue

            self.follow_active = True
            tracks = self._get_tracks()

            # Triggerbot runs regardless of which target is locked
            self._handle_triggerbot(tracks)

            fps_mode = self._cfg.get("fps_mode", False)

            # Snap-back check (normal mode only)
            if not fps_mode:
                now = time.perf_counter()
                if now < self._snapback_until:
                    self.active_target_id = None
                    self._sleep_remaining(interval, t0)
                    continue

                actual = _get_cursor_pos()
                if self._check_snapback(actual):
                    pause_ms = self._cfg.get("snapback_pause_ms", 200)
                    self._snapback_until = time.perf_counter() + pause_ms / 1000.0
                    self._reset_lock()
                    self._expected_cursor = None
                    self._sleep_remaining(interval, t0)
                    continue

            self._expected_cursor = None

            target = self._select_target(tracks)
            if target is None:
                self.active_target_id = None
                # On target loss: reset PID and SmartTracker
                self._pid_x.reset()
                self._pid_y.reset()
                self._smart_tracker.reset()
                self._sleep_remaining(interval, t0)
                continue

            self.active_target_id = target["id"]

            fp = self._compute_follow_point(target)
            predicted = self._predict_position(fp[0], fp[1], target["id"])

            rx, ry = self._ref_point()
            dx = predicted[0] - rx
            dy = predicted[1] - ry

            # Use SmartTracker deadzone check
            if self._smart_tracker.is_in_deadzone(predicted[0], predicted[1], rx, ry):
                self._sleep_remaining(interval, t0)
                continue

            # FPS mode: also check simple deadzone
            if fps_mode:
                dist = math.sqrt(dx * dx + dy * dy)
                deadzone = self._cfg.get("deadzone", 5)
                if dist <= deadzone:
                    self._sleep_remaining(interval, t0)
                    continue

            dist = math.sqrt(dx * dx + dy * dy)
            move_x, move_y = self._compute_movement(dx, dy, dist, fps_mode)

            # Record expected cursor position before sending move (snapback detection)
            if not fps_mode:
                self._expected_cursor = (rx + int(move_x), ry + int(move_y))

            _send_mouse_move(int(move_x), int(move_y))

            self._sleep_remaining(interval, t0)
