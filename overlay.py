"""
overlay.py — Transparent always-on-top fullscreen overlay drawn with QPainter.
Renders bounding boxes, trails, velocity arrows, mini-map, direction cone, and status info.

Direction cone: tracks average velocity of all detected objects to infer camera rotation
direction and draws an arrow on the mini-map.
"""

import logging
import math
import threading
import time
from collections import deque
from typing import List, Optional, Tuple

from PyQt6.QtCore import Qt, QTimer, QPoint, QRect
from PyQt6.QtGui import (
    QColor, QFont, QPainter, QPen, QBrush, QPolygon, QFontMetrics, QCursor,
)
from PyQt6.QtWidgets import QApplication, QWidget

logger = logging.getLogger(__name__)

MINIMAP_W = 200
MINIMAP_H = 150
MINIMAP_MARGIN = 10

# Rolling window for camera direction estimation
_DIR_HISTORY_LEN = 10


def _color(rgb: list, alpha: int = 255) -> QColor:
    return QColor(rgb[0], rgb[1], rgb[2], alpha)


# Team color name → QColor
_TEAM_COLORS = {
    "red":     QColor(255, 80,  80,  220),
    "blue":    QColor(80,  140, 255, 220),
    "green":   QColor(80,  220, 80,  220),
    "orange":  QColor(255, 160, 40,  220),
    "purple":  QColor(180, 80,  255, 220),
    "neutral": QColor(180, 180, 180, 160),
}


class Overlay(QWidget):
    """Fullscreen transparent overlay, click-through to the underlying window."""

    def __init__(self, config: dict, get_tracks_fn, get_cursor_state_fn):
        super().__init__()
        self._full_config = config
        self._cfg = config.get("overlay", {})
        self._get_tracks = get_tracks_fn
        self._get_cursor_state = get_cursor_state_fn
        self._lock = threading.Lock()

        # FPS counters updated externally
        self.capture_fps: float = 0.0
        self.inference_fps: float = 0.0

        # Pulse animation state
        self._pulse_phase: float = 0.0
        self._pulse_timer = QTimer(self)
        self._pulse_timer.setInterval(500)  # 2 Hz
        self._pulse_timer.timeout.connect(self._advance_pulse)
        self._pulse_timer.start()

        # Rolling velocity history for camera direction estimation
        self._vel_history: deque = deque(maxlen=_DIR_HISTORY_LEN)

        self._setup_window()

        # Repaint at ~60fps
        self._repaint_timer = QTimer(self)
        self._repaint_timer.setInterval(16)
        self._repaint_timer.timeout.connect(self.update)
        self._repaint_timer.start()

    # ------------------------------------------------------------------
    # Window setup
    # ------------------------------------------------------------------

    def _setup_window(self) -> None:
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)

        screen_geom = QApplication.primaryScreen().geometry()
        self.setGeometry(screen_geom)
        self.showFullScreen()
        self._apply_win32_styles()

    def _apply_win32_styles(self) -> None:
        try:
            import win32gui
            import win32con
            hwnd = int(self.winId())
            style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
            win32gui.SetWindowLong(
                hwnd,
                win32con.GWL_EXSTYLE,
                style
                | win32con.WS_EX_LAYERED
                | win32con.WS_EX_TRANSPARENT
                | win32con.WS_EX_TOOLWINDOW,
            )
        except Exception as exc:
            logger.warning("Could not apply Win32 overlay styles: %s", exc)

    # ------------------------------------------------------------------
    # Animation
    # ------------------------------------------------------------------

    def _advance_pulse(self) -> None:
        self._pulse_phase = (self._pulse_phase + 1) % 2

    # ------------------------------------------------------------------
    # Camera direction estimation
    # ------------------------------------------------------------------

    def _compute_camera_direction(
        self, tracks: List[dict]
    ) -> Tuple[Optional[Tuple[float, float]], float]:
        """
        Estimates camera rotation direction from consensus object velocities.
        When multiple tracked objects all move in the same direction, it's likely
        that the camera is rotating rather than all objects moving simultaneously.

        Returns (normalized_dir, consensus_speed) or (None, 0.0).
        """
        moving = [
            t["velocity"] for t in tracks
            if t.get("speed", 0.0) > 1.5
        ]
        if len(moving) < 2:
            return None, 0.0

        avg_vx = sum(v[0] for v in moving) / len(moving)
        avg_vy = sum(v[1] for v in moving) / len(moving)
        speed = math.sqrt(avg_vx ** 2 + avg_vy ** 2)

        if speed < 0.5:
            return None, 0.0

        # Add to rolling history for smoothing
        self._vel_history.append((avg_vx / speed, avg_vy / speed))

        # Smooth over history
        hist = list(self._vel_history)
        sx = sum(d[0] for d in hist) / len(hist)
        sy = sum(d[1] for d in hist) / len(hist)
        mag = math.sqrt(sx ** 2 + sy ** 2)
        if mag < 0.1:
            return None, 0.0

        return (sx / mag, sy / mag), speed

    # ------------------------------------------------------------------
    # Paint
    # ------------------------------------------------------------------

    def paintEvent(self, event) -> None:
        if not self._cfg.get("enabled", True):
            return

        tracks = self._get_tracks()
        follow_active, target_id, detection_active = self._get_cursor_state()

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        screen_w = self.width()
        screen_h = self.height()

        active_color   = _color(self._cfg.get("active_color",   [0, 255, 80]))
        inactive_color = _color(self._cfg.get("inactive_color", [0, 200, 255]))
        thickness      = self._cfg.get("box_thickness", 1)

        for track in tracks:
            is_active = (track["id"] == target_id)
            color = active_color if is_active else inactive_color

            if self._cfg.get("show_trails", True):
                self._draw_trail(painter, track, color)

            if self._cfg.get("show_boxes", True):
                self._draw_box(painter, track, color, thickness, is_active)

            if self._cfg.get("show_velocity", True):
                self._draw_velocity(painter, track, color)

            if is_active and follow_active:
                self._draw_active_ring(painter, track, active_color)

        if self._cfg.get("show_radius_circle", True):
            self._draw_radius_circle(painter, screen_w, screen_h, follow_active)

        self._draw_status(painter, follow_active, detection_active)
        self._draw_fps(painter, screen_w)

        if self._cfg.get("show_minimap", True):
            cam_dir, cam_speed = self._compute_camera_direction(tracks)
            self._draw_minimap(
                painter, tracks, screen_w, screen_h,
                target_id, active_color, inactive_color,
                cam_dir, cam_speed,
            )

        painter.end()

    # ------------------------------------------------------------------
    # Drawing helpers
    # ------------------------------------------------------------------

    def _draw_trail(self, painter: QPainter, track: dict, color: QColor) -> None:
        trail = track.get("trail", [])
        if len(trail) < 2:
            return
        for i in range(1, len(trail)):
            alpha = int(80 + 175 * (i / len(trail)))
            c = QColor(color.red(), color.green(), color.blue(), alpha)
            painter.setPen(QPen(c, 1))
            x0, y0 = trail[i - 1]
            x1, y1 = trail[i]
            painter.drawLine(x0, y0, x1, y1)

    def _draw_box(
        self,
        painter: QPainter,
        track: dict,
        color: QColor,
        thickness: int,
        is_active: bool,
    ) -> None:
        x1, y1, x2, y2 = track["bbox"]
        pen = QPen(color, thickness + (1 if is_active else 0))
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(x1, y1, x2 - x1, y2 - y1)

        # Team color indicator: small square in top-left of box
        team_color = track.get("team_color")
        if team_color and team_color in _TEAM_COLORS:
            tc = _TEAM_COLORS[team_color]
            painter.setBrush(QBrush(tc))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRect(x1, y1, 6, 6)
            painter.setBrush(Qt.BrushStyle.NoBrush)

        # Depth indicator: small bar in bottom-right corner of box
        depth = track.get("depth_score", 0.0)
        bar_w = int((x2 - x1) * depth)
        if bar_w > 0:
            depth_color = QColor(255, int(200 * (1 - depth)), 0, 160)
            painter.setBrush(QBrush(depth_color))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRect(x1, y2 - 3, bar_w, 3)
            painter.setBrush(Qt.BrushStyle.NoBrush)

        # ID + confidence label
        font = QFont("Consolas", 8)
        painter.setFont(font)
        painter.setPen(QPen(color))
        depth_pct = int(depth * 100)
        label = f"#{track['id']}  {track['confidence']:.2f}  d:{depth_pct}%"
        painter.drawText(x1, max(y1 - 4, 12), label)

    def _draw_velocity(self, painter: QPainter, track: dict, color: QColor) -> None:
        cx, cy = track["center"]
        vx, vy = track["velocity"]
        speed = track.get("speed", 0.0)
        if speed < 1.0:
            return
        scale = min(speed * 3, 60)
        length = math.sqrt(vx ** 2 + vy ** 2)
        if length == 0:
            return
        ex = cx + int(vx / length * scale)
        ey = cy + int(vy / length * scale)
        painter.setPen(QPen(color, 1))
        painter.drawLine(cx, cy, ex, ey)
        angle = math.atan2(ey - cy, ex - cx)
        arrow_len = 6
        for side in (0.5, -0.5):
            ax = ex - int(arrow_len * math.cos(angle + side))
            ay = ey - int(arrow_len * math.sin(angle + side))
            painter.drawLine(ex, ey, ax, ay)

    def _draw_active_ring(self, painter: QPainter, track: dict, color: QColor) -> None:
        cx, cy = track["center"]
        x1, y1, x2, y2 = track["bbox"]
        r = max(x2 - x1, y2 - y1) // 2 + 10 + self._pulse_phase * 5
        alpha = 200 - self._pulse_phase * 80
        c = QColor(color.red(), color.green(), color.blue(), int(alpha))
        painter.setPen(QPen(c, 2, Qt.PenStyle.DashLine))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(int(cx - r), int(cy - r), int(r * 2), int(r * 2))

    def _draw_radius_circle(self, painter: QPainter, sw: int, sh: int, follow_active: bool) -> None:
        cf = self._full_config.get("cursor_follow", {})
        radius = cf.get("follow_radius", 150)
        if cf.get("fps_mode", False):
            cx, cy = sw // 2, sh // 2
        else:
            p = QCursor.pos()
            cx, cy = p.x(), p.y()
        alpha = 140 if follow_active else 50
        painter.setPen(QPen(QColor(255, 255, 0, alpha), 1, Qt.PenStyle.DashLine))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(cx - radius, cy - radius, radius * 2, radius * 2)

    def _draw_status(self, painter: QPainter, follow_active: bool, detection_active: bool) -> None:
        font = QFont("Consolas", 10, QFont.Weight.Bold)
        painter.setFont(font)

        det_text  = "● DET ON"  if detection_active else "○ DET OFF"
        det_color = QColor(0, 200, 255, 220) if detection_active else QColor(180, 180, 180, 160)
        painter.setPen(QPen(det_color))
        painter.drawText(10, 20, det_text)

        lock_text  = "● LOCK ON"  if follow_active else "○ LOCK OFF"
        lock_color = QColor(0, 255, 80, 220) if follow_active else QColor(255, 60, 60, 180)
        painter.setPen(QPen(lock_color))
        painter.drawText(10, 38, lock_text)

    def _draw_fps(self, painter: QPainter, sw: int) -> None:
        font = QFont("Consolas", 9)
        painter.setFont(font)
        painter.setPen(QPen(QColor(200, 200, 200, 180)))
        text = f"Cap {self.capture_fps:.0f}fps  Inf {self.inference_fps:.0f}fps"
        fm = QFontMetrics(font)
        tw = fm.horizontalAdvance(text)
        painter.drawText(sw - tw - 10, 20, text)

    def _draw_minimap(
        self,
        painter: QPainter,
        tracks: list,
        sw: int,
        sh: int,
        target_id: Optional[int],
        active_color: QColor,
        inactive_color: QColor,
        cam_dir: Optional[Tuple[float, float]],
        cam_speed: float,
    ) -> None:
        mx = sw - MINIMAP_W - MINIMAP_MARGIN
        my = sh - MINIMAP_H - MINIMAP_MARGIN

        # Background
        painter.setBrush(QBrush(QColor(0, 0, 0, 120)))
        painter.setPen(QPen(QColor(100, 100, 100, 180), 1))
        painter.drawRect(mx, my, MINIMAP_W, MINIMAP_H)

        # Track dots
        for track in tracks:
            tx, ty = track["center"]
            dot_x = mx + int(tx / sw * MINIMAP_W)
            dot_y = my + int(ty / sh * MINIMAP_H)
            is_active = track["id"] == target_id
            c = active_color if is_active else inactive_color
            painter.setBrush(QBrush(c))
            painter.setPen(Qt.PenStyle.NoPen)
            r = 4 if is_active else 3
            painter.drawEllipse(dot_x - r, dot_y - r, r * 2, r * 2)

        # Camera direction cone/arrow
        if cam_dir is not None and self._cfg.get("show_direction_cone", True):
            self._draw_direction_cone(painter, mx, my, cam_dir, cam_speed)

        # Label
        font = QFont("Consolas", 7)
        painter.setFont(font)
        painter.setPen(QPen(QColor(180, 180, 180, 180)))
        painter.drawText(mx + 4, my + MINIMAP_H - 4, "MINIMAP")

    def _draw_direction_cone(
        self,
        painter: QPainter,
        mx: int,
        my: int,
        cam_dir: Tuple[float, float],
        speed: float,
    ) -> None:
        """Draw a camera-direction arrow from the mini-map center."""
        dir_x, dir_y = cam_dir
        center_x = mx + MINIMAP_W // 2
        center_y = my + MINIMAP_H // 2
        arrow_len = 32

        end_x = center_x + int(dir_x * arrow_len)
        end_y = center_y + int(dir_y * arrow_len)

        alpha = min(255, int(80 + speed * 15))
        pen = QPen(QColor(255, 210, 0, alpha), 2)
        painter.setPen(pen)
        painter.drawLine(center_x, center_y, end_x, end_y)

        # Small center dot
        painter.setBrush(QBrush(QColor(255, 210, 0, alpha)))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(center_x - 3, center_y - 3, 6, 6)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        # Arrowhead
        angle = math.atan2(dir_y, dir_x)
        arrow_head = 8
        pen2 = QPen(QColor(255, 210, 0, alpha), 2)
        painter.setPen(pen2)
        for side in (0.45, -0.45):
            ax = end_x - int(arrow_head * math.cos(angle + side))
            ay = end_y - int(arrow_head * math.sin(angle + side))
            painter.drawLine(end_x, end_y, ax, ay)

    # ------------------------------------------------------------------
    # External config updates
    # ------------------------------------------------------------------

    def update_config(self, key: str, value) -> None:
        self._cfg[key] = value

    def set_follow_radius(self, radius: int) -> None:
        self._cfg["follow_radius"] = radius