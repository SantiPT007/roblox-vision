"""
detector.py — YOLOv8 inference wrapper with optional background subtraction.
Runs inference asynchronously via a single-worker ThreadPoolExecutor.
"""

import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from typing import List, Optional

import cv2
import numpy as np

logger = logging.getLogger(__name__)


class Detector:
    """
    Wraps YOLOv8 for async person/humanoid detection.
    Background subtraction pre-filters static pixels to reduce false positives.
    """

    def __init__(self, config: dict):
        det_cfg = config["detection"]
        self._model_path: str = det_cfg.get("model", "models/yolov8n.pt")
        self._confidence: float = det_cfg.get("confidence", 0.45)
        self._nms_iou: float = det_cfg.get("nms_iou", 0.45)
        self._use_bg_sub: bool = det_cfg.get("use_background_subtraction", True)
        self._device: str = det_cfg.get("device", "cuda")

        self._detect_all_classes: bool = det_cfg.get("detect_all_classes", False)

        self._model = None
        self._bg_subtractor = None
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="InferenceWorker")
        self._pending_future: Optional[Future] = None
        self._last_detections: Optional[List[dict]] = None  # completed result waiting to be consumed
        self._lock = threading.Lock()
        self._inference_fps: float = 0.0
        self._fps_lock = threading.Lock()
        self._reload_requested: bool = False

        # Morphological kernel for bg-sub post-processing
        self._morph_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(self) -> None:
        """Load the YOLO model. Call once from inference thread before loop."""
        from ultralytics import YOLO
        import torch

        # Fallback to CPU if CUDA unavailable
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
        """Live-update confidence threshold from GUI."""
        self._confidence = max(0.1, min(0.95, float(value)))

    def reload_model(self, new_path: str) -> None:
        """Hot-swap model. Actual reload happens on the next inference frame."""
        logger.info("Model swap requested: %s → %s", self._model_path, new_path)
        self._model_path = new_path
        self._reload_requested = True

    def set_detect_all_classes(self, value: bool) -> None:
        self._detect_all_classes = value

    def submit_frame(self, frame: np.ndarray) -> None:
        """Submit a frame for async inference. Drops frame if previous is still running."""
        with self._lock:
            if self._pending_future is not None and not self._pending_future.done():
                return  # Still in-flight; drop this frame
            # Harvest result from the just-completed future before replacing it
            if self._pending_future is not None:
                try:
                    self._last_detections = self._pending_future.result()
                except Exception as exc:
                    logger.error("Inference error: %s", exc, exc_info=True)
                    self._last_detections = []
            self._pending_future = self._executor.submit(self._infer, frame.copy())

    def get_detections(self) -> Optional[List[dict]]:
        """Returns the most recently completed detection result, or None if nothing new."""
        with self._lock:
            result = self._last_detections
            self._last_detections = None  # consume
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
        """Zero-out static background pixels before passing to YOLO."""
        fg_mask = self._bg_subtractor.apply(frame)
        fg_mask = cv2.dilate(fg_mask, self._morph_kernel, iterations=2)
        # Mask out background (where fg_mask == 0)
        result = frame.copy()
        result[fg_mask == 0] = 0
        return result

    def _infer(self, frame: np.ndarray) -> List[dict]:
        import time

        t0 = time.perf_counter()

        # Hot-swap model if requested (runs on executor thread, safe to load here)
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

        # classes=None detects all classes (for custom models with their own class set)
        # classes=[0] restricts to COCO person class (for standard yolov8 weights)
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

        return detections
