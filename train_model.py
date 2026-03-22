"""
train_model.py — Download the arsenaldetection dataset from Roboflow and
train a YOLOv8 model locally, then save the best weights to models/.

Usage:
    python train_model.py YOUR_API_KEY [--base yolov8n.pt] [--epochs 50] [--imgsz 640]

Example:
    python train_model.py abc123
    python train_model.py abc123 --base yolov8s.pt --epochs 100

Get your free API key at: https://app.roboflow.com/settings/api
"""

import argparse
import os
import shutil
import sys

WORKSPACE    = "stormcph"
PROJECT      = "arsenaldetection"
OUTPUT_NAME  = "arsenal.pt"
DATASET_DIR  = "rf_dataset"


def install(pkg: str) -> None:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", pkg, "--quiet"])


def main() -> None:
    parser = argparse.ArgumentParser(description="Train YOLOv8 on arsenaldetection")
    parser.add_argument("api_key", help="Roboflow API key")
    parser.add_argument("--base",   default="yolov8n.pt",
                        help="Base YOLO weights to fine-tune (default: yolov8n.pt)")
    parser.add_argument("--epochs", type=int, default=50,
                        help="Training epochs (default: 50)")
    parser.add_argument("--imgsz",  type=int, default=640,
                        help="Input image size (default: 640)")
    parser.add_argument("--device", default="",
                        help="Device: '' = auto, 'cpu', '0' = first GPU")
    args = parser.parse_args()

    # ── dependencies ──────────────────────────────────────────────────────────
    try:
        from roboflow import Roboflow
    except ImportError:
        print("Installing roboflow...")
        install("roboflow")
        from roboflow import Roboflow

    try:
        from ultralytics import YOLO
    except ImportError:
        print("Installing ultralytics...")
        install("ultralytics")
        from ultralytics import YOLO

    # ── download dataset ───────────────────────────────────────────────────────
    print(f"\n[1/3] Connecting to Roboflow ({WORKSPACE}/{PROJECT})...")
    rf      = Roboflow(api_key=args.api_key)
    project = rf.workspace(WORKSPACE).project(PROJECT)
    versions = project.versions()
    latest   = versions[-1] if versions else project.version(1)
    print(f"      Downloading dataset version {latest.version} (YOLOv8 format)...")

    if os.path.exists(DATASET_DIR):
        shutil.rmtree(DATASET_DIR)

    dataset = latest.download("yolov8", location=DATASET_DIR)
    data_yaml = os.path.join(DATASET_DIR, "data.yaml")
    if not os.path.exists(data_yaml):
        # Some Roboflow exports nest one level deeper
        for root, dirs, files in os.walk(DATASET_DIR):
            if "data.yaml" in files:
                data_yaml = os.path.join(root, "data.yaml")
                break

    if not os.path.exists(data_yaml):
        print("ERROR: data.yaml not found in downloaded dataset.")
        sys.exit(1)

    print(f"      Dataset ready: {data_yaml}")

    # ── train ──────────────────────────────────────────────────────────────────
    print(f"\n[2/3] Training {args.base} for {args.epochs} epochs at {args.imgsz}px...")
    model   = YOLO(args.base)
    results = model.train(
        data=data_yaml,
        epochs=args.epochs,
        imgsz=args.imgsz,
        device=args.device or None,
        project="runs/train",
        name="arsenal",
        exist_ok=True,
    )

    # ── export best weights ────────────────────────────────────────────────────
    best_pt = os.path.join("runs", "train", "arsenal", "weights", "best.pt")
    if not os.path.exists(best_pt):
        print(f"ERROR: best.pt not found at {best_pt}. Check runs/train/arsenal/")
        sys.exit(1)

    os.makedirs("models", exist_ok=True)
    dest = os.path.join("models", OUTPUT_NAME)
    shutil.copy2(best_pt, dest)

    print(f"\n[3/3] Done! Model saved to: {dest}")
    print("      Export to ONNX before use: yolo export model=models/arsenal.pt format=onnx")
    print("      Then select 'arsenal.onnx' from the Model dropdown in the app.")

    # cleanup dataset
    shutil.rmtree(DATASET_DIR, ignore_errors=True)


if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    main()
