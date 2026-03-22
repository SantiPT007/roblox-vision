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
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
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


class ControlPanel(QWidget):
    """Settings and control panel window."""

    def __init__(
        self,
        config: dict,
        on_config_change: Callable[[str, str, object], None],
        get_status_fn: Callable[[], dict],
        save_config_fn: Optional[Callable[[], None]] = None,
        toggle_perf_dashboard_fn: Optional[Callable[[], None]] = None,
    ):
        super().__init__()
        self._config = config
        self._on_change = on_config_change
        self._get_status = get_status_fn
        self._save_config = save_config_fn
        self._toggle_perf = toggle_perf_dashboard_fn

        self.setWindowTitle("Character Tracker — Control Panel")
        self.setMinimumWidth(560)
        self.setMinimumHeight(480)
        self.resize(580, 860)
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint)

        self._build_ui()

        self._status_timer = QTimer(self)
        self._status_timer.setInterval(500)
        self._status_timer.timeout.connect(self._refresh_status)
        self._status_timer.start()

    # ------------------------------------------------------------------
    # UI Construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        inner = QWidget()
        root = QVBoxLayout(inner)
        root.setSpacing(6)

        root.addWidget(self._build_capture_group())
        root.addWidget(self._build_detection_group())
        root.addWidget(self._build_lock_group())
        root.addWidget(self._build_triggerbot_group())
        root.addWidget(self._build_hotkeys_group())
        root.addWidget(self._build_overlay_group())
        root.addWidget(self._build_profiles_group())
        root.addWidget(self._build_follow_button())

        btn_row = QHBoxLayout()
        if self._save_config:
            save_btn = QPushButton("Save Config to Disk")
            save_btn.clicked.connect(self._on_save)
            btn_row.addWidget(save_btn)
        if self._toggle_perf:
            perf_btn = QPushButton("Toggle Perf Dashboard")
            perf_btn.clicked.connect(self._toggle_perf)
            btn_row.addWidget(perf_btn)
        root.addLayout(btn_row)

        self._status_bar = QStatusBar()
        root.addWidget(self._status_bar)

        scroll.setWidget(inner)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

    # ------------------------------------------------------------------
    # Capture
    # ------------------------------------------------------------------

    def _build_capture_group(self) -> QGroupBox:
        grp = QGroupBox("Capture")
        form = QFormLayout(grp)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

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
        fps_spin.valueChanged.connect(lambda v: self._on_change("capture", "fps_cap", v))
        form.addRow("FPS cap:", fps_spin)

        return grp

    # ------------------------------------------------------------------
    # Detection
    # ------------------------------------------------------------------

    def _build_detection_group(self) -> QGroupBox:
        grp = QGroupBox("Detection")
        form = QFormLayout(grp)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        # Model selector (.onnx files only)
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

        # Confidence
        self._conf_label = QLabel()
        conf_val = self._config.get("detection", {}).get("confidence", 0.35)
        conf_slider = QSlider(Qt.Orientation.Horizontal)
        conf_slider.setRange(5, 90)
        conf_slider.setValue(int(conf_val * 100))
        self._conf_label.setText(f"{conf_val:.2f}")
        conf_slider.valueChanged.connect(self._on_conf_change)
        conf_row = QHBoxLayout()
        conf_row.addWidget(conf_slider, 1)
        conf_row.addWidget(self._conf_label)
        form.addRow("Confidence:", conf_row)

        return grp

    # ------------------------------------------------------------------
    # Mouse Lock
    # ------------------------------------------------------------------

    def _build_lock_group(self) -> QGroupBox:
        grp = QGroupBox("Mouse Lock")
        form = QFormLayout(grp)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        cf = self._config.get("cursor_follow", {})

        fps_chk = QCheckBox("FPS mode  (camera-controlled — low-gain, no prediction, deadzone)")
        fps_chk.setChecked(cf.get("fps_mode", False))
        fps_chk.toggled.connect(lambda v: self._on_change("cursor_follow", "fps_mode", v))
        form.addRow("", fps_chk)

        # Aimlock presets
        presets = self._config.get("presets", {})
        if presets:
            preset_combo = QComboBox()
            preset_combo.addItem("— aimlock preset —")
            for name in presets:
                preset_combo.addItem(name)
            preset_combo.currentTextChanged.connect(self._on_preset_selected)
            form.addRow("Aimlock preset:", preset_combo)

        # Smoothing
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

        # Speed
        self._speed_label = QLabel()
        speed_val = cf.get("speed", 1.0)
        self._speed_slider = QSlider(Qt.Orientation.Horizontal)
        self._speed_slider.setRange(10, 400)
        self._speed_slider.setValue(int(speed_val * 100))
        self._speed_label.setText(f"{speed_val:.2f}x")
        self._speed_slider.valueChanged.connect(self._on_speed_change)
        speed_row = QHBoxLayout()
        speed_row.addWidget(self._speed_slider, 1)
        speed_row.addWidget(self._speed_label)
        form.addRow("Speed:", speed_row)

        # FOV radius
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

        # Follow point
        self._fp_combo = QComboBox()
        self._fp_combo.addItems(["head", "chest", "center"])
        self._fp_combo.setCurrentText(cf.get("follow_point", "chest"))
        self._fp_combo.currentTextChanged.connect(
            lambda v: self._on_change("cursor_follow", "follow_point", v)
        )
        form.addRow("Aim point:", self._fp_combo)

        # Prediction
        self._pred_spin = QSpinBox()
        self._pred_spin.setRange(0, 200)
        self._pred_spin.setSuffix(" ms")
        self._pred_spin.setValue(cf.get("prediction_ms", 60))
        self._pred_spin.valueChanged.connect(
            lambda v: self._on_change("cursor_follow", "prediction_ms", v)
        )
        form.addRow("Prediction:", self._pred_spin)

        # Deadzone
        self._dz_spin = QSpinBox()
        self._dz_spin.setRange(0, 50)
        self._dz_spin.setSuffix(" px")
        self._dz_spin.setValue(cf.get("deadzone", 5))
        self._dz_spin.valueChanged.connect(
            lambda v: self._on_change("cursor_follow", "deadzone", v)
        )
        form.addRow("Deadzone (FPS):", self._dz_spin)

        # Depth preference
        depth_chk = QCheckBox("Prefer closest target  (largest bounding box = nearest)")
        depth_chk.setChecked(cf.get("prefer_closest_depth", False))
        depth_chk.toggled.connect(
            lambda v: self._on_change("cursor_follow", "prefer_closest_depth", v)
        )
        form.addRow("", depth_chk)

        # Head height ratio
        hhr_spin = QSpinBox()
        hhr_spin.setRange(5, 50)
        hhr_spin.setSuffix(" %")
        hhr_spin.setValue(int(cf.get("head_height_ratio", 0.15) * 100))
        hhr_spin.setToolTip("How far down the bounding box the head aim point sits (5% = very top, 25% = upper quarter)")
        hhr_spin.valueChanged.connect(
            lambda v: self._on_change("cursor_follow", "head_height_ratio", v / 100.0)
        )
        form.addRow("Head aim offset:", hhr_spin)

        # aim_y_reduce
        ayr_chk = QCheckBox("Suppress Y after lock  (stops downward drift on stationary targets)")
        ayr_chk.setChecked(cf.get("aim_y_reduce", False))
        ayr_chk.toggled.connect(lambda v: self._on_change("cursor_follow", "aim_y_reduce", v))
        form.addRow("", ayr_chk)

        ayr_delay_spin = QSpinBox()
        ayr_delay_spin.setRange(100, 3000)
        ayr_delay_spin.setSuffix(" ms")
        ayr_delay_spin.setValue(int(cf.get("aim_y_reduce_delay", 0.6) * 1000))
        ayr_delay_spin.setToolTip("How long after acquiring a lock before Y correction is suppressed")
        ayr_delay_spin.valueChanged.connect(
            lambda v: self._on_change("cursor_follow", "aim_y_reduce_delay", v / 1000.0)
        )
        form.addRow("Y reduce delay:", ayr_delay_spin)

        # Snap-back
        sb_thresh_spin = QSpinBox()
        sb_thresh_spin.setRange(5, 200)
        sb_thresh_spin.setSuffix(" px")
        sb_thresh_spin.setValue(cf.get("snapback_threshold", 15))
        sb_thresh_spin.setToolTip(
            "If the cursor moves this many pixels unexpectedly (user grabbed mouse),\n"
            "the lock pauses briefly. Set very high (200) to disable snap-back detection."
        )
        sb_thresh_spin.valueChanged.connect(
            lambda v: self._on_change("cursor_follow", "snapback_threshold", v)
        )
        form.addRow("Snap-back threshold:", sb_thresh_spin)

        sb_pause_spin = QSpinBox()
        sb_pause_spin.setRange(0, 2000)
        sb_pause_spin.setSuffix(" ms")
        sb_pause_spin.setValue(cf.get("snapback_pause_ms", 200))
        sb_pause_spin.setToolTip("How long the lock pauses after snap-back is detected")
        sb_pause_spin.valueChanged.connect(
            lambda v: self._on_change("cursor_follow", "snapback_pause_ms", v)
        )
        form.addRow("Snap-back pause:", sb_pause_spin)

        return grp

    # ------------------------------------------------------------------
    # Triggerbot
    # ------------------------------------------------------------------

    def _build_triggerbot_group(self) -> QGroupBox:
        grp = QGroupBox("Triggerbot")
        form = QFormLayout(grp)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        tb = self._config.get("triggerbot", {})

        enabled_chk = QCheckBox("Enable triggerbot")
        enabled_chk.setChecked(tb.get("enabled", False))
        enabled_chk.toggled.connect(lambda v: self._on_change("triggerbot", "enabled", v))
        form.addRow("", enabled_chk)

        hotkey_edit = QLineEdit(tb.get("hotkey", "F5"))
        hotkey_edit.setMaximumWidth(70)
        hotkey_edit.setToolTip("Hotkey to toggle triggerbot on/off at runtime")
        hotkey_edit.textChanged.connect(lambda v: self._on_change("triggerbot", "hotkey", v))
        form.addRow("Toggle hotkey:", hotkey_edit)

        delay_min_spin = QSpinBox()
        delay_min_spin.setRange(0, 500)
        delay_min_spin.setSuffix(" ms")
        delay_min_spin.setValue(tb.get("delay_min_ms", 50))
        delay_min_spin.setToolTip("Minimum random delay before the click fires")
        delay_min_spin.valueChanged.connect(
            lambda v: self._on_change("triggerbot", "delay_min_ms", v)
        )
        form.addRow("Min delay:", delay_min_spin)

        delay_max_spin = QSpinBox()
        delay_max_spin.setRange(0, 1000)
        delay_max_spin.setSuffix(" ms")
        delay_max_spin.setValue(tb.get("delay_max_ms", 120))
        delay_max_spin.valueChanged.connect(
            lambda v: self._on_change("triggerbot", "delay_max_ms", v)
        )
        form.addRow("Max delay:", delay_max_spin)

        padding_spin = QSpinBox()
        padding_spin.setRange(0, 50)
        padding_spin.setSuffix(" px")
        padding_spin.setValue(tb.get("padding", 5))
        padding_spin.setToolTip("Extra pixels outside the bbox edge that still count as 'inside'")
        padding_spin.valueChanged.connect(
            lambda v: self._on_change("triggerbot", "padding", v)
        )
        form.addRow("Bbox padding:", padding_spin)

        return grp

    # ------------------------------------------------------------------
    # Hotkeys
    # ------------------------------------------------------------------

    def _build_hotkeys_group(self) -> QGroupBox:
        grp = QGroupBox("Hotkeys")
        form = QFormLayout(grp)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        hk = self._config.get("hotkeys", {})

        fields = [
            ("detection_toggle", "Detection on/off:", "F6"),
            ("lock_toggle",      "Lock toggle:",      "F7"),
            ("lock_hold",        "Lock hold:",        "F4"),
        ]
        for key, label, default in fields:
            edit = QLineEdit(hk.get(key, default))
            edit.setMaximumWidth(70)
            edit.textChanged.connect(lambda v, k=key: self._on_change("hotkeys", k, v))
            form.addRow(label, edit)

        tb_key = self._config.get("triggerbot", {}).get("hotkey", "F5")
        note = QLabel(f"Triggerbot toggle: {tb_key}  (change in Triggerbot section)")
        note.setStyleSheet("color: gray; font-size: 10px;")
        form.addRow("", note)

        return grp

    # ------------------------------------------------------------------
    # Overlay
    # ------------------------------------------------------------------

    def _build_overlay_group(self) -> QGroupBox:
        grp = QGroupBox("Overlay")
        layout = QVBoxLayout(grp)
        ov = self._config.get("overlay", {})

        checkboxes = [
            ("show_boxes",          "Show bounding boxes"),
            ("show_trails",         "Show movement trails"),
            ("show_velocity",       "Show velocity arrows"),
            ("show_radius_circle",  "Show FOV circle"),
            ("show_minimap",        "Show mini-map"),
            ("show_direction_cone", "Show camera direction arrow on mini-map"),
        ]
        for key, label in checkboxes:
            chk = QCheckBox(label)
            chk.setChecked(ov.get(key, True))
            chk.toggled.connect(lambda v, k=key: self._on_change("overlay", k, v))
            layout.addWidget(chk)

        return grp

    # ------------------------------------------------------------------
    # Profiles
    # ------------------------------------------------------------------

    def _build_profiles_group(self) -> QGroupBox:
        grp = QGroupBox("Saved Profiles  (save/load full config snapshots)")
        form = QFormLayout(grp)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self._profile_combo = QComboBox()
        self._populate_profiles()
        form.addRow("Profile:", self._profile_combo)

        btn_row = QHBoxLayout()
        load_btn = QPushButton("Load")
        load_btn.clicked.connect(self._on_profile_load)
        btn_row.addWidget(load_btn)
        save_btn = QPushButton("Save As…")
        save_btn.clicked.connect(self._on_profile_save)
        btn_row.addWidget(save_btn)
        del_btn = QPushButton("Delete")
        del_btn.clicked.connect(self._on_profile_delete)
        btn_row.addWidget(del_btn)
        ref_btn = QPushButton("↺")
        ref_btn.setFixedWidth(28)
        ref_btn.clicked.connect(self._populate_profiles)
        btn_row.addWidget(ref_btn)
        form.addRow("", btn_row)

        return grp

    # ------------------------------------------------------------------
    # Lock toggle button
    # ------------------------------------------------------------------

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
    # Aimlock preset
    # ------------------------------------------------------------------

    def _on_preset_selected(self, name: str) -> None:
        if name.startswith("—"):
            return
        presets = self._config.get("presets", {})
        preset = presets.get(name)
        if not preset:
            return

        mapping = {
            "smoothing":     (self._smooth_slider, self._smooth_label,
                              lambda v: int(v * 100), lambda v: f"{v:.2f}"),
            "speed":         (self._speed_slider,  self._speed_label,
                              lambda v: int(v * 100), lambda v: f"{v:.2f}x"),
            "follow_radius": (self._radius_slider, self._radius_label,
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

    def _sync_key_widgets(self) -> None:
        """Update the main slider/combo widgets to reflect current config values."""
        cf = self._config.get("cursor_follow", {})

        sv = cf.get("smoothing", 0.12)
        self._smooth_slider.blockSignals(True)
        self._smooth_slider.setValue(int(sv * 100))
        self._smooth_label.setText(f"{sv:.2f}")
        self._smooth_slider.blockSignals(False)

        spv = cf.get("speed", 1.0)
        self._speed_slider.blockSignals(True)
        self._speed_slider.setValue(int(spv * 100))
        self._speed_label.setText(f"{spv:.2f}x")
        self._speed_slider.blockSignals(False)

        rv = cf.get("follow_radius", 150)
        self._radius_slider.blockSignals(True)
        self._radius_slider.setValue(rv)
        self._radius_label.setText(f"{rv}px")
        self._radius_slider.blockSignals(False)

        self._fp_combo.blockSignals(True)
        self._fp_combo.setCurrentText(cf.get("follow_point", "chest"))
        self._fp_combo.blockSignals(False)

        self._pred_spin.blockSignals(True)
        self._pred_spin.setValue(cf.get("prediction_ms", 60))
        self._pred_spin.blockSignals(False)

    # ------------------------------------------------------------------
    # Profile callbacks
    # ------------------------------------------------------------------

    def _on_profile_load(self) -> None:
        name = self._profile_combo.currentText()
        if not name:
            return
        try:
            from profiles import load_profile
            data = load_profile(name)
            for section, vals in data.items():
                if isinstance(vals, dict):
                    for key, value in vals.items():
                        self._on_change(section, key, value)
            self._sync_key_widgets()
            self._status_bar.showMessage(f"Profile '{name}' loaded.", 3000)
        except Exception as exc:
            self._status_bar.showMessage(f"Load failed: {exc}", 4000)

    def _on_profile_save(self) -> None:
        name, ok = QInputDialog.getText(self, "Save Profile", "Profile name:")
        if not ok or not name.strip():
            return
        try:
            from profiles import save_profile
            save_profile(name.strip(), self._config)
            self._populate_profiles()
            self._status_bar.showMessage(f"Profile '{name.strip()}' saved.", 3000)
        except Exception as exc:
            self._status_bar.showMessage(f"Save failed: {exc}", 4000)

    def _on_profile_delete(self) -> None:
        name = self._profile_combo.currentText()
        if not name:
            return
        reply = QMessageBox.question(
            self, "Delete Profile", f"Delete profile '{name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            try:
                from profiles import delete_profile
                delete_profile(name)
                self._populate_profiles()
                self._status_bar.showMessage(f"Profile '{name}' deleted.", 3000)
            except Exception as exc:
                self._status_bar.showMessage(f"Delete failed: {exc}", 4000)

    # ------------------------------------------------------------------
    # Slider callbacks
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
            onnx_files = sorted(f for f in os.listdir(models_dir) if f.endswith(".onnx"))
            self._model_combo.addItems(onnx_files)
        if current:
            self._model_combo.setCurrentText(current)
        self._model_combo.blockSignals(False)

    def _on_model_changed(self, filename: str) -> None:
        if not filename:
            return
        self._on_change("detection", "model", os.path.join("models", filename))

    def _populate_windows(self) -> None:
        current = self._window_combo.currentText()
        self._window_combo.blockSignals(True)
        self._window_combo.clear()
        self._window_combo.addItems(_get_window_titles())
        self._window_combo.setCurrentText(current)
        self._window_combo.blockSignals(False)

    def _populate_profiles(self) -> None:
        self._profile_combo.blockSignals(True)
        self._profile_combo.clear()
        try:
            from profiles import list_profiles
            self._profile_combo.addItems(list_profiles())
        except Exception:
            pass
        self._profile_combo.blockSignals(False)

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
            det  = "DET:ON"  if s.get("detection_active", True)  else "DET:OFF"
            lck  = "LOCK:ON" if s.get("follow_active", False)     else "LOCK:OFF"
            model = os.path.basename(self._config.get("detection", {}).get("model", ""))
            tid  = s.get("target_id")
            conf = self._config.get("detection", {}).get("confidence", 0)
            msg = (
                f"{det}  {lck}  "
                f"Cap {s.get('fps_capture', 0):.0f}fps  "
                f"Inf {s.get('fps_inference', 0):.0f}fps  "
                f"Tracks:{s.get('track_count', 0)}  "
                f"Target:{'#'+str(tid) if tid is not None else '-'}  "
                f"Conf:{conf:.2f}  [{model}]"
            )
            self._status_bar.showMessage(msg)
            self._follow_btn.blockSignals(True)
            self._follow_btn.setChecked(s.get("follow_active", False))
            self._follow_btn.blockSignals(False)
        except Exception:
            pass
