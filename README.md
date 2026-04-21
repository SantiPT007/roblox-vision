<div align="center">

# 🎯 Roblox Vision

**Real-time AI character detection, tracking, and overlay for Roblox on Windows**

![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?style=flat-square&logo=python&logoColor=white)
![Platform](https://img.shields.io/badge/Platform-Windows%2010%2F11-0078D4?style=flat-square&logo=windows&logoColor=white)
![GPU](https://img.shields.io/badge/GPU-DirectX%2012%20%28DirectML%29-76B900?style=flat-square&logo=nvidia&logoColor=white)
![Runtime](https://img.shields.io/badge/Runtime-ONNX-FF6F00?style=flat-square)
![License](https://img.shields.io/badge/License-AGPL--3.0-green?style=flat-square)

*No process injection. No memory reading.*

</div>

---

## ✨ What it does

Captures the Roblox window via DXGI Desktop Duplication (GPU path), runs a YOLOv8 ONNX model for real-time character detection, tracks each character across frames with ByteTrack, and renders a fully transparent always-on-top overlay. All in a multi-threaded pipeline tuned for low latency.

---

## 🚀 Features

### 🔍 Detection & Tracking
| Feature | Details |
|---------|---------|
| **ONNX + DirectML inference** | GPU-accelerated on any DirectX 12 GPU — no CUDA required |
| **ByteTrack** | Persistent character IDs across frames, handles occlusion |
| **Depth estimation** | Ranks targets by approximate proximity (bounding box area) |
| **Hot model swap** | Reload any `.onnx` file from the GUI without restarting |

### 🖼️ Overlay
| Element | Details |
|---------|---------|
| Bounding boxes | ID, confidence, and depth label per character |
| Motion trails | Fading path showing recent movement |
| Velocity arrows | Live direction + speed indicator per track |
| FOV circle | Follows cursor (normal mode) or screen center (FPS mode) |
| Mini-map | Scaled character positions across the entire game view |
| Camera cone | Estimates which direction the camera is rotating |
| Status indicators | Detection on/off, lock on/off |

### 🖱️ Mouse Lock
| Feature | Details |
|---------|---------|
| **PID controller** | Smooth, overshoot-free movement (`pid_controller.py`) |
| **SmartTracker** | Velocity prediction with direction-change smoothing |
| **Normal + FPS modes** | Visible-cursor PID vs. camera-space low-gain with deadzone |
| **Depth-priority targeting** | Target the closest (largest bbox) character |
| **Snap-back prevention** | Pauses lock when user grabs the mouse |
| **Aim Y-reduce** | Suppresses vertical correction after a configurable delay |
| **Configurable aim point** | `head`, `chest`, or `center` with ratio offset |

### 🔫 Triggerbot
- Auto-clicks when cursor is inside a detected bounding box
- Configurable random delay (`delay_min_ms` / `delay_max_ms`) for natural timing
- Toggle on/off with a hotkey (default **F5**)
- Configurable hit padding in pixels

### 🗂️ Profiles & Presets
- **Aimlock presets** — Flick, Precise, Smooth, FPS Flick, FPS Smooth, FPS Gentle (apply in one click)
- **Full config profiles** — save/load/delete complete config snapshots as named YAML files

### 📊 System
- **Settings GUI** — live config edits propagate to all threads without restart
- **Performance dashboard** — floating widget: CPU%, RAM%, GPU%, VRAM, capture FPS, inference FPS, track count, active target
- **Save to disk** — persist your settings to `config.yaml` at any time

---

## 📋 Requirements

- **OS**: Windows 10 / 11 (64-bit)
- **Python**: 3.11 or newer
- **GPU**: DirectX 12 capable (required for DirectML; CPU fallback available)
- **No CUDA, no PyTorch, no administrator required to run**

---

## 📦 Installation

```bat
install.bat
```

Requests administrator once to install Python packages, then you never need to run as admin again. Installs `onnxruntime-directml`, PyQt6, OpenCV, dxcam, boxmot, and all other dependencies automatically.

---

## ▶️ Usage

```bat
start.bat
```

Or from a terminal:

```bash
python main.py
python main.py --no-overlay        # headless, no overlay window
python main.py --config my.yaml    # custom config file
```

---

## ⌨️ Hotkeys

| Key | Action |
|-----|--------|
| **F6** | Toggle character detection on/off |
| **F7** | Toggle mouse lock on/off |
| **F4** | Hold to lock, release to stop |
| **F5** | Toggle triggerbot on/off |

All hotkeys are fully configurable in the GUI or directly in `config.yaml`.

---

## ⚙️ Configuration

All settings live in `config.yaml`. Every setting can also be changed live in the GUI without restarting.

<details>
<summary><b>capture</b></summary>

| Key | Default | Description |
|-----|---------|-------------|
| `target_window` | `"Roblox"` | Window title to capture (partial match) |
| `fps_cap` | `60` | Target capture frame rate |
| `region` | `null` | `null` = full window; `[x, y, w, h]` for sub-region |

</details>

<details>
<summary><b>detection</b></summary>

| Key | Default | Description |
|-----|---------|-------------|
| `model` | `"models/Roblox.onnx"` | Path to ONNX model weights |
| `confidence` | `0.35` | Detection confidence threshold |
| `nms_iou` | `0.35` | NMS IoU threshold |

</details>

<details>
<summary><b>tracking</b></summary>

| Key | Default | Description |
|-----|---------|-------------|
| `max_age` | `30` | Frames before a lost track is dropped |
| `min_hits` | `2` | Minimum detections to confirm a track |

</details>

<details>
<summary><b>cursor_follow</b></summary>

| Key | Default | Description |
|-----|---------|-------------|
| `enabled` | `false` | Start with lock enabled |
| `fps_mode` | `false` | FPS mode for camera-controlled games |
| `smoothing` | `0.12` | 0.0 = instant snap, 1.0 = no movement |
| `speed` | `1.0` | Movement speed multiplier |
| `follow_radius` | `150` | Pixel radius around cursor to activate lock |
| `follow_point` | `"chest"` | `"head"`, `"chest"`, or `"center"` |
| `head_height_ratio` | `0.15` | 0.0–1.0, how far down the box the aim point sits |
| `prediction_ms` | `60` | ms to extrapolate target position forward |
| `deadzone` | `5` | No correction within this radius (FPS mode only) |
| `prefer_closest_depth` | `false` | Prefer the largest (nearest) character |
| `aim_y_reduce` | `false` | Suppress Y correction after locking for a delay |
| `aim_y_reduce_delay` | `0.6` | Seconds before Y correction is suppressed |
| `snapback_threshold` | `15` | Pixels of unexpected movement to trigger pause |
| `snapback_pause_ms` | `200` | ms to pause lock after snap-back detection |
| `pid.kp / ki / kd` | `0.4 / 0.0 / 0.08` | PID controller gains |

</details>

<details>
<summary><b>triggerbot</b></summary>

| Key | Default | Description |
|-----|---------|-------------|
| `enabled` | `false` | Enable triggerbot |
| `hotkey` | `"F5"` | Runtime toggle hotkey |
| `delay_min_ms` | `50` | Minimum random click delay |
| `delay_max_ms` | `120` | Maximum random click delay |
| `padding` | `5` | Extra pixels outside bbox that count as "inside" |

</details>

<details>
<summary><b>overlay</b></summary>

| Key | Default | Description |
|-----|---------|-------------|
| `show_boxes` | `true` | Bounding boxes + labels |
| `show_trails` | `true` | Fading motion trails |
| `show_velocity` | `true` | Velocity direction arrows |
| `show_radius_circle` | `true` | FOV circle |
| `show_minimap` | `true` | Mini-map (bottom-right corner) |
| `show_direction_cone` | `true` | Camera direction indicator |
| `trail_length` | `20` | Positions kept in trail history |
| `active_color` | `[0,255,80]` | RGB color for the locked target |
| `inactive_color` | `[0,200,255]` | RGB color for all other tracks |

</details>

---

## 🤖 Model

Place `.onnx` files in the `models/` folder. The GUI model picker auto-scans this folder on launch (hit **↺** to refresh).

### Download from Roboflow

```bash
python download_model.py YOUR_API_KEY YOUR_WORKSPACE YOUR_PROJECT
```

### Train a custom model

```bash
python train_model.py YOUR_API_KEY
# then export:
yolo export model=runs/detect/train/weights/best.pt format=onnx
```

---

## 🗂️ Profiles

Profiles are YAML files in the `profiles/` directory. Load, save, and delete them from the **Saved Profiles** section in the GUI.

Five presets are included: `Precision`, `Aggressive`, `Smooth`, `FPS Competitive`, `Passive`.

---

## 🏗️ Project Structure

```
character-tracker/
├── main.py             Entry point — thread orchestration + Qt event loop
├── capture.py          dxcam DXGI screen capture thread
├── detector.py         ONNX async inference (DirectML / CPU fallback)
├── tracker.py          ByteTrack wrapper — trails, velocity, depth
├── cursor.py           Mouse lock, PID, triggerbot, snap-back prevention
├── overlay.py          PyQt6 transparent always-on-top overlay
├── gui.py              Settings control panel
├── profiles.py         Profile save / load / delete
├── perf_monitor.py     Floating performance dashboard widget
├── pid_controller.py   PID controller for smooth aiming
├── smart_tracker.py    Velocity prediction with direction-change smoothing
├── config.yaml         User configuration + aimlock presets
├── requirements.txt    Python dependencies
├── download_model.py   Roboflow ONNX model downloader utility
├── train_model.py      YOLOv8 training utility
├── install.bat         Automated dependency installer
├── start.bat           Launcher (no elevation required)
├── models/             ONNX model weights (.onnx files go here)
└── profiles/           Saved config snapshots (.yaml files)
```

### Thread layout

```
Main thread  (Qt event loop)
├── CaptureThread      dxcam DXGI → frame_queue  (~60 fps)
├── FrameBridge        queue bridge               (daemon)
├── InferenceThread    ONNX + ByteTrack → SharedState
├── CursorFollower     SharedState → Win32 SendInput @ 120 Hz
│                      (PID, SmartTracker, triggerbot, snap-back)
├── CursorSync         sync cursor state → SharedState (daemon)
├── Overlay            transparent QPainter overlay @ 60 fps
├── ControlPanel       settings GUI
└── PerfDashboard      optional floating perf widget
```

---

## 🛠️ Troubleshooting

**Black frames / dxcam fails**
> Ensure the Roblox window is not minimized and is on the primary display. Use borderless windowed mode — exclusive fullscreen blocks DXGI.

**DirectML not detected / inference runs on CPU**
> Run `pip show onnxruntime-directml` to confirm it is installed. Make sure your GPU supports DirectX 12 (`dxdiag` → Display tab).

**Hotkeys not responding**
> The program does not need to run as administrator. If using a non-default hotkey that conflicts with another application, change it in `config.yaml`.

**GPU stats show N/A in the performance dashboard**
> Install `nvidia-ml-py`: `pip install nvidia-ml-py` (NVIDIA GPUs only).

---

## 🙏 Credits

Big thanks to **[Axiom AI](https://github.com/iisHong0w0/Axiom-AI-Aimbot)** — the `Roblox.onnx` character detection model used by this project comes from their repository.

---

## 📜 License

AGPL-3.0 — see [LICENSE](LICENSE) for details.

This project links against PyQt6 (GPL-3.0) and boxmot (AGPL-3.0), which require the combined work to be distributed under AGPL-3.0 or a compatible license.
