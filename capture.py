"""
capture.py — Screen capture thread using dxcam DXGI Desktop Duplication API.
Targets a specific window by name, captures at configured FPS into a queue.
"""

import logging
import queue
import threading
import time
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


class CaptureThread:
    """
    Captures frames from the configured target window using dxcam.
    Runs in a dedicated daemon thread. Frames are BGR uint8 NumPy arrays.
    """

    def __init__(self, config: dict):
        self._cfg = config["capture"]
        self._frame_queue: queue.Queue = queue.Queue(maxsize=2)
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._camera = None
        self._capture_fps: float = 0.0
        self._fps_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="CaptureThread",
            daemon=True,
        )
        self._thread.start()
        logger.info("Capture thread started.")

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=3.0)
        if self._camera is not None:
            try:
                self._camera.stop()
            except Exception:
                pass
        logger.info("Capture thread stopped.")

    def get_frame(self) -> Optional[np.ndarray]:
        """Return the most recent frame, or None if none available."""
        try:
            return self._frame_queue.get_nowait()
        except queue.Empty:
            return None

    @property
    def capture_fps(self) -> float:
        with self._fps_lock:
            return self._capture_fps

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _get_window_rect(self) -> Optional[tuple]:
        """Resolve the target window rect. Returns (left, top, right, bottom) or None."""
        import win32gui
        import win32con

        target = self._cfg.get("target_window", "")
        region_cfg = self._cfg.get("region", None)

        if region_cfg:
            x, y, w, h = region_cfg
            return (x, y, x + w, y + h)

        if not target:
            # No window configured — capture full primary screen
            import ctypes
            w = ctypes.windll.user32.GetSystemMetrics(0)
            h = ctypes.windll.user32.GetSystemMetrics(1)
            return (0, 0, w, h)

        # Try exact match first, then case-insensitive partial match
        hwnd = win32gui.FindWindow(None, target)
        if not hwnd:
            target_lower = target.lower()
            match = [None]
            def _enum(h, _):
                if match[0]:
                    return
                if win32gui.IsWindowVisible(h):
                    t = win32gui.GetWindowText(h)
                    if t and target_lower in t.lower():
                        match[0] = h
            win32gui.EnumWindows(_enum, None)
            hwnd = match[0]

        if not hwnd:
            logger.warning("Window '%s' not found. Check the target window name in settings.", target)
            return None

        logger.debug("Found window '%s' (hwnd=%s).", win32gui.GetWindowText(hwnd), hwnd)

        if win32gui.IsIconic(hwnd):
            logger.warning("Window '%s' is minimized.", target)
            return None

        rect = win32gui.GetWindowRect(hwnd)
        if rect[2] - rect[0] <= 0 or rect[3] - rect[1] <= 0:
            logger.warning("Window '%s' has zero-size rect.", target)
            return None

        return rect

    def _run(self) -> None:
        import dxcam

        fps_cap = self._cfg.get("fps_cap", 60)

        frame_times = []
        fps_window = 30  # rolling average over N frames

        while not self._stop_event.is_set():
            rect = self._get_window_rect()
            if rect is None:
                time.sleep(0.5)
                continue

            # (Re-)create camera if region changed or first time
            try:
                if self._camera is not None:
                    try:
                        self._camera.stop()
                    except Exception:
                        pass

                self._camera = dxcam.create(
                    region=rect,
                    output_color="BGR",
                )
                self._camera.start(target_fps=fps_cap, video_mode=True)
                logger.info("dxcam started, region=%s, fps=%d", rect, fps_cap)

                last_rect_check = time.perf_counter()
                while not self._stop_event.is_set():
                    t0 = time.perf_counter()

                    frame = self._camera.get_latest_frame()
                    if frame is None:
                        continue

                    # Discard oldest frame to keep queue fresh
                    if self._frame_queue.full():
                        try:
                            self._frame_queue.get_nowait()
                        except queue.Empty:
                            pass

                    self._frame_queue.put_nowait(frame)

                    # Rolling FPS calculation
                    frame_times.append(t0)
                    if len(frame_times) > fps_window:
                        frame_times.pop(0)
                    if len(frame_times) >= 2:
                        elapsed = frame_times[-1] - frame_times[0]
                        if elapsed > 0:
                            with self._fps_lock:
                                self._capture_fps = (len(frame_times) - 1) / elapsed

                    # Check window rect every 2s, not every frame
                    if t0 - last_rect_check >= 2.0:
                        last_rect_check = t0
                        new_rect = self._get_window_rect()
                        if new_rect != rect:
                            logger.info("Window rect changed, reinitialising camera.")
                            break

            except Exception as exc:
                logger.error("Capture error: %s", exc, exc_info=True)
                if self._camera is not None:
                    try:
                        self._camera.stop()
                    except Exception:
                        pass
                    self._camera = None
                time.sleep(0.5)
