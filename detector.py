"""
detector.py — ONNX-based inference wrapper.
"""

import logging
import os
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from typing import List, Optional

import cv2
import numpy as np

# Register the onnxruntime DLL directory before importing.
# When the app runs elevated via UAC, Windows strips the user PATH, which
# prevents onnxruntime's bundled DLLs from being found. os.add_dll_directory
# fixes this by registering the path directly with the DLL loader.
try:
    import importlib.util as _ilu
    _spec = _ilu.find_spec("onnxruntime")
    if _spec and _spec.origin:
        _ort_capi = os.path.join(os.path.dirname(_spec.origin), "capi")
        if os.path.isdir(_ort_capi):
            os.add_dll_directory(_ort_capi)
except Exception:
    pass

import onnxruntime as ort

logger = logging.getLogger(__name__)


def _build_session_options() -> Optional[ort.SessionOptions]:
    """Create optimised ONNX session options."""
    try:
        session_options = ort.SessionOptions()
        session_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        session_options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        session_options.enable_mem_pattern = True
        session_options.enable_cpu_mem_arena = True
        try:
            session_options.intra_op_num_threads = 1
            session_options.inter_op_num_threads = 1
        except Exception as e:
            logger.warning("ONNX thread param failed: %s", e)
        try:
            session_options.add_session_config_entry("session.intra_op.allow_spinning", "0")
            session_options.add_session_config_entry("session.inter_op.allow_spinning", "0")
        except Exception as e:
            logger.warning("ONNX allow_spinning failed: %s", e)
        return session_options
    except Exception as e:
        logger.error("ONNX session options failed: %s", e)
        return None


def _preprocess_image(image: np.ndarray, model_input_size: int) -> np.ndarray:
    """Preprocess image for ONNX model."""
    # BGRA -> BGR
    if image.ndim == 3 and image.shape[2] == 4:
        image = cv2.cvtColor(image, cv2.COLOR_BGRA2BGR)

    # Resize with INTER_NEAREST for speed
    if image.shape[0] != model_input_size or image.shape[1] != model_input_size:
        image = cv2.resize(image, (model_input_size, model_input_size),
                           interpolation=cv2.INTER_NEAREST)

    blob = cv2.dnn.blobFromImage(
        image,
        scalefactor=1.0 / 255.0,
        size=(model_input_size, model_input_size),
        swapRB=True,
        crop=False,
    )
    return np.ascontiguousarray(blob, dtype=np.float32)


def _postprocess_outputs(outputs, original_width: int, original_height: int,
                          model_input_size: int, min_confidence: float,
                          offset_x: int = 0, offset_y: int = 0):
    """Post-process ONNX model outputs."""
    predictions = outputs[0][0].T

    # Vectorised confidence filter
    conf_mask = predictions[:, 4] >= min_confidence
    filtered_predictions = predictions[conf_mask]

    if len(filtered_predictions) == 0:
        return [], []

    scale_x = original_width / model_input_size
    scale_y = original_height / model_input_size

    cx = filtered_predictions[:, 0]
    cy = filtered_predictions[:, 1]
    w  = filtered_predictions[:, 2]
    h  = filtered_predictions[:, 3]

    x1 = (cx - w / 2) * scale_x + offset_x
    y1 = (cy - h / 2) * scale_y + offset_y
    x2 = (cx + w / 2) * scale_x + offset_x
    y2 = (cy + h / 2) * scale_y + offset_y

    boxes = np.stack([x1, y1, x2, y2], axis=1).tolist()
    confidences = filtered_predictions[:, 4].tolist()

    return boxes, confidences


def _non_max_suppression(boxes, confidences, iou_threshold: float = 0.35):
    """Non-maximum suppression."""
    if len(boxes) == 0:
        return [], []

    boxes_arr = np.array(boxes)
    confidences_arr = np.array(confidences)
    areas = (boxes_arr[:, 2] - boxes_arr[:, 0]) * (boxes_arr[:, 3] - boxes_arr[:, 1])
    order = confidences_arr.argsort()[::-1]

    keep = []
    while len(order) > 0:
        i = order[0]
        keep.append(i)
        if len(order) == 1:
            break

        xx1 = np.maximum(boxes_arr[i, 0], boxes_arr[order[1:], 0])
        yy1 = np.maximum(boxes_arr[i, 1], boxes_arr[order[1:], 1])
        xx2 = np.minimum(boxes_arr[i, 2], boxes_arr[order[1:], 2])
        yy2 = np.minimum(boxes_arr[i, 3], boxes_arr[order[1:], 3])

        w = np.maximum(0, xx2 - xx1)
        h = np.maximum(0, yy2 - yy1)
        intersection = w * h
        union = areas[i] + areas[order[1:]] - intersection
        iou = intersection / np.maximum(union, 1e-6)

        order = order[1:][iou <= iou_threshold]

    return boxes_arr[keep].tolist(), confidences_arr[keep].tolist()


class Detector:
    """
    Wraps an ONNX model for async humanoid/character detection.
    Uses DmlExecutionProvider (DirectML — any DirectX 12 GPU) with CPU fallback.
    """

    def __init__(self, config: dict):
        det_cfg = config["detection"]
        self._model_path: str = det_cfg.get("model", "models/Roblox.onnx")
        self._confidence: float = det_cfg.get("confidence", 0.35)
        self._nms_iou: float = det_cfg.get("nms_iou", 0.35)

        self._session: Optional[ort.InferenceSession] = None
        self._input_name: Optional[str] = None
        self._model_input_size: int = 640

        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="InferenceWorker")
        self._pending_future: Optional[Future] = None
        self._last_detections: Optional[List[dict]] = None
        self._lock = threading.Lock()
        self._inference_fps: float = 0.0
        self._fps_lock = threading.Lock()
        self._reload_requested: bool = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(self) -> None:
        """Load the ONNX model. Call once from inference thread before loop."""
        logger.info("Loading ONNX model '%s'.", self._model_path)
        session_options = _build_session_options()
        providers = ['DmlExecutionProvider', 'CPUExecutionProvider']
        if session_options is not None:
            self._session = ort.InferenceSession(
                self._model_path, providers=providers, sess_opts=session_options
            )
        else:
            self._session = ort.InferenceSession(self._model_path, providers=providers)
        self._input_name = self._session.get_inputs()[0].name
        self._model_input_size = self._session.get_inputs()[0].shape[2]
        logger.info(
            "Detector ready. Provider=%s  input_size=%d",
            self._session.get_providers(),
            self._model_input_size,
        )

    def set_confidence(self, value: float) -> None:
        self._confidence = max(0.05, min(0.95, float(value)))

    def reload_model(self, new_path: str) -> None:
        logger.info("Model swap requested: %s → %s", self._model_path, new_path)
        self._model_path = new_path
        self._reload_requested = True

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

    def _infer(self, frame: np.ndarray) -> List[dict]:
        import time

        t0 = time.perf_counter()

        # Hot-swap model if requested
        if self._reload_requested:
            self._reload_requested = False
            try:
                logger.info("Hot-swapping model '%s'...", self._model_path)
                session_options = _build_session_options()
                providers = ['DmlExecutionProvider', 'CPUExecutionProvider']
                if session_options is not None:
                    self._session = ort.InferenceSession(
                        self._model_path, providers=providers, sess_opts=session_options
                    )
                else:
                    self._session = ort.InferenceSession(self._model_path, providers=providers)
                self._input_name = self._session.get_inputs()[0].name
                self._model_input_size = self._session.get_inputs()[0].shape[2]
                logger.info("Model loaded: %s", self._model_path)
            except Exception as exc:
                logger.error("Model reload failed: %s", exc, exc_info=True)
                self._session = None

        if self._session is None:
            return []

        input_tensor = _preprocess_image(frame, self._model_input_size)
        outputs = self._session.run(None, {self._input_name: input_tensor})

        h, w = frame.shape[:2]
        boxes, confidences = _postprocess_outputs(
            outputs, w, h, self._model_input_size, self._confidence
        )
        boxes, confidences = _non_max_suppression(boxes, confidences, self._nms_iou)

        detections = []
        for box, conf in zip(boxes, confidences):
            detections.append({
                "bbox": [int(box[0]), int(box[1]), int(box[2]), int(box[3])],
                "confidence": float(conf),
                "class_id": 0,  # Roblox.onnx is single-class
            })

        elapsed = time.perf_counter() - t0
        if elapsed > 0:
            with self._fps_lock:
                self._inference_fps = 1.0 / elapsed

        return detections
