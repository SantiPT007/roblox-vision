# Changelog

All notable changes to this project are documented here.

---

## [Unreleased] — experimental branch

### Added

#### Detection & AI
- **Depth estimation** — every tracked character now gets a `depth_score` (0–1) based on bounding box area. Displayed as a bar at the bottom of each box and shown as `d:XX%` in the label. Used by the new depth-priority target selection option.
- **Team color detection** *(experimental)* — samples a pixel strip above each bounding box to classify the character's team by hue. Draws a small colored square in the top-left of each box. Enable with `detection.team_detection: true`.
- **Auto-confidence tuning** — rolls a window of recent detection counts and nudges the confidence threshold up or down to stay near a configured target count. Configurable floor/ceiling (`auto_conf_min`, `auto_conf_max`, `auto_conf_target`).

#### Mouse Lock
- **Smoothing curves** — three options: `linear` (original), `exponential` (ease-in, scales down near target to prevent overshoot), `bezier` (smoothstep S-curve). Selectable per-preset and in the GUI.
- **Depth-priority targeting** — new `prefer_closest_depth` option selects the character with the largest bounding box (nearest in 3D) instead of nearest to cursor.
- **Snap-back prevention** — records expected cursor position after each move. If the cursor deviates by more than `snapback_threshold` pixels (user grabbed the mouse), the lock pauses for `snapback_pause_ms` milliseconds.

#### Triggerbot
- Auto-clicks when the cursor is inside a detected character's bounding box (plus configurable padding).
- Random delay between `delay_min_ms` and `delay_max_ms` for natural timing.
- Runtime toggle via hotkey (default **F5**), configurable.

#### Recoil Compensation
- Steps through a `[dx, dy]` pattern while the configured fire key is held.
- Positive `dy` = push mouse down = counteracts upward gun kick.
- Configurable step interval (`step_ms`) and reset delay (`reset_ms`).
- **5 recoil pattern presets** selectable from the GUI: Light, Medium, Heavy Spray, AR Pattern, SMG Burst.

#### Overlay
- **Camera direction cone** — estimates which way the camera is rotating by averaging velocities of all moving tracks. Draws a yellow arrow from the mini-map center.
- Bounding box labels now include depth percentage (`d:XX%`).
- Team color indicator (small colored square) shown on boxes when team detection is enabled.
- Depth bar shown at the bottom of each bounding box.

#### Profiles & Presets
- **Profile system** (`profiles.py`) — save, load, and delete full config snapshots as named YAML files in `profiles/`.
- **8 built-in profiles**: Aggressive, Stealth, FPS Competitive, Sniper, Spray Control, Close Quarters, Passive Observer, Dev Testing.
- Aimlock presets updated: all now include `smoothing_curve` setting.
- Recoil pattern presets added to `config.yaml` under `recoil_presets`.

#### System
- **Performance dashboard** (`perf_monitor.py`) — floating semi-transparent widget showing CPU%, RAM%, GPU%, VRAM used/total (requires `pynvml`), capture FPS, inference FPS, track count, and current target. Toggle from the GUI.
- New dependencies: `psutil`, `pynvml` (both optional — graceful fallback if missing).

#### GUI
- **Profiles group** — load/save/delete named profiles from the control panel.
- **Recoil group** — new section with enable toggle, fire key, step interval, reset delay, pattern preset selector.
- **Triggerbot group** — new section with enable toggle, hotkey, delay range, bbox padding.
- **Smoothing curve selector** in Mouse Lock section.
- **Snap-back settings** (threshold + pause) in Mouse Lock section.
- **Depth preference checkbox** in Mouse Lock section.
- **Team detection checkbox** in Detection section (marked experimental).
- **Auto-confidence** settings simplified: single "Target detections" spinbox; floor/ceiling configured in `config.yaml`.
- **Toggle Perf Dashboard** button.
- Scroll area wrapping — panel no longer overflows on small screens.
- Status bar now shows current confidence value.

### Changed
- `config.yaml` restructured: added `recoil`, `triggerbot`, `perf_dashboard`, `recoil_presets` sections. Aimlock presets updated with `smoothing_curve` key.
- `tracker.py` — enriched track dicts now include `depth_score` and `team_color` fields.
- `detector.py` — added auto-confidence rolling adjustment logic.
- `cursor.py` — full rewrite: smoothing curves, recoil, triggerbot, snap-back all integrated into the main 120 Hz loop.
- `overlay.py` — direction cone, depth bars, team color indicators, box labels updated.
- `download_model.py` — workspace and project are now CLI arguments instead of hardcoded values.

### Fixed
- Recoil pattern `dy` values corrected to positive (push mouse down = counter upward recoil). Previous negative values caused the mouse to move in the wrong direction.

### Removed
- Multi-monitor support removed (overlay always uses primary screen). May be re-added in a future release.

---

## [1.0.0] — 2024 — master branch (stable)

Initial public release.

- YOLOv8 + ByteTrack real-time character detection and tracking
- Transparent always-on-top overlay (bounding boxes, trails, velocity arrows, mini-map, FOV circle)
- Normal and FPS mouse lock modes with proportional smoothing and velocity prediction
- Configurable hotkeys (F6 detection toggle, F7 lock toggle, F4 hold-to-lock)
- Live settings GUI with preset support
- YAML config with save-to-disk
- DXGI GPU screen capture via dxcam
- Three bundled models: `yolov8n-default.pt`, `roblox-character-mid.pt`, `rivals-best.pt`
- Roboflow model downloader utility
