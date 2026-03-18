            """
detector.py — YOLOv8 inference wrapper with optional background subtraction
and automatic confidence tuning.

Auto-confidence mode: tracks a rolling window of detection counts and nudges
the confidence threshold up/down to stay near a configured target count,
bounded by auto_conf_min / auto_conf_max.
"""

import logging
import threading
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from typing import List, Optional

import cv2
import numpy as np

logger = logging.getLogger(__name__)

_AUTO_CONF_WINDOW = 30   # frames in rolling detection-count history
_AUTO_CONF_STEP   = 0.005  # confidence nudge per adjustment


class Detector:
    """
    Wraps YOLOv8 for async person/humanoid detection.
    Background subtraction pre-filters static pixels to reduce false positives.
    Auto-confidence tuning adjusts the threshold toward a target detection count.
    """

    def __init__(self, config: dict):
        det_cfg = config["detection"]
        self._model_path: str = det_cfg.get("model", "models/yolov8n.pt")
        self._confidence: float = det_cfg.get("confidence", 0.45)
        self._nms_iou: float = det_cfg.get("nms_iou", 0.45)
        self._use_bg_sub: bool = det_cfg.get("use_background_subtraction", True)
        self._device: str = det_cfg.get("device", "cuda")
        self._detect_all_classes: bool = det_cfg.get("detect_all_classes", False)

        # Auto-confidence
        self._auto_conf: bool = det_cfg.get("auto_confidence", False)
        self._auto_conf_min: float = det_cfg.get("auto_conf_min", 0.08)
        self._auto_conf_max: float = det_cfg.get("auto_conf_max", 0.60)
        self._auto_conf_target: int = det_cfg.get("auto_conf_target", 3)
        self._conf_history: deque = deque(maxlen=_AUTO_CONF_WINDOW)

        self._model = None
        self._bg_subtractor = None
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="InferenceWorker")
        self._pending_future: Optional[Future] = None
        self._last_detections: Optional[List[dict]] = None
        self._lock = threading.Lock()
        self._inference_fps: float = 0.0
        self._fps_lock = threading.Lock()
        self._reload_requested: bool = False

        self._morph_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(self) -> None:
        """Load the YOLO model. Call once from inference thread before loop."""
        from ultralytics import YOLO
        import torch

        device = self._device
        if device == "cuda" and not torch.cuda.is_available():
            logger.warning("CUDA requested but not available, falling back to CPU.")
            device = "cpu"
        self._device = device

        logger.info("Loading YOLO model '%s' on device '%s'.", self._model_path, device)
        self._model = YOLO(self._model_path)
        self._model.to(device)

        if self._use_bg_sub:
            self._bg_subtractor = cv2.createBackgroundSubtractorMOG2(
                history=200, varThreshold=50, detectShadows=False
            )
        logger.info("Detector ready.")

    def set_confidence(self, value: float) -> None:
        self._confidence = max(0.05, min(0.95, float(value)))

    def set_auto_confidence(self, enabled: bool) -> None:
        self._auto_conf = enabled
        if not enabled:
            self._conf_history.clear()

    def set_auto_conf_params(self, min_val: float, max_val: float, target: int) -> None:
        self._auto_conf_min = min_val
        self._auto_conf_max = max_val
        self._auto_conf_target = max(1, target)

    def reload_model(self, new_path: str) -> None:
        logger.info("Model swap requested: %s → %s", self._model_path, new_path)
        self._model_path = new_path
        self._reload_requested = True

    def set_detect_all_classes(self, value: bool) -> None:
        self._detect_all_classes = value

    @property
    def current_confidence(self) -> float:
        return self._confidence

    def submit_frame(self, frame: np.ndarray) -> None:
        """Submit a frame for async inference. Drops frame if previous is still running."""
        with self._lock:
            if self._pending_future is not None and not self._pending_future.done():
                return
            if self._pending_future is not None:
                try:
                    self._last_detections = self._pending_future.result()
                except Exception as exc:
                    logger.error("Inference error: %s", exc, exc_info=True)
                    self._last_detections = []
            self._pending_future = self._executor.submit(self._infer, frame.copy())

    def get_detections(self) -> Optional[List[dict]]:
        with self._lock:
            result = self._last_detections
            self._last_detections = None
        return result

    @property
    def inference_fps(self) -> float:
        with self._fps_lock:
            return self._inference_fps

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _apply_bg_subtraction(self, frame: np.ndarray) -> np.ndarray:
        fg_mask = self._bg_subtractor.apply(frame)
        fg_mask = cv2.dilate(fg_mask, self._morph_kernel, iterations=2)
        result = frame.copy()
        result[fg_mask == 0] = 0
        return result

    def _adjust_confidence(self, detection_count: int) -> None:
        """Nudge confidence toward the target detection count."""
        self._conf_history.append(detection_count)
        if len(self._conf_history) < _AUTO_CONF_WINDOW // 2:
            return  # Not enough history yet

        avg = sum(self._conf_history) / len(self._conf_history)
        if avg > self._auto_conf_target + 1:
            # Too many detections → raise confidence (be more selective)
            self._confidence = min(
                self._auto_conf_max,
                self._confidence + _AUTO_CONF_STEP,
            )
        elif avg < self._auto_conf_target - 0.5:
            # Too few detections → lower confidence (be more permissive)
            self._confidence = max(
                self._auto_conf_min,
                self._confidence - _AUTO_CONF_STEP,
            )

    def _infer(self, frame: np.ndarray) -> List[dict]:
        import time

        t0 = time.perf_counter()

        # Hot-swap model if requested
        if self._reload_requested:
            self._reload_requested = False
            try:
                from ultralytics import YOLO
                logger.info("Loading model '%s'...", self._model_path)
                self._model = YOLO(self._model_path)
                self._model.to(self._device)
                logger.info("Model loaded: %s", self._model_path)
            except Exception as exc:
                logger.error("Model reload failed: %s", exc, exc_info=True)
                self._model = None

        if self._model is None:
            return []

        infer_frame = frame
        if self._use_bg_sub and self._bg_subtractor is not None:
            infer_frame = self._apply_bg_subtraction(frame)

        cls_filter = None if self._detect_all_classes else [0]
        results = self._model(
            infer_frame,
            conf=self._confidence,
            iou=self._nms_iou,
            verbose=False,
            classes=cls_filter,
        )

        detections = []
        for result in results:
            if result.boxes is None:
                continue
            for box in result.boxes:
                x1, y1, x2, y2 = box.xyxy[0].tolist()
                conf = float(box.conf[0])
                cls_id = int(box.cls[0])
                detections.append({
                    "bbox": [int(x1), int(y1), int(x2), int(y2)],
                    "confidence": conf,
                    "class_id": cls_id,
                })

        elapsed = time.perf_counter() - t0
        if elapsed > 0:
            with self._fps_lock:
                self._inference_fps = 1.0 / elapsed

        if self._auto_conf:
            self._adjust_confidence(len(detections))

        return detections