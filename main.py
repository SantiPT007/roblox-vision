"""
main.py — Entry point for the Character Tracker.

Thread layout:
  - Main thread    : Qt event loop
  - CaptureThread  : dxcam loop → frame_queue
  - Inference loop : frame_queue → YOLO + ByteTrack → current_tracks
  - CursorThread   : reads current_tracks at high frequency, sends movement

Usage:
    python main.py [--no-overlay] [--config path/to/config.yaml]
"""

import argparse
import ctypes
import logging
import os
import queue
import sys
import threading
import time
from typing import List, Optional

import yaml

# Import detector and tracker at module level so onnxruntime's DLLs are
# loaded in the main thread. Loading them inside a worker thread can cause
# DLL initialisation failures on Windows (DllMain restrictions + PATH issues).
from detector import Detector
from tracker import Tracker

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(threadName)s] %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("main")


# ---------------------------------------------------------------------------
# Precision sleep helpers
# ---------------------------------------------------------------------------

def _sleep_precise(seconds: float) -> None:
    """Sleep with better precision for very short intervals on Windows."""
    if seconds <= 0:
        return

    if seconds >= 0.002:
        time.sleep(seconds)
        return

    # Reduce CPU spin on sub-2ms waits:
    # 1) cooperatively yield while remaining time is still relatively large
    # 2) only busy-wait in a very small tail window for precision
    deadline = time.perf_counter() + seconds
    spin_threshold = 0.0002  # 0.2ms spin window

    while True:
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            break

        if remaining > spin_threshold:
            sleep_for = max(0.0, remaining - spin_threshold)
            if sleep_for >= 0.001:
                time.sleep(sleep_for)
            else:
                time.sleep(0)


def _set_windows_timer_resolution_1ms(enable: bool) -> bool:
    """Enable/disable 1ms timer resolution on Windows. Returns success status."""
    if os.name != 'nt':
        return False
    try:
        winmm = ctypes.WinDLL('winmm')
        if enable:
            return winmm.timeBeginPeriod(1) == 0
        return winmm.timeEndPeriod(1) == 0
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------

_DEFAULT_CONFIG = {
    "capture": {
        "target_window": "Roblox",
        "fps_cap": 60,
        "region": None,
    },
    "detection": {
        "model": "models/Roblox.onnx",
        "confidence": 0.35,
        "nms_iou": 0.35,
    },
    "tracking": {
        "max_age": 30,
        "min_hits": 2,
    },
    "hotkeys": {
        "detection_toggle": "F6",
        "lock_toggle": "F7",
        "lock_hold": "F4",
    },
    "cursor_follow": {
        "enabled": False,
        "fps_mode": False,
        "smoothing": 0.12,
        "speed": 1.0,
        "follow_radius": 150,
        "follow_point": "chest",
        "prediction_ms": 60,
        "deadzone": 5,
        "prefer_closest_depth": False,
        "head_height_ratio": 0.15,
        "aim_y_reduce": False,
        "aim_y_reduce_delay": 0.6,
        "snapback_threshold": 15,
        "snapback_pause_ms": 200,
        "pid": {"kp": 0.4, "ki": 0.0, "kd": 0.08},
        "tracker": {"smoothing_factor": 0.5, "stop_threshold": 20.0, "position_deadzone": 4.0},
    },
    "triggerbot": {
        "enabled": False,
        "hotkey": "F5",
        "delay_min_ms": 50,
        "delay_max_ms": 120,
        "padding": 5,
    },
    "overlay": {
        "enabled": True,
        "show_boxes": True,
        "show_trails": True,
        "show_velocity": True,
        "show_radius_circle": True,
        "show_minimap": True,
        "show_direction_cone": True,
        "trail_length": 20,
        "active_color": [0, 255, 80],
        "inactive_color": [0, 200, 255],
        "box_thickness": 1,
        "font_scale": 0.5,
    },
    "perf_dashboard": {
        "enabled": False,
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def save_config(path: str, config: dict) -> None:
    """Write the live config back to disk, stripping the read-only 'presets' key."""
    import copy
    to_save = copy.deepcopy(config)
    to_save.pop("presets", None)
    with open(path, "w") as f:
        yaml.dump(to_save, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
    logger.info("Config saved to '%s'.", path)


def load_config(path: str) -> dict:
    config = dict(_DEFAULT_CONFIG)
    if os.path.exists(path):
        with open(path, "r") as f:
            user_cfg = yaml.safe_load(f) or {}
        config = _deep_merge(config, user_cfg)
        logger.info("Loaded config from '%s'.", path)
    else:
        logger.warning("Config file '%s' not found, using defaults.", path)
    return config


# ---------------------------------------------------------------------------
# Shared state container
# ---------------------------------------------------------------------------

class SharedState:
    def __init__(self):
        self._lock = threading.Lock()
        self._tracks: List[dict] = []
        self.capture_fps: float = 0.0
        self.inference_fps: float = 0.0
        self.follow_active: bool = False
        self.active_target_id: Optional[int] = None
        self.detection_active: bool = True
        self.stop_event = threading.Event()
        self.detector = None
        self.tracker = None

    def set_tracks(self, tracks: List[dict]) -> None:
        with self._lock:
            self._tracks = tracks

    def get_tracks(self) -> List[dict]:
        with self._lock:
            return list(self._tracks)

    def get_cursor_state(self):
        return self.follow_active, self.active_target_id, self.detection_active

    def get_status(self) -> dict:
        tracks = self.get_tracks()
        return {
            "fps_capture": self.capture_fps,
            "fps_inference": self.inference_fps,
            "track_count": len(tracks),
            "target_id": self.active_target_id,
            "follow_active": self.follow_active,
            "detection_active": self.detection_active,
        }


# ---------------------------------------------------------------------------
# Inference thread function
# ---------------------------------------------------------------------------

def inference_loop(
    config: dict,
    frame_queue: queue.Queue,
    state: SharedState,
    stop_event: threading.Event,
) -> None:
    detector = Detector(config)
    tracker  = Tracker(config)

    try:
        detector.load()
        tracker.load()
    except Exception as exc:
        logger.error("Failed to load detector/tracker: %s", exc, exc_info=True)
        return

    state.detector = detector
    state.tracker  = tracker

    last_frame: Optional[object] = None
    frame_count = 0
    diag_frames = 0
    diag_detections = 0
    diag_last = time.time()

    while not stop_event.is_set():
        if not state.detection_active:
            state.set_tracks([])
            time.sleep(0.05)
            continue

        frame = None
        while True:
            try:
                frame = frame_queue.get_nowait()
            except queue.Empty:
                break

        if frame is None:
            if last_frame is not None:
                frame = last_frame
            else:
                _sleep_precise(0.005)
                continue

        last_frame = frame
        frame_count += 1

        detector.submit_frame(frame)
        detections = detector.get_detections()

        if detections is not None:
            diag_frames += 1
            diag_detections += len(detections)
            tracks = tracker.update(detections, frame)
            state.set_tracks(tracks)
            state.inference_fps = detector.inference_fps

        now = time.time()
        if now - diag_last >= 5.0:
            logger.info(
                "[DIAG] inf_frames=%d  total_detections=%d  tracks=%d  inf_fps=%.1f  conf=%.2f",
                diag_frames, diag_detections, len(state.get_tracks()),
                detector.inference_fps, detector.current_confidence,
            )
            diag_frames = 0
            diag_detections = 0
            diag_last = now

        _sleep_precise(0.001)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Character Tracker")
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    parser.add_argument("--no-overlay", action="store_true", help="Run without overlay")
    args = parser.parse_args()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(script_dir)

    config = load_config(args.config)

    if args.no_overlay:
        config["overlay"]["enabled"] = False

    state = SharedState()

    from PyQt6.QtWidgets import QApplication
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    screen = app.primaryScreen().geometry()
    screen_size = (screen.width(), screen.height())

    # --- Capture ---
    from capture import CaptureThread
    frame_queue: queue.Queue = queue.Queue(maxsize=4)

    capture = CaptureThread(config)
    capture.start()

    # Enable 1ms timer resolution for the lifetime of the app
    _high_res_timer = _set_windows_timer_resolution_1ms(True)

    def _bridge_frames():
        while not state.stop_event.is_set():
            frame = capture.get_frame()
            if frame is not None:
                if frame_queue.full():
                    try:
                        frame_queue.get_nowait()
                    except queue.Empty:
                        pass
                try:
                    frame_queue.put_nowait(frame)
                except queue.Full:
                    pass
            else:
                _sleep_precise(0.001)
            state.capture_fps = capture.capture_fps

    bridge_thread = threading.Thread(
        target=_bridge_frames, name="FrameBridge", daemon=True
    )
    bridge_thread.start()

    # --- Inference thread ---
    inf_thread = threading.Thread(
        target=inference_loop,
        args=(config, frame_queue, state, state.stop_event),
        name="InferenceThread",
        daemon=True,
    )
    inf_thread.start()

    # --- Cursor follow ---
    from cursor import CursorFollower
    cursor = CursorFollower(
        config=config,
        get_tracks_fn=state.get_tracks,
        screen_size=screen_size,
    )
    cursor.start()

    # --- F6 detection toggle ---
    def _setup_detection_hotkey():
        try:
            import keyboard
            det_key = config.get("hotkeys", {}).get("detection_toggle", "F6")
            def _toggle_detection():
                state.detection_active = not state.detection_active
                logger.info("Detection %s", "ON" if state.detection_active else "OFF")
            keyboard.add_hotkey(det_key, _toggle_detection)
            logger.info("Detection toggle hotkey: %s", det_key)
        except Exception as exc:
            logger.warning("Could not install detection hotkey: %s", exc)
    _setup_detection_hotkey()

    # Sync cursor state to shared state
    def _sync_cursor_state():
        while not state.stop_event.is_set():
            state.follow_active    = cursor.follow_active
            state.active_target_id = cursor.active_target_id
            time.sleep(0.05)

    cursor_sync = threading.Thread(
        target=_sync_cursor_state, name="CursorSync", daemon=True
    )
    cursor_sync.start()

    # --- Overlay ---
    overlay = None
    if config.get("overlay", {}).get("enabled", True):
        from overlay import Overlay
        overlay = Overlay(
            config=config,
            get_tracks_fn=state.get_tracks,
            get_cursor_state_fn=state.get_cursor_state,
        )
        from PyQt6.QtCore import QTimer

        def _update_overlay_fps():
            if overlay:
                overlay.capture_fps   = state.capture_fps
                overlay.inference_fps = state.inference_fps

        fps_timer = QTimer()
        fps_timer.setInterval(200)
        fps_timer.timeout.connect(_update_overlay_fps)
        fps_timer.start()

    # --- Performance dashboard ---
    perf_dashboard = None

    def _toggle_perf_dashboard():
        nonlocal perf_dashboard
        if perf_dashboard is None:
            from perf_monitor import PerfDashboard
            perf_dashboard = PerfDashboard(get_status_fn=state.get_status)
            perf_dashboard.show()
            config["perf_dashboard"]["enabled"] = True
            logger.info("Performance dashboard opened.")
        else:
            if perf_dashboard.isVisible():
                perf_dashboard.hide()
                config["perf_dashboard"]["enabled"] = False
            else:
                perf_dashboard.show()
                config["perf_dashboard"]["enabled"] = True

    if config.get("perf_dashboard", {}).get("enabled", False):
        _toggle_perf_dashboard()

    # --- Config change handler ---
    def on_config_change(section: str, key: str, value):
        config[section][key] = value
        logger.debug("Config updated: [%s] %s = %r", section, key, value)

        if section == "detection":
            det = state.detector
            if det is not None:
                if key == "model":
                    det.reload_model(value)
                elif key == "confidence":
                    det.set_confidence(value)

        elif section == "cursor_follow":
            cursor.update_config(key, value)
            if key == "enabled":
                cursor.set_enabled(bool(value))

        elif section == "triggerbot":
            cursor.update_triggerbot(key, value)

        elif section == "hotkeys":
            cursor.update_config(key, value)

        elif section == "overlay" and overlay:
            overlay.update_config(key, value)

        elif section == "perf_dashboard":
            if key == "enabled":
                # Handled by the toggle function
                pass

    # --- Control panel ---
    config_path = os.path.abspath(args.config)
    from gui import ControlPanel
    panel = ControlPanel(
        config=config,
        on_config_change=on_config_change,
        get_status_fn=state.get_status,
        save_config_fn=lambda: save_config(config_path, config),
        toggle_perf_dashboard_fn=_toggle_perf_dashboard,
    )
    panel.show()

    # --- Shutdown ---
    def _cleanup():
        logger.info("Shutting down...")
        state.stop_event.set()
        cursor.stop()
        capture.stop()
        if _high_res_timer:
            _set_windows_timer_resolution_1ms(False)

    app.aboutToQuit.connect(_cleanup)

    hk = config.get("hotkeys", {})
    tb = config.get("triggerbot", {})
    logger.info(
        "Character Tracker running.  %s=detection  %s=lock-toggle  %s=lock-hold  %s=triggerbot",
        hk.get("detection_toggle", "F6"),
        hk.get("lock_toggle",      "F7"),
        hk.get("lock_hold",        "F4"),
        tb.get("hotkey",           "F5"),
    )
    sys.exit(app.exec())


if __name__ == "__main__":
    main()