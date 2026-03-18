"""
perf_monitor.py — Floating performance dashboard widget.

Displays CPU, RAM, GPU utilization, VRAM usage, and pipeline metrics
(capture FPS, inference FPS, active tracks, current target).

Dependencies:
  - psutil   (CPU/RAM) — pip install psutil
  - pynvml   (GPU/VRAM) — pip install pynvml   [optional, graceful fallback]
"""

import logging

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QBrush, QColor, QPainter
from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget

logger = logging.getLogger(__name__)


class PerfDashboard(QWidget):
    """Semi-transparent floating widget showing real-time performance metrics."""

    def __init__(self, get_status_fn, parent=None):
        super().__init__(parent)
        self._get_status = get_status_fn
        self._nvml_init_ok: bool = False
        self._nvml_handle = None
        self._setup_window()
        self._build_ui()
        self._try_init_nvml()

        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._refresh)
        self._timer.start()

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def _setup_window(self) -> None:
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedWidth(240)
        self.move(12, 60)  # Below the status indicators

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(2)

        self._labels: dict = {}
        rows = [
            ("title",   "── PERFORMANCE ──"),
            ("cpu",     "CPU   : --"),
            ("ram",     "RAM   : --"),
            ("gpu",     "GPU   : --"),
            ("vram",    "VRAM  : --"),
            ("sep",     "── PIPELINE ──────"),
            ("cap_fps", "Cap   : -- fps"),
            ("inf_fps", "Inf   : -- fps"),
            ("tracks",  "Tracks: --"),
            ("target",  "Target: --"),
        ]
        for key, text in rows:
            lbl = QLabel(text)
            lbl.setStyleSheet(
                "color: #00e87a; font-family: Consolas; font-size: 10px;"
                " background: transparent; padding: 0px;"
            )
            layout.addWidget(lbl)
            self._labels[key] = lbl

        self.adjustSize()

    def _try_init_nvml(self) -> None:
        try:
            import pynvml
            pynvml.nvmlInit()
            self._nvml_handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            self._nvml_init_ok = True
        except Exception:
            self._nvml_init_ok = False

    # ------------------------------------------------------------------
    # Refresh
    # ------------------------------------------------------------------

    def _refresh(self) -> None:
        self._refresh_system()
        self._refresh_gpu()
        self._refresh_pipeline()
        self.adjustSize()

    def _refresh_system(self) -> None:
        try:
            import psutil
            cpu = psutil.cpu_percent(interval=None)
            ram = psutil.virtual_memory().percent
            self._labels["cpu"].setText(f"CPU   : {cpu:.0f}%")
            self._labels["ram"].setText(f"RAM   : {ram:.0f}%")
        except ImportError:
            self._labels["cpu"].setText("CPU   : install psutil")
            self._labels["ram"].setText("")
        except Exception as exc:
            self._labels["cpu"].setText(f"CPU   : err")

    def _refresh_gpu(self) -> None:
        if not self._nvml_init_ok:
            self._labels["gpu"].setText("GPU   : N/A")
            self._labels["vram"].setText("VRAM  : N/A")
            return
        try:
            import pynvml
            util = pynvml.nvmlDeviceGetUtilizationRates(self._nvml_handle)
            mem = pynvml.nvmlDeviceGetMemoryInfo(self._nvml_handle)
            used_mb = mem.used // (1024 * 1024)
            total_mb = mem.total // (1024 * 1024)
            self._labels["gpu"].setText(f"GPU   : {util.gpu:.0f}%")
            self._labels["vram"].setText(f"VRAM  : {used_mb}MB / {total_mb}MB")
        except Exception:
            self._labels["gpu"].setText("GPU   : N/A")
            self._labels["vram"].setText("VRAM  : N/A")

    def _refresh_pipeline(self) -> None:
        try:
            s = self._get_status()
            self._labels["cap_fps"].setText(f"Cap   : {s.get('fps_capture', 0):.0f} fps")
            self._labels["inf_fps"].setText(f"Inf   : {s.get('fps_inference', 0):.0f} fps")
            self._labels["tracks"].setText(f"Tracks: {s.get('track_count', 0)}")
            tid = s.get("target_id")
            self._labels["target"].setText(
                f"Target: {'#' + str(tid) if tid is not None else '-'}"
            )
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Paint semi-transparent background
    # ------------------------------------------------------------------

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QBrush(QColor(0, 0, 0, 170)))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(self.rect(), 6, 6)
        painter.end()
        super().paintEvent(event)