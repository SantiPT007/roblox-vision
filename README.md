# Character Tracker

Real-time AI character detection and tracking overlay for Windows, built for Roblox.

Captures game footage via DXGI Desktop Duplication (GPU), detects characters with YOLOv8, tracks them with ByteTrack, and renders a transparent always-on-top overlay — all without touching game memory.

---

## Features

- **Real-time detection** — YOLOv8 running on CUDA, ~60 fps inference
- **Multi-object tracking** — ByteTrack assigns persistent IDs across frames
- **Transparent overlay** — bounding boxes, motion trails, velocity arrows, mini-map, FOV circle
- **Cursor follow** — optional mouse lock onto detected targets
  - Normal mode: proportional smoothing with position prediction
  - FPS mode: low-gain proportional movement for camera-controlled games, no prediction
- **Multiple models** — swap between YOLO `.pt` models live from the GUI
- **Class filter** — lock only on specific classes (e.g. enemies-only with `rivals.pt`)
- **Hotkeys** — toggle detection (F6), toggle lock (F7), hold-to-lock (F4)
- **Settings GUI** — live config edits with save-to-disk

---

## Requirements

- Windows 10/11 (64-bit)
- Python 3.11+
- NVIDIA GPU with CUDA 12.8 recommended (CPU fallback works but is slow)
- DirectX 11/12 capable display (for dxcam screen capture)

---

## Installation

```bat
install.bat
```

Runs as Administrator, detects your Python installation, and installs all dependencies including PyTorch (CUDA 12.8 build, ~2 GB download).

> **Different CUDA version?** Edit `install.bat` and change `cu128` to match your driver (e.g. `cu121` for CUDA 12.1). Check with `nvidia-smi`.

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

## Configuration

Edit `config.yaml` before launching. All settings can also be changed live in the GUI.

### `capture`

| Key | Default | Description |
|-----|---------|-------------|
| `target_window` | `"Roblox"` | Window title to capture |
| `fps_cap` | `60` | Target capture frame rate |
| `region` | `null` | `null` = full window. Or `[x, y, w, h]` for sub-region |

### `detection`

| Key | Default | Description |
|-----|---------|-------------|
| `model` | `"models/yolov8n.pt"` | Path to YOLO weights inside `models/` |
| `confidence` | `0.15` | Detection confidence threshold |
| `nms_iou` | `0.45` | NMS IoU threshold |
| `detect_all_classes` | `false` | `false` = COCO person only; `true` = all classes (for custom models) |
| `device` | `"cuda"` | `"cuda"` or `"cpu"` |

### `tracking`

| Key | Default | Description |
|-----|---------|-------------|
| `max_age` | `30` | Frames before dropping a lost track |
| `min_hits` | `2` | Detections required before confirming a new track |

### `cursor_follow`

| Key | Default | Description |
|-----|---------|-------------|
| `enabled` | `false` | Start with follow enabled |
| `fps_mode` | `false` | FPS mode for camera-controlled games |
| `smoothing` | `0.12` | Movement smoothing (0.0 = instant, 1.0 = no movement) |
| `speed` | `1.0` | Movement speed multiplier |
| `follow_radius` | `150` | Pixel radius around cursor to consider targets |
| `follow_point` | `"chest"` | Aim point: `"head"`, `"chest"`, or `"center"` |
| `prediction_ms` | `60` | Milliseconds to extrapolate target position (normal mode only) |
| `deadzone` | `5` | No correction within this pixel radius (FPS mode only) |
| `target_class` | `null` | `null` = any class; `1` = enemies only with `rivals.pt` |

### `hotkeys`

| Key | Default | Description |
|-----|---------|-------------|
| `detection_toggle` | `"F6"` | Toggle detection on/off |
| `lock_toggle` | `"F7"` | Toggle mouse lock on/off |
| `lock_hold` | `"F4"` | Hold to lock, release to stop |

### `overlay`

| Key | Default | Description |
|-----|---------|-------------|
| `enabled` | `true` | Show/hide overlay |
| `show_boxes` | `true` | Bounding boxes and ID labels |
| `show_trails` | `true` | Fading motion trails |
| `show_velocity` | `true` | Velocity direction arrows |
| `show_radius_circle` | `true` | FOV circle (at cursor in normal mode, center in FPS mode) |
| `show_minimap` | `true` | Scaled mini-map (bottom-right) |
| `trail_length` | `20` | Historical positions in trail |
| `active_color` | `[0, 255, 80]` | RGB color for the locked target |
| `inactive_color` | `[0, 200, 255]` | RGB color for other tracks |

---

## Models

Place `.pt` files in the `models/` folder. The GUI model dropdown auto-scans this folder.

| Model | Classes | Notes |
|-------|---------|-------|
| `yolov8n-default.pt` | COCO person (class 0) | General purpose baseline |
| `roblox-character-mid.pt` | 1 class: character | Trained on 6k+ Roblox images |
| `rivals-best.pt` | 2 classes: `friendly` (0), `roblox avatar` (1) | Set `detect_all_classes: true` and enable "Enemies only" in GUI |

When using custom models, enable **Detect all classes** in the GUI (or set `detect_all_classes: true` in config).

---

## Project Structure

```
charactertracker/
├── main.py            # Entry point, thread setup
├── capture.py         # dxcam screen capture thread
├── detector.py        # YOLOv8 inference (async, CUDA)
├── tracker.py         # ByteTrack wrapper with trail/velocity enrichment
├── cursor.py          # Mouse lock (Win32 SendInput)
├── overlay.py         # PyQt6 transparent overlay
├── gui.py             # Settings control panel
├── config.yaml        # User configuration
├── models/            # YOLO model weights (.pt files)
├── install.bat        # Dependency installer
└── start.bat          # Launcher
```

### Thread layout

```
Main thread (Qt event loop)
    ├── CaptureThread     dxcam DXGI → frame_queue (~60 fps)
    ├── FrameBridge       frame_queue bridge (daemon)
    ├── InferenceThread   YOLOv8 + ByteTrack → shared state (async)
    ├── CursorThread      shared state → Win32 SendInput (~120 Hz)
    ├── Overlay           transparent QPainter overlay (Qt, ~60 fps)
    └── ControlPanel      settings GUI (Qt)
```

---

## Troubleshooting

**Black frames / dxcam fails**
- Ensure the target window is not minimized and is on the primary display.
- Use borderless windowed mode; fullscreen-exclusive can block DXGI capture.
- Run as Administrator.

**CUDA not detected**
- Check with `python -c "import torch; print(torch.cuda.is_available())"`.
- Reinstall PyTorch with the correct CUDA index URL for your driver version.

**Hotkeys not working**
- Global hotkeys require Administrator privileges. Use `start.bat` (auto-elevates).

---

## Notes

- This tool uses **screen capture only** — it never calls `ReadProcessMemory` or any process introspection API.
- Admin privileges are required for global hotkeys (`keyboard` library requirement on Windows).
- The overlay is fully click-through and will not interfere with mouse input to the game.
- Check the terms of service of any online game before use.