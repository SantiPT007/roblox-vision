"""
gui.py — PyQt6 settings and control panel.
All changes propagate live to running threads without restart.
"""

import logging
import os
from typing import Callable, Optional

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSlider,
    QSpinBox,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)


def _get_window_titles() -> list:
    try:
        import win32gui
        titles = []
        def _enum(hwnd, _):
            if win32gui.IsWindowVisible(hwnd):
                t = win32gui.GetWindowText(hwnd)
                if t:
                    titles.append(t)
        win32gui.EnumWindows(_enum, None)
        return sorted(set(titles))
    except Exception:
        return []


# Presets are defined in config.yaml under the "presets" key.
# The GUI reads them from there so users can add their own.

class ControlPanel(QWidget):
    """Settings and control panel window."""

    def __init__(
        self,
        config: dict,
        on_config_change: Callable[[str, str, object], None],
        get_status_fn: Callable[[], dict],
        save_config_fn: Optional[Callable[[], None]] = None,
    ):
        super().__init__()
        self._config = config
        self._on_change = on_config_change
        self._get_status = get_status_fn
        self._save_config = save_config_fn

        self.setWindowTitle("Character Tracker — Control Panel")
        self.setMinimumWidth(400)
        self.setWindowFlags(
            Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint
        )

        self._build_ui()

        self._status_timer = QTimer(self)
        self._status_timer.setInterval(500)
        self._status_timer.timeout.connect(self._refresh_status)
        self._status_timer.start()

    # ------------------------------------------------------------------
    # UI Construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(8)

        root.addWidget(self._build_capture_group())
        root.addWidget(self._build_detection_group())
        root.addWidget(self._build_lock_group())
        root.addWidget(self._build_hotkeys_group())
        root.addWidget(self._build_overlay_group())
        root.addWidget(self._build_follow_button())

        if self._save_config:
            save_btn = QPushButton("Save Config to Disk")
            save_btn.clicked.connect(self._on_save)
            root.addWidget(save_btn)

        self._status_bar = QStatusBar()
        root.addWidget(self._status_bar)

    def _build_capture_group(self) -> QGroupBox:
        grp = QGroupBox("Capture")
        form = QFormLayout(grp)

        row = QHBoxLayout()
        self._window_combo = QComboBox()
        self._window_combo.setEditable(True)
        self._populate_windows()
        self._window_combo.setCurrentText(
            self._config.get("capture", {}).get("target_window", "")
        )
        self._window_combo.currentTextChanged.connect(
            lambda v: self._on_change("capture", "target_window", v)
        )
        row.addWidget(self._window_combo, 1)

        refresh_btn = QPushButton("↺")
        refresh_btn.setFixedWidth(28)
        refresh_btn.clicked.connect(self._populate_windows)
        row.addWidget(refresh_btn)
        form.addRow("Target window:", row)

        fps_spin = QSpinBox()
        fps_spin.setRange(10, 240)
        fps_spin.setValue(self._config.get("capture", {}).get("fps_cap", 60))
        fps_spin.valueChanged.connect(
            lambda v: self._on_change("capture", "fps_cap", v)
        )
        form.addRow("FPS cap:", fps_spin)

        return grp

    def _build_detection_group(self) -> QGroupBox:
        grp = QGroupBox("Detection")
        form = QFormLayout(grp)

        # ----- Model selector -----
        model_row = QHBoxLayout()
        self._model_combo = QComboBox()
        self._model_combo.setEditable(False)
        self._populate_models()
        self._model_combo.setCurrentText(
            os.path.basename(self._config.get("detection", {}).get("model", ""))
        )
        self._model_combo.currentTextChanged.connect(self._on_model_changed)
        model_row.addWidget(self._model_combo, 1)
        model_refresh_btn = QPushButton("↺")
        model_refresh_btn.setFixedWidth(28)
        model_refresh_btn.clicked.connect(self._populate_models)
        model_row.addWidget(model_refresh_btn)
        form.addRow("Model:", model_row)

        self._conf_label = QLabel()
        conf_val = self._config.get("detection", {}).get("confidence", 0.45)
        conf_slider = QSlider(Qt.Orientation.Horizontal)
        conf_slider.setRange(10, 90)
        conf_slider.setValue(int(conf_val * 100))
        self._conf_label.setText(f"{conf_val:.2f}")
        conf_slider.valueChanged.connect(self._on_conf_change)
        conf_row = QHBoxLayout()
        conf_row.addWidget(conf_slider, 1)
        conf_row.addWidget(self._conf_label)
        form.addRow("Confidence:", conf_row)

        device_combo = QComboBox()
        device_combo.addItems(["cuda", "cpu"])
        device_combo.setCurrentText(
            self._config.get("detection", {}).get("device", "cuda")
        )
        device_combo.currentTextChanged.connect(
            lambda v: self._on_change("detection", "device", v)
        )
        form.addRow("Device:", device_combo)

        bg_chk = QCheckBox()
        bg_chk.setChecked(
            self._config.get("detection", {}).get("use_background_subtraction", True)
        )
        bg_chk.toggled.connect(
            lambda v: self._on_change("detection", "use_background_subtraction", v)
        )
        form.addRow("BG subtraction:", bg_chk)

        all_cls_chk = QCheckBox("All classes  (enable for custom models — disables COCO class-0 filter)")
        all_cls_chk.setChecked(
            self._config.get("detection", {}).get("detect_all_classes", False)
        )
        all_cls_chk.toggled.connect(
            lambda v: self._on_change("detection", "detect_all_classes", v)
        )
        form.addRow("", all_cls_chk)

        return grp

    def _build_lock_group(self) -> QGroupBox:
        grp = QGroupBox("Mouse Lock")
        form = QFormLayout(grp)
        cf = self._config.get("cursor_follow", {})

        # ----- FPS mode -----
        fps_chk = QCheckBox("FPS mode  (camera-controlled games — low-gain movement, no prediction, deadzone)")
        fps_chk.setChecked(cf.get("fps_mode", False))
        fps_chk.toggled.connect(lambda v: self._on_change("cursor_follow", "fps_mode", v))
        form.addRow("", fps_chk)

        # ----- Presets -----
        presets = self._config.get("presets", {})
        if presets:
            preset_combo = QComboBox()
            preset_combo.addItem("— select preset —")
            for name in presets:
                preset_combo.addItem(name)
            preset_combo.currentTextChanged.connect(self._on_preset_selected)
            form.addRow("Preset:", preset_combo)

        # ----- Smoothing -----
        self._smooth_label = QLabel()
        smooth_val = cf.get("smoothing", 0.12)
        self._smooth_slider = QSlider(Qt.Orientation.Horizontal)
        self._smooth_slider.setRange(1, 95)
        self._smooth_slider.setValue(int(smooth_val * 100))
        self._smooth_label.setText(f"{smooth_val:.2f}")
        self._smooth_slider.valueChanged.connect(self._on_smooth_change)
        smooth_row = QHBoxLayout()
        smooth_row.addWidget(self._smooth_slider, 1)
        smooth_row.addWidget(self._smooth_label)
        form.addRow("Smoothing:", smooth_row)

        # ----- Speed -----
        self._speed_label = QLabel()
        speed_val = cf.get("speed", 1.0)
        self._speed_slider = QSlider(Qt.Orientation.Horizontal)
        self._speed_slider.setRange(10, 400)   # 0.1x – 4.0x, step 0.01
        self._speed_slider.setValue(int(speed_val * 100))
        self._speed_label.setText(f"{speed_val:.2f}x")
        self._speed_slider.valueChanged.connect(self._on_speed_change)
        speed_row = QHBoxLayout()
        speed_row.addWidget(self._speed_slider, 1)
        speed_row.addWidget(self._speed_label)
        form.addRow("Speed:", speed_row)

        # ----- FOV radius -----
        self._radius_label = QLabel()
        radius_val = cf.get("follow_radius", 150)
        self._radius_slider = QSlider(Qt.Orientation.Horizontal)
        self._radius_slider.setRange(30, 800)
        self._radius_slider.setValue(radius_val)
        self._radius_label.setText(f"{radius_val}px")
        self._radius_slider.valueChanged.connect(self._on_radius_change)
        radius_row = QHBoxLayout()
        radius_row.addWidget(self._radius_slider, 1)
        radius_row.addWidget(self._radius_label)
        form.addRow("FOV radius:", radius_row)

        # ----- Follow point -----
        self._fp_combo = QComboBox()
        self._fp_combo.addItems(["head", "chest", "center"])
        self._fp_combo.setCurrentText(cf.get("follow_point", "chest"))
        self._fp_combo.currentTextChanged.connect(
            lambda v: self._on_change("cursor_follow", "follow_point", v)
        )
        form.addRow("Aim point:", self._fp_combo)

        # ----- Prediction -----
        self._pred_spin = QSpinBox()
        self._pred_spin.setRange(0, 200)
        self._pred_spin.setSuffix(" ms")
        self._pred_spin.setValue(cf.get("prediction_ms", 60))
        self._pred_spin.valueChanged.connect(
            lambda v: self._on_change("cursor_follow", "prediction_ms", v)
        )
        form.addRow("Prediction:", self._pred_spin)

        # ----- Deadzone (FPS mode) -----
        self._dz_spin = QSpinBox()
        self._dz_spin.setRange(0, 50)
        self._dz_spin.setSuffix(" px")
        self._dz_spin.setValue(cf.get("deadzone", 5))
        self._dz_spin.valueChanged.connect(
            lambda v: self._on_change("cursor_follow", "deadzone", v)
        )
        form.addRow("Deadzone (FPS):", self._dz_spin)

        # ----- Enemy-only filter -----
        # target_class=None locks any class; =1 locks "roblox avatar" only in rivals.pt
        enemy_chk = QCheckBox(
            "Enemies only  (class 1 — for rivals.pt: ignores 'friendly' teammates)"
        )
        current_tc = cf.get("target_class")
        enemy_chk.setChecked(current_tc == 1)
        enemy_chk.toggled.connect(
            lambda v: self._on_change("cursor_follow", "target_class", 1 if v else None)
        )
        form.addRow("", enemy_chk)

        return grp

    def _build_hotkeys_group(self) -> QGroupBox:
        grp = QGroupBox("Hotkeys  (lock_toggle and lock_hold are live; detection_toggle needs restart)")
        form = QFormLayout(grp)
        hk = self._config.get("hotkeys", {})

        fields = [
            ("detection_toggle", "Detection on/off:", "F6"),
            ("lock_toggle",      "Lock toggle:",      "F7"),
            ("lock_hold",        "Lock hold:",        "F4"),
        ]
        for key, label, default in fields:
            edit = QLineEdit(hk.get(key, default))
            edit.setMaximumWidth(70)
            edit.textChanged.connect(
                lambda v, k=key: self._on_change("hotkeys", k, v)
            )
            form.addRow(label, edit)

        return grp

    def _build_overlay_group(self) -> QGroupBox:
        grp = QGroupBox("Overlay")
        layout = QVBoxLayout(grp)
        ov = self._config.get("overlay", {})

        checkboxes = [
            ("show_boxes",         "Show bounding boxes"),
            ("show_trails",        "Show trails"),
            ("show_velocity",      "Show velocity arrows"),
            ("show_radius_circle", "Show FOV circle"),
            ("show_minimap",       "Show mini-map"),
        ]
        for key, label in checkboxes:
            chk = QCheckBox(label)
            chk.setChecked(ov.get(key, True))
            chk.toggled.connect(
                lambda v, k=key: self._on_change("overlay", k, v)
            )
            layout.addWidget(chk)

        return grp

    def _build_follow_button(self) -> QPushButton:
        toggle_key = self._config.get("hotkeys", {}).get("lock_toggle", "F7")
        self._follow_btn = QPushButton(f"Enable Mouse Lock  [{toggle_key}]")
        self._follow_btn.setCheckable(True)
        self._follow_btn.setChecked(
            self._config.get("cursor_follow", {}).get("enabled", False)
        )
        self._follow_btn.toggled.connect(
            lambda v: self._on_change("cursor_follow", "enabled", v)
        )
        return self._follow_btn

    # ------------------------------------------------------------------
    # Preset application
    # ------------------------------------------------------------------

    def _on_preset_selected(self, name: str) -> None:
        if name.startswith("—"):
            return
        presets = self._config.get("presets", {})
        preset = presets.get(name)
        if not preset:
            return

        mapping = {
            "smoothing":    (self._smooth_slider, self._smooth_label,
                             lambda v: int(v * 100), lambda v: f"{v:.2f}"),
            "speed":        (self._speed_slider,  self._speed_label,
                             lambda v: int(v * 100), lambda v: f"{v:.2f}x"),
            "follow_radius":(self._radius_slider, self._radius_label,
                             lambda v: int(v),       lambda v: f"{v}px"),
        }

        for key, value in preset.items():
            self._on_change("cursor_follow", key, value)
            if key in mapping:
                slider, label, to_int, to_str = mapping[key]
                slider.blockSignals(True)
                slider.setValue(to_int(value))
                slider.blockSignals(False)
                label.setText(to_str(value))
            elif key == "follow_point":
                self._fp_combo.blockSignals(True)
                self._fp_combo.setCurrentText(value)
                self._fp_combo.blockSignals(False)
            elif key == "prediction_ms":
                self._pred_spin.blockSignals(True)
                self._pred_spin.setValue(value)
                self._pred_spin.blockSignals(False)
            elif key == "deadzone":
                self._dz_spin.blockSignals(True)
                self._dz_spin.setValue(value)
                self._dz_spin.blockSignals(False)

    # ------------------------------------------------------------------
    # Slider / spinner callbacks
    # ------------------------------------------------------------------

    def _on_conf_change(self, value: int) -> None:
        v = value / 100.0
        self._conf_label.setText(f"{v:.2f}")
        self._on_change("detection", "confidence", v)

    def _on_smooth_change(self, value: int) -> None:
        v = value / 100.0
        self._smooth_label.setText(f"{v:.2f}")
        self._on_change("cursor_follow", "smoothing", v)

    def _on_speed_change(self, value: int) -> None:
        v = value / 100.0
        self._speed_label.setText(f"{v:.2f}x")
        self._on_change("cursor_follow", "speed", v)

    def _on_radius_change(self, value: int) -> None:
        self._radius_label.setText(f"{value}px")
        self._on_change("cursor_follow", "follow_radius", value)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _populate_models(self) -> None:
        current = self._model_combo.currentText()
        self._model_combo.blockSignals(True)
        self._model_combo.clear()
        models_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")
        if os.path.isdir(models_dir):
            pts = sorted(f for f in os.listdir(models_dir) if f.endswith(".pt"))
            self._model_combo.addItems(pts)
        if current:
            self._model_combo.setCurrentText(current)
        self._model_combo.blockSignals(False)

    def _on_model_changed(self, filename: str) -> None:
        if not filename:
            return
        full_path = os.path.join("models", filename)
        self._on_change("detection", "model", full_path)

    def _populate_windows(self) -> None:
        current = self._window_combo.currentText()
        self._window_combo.blockSignals(True)
        self._window_combo.clear()
        titles = _get_window_titles()
        self._window_combo.addItems(titles)
        self._window_combo.setCurrentText(current)
        self._window_combo.blockSignals(False)

    def _on_save(self) -> None:
        if self._save_config:
            try:
                self._save_config()
                self._status_bar.showMessage("Config saved.", 3000)
            except Exception as exc:
                self._status_bar.showMessage(f"Save failed: {exc}", 4000)

    def _refresh_status(self) -> None:
        try:
            s = self._get_status()
            det   = "DET:ON"  if s.get("detection_active", True)  else "DET:OFF"
            lck   = "LOCK:ON" if s.get("follow_active", False)     else "LOCK:OFF"
            model = os.path.basename(self._config.get("detection", {}).get("model", ""))
            tid   = s.get("target_id")
            msg = (
                f"{det}  {lck}  "
                f"Cap {s.get('fps_capture', 0):.0f}fps  "
                f"Inf {s.get('fps_inference', 0):.0f}fps  "
                f"Tracks:{s.get('track_count', 0)}  "
                f"Target:{'#'+str(tid) if tid is not None else '-'}  "
                f"[{model}]"
            )
            self._status_bar.showMessage(msg)
            self._follow_btn.blockSignals(True)
            self._follow_btn.setChecked(s.get("follow_active", False))
            self._follow_btn.blockSignals(False)
        except Exception:
            pass
