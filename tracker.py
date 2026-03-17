"""
tracker.py — ByteTrack multi-object tracker via boxmot.
Maintains persistent IDs, velocity, speed, angle, and positional trails.
"""

import logging
import math
import threading
from collections import deque
from typing import Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)


class Tracker:
    """
    Wraps boxmot ByteTrack and enriches track output with velocity, trail,
    and age metadata.
    """

    def __init__(self, config: dict):
        trk_cfg = config["tracking"]
        ovr_cfg = config.get("overlay", {})

        self._max_age: int = trk_cfg.get("max_age", 30)
        self._min_hits: int = trk_cfg.get("min_hits", 2)
        self._trail_length: int = ovr_cfg.get("trail_length", 20)

        self._tracker = None
        self._history: Dict[int, dict] = {}  # keyed by track ID
        self._current_tracks: List[dict] = []
        self._lock = threading.Lock()
        self._frame_count: int = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(self) -> None:
        from boxmot import ByteTrack

        self._tracker = ByteTrack(
            track_thresh=0.45,
            track_buffer=self._max_age,
            match_thresh=0.8,
            frame_rate=30,
        )
        logger.info("ByteTrack tracker initialised.")

    def update(self, detections: List[dict], frame: np.ndarray) -> List[dict]:
        """
        Run tracker update with new detections for the current frame.
        Returns enriched track list and stores it for get_tracks().
        """
        self._frame_count += 1

        if self._tracker is None:
            return []

        # Build detection array [[x1,y1,x2,y2,conf,cls], ...]
        if detections:
            dets = np.array([
                [
                    d["bbox"][0], d["bbox"][1],
                    d["bbox"][2], d["bbox"][3],
                    d["confidence"], d["class_id"],
                ]
                for d in detections
            ], dtype=np.float32)
        else:
            dets = np.empty((0, 6), dtype=np.float32)

        try:
            raw_tracks = self._tracker.update(dets, frame)
        except Exception as exc:
            logger.error("Tracker update failed: %s", exc, exc_info=True)
            raw_tracks = np.empty((0, 7), dtype=np.float32)

        active_ids = set()
        enriched = []

        for row in raw_tracks:
            # boxmot output: [x1, y1, x2, y2, track_id, conf, cls] or similar
            # Handle both 7-column and 8-column outputs from different boxmot versions
            if len(row) >= 7:
                x1, y1, x2, y2 = int(row[0]), int(row[1]), int(row[2]), int(row[3])
                track_id = int(row[4])
                conf     = float(row[5])
                class_id = int(row[6]) if len(row) >= 7 else 0
            else:
                continue

            cx = (x1 + x2) // 2
            cy = (y1 + y2) // 2
            active_ids.add(track_id)

            hist = self._history.get(track_id)
            if hist is None:
                hist = {
                    "trail": deque(maxlen=self._trail_length),
                    "age": 0,
                    "last_center": (cx, cy),
                    "velocity": (0.0, 0.0),
                    "frames_gone": 0,
                }
                self._history[track_id] = hist

            last_cx, last_cy = hist["last_center"]
            vx = float(cx - last_cx)
            vy = float(cy - last_cy)
            speed = math.sqrt(vx ** 2 + vy ** 2)
            angle = math.degrees(math.atan2(vy, vx)) if speed > 0 else 0.0

            hist["trail"].append((cx, cy))
            hist["last_center"] = (cx, cy)
            hist["velocity"] = (vx, vy)
            hist["age"] += 1
            hist["frames_gone"] = 0

            enriched.append({
                "id":         track_id,
                "bbox":       [x1, y1, x2, y2],
                "center":     (cx, cy),
                "velocity":   (vx, vy),
                "speed":      speed,
                "angle":      angle,
                "confidence": conf,
                "class_id":   class_id,
                "age":        hist["age"],
                "trail":      list(hist["trail"]),
            })

        # Age-out tracks that are no longer active
        stale = []
        for tid, hist in self._history.items():
            if tid not in active_ids:
                hist["frames_gone"] += 1
                if hist["frames_gone"] > self._max_age:
                    stale.append(tid)
        for tid in stale:
            del self._history[tid]

        with self._lock:
            self._current_tracks = enriched

        return enriched

    def get_tracks(self) -> List[dict]:
        with self._lock:
            return list(self._current_tracks)

    def reset(self) -> None:
        """Reset tracker state (e.g. when window changes)."""
        if self._tracker is not None:
            try:
                self.load()
            except Exception:
                pass
        self._history.clear()
        with self._lock:
            self._current_tracks = []
