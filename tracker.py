"""
tracker.py — ByteTrack multi-object tracker via boxmot.
Maintains persistent IDs, velocity, speed, angle, positional trails,
depth score (bbox area heuristic), and team color (pixel sampling above bbox).
"""

import logging
import math
import threading
from collections import deque
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# Hue ranges (OpenCV: 0-179) → team color name
_HUE_BANDS = [
    (0,   10,  "red"),
    (10,  25,  "orange"),
    (25,  85,  "green"),
    (85,  130, "blue"),
    (130, 155, "purple"),
    (155, 179, "red"),
]

# Reference bbox area (px²) used to normalize depth_score.
# A character this size is treated as "fully close" (score = 1.0).
_DEPTH_REF_AREA = 40_000.0


def _classify_hue(h: float) -> str:
    for lo, hi, name in _HUE_BANDS:
        if lo <= h <= hi:
            return name
    return "red"


def _sample_team_color(
    frame: np.ndarray,
    x1: int, y1: int,
    x2: int, y2: int,
) -> Optional[str]:
    """
    Sample a strip above the character's bounding box to detect team color
    (e.g. nametag or team indicator hue).
    Returns a color name string or None if the region is too dark/neutral.
    """
    bbox_h = y2 - y1
    strip_h = max(4, bbox_h // 8)
    s_y1 = max(0, y1 - strip_h - 2)
    s_y2 = max(1, y1 - 2)

    # Use middle 50% of bbox width to avoid edge noise
    margin = (x2 - x1) // 4
    s_x1 = max(0, x1 + margin)
    s_x2 = min(frame.shape[1] - 1, x2 - margin)

    if s_y2 <= s_y1 or s_x2 <= s_x1:
        return None

    region = frame[s_y1:s_y2, s_x1:s_x2]
    if region.size == 0:
        return None

    hsv = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)
    avg_h = float(np.mean(hsv[:, :, 0]))
    avg_s = float(np.mean(hsv[:, :, 1]))
    avg_v = float(np.mean(hsv[:, :, 2]))

    # Reject washed-out or too-dark regions
    if avg_s < 50 or avg_v < 50:
        return "neutral"

    return _classify_hue(avg_h)


class Tracker:
    """
    Wraps boxmot ByteTrack and enriches track output with:
      velocity, trail, age, depth_score, team_color
    """

    def __init__(self, config: dict):
        trk_cfg = config["tracking"]
        ovr_cfg = config.get("overlay", {})
        det_cfg = config.get("detection", {})

        self._max_age: int = trk_cfg.get("max_age", 30)
        self._min_hits: int = trk_cfg.get("min_hits", 2)
        self._trail_length: int = ovr_cfg.get("trail_length", 20)
        self._team_detection: bool = det_cfg.get("team_detection", False)

        self._tracker = None
        self._history: Dict[int, dict] = {}
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
        Returns enriched track list.
        """
        self._frame_count += 1

        if self._tracker is None:
            return []

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
            if len(row) < 7:
                continue
            x1, y1, x2, y2 = int(row[0]), int(row[1]), int(row[2]), int(row[3])
            track_id = int(row[4])
            conf     = float(row[5])
            class_id = int(row[6])

            cx = (x1 + x2) // 2
            cy = (y1 + y2) // 2
            active_ids.add(track_id)

            # Depth score: bbox area relative to reference — larger = closer
            bbox_area = max(1, (x2 - x1) * (y2 - y1))
            depth_score = min(1.0, bbox_area / _DEPTH_REF_AREA)

            # Team color
            team_color: Optional[str] = None
            if self._team_detection and frame is not None:
                try:
                    team_color = _sample_team_color(frame, x1, y1, x2, y2)
                except Exception:
                    pass

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
                "id":          track_id,
                "bbox":        [x1, y1, x2, y2],
                "center":      (cx, cy),
                "velocity":    (vx, vy),
                "speed":       speed,
                "angle":       angle,
                "confidence":  conf,
                "class_id":    class_id,
                "age":         hist["age"],
                "trail":       list(hist["trail"]),
                "depth_score": depth_score,
                "team_color":  team_color,
            })

        # Age-out stale tracks
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

    def set_team_detection(self, value: bool) -> None:
        self._team_detection = value

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