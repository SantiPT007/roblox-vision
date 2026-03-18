# roblox-vision

Real-time AI character detection, tracking, and overlay for Windows — built for Roblox.

Captures game footage via DXGI Desktop Duplication (GPU), runs YOLOv8 for character detection, ByteTrack for multi-object tracking, and renders a transparent always-on-top overlay. No memory reading, no process injection.

> **Branch guide**
> - `master` — stable release
> - `experimental` — active development, new features (may be unstable)

---

## Features

### Detection & Tracking
- **YOLOv8 inference** — CUDA-accelerated, ~60 fps on GPU, CPU fallback
- **ByteTrack** — persistent character IDs across frames
- **Depth estimation** — ranks targets by proximity using bounding box area
- **Team color detection** *(experimental)* — samples pixels above each bbox to detect team indicator hue
- **Auto-confidence tuning** — automatically adjusts detection threshold to stay near a target detection count
- **Background subtraction** — optional MOG2 pre-filter for static-camera scenes
- **Live model swap** — hot-reload any `.pt` file from the GUI without restart

### Overlay
- Bounding boxes with ID, confidence, and depth labels
- Fading motion trails
- Velocity direction arrows
- FOV circle (follows cursor in normal mode, screen center in FPS mode)
- Mini-map with scaled character positions
- Camera direction cone — estimates which way the camera is rotating
- Status indicators (detection on/off, lock on/off)
- FPS counters (capture + inference)

### Mouse Lock
- **Two modes** — Normal (visible cursor, proportional) and FPS (low-gain, deadzone, no prediction)
- **Smoothing curves** — `linear`, `exponential` (ease-in), `bezier` (S-curve)
- **Velocity prediction** — extrapolates target position forward in time
- **Depth-priority targeting** — prefer the closest (largest bbox) character over nearest-to-cursor
- **Snap-back prevention** — pauses lock when unexpected cursor movement is detected (user grabbed mouse)

### Triggerbot
- Auto-clicks when cursor is inside a detected bounding box
- Configurable random delay (min/max ms) for natural timing
- Runtime toggle via hotkey (default F5)

### Recoil Compensation
- Steps through a configurable `[dx, dy]` pattern while fire key is held
- Positive dy = push mouse down = counter upward gun kick
- Configurable step interval (match to weapon fire rate) and reset delay
- 5 built-in pattern presets: Light, Medium, Heavy Spray, AR Pattern, SMG Burst

### Profiles & Presets
- **Aimlock presets** — quick-apply cursor_follow settings (Flick, Precise, Smooth, FPS variants)
- **Recoil pattern presets** — swap recoil patterns from the GUI
- **Full config profiles** — save/load complete config snapshots as named YAML files (`profiles/`)
  - 7 built-in profiles: Aggressive, Stealth, FPS Competitive, Sniper, Spray Control, Close Quarters, Passive Observer, Dev Testing

### System
- **Performance dashboard** — floating widget showing CPU%, RAM%, GPU%, VRAM, pipeline FPS
- **Settings GUI** — live config edits, all changes propagate to running threads without restart
- **Save to disk** — persist settings to `config.yaml` at any time

---

## Requirements

- Windows 10/11 (64-bit)
- Python 3.11+
- NVIDIA GPU with CUDA 12.8 recommended (CPU fallback works but is slow)
- DirectX 11/12 capable display

---

## Installation

```bat
install.bat
```

Runs as Administrator, detects your Python installation, and installs all dependencies including PyTorch (CUDA 12.8, ~2 GB download).

> **Different CUDA version?** Edit `install.bat` and change `cu128` to match your driver (e.g. `cu121` for CUDA 12.1). Check your version with `nvidia-smi`.

---

## Usage

```bat
start.bat
```

Or directly:

```bash
python main.py
python main.py --no-overlay        # headless, no overlay window
python main.py --config my.yaml    # custom config file
```

---

## Hotkeys (defaults)

| Key | Action |
|-----|--------|
| F6 | Toggle detection on/off |
| F7 | Toggle mouse lock on/off |
| F4 | Hold to lock, release to stop |
| F5 | Toggle triggerbot on/off |

All hotkeys are configurable in the GUI or `config.yaml`.

---

## Configuration

All settings are in `config.yaml`. Everything can also be changed live in the GUI.

### `capture`

| Key | Default | Description |
|-----|---------|-------------|
| `target_window` | `"Roblox"` | Window title to capture (partial match) |
| `fps_cap` | `60` | Target capture frame rate |
| `region` | `null` | `null` = full window; `[x, y, w, h]` for sub-region |

### `detection`

| Key | Default | Description |
|-----|---------|-------------|
| `model` | `"models/yolov8n-default.pt"` | Path to YOLO weights |
| `confidence` | `0.15` | Detection confidence threshold |
| `nms_iou` | `0.45` | NMS IoU threshold |
| `device` | `"cuda"` | `"cuda"` or `"cpu"` |
| `detect_all_classes` | `false` | `false` = COCO person only; `true` = all (custom models) |
| `auto_confidence` | `false` | Auto-adjust threshold toward `auto_conf_target` |
| `auto_conf_target` | `3` | Desired detections per frame when auto-confidence is on |
| `auto_conf_min` | `0.08` | Auto-confidence floor |
| `auto_conf_max` | `0.60` | Auto-confidence ceiling |
| `team_detection` | `false` | *(experimental)* Detect team color from nametag pixels |

### `cursor_follow`

| Key | Default | Description |
|-----|---------|-------------|
| `enabled` | `false` | Start with lock enabled |
| `fps_mode` | `false` | FPS mode (camera-controlled games) |
| `smoothing` | `0.12` | 0.0 = instant, 1.0 = no movement |
| `smoothing_curve` | `"linear"` | `"linear"`, `"exponential"`, or `"bezier"` |
| `speed` | `1.0` | Movement speed multiplier |
| `follow_radius` | `150` | Pixel radius around cursor to activate locking |
| `follow_point` | `"chest"` | `"head"`, `"chest"`, or `"center"` |
| `prediction_ms` | `60` | ms to extrapolate target position (normal mode only) |
| `deadzone` | `5` | No correction within this radius (FPS mode only) |
| `target_class` | `null` | `null` = any class; `1` = enemies only (`rivals.pt`) |
| `prefer_closest_depth` | `false` | Prefer target with the largest bbox (nearest in 3D) |
| `snapback_threshold` | `15` | Pixels of unexpected movement that trigger a pause |
| `snapback_pause_ms` | `200` | ms to pause lock after snap-back detected |

### `recoil`

| Key | Default | Description |
|-----|---------|-------------|
| `enabled` | `false` | Enable recoil compensation |
| `fire_key` | `"left"` | `"left"` = LMB, `"right"` = RMB, or any key |
| `step_ms` | `80` | ms between pattern steps — match weapon fire rate |
| `reset_ms` | `600` | ms of inactivity before pattern resets to step 0 |
| `pattern` | `[...]` | List of `[dx, dy]` offsets. Positive dy = push mouse down |

### `triggerbot`

| Key | Default | Description |
|-----|---------|-------------|
| `enabled` | `false` | Enable triggerbot |
| `hotkey` | `"F5"` | Runtime toggle hotkey |
| `delay_min_ms` | `50` | Minimum random click delay |
| `delay_max_ms` | `120` | Maximum random click delay |
| `padding` | `5` | Extra pixels outside bbox edge that count as "inside" |

### `overlay`

| Key | Default | Description |
|-----|---------|-------------|
| `show_boxes` | `true` | Bounding boxes and labels |
| `show_trails` | `true` | Fading motion trails |
| `show_velocity` | `true` | Velocity arrows |
| `show_radius_circle` | `true` | FOV circle |
| `show_minimap` | `true` | Mini-map (bottom-right) |
| `show_direction_cone` | `true` | Camera direction arrow on mini-map |
| `trail_length` | `20` | Positions in trail history |
| `active_color` | `[0,255,80]` | RGB color for locked target |
| `inactive_color` | `[0,200,255]` | RGB color for other tracks |

---

## Models

Place `.pt` files in the `models/` folder. The GUI model dropdown auto-scans this folder on launch (use the ↺ button to refresh).

| Model | Classes | Notes |
|-------|---------|-------|
| `yolov8n-default.pt` | COCO person (class 0) | General baseline |
| `roblox-character-mid.pt` | 1 class | Trained on 6 000+ Roblox avatar images |
| `rivals-best.pt` | `friendly` (0), `roblox avatar` (1) | Enable "Enemies only" in GUI to lock class 1 only |

Enable **Detect all classes** in the GUI when using custom multi-class models.

To download your own Roboflow model:

```bash
python download_model.py YOUR_API_KEY YOUR_WORKSPACE YOUR_PROJECT
```

---

## Profiles

Saved profiles live in `profiles/` as YAML files. Load/save/delete from the GUI.

Built-in profiles:

| Profile | Description |
|---------|-------------|
| `Aggressive` | Max speed, head aim, triggerbot on, AR recoil |
| `Stealth` | Slow bezier movement, no automation, looks human |
| `FPS Competitive` | FPS mode, recoil on, no triggerbot |
| `Sniper` | Tight FOV, head aim, exponential curve, delayed triggerbot |
| `Spray Control` | Heavy recoil pattern, fast triggerbot, closest-target priority |
| `Close Quarters` | SMG pattern, huge FOV, max speed |
| `Passive Observer` | No lock/recoil/triggerbot — overlay + team detection only |
| `Dev Testing` | High confidence, all overlays, perf dashboard |

---

## Project Structure

```
roblox-vision/
├── main.py            # Entry point, thread orchestration
├── capture.py         # dxcam DXGI screen capture thread
├── detector.py        # YOLOv8 async inference + auto-confidence
├── tracker.py         # ByteTrack wrapper — trails, velocity, depth, team color
├── cursor.py          # Mouse lock, recoil, triggerbot, snap-back
├── overlay.py         # PyQt6 transparent overlay
├── gui.py             # Settings control panel
├── profiles.py        # Profile save/load/delete
├── perf_monitor.py    # Floating performance dashboard widget
├── config.yaml        # User configuration + presets
├── requirements.txt   # Python dependencies
├── download_model.py  # Roboflow model downloader utility
├── install.bat        # Automated dependency installer
├── start.bat          # Launcher (auto-elevates to Admin)
├── models/            # YOLO model weights (.pt files)
└── profiles/          # Saved config snapshots (.yaml files)
```

### Thread layout

```
Main thread (Qt event loop)
├── CaptureThread     dxcam DXGI → frame_queue (~60 fps)
├── FrameBridge       frame queue bridge (daemon)
├── InferenceThread   YOLOv8 + ByteTrack → SharedState (async)
├── CursorFollower    SharedState → Win32 SendInput @ 120 Hz
│                     (recoil, triggerbot, snap-back, smoothing curves)
├── CursorSync        syncs cursor state to SharedState (daemon)
├── Overlay           transparent QPainter overlay @ 60 fps
├── ControlPanel      settings GUI
└── PerfDashboard     optional floating perf widget
```

---

## Troubleshooting

**Black frames / dxcam fails**
- Ensure the window is not minimized and is on the primary display.
- Use borderless windowed mode — exclusive fullscreen blocks DXGI.
- Run as Administrator.

**CUDA not detected**
- Run: `python -c "import torch; print(torch.cuda.is_available())"`
- Reinstall PyTorch with the correct CUDA index URL for your driver.

**Hotkeys not working**
- Global hotkeys require Administrator privileges. Use `start.bat`.

**Recoil going the wrong way**
- The pattern uses positive `dy` = push mouse down. If your recoil still feels wrong, flip the dy signs in your pattern.

**Performance dashboard shows N/A for GPU**
- Install `pynvml`: `pip install pynvml`

---

## Notes

- Screen capture only — never calls `ReadProcessMemory` or any process introspection API.
- Admin privileges are required solely for global hotkeys (`keyboard` library limitation on Windows).
- The overlay is fully click-through and does not interfere with mouse input to the game.
- Check the terms of service of any game before use.
