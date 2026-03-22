# Changelog

All notable changes to this project are documented here.

---

## [3.0.0] — 2026-03-22

Complete overhaul. Dropped PyTorch/CUDA in favour of ONNX Runtime with DirectML, ported aiming improvements from Axiom AI, removed recoil compensation, auto-confidence tuning, background subtraction, team color detection, and all-classes mode. Restructured to a clean multi-threaded pipeline.

### Added
- **ONNX Runtime + DirectML** — GPU-accelerated inference on any DirectX 12 GPU, no CUDA or PyTorch required.
- **PID-controlled mouse lock** — smooth, overshoot-free movement via `pid_controller.py`.
- **SmartTracker** (`smart_tracker.py`) — velocity prediction with direction-change aware smoothing; replaces linear extrapolation.
- **Snap-back prevention** — records expected cursor position after each move; pauses the lock when user grabs the mouse.
- **Aim Y-reduce** — suppresses vertical correction after the cursor has been locked on a target for a configurable number of seconds (`aim_y_reduce`, `aim_y_reduce_delay`).
- **Configurable aim point offset** (`head_height_ratio`) — fine-tune exactly how far down the bounding box the head aim point sits.
- **Triggerbot** — auto-clicks when cursor is inside a detected bbox; configurable random delay; runtime toggle hotkey.
- **Aimlock presets** — Flick, Precise, Smooth, FPS Flick, FPS Smooth, FPS Gentle; apply from Mouse Lock section in GUI.
- **Full config profile system** (`profiles.py`) — save/load/delete named YAML snapshots in `profiles/`.
- **Performance dashboard** (`perf_monitor.py`) — floating widget: CPU%, RAM%, GPU%, VRAM, capture FPS, inference FPS, track count, current target.
- **Camera direction cone** — averages object velocities to estimate camera rotation; draws directional indicator on mini-map.
- **Depth estimation** — `depth_score` per track (bbox area heuristic); shown as depth bar and `d:XX%` label on overlay.
- **Hot model swap** — reload `.onnx` model at runtime from the GUI without restart.
- **Live config propagation** — all GUI changes take effect immediately in running threads.
- `_sleep_precise()` helper — sub-2ms sleep with spin-window for high-frequency cursor loop.
- `_set_windows_timer_resolution_1ms()` — enables 1ms system timer resolution for the lifetime of the process.
- Five new built-in profiles: `Precision`, `Aggressive`, `Smooth`, `FPS Competitive`, `Passive`.

### Changed
- `detector.py` fully rewritten for pure ONNX inference; async `ThreadPoolExecutor` for non-blocking frame submission; DirectML provider with CPU fallback.
- `cursor.py` rewritten: PID + SmartTracker + triggerbot + snap-back + aim Y-reduce in a single 120 Hz loop. Target selection no longer filters by class.
- `main.py`: `Detector` and `Tracker` now imported at module level (main thread) to satisfy Windows DLL init restrictions; `inference_loop` removed lazy imports.
- `config.yaml` restructured: removed `recoil`, `device`, `detect_all_classes`, `auto_confidence`, `smoothing_curve`, `target_class`, `team_detection`, `use_background_subtraction` keys; added `triggerbot`, `perf_dashboard`, `pid`, `tracker`, `head_height_ratio`, `aim_y_reduce`, `aim_y_reduce_delay` keys; `presets` section covers aimlock only.
- `gui.py`: removed recoil tab, auto-confidence, all-classes mode, team detection, enemy-only filter, background subtraction, device selector; model picker now filters `.onnx` only; added `head_height_ratio` spin box and `aim_y_reduce` controls; fixed minimum window dimensions.
- `download_model.py` now downloads ONNX format directly instead of `.pt`.
- `install.bat` installs `onnxruntime-directml` instead of PyTorch; simplified to 4 steps.
- `start.bat` no longer requests elevation — `SendInput` and `keyboard` hooks work without administrator rights. Merges user PATH explicitly to ensure packages installed to the user profile are found.
- `requirements.txt` — replaced `torch`, `ultralytics`, `torch-directml` with `onnxruntime-directml`; replaced `pynvml` with `nvidia-ml-py`.
- `README.md` rewritten with badges, visual sections, collapsible config tables, and project structure.

### Removed
- PyTorch / CUDA / ultralytics dependency — fully replaced by `onnxruntime-directml`.
- Recoil compensation module and all recoil config keys.
- Background subtraction (MOG2 pre-filter).
- Auto-confidence tuning.
- Team color detection (pixel strip heuristic).
- All-classes / enemy-only detection mode.
- Smoothing curve selector (`linear` / `exponential` / `bezier`) — replaced by single `smoothing` factor with PID.
- `detect_all_classes`, `device`, `target_class`, `auto_confidence`, `team_detection` config keys.
- `.pt` model support — only `.onnx` files are accepted.
- Nine old built-in profiles replaced by five new ones.

### Fixed
- `IndentationError` in `detector.py` module docstring (leading whitespace before `"""`).
- DLL initialization failure when running under UAC elevation — root cause was Windows stripping user PATH in elevated context; fix: removed elevation from `start.bat` + moved ONNX imports to main thread.

---

## [2.0.0] — 2025 — experimental branch

### Added

#### Detection & AI
- **Depth estimation** — every tracked character now gets a `depth_score` (0–1) based on bounding box area. Displayed as a bar at the bottom of each box and shown as `d:XX%` in the label.
- **Team color detection** *(experimental)* — samples a pixel strip above each bounding box to classify the character's team by hue.
- **Auto-confidence tuning** — rolls a window of recent detection counts and nudges the confidence threshold up or down to stay near a configured target count.

#### Mouse Lock
- **Smoothing curves** — three options: `linear`, `exponential`, `bezier`.
- **Depth-priority targeting** — `prefer_closest_depth` option.
- **Snap-back prevention** — `snapback_threshold` and `snapback_pause_ms`.

#### Triggerbot
- Auto-clicks when the cursor is inside a detected character's bounding box (plus configurable padding).
- Random delay between `delay_min_ms` and `delay_max_ms`.
- Runtime toggle via hotkey (default **F5**).

#### Recoil Compensation
- Steps through a `[dx, dy]` pattern while the configured fire key is held.
- Configurable step interval (`step_ms`) and reset delay (`reset_ms`).
- 5 recoil pattern presets: Light, Medium, Heavy Spray, AR Pattern, SMG Burst.

#### Overlay
- Camera direction cone on mini-map.
- Depth bar and depth percentage on bounding box labels.
- Team color indicator on boxes.

#### Profiles & Presets
- Profile system (`profiles.py`) — save/load/delete full config snapshots.
- 8 built-in profiles: Aggressive, Stealth, FPS Competitive, Sniper, Spray Control, Close Quarters, Passive Observer, Dev Testing.

#### System
- Performance dashboard (`perf_monitor.py`).

### Changed
- `config.yaml` added `recoil`, `triggerbot`, `perf_dashboard`, `recoil_presets` sections.
- `tracker.py` enriched track dicts with `depth_score` and `team_color`.
- `detector.py` added auto-confidence rolling adjustment.
- `cursor.py` full rewrite: smoothing curves, recoil, triggerbot, snap-back in 120 Hz loop.

### Fixed
- Recoil pattern `dy` values corrected to positive (push mouse down = counter upward recoil).

### Removed
- Multi-monitor overlay support.

---

## [1.0.0] — 2024 — initial release

- YOLOv8 + ByteTrack real-time character detection and tracking
- Transparent always-on-top overlay (bounding boxes, trails, velocity arrows, mini-map, FOV circle)
- Normal and FPS mouse lock modes with proportional smoothing and velocity prediction
- Configurable hotkeys (F6 detection toggle, F7 lock toggle, F4 hold-to-lock)
- Live settings GUI with preset support
- YAML config with save-to-disk
- DXGI GPU screen capture via dxcam
- Three bundled models: `yolov8n-default.pt`, `roblox-character-mid.pt`, `rivals-best.pt`
- Roboflow model downloader utility
