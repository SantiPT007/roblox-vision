"""
overlay.py — Transparent always-on-top fullscreen overlay drawn with QPainter.
Renders bounding boxes, trails, velocity arrows, mini-map, and status info.
"""

import logging
import math
import threading
import time
from typing import List, Optional, Tuple

from PyQt6.QtCore import Qt, QTimer, QPoint, QRect
from PyQt6.QtGui import (
    QColor, QFont, QPainter, QPen, QBrush, QPolygon, QFontMetrics,
)
from PyQt6.QtWidgets import QApplication, QWidget

logger = logging.getLogger(__name__)

MINIMAP_W = 200
MINIMAP_H = 150
MINIMAP_MARGIN = 10


def _color(rgb: list, alpha: int = 255) -> QColor:
    return QColor(rgb[0], rgb[1], rgb[2], alpha)


class Overlay(QWidget):
    """Fullscreen transparent overlay, click-through to the underlying window."""

    def __init__(self, config: dict, get_tracks_fn, get_cursor_state_fn):
        super().__init__()
        self._full_config = config          # keep reference for cursor_follow keys
        self._cfg = config.get("overlay", {})
        self._get_tracks = get_tracks_fn
        self._get_cursor_state = get_cursor_state_fn  # returns (follow_active, target_id)
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
        screen = QApplication.primaryScreen().geometry()
        self.setGeometry(screen)
        self.showFullScreen()

        # Apply Win32 extended styles for full click-through
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
        self._pulse_phase = (self._pulse_phase + 1) % 2  # 0 or 1

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

        active_color = _color(self._cfg.get("active_color", [0, 255, 80]))
        inactive_color = _color(self._cfg.get("inactive_color", [0, 200, 255]))
        thickness = self._cfg.get("box_thickness", 1)

        # --- Per-track rendering ---
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

        # --- FOV circle — visible whenever the option is on (not just when follow active) ---
        if self._cfg.get("show_radius_circle", True):
            self._draw_radius_circle(painter, screen_w, screen_h, follow_active)

        # --- Status text ---
        self._draw_status(painter, follow_active, detection_active)

        # --- FPS counter ---
        self._draw_fps(painter, screen_w)

        # --- Mini-map ---
        if self._cfg.get("show_minimap", True):
            self._draw_minimap(painter, tracks, screen_w, screen_h, target_id, active_color, inactive_color)

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
            pen = QPen(c, 1)
            painter.setPen(pen)
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

        # ID label above box
        font = QFont("Consolas", 8)
        painter.setFont(font)
        label = f"#{track['id']}  {track['confidence']:.2f}"
        pen_text = QPen(color)
        painter.setPen(pen_text)
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
        pen = QPen(color, 1)
        painter.setPen(pen)
        painter.drawLine(cx, cy, ex, ey)
        # Arrowhead
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
        pen = QPen(c, 2, Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(int(cx - r), int(cy - r), int(r * 2), int(r * 2))

    def _draw_radius_circle(self, painter: QPainter, sw: int, sh: int, follow_active: bool) -> None:
        cf = self._full_config.get("cursor_follow", {})
        radius = cf.get("follow_radius", 150)
        # In FPS mode the cursor is locked to screen center by the game;
        # in normal mode follow the actual cursor so the circle shows exactly
        # which area will trigger a lock.
        if cf.get("fps_mode", False):
            cx, cy = sw // 2, sh // 2
        else:
            from PyQt6.QtGui import QCursor
            p = QCursor.pos()
            cx, cy = p.x(), p.y()
        alpha = 140 if follow_active else 50
        pen = QPen(QColor(255, 255, 0, alpha), 1, Qt.PenStyle.DashLine)
        painter.setPen(pen)
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
    ) -> None:
        mx = sw - MINIMAP_W - MINIMAP_MARGIN
        my = sh - MINIMAP_H - MINIMAP_MARGIN

        # Background
        painter.setBrush(QBrush(QColor(0, 0, 0, 120)))
        painter.setPen(QPen(QColor(100, 100, 100, 180), 1))
        painter.drawRect(mx, my, MINIMAP_W, MINIMAP_H)

        # Scale and draw track dots
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

        # Label
        font = QFont("Consolas", 7)
        painter.setFont(font)
        painter.setPen(QPen(QColor(180, 180, 180, 180)))
        painter.drawText(mx + 4, my + MINIMAP_H - 4, "MINIMAP")

    # ------------------------------------------------------------------
    # External config updates
    # ------------------------------------------------------------------

    def update_config(self, key: str, value) -> None:
        self._cfg[key] = value

    def set_follow_radius(self, radius: int) -> None:
        self._cfg["follow_radius"] = radius
