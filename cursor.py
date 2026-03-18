"""
cursor.py — Mouse lock logic using Win32 SendInput.

Modes:
  Normal  — cursor visible, moves on screen. Proportional movement toward target.
  FPS     — mouse controls camera. Low-gain proportional (factor=0.12*speed) with
            no velocity prediction, preventing oscillation from camera feedback.

Features:
  Smoothing curves  — "linear" (default), "exponential" (ease-in), "bezier" (S-curve)
  Snap-back         — detects unexpected cursor movement (user input) and pauses lock
  Triggerbot        — auto-clicks when cursor is inside a detected bbox
  Recoil comp.      — steps through a configurable pattern while fire key is held
  Depth preference  — optionally prefer the target with the largest bbox (closest)

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
        self._recoil_cfg   = config.get("recoil", {})
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

        # Recoil state
        self._recoil_idx: int = 0
        self._recoil_last_step_t: float = 0.0

        # Triggerbot state
        self._tb_enabled: bool = self._tb_cfg.get("enabled", False)
        self._tb_last_click_t: float = 0.0

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

    def update_recoil(self, key: str, value) -> None:
        self._recoil_cfg[key] = value

    def update_triggerbot(self, key: str, value) -> None:
        self._tb_cfg[key] = value
        if key == "enabled":
            self._tb_enabled = bool(value)
        elif key == "hotkey":
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
        radius        = self._cfg.get("follow_radius", 150)
        target_class  = self._cfg.get("target_class")
        prefer_depth  = self._cfg.get("prefer_closest_depth", False)

        candidates: List[Tuple[float, dict]] = []
        for t in tracks:
            if target_class is not None and t.get("class_id") != target_class:
                continue
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
        if self._cfg.get("fps_mode", False):
            return pos
        prediction_ms = self._cfg.get("prediction_ms", 60)
        scale = prediction_ms / max(self.frame_time_ms, 1.0)
        px = max(0.0, min(float(self._screen_w), pos[0] + velocity[0] * scale))
        py = max(0.0, min(float(self._screen_h), pos[1] + velocity[1] * scale))
        return px, py

    # ------------------------------------------------------------------
    # Movement computation (smoothing curves)
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

        smoothing = self._cfg.get("smoothing", 0.12)
        base_factor = (1.0 - smoothing) * speed
        curve = self._cfg.get("smoothing_curve", "linear")

        if curve == "exponential":
            # Ease-in: scales down near target, full speed far away.
            # Prevents overshooting and small oscillations near the target.
            normalized = min(1.0, dist / 400.0)
            factor = base_factor * (0.3 + 0.7 * normalized)

        elif curve == "bezier":
            # Smoothstep S-curve: gentle at extremes, fastest at mid-distance.
            t = min(1.0, dist / 400.0)
            smooth_t = t * t * (3.0 - 2.0 * t)
            factor = base_factor * (0.1 + 0.9 * smooth_t)

        else:  # linear
            factor = base_factor

        move_x = dx * factor
        move_y = dy * factor

        # Guarantee at least 1px movement if there's meaningful delta
        if abs(dx) > 0.5 and abs(move_x) < 1.0:
            move_x = math.copysign(1.0, dx)
        if abs(dy) > 0.5 and abs(move_y) < 1.0:
            move_y = math.copysign(1.0, dy)

        return move_x, move_y

    # ------------------------------------------------------------------
    # Recoil compensation
    # ------------------------------------------------------------------

    def _is_fire_key_down(self) -> bool:
        fire_key = self._recoil_cfg.get("fire_key", "left")
        if fire_key == "left":
            return _is_vk_down(_VK_LBUTTON)
        elif fire_key == "right":
            return _is_vk_down(_VK_RBUTTON)
        else:
            try:
                import keyboard
                return keyboard.is_pressed(fire_key)
            except Exception:
                return False

    def _handle_recoil(self) -> None:
        if not self._recoil_cfg.get("enabled", False):
            return

        pattern  = self._recoil_cfg.get("pattern", [])
        step_ms  = self._recoil_cfg.get("step_ms", 80)
        reset_ms = self._recoil_cfg.get("reset_ms", 600)

        if not pattern:
            return

        now = time.perf_counter()
        if self._is_fire_key_down():
            if now - self._recoil_last_step_t >= step_ms / 1000.0:
                step = pattern[self._recoil_idx % len(pattern)]
                _send_mouse_move(int(step[0]), int(step[1]))
                self._recoil_idx += 1
                self._recoil_last_step_t = now
        else:
            if now - self._recoil_last_step_t > reset_ms / 1000.0:
                self._recoil_idx = 0

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

            # Recoil runs regardless of lock state
            self._handle_recoil()

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
                self._sleep_remaining(interval, t0)
                continue

            self.active_target_id = target["id"]

            fp        = self._compute_follow_point(target)
            predicted = self._predict_position(fp, target["velocity"])

            rx, ry = self._ref_point()
            dx = predicted[0] - rx
            dy = predicted[1] - ry
            dist = math.sqrt(dx * dx + dy * dy)

            deadzone = self._cfg.get("deadzone", 5) if fps_mode else 0
            if dist <= deadzone:
                self._sleep_remaining(interval, t0)
                continue

            move_x, move_y = self._compute_movement(dx, dy, dist, fps_mode)

            # Record expected cursor position before sending move (snapback detection)
            if not fps_mode:
                self._expected_cursor = (rx + int(move_x), ry + int(move_y))

            _send_mouse_move(int(move_x), int(move_y))

            self._sleep_remaining(interval, t0)