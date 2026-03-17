"""
download_model.py — Downloads the roblox-character YOLOv8 model from Roboflow.

Usage:
    python download_model.py YOUR_API_KEY

Get your free API key at: https://app.roboflow.com/settings/api
"""

import sys
import os
import shutil

def main():
    if len(sys.argv) < 2:
        print("Usage: python download_model.py YOUR_API_KEY")
        print("Get your free key at: https://app.roboflow.com/settings/api")
        sys.exit(1)

    api_key = sys.argv[1].strip()

    try:
        from roboflow import Roboflow
    except ImportError:
        print("Installing roboflow package...")
        import subprocess
        subprocess.check_call([sys.executable, "-m", "pip", "install", "roboflow"])
        from roboflow import Roboflow

    print("Connecting to Roboflow...")
    rf = Roboflow(api_key=api_key)
    project = rf.workspace("nicolas-hard").project("roblox-character")

    # Get latest version
    versions = project.versions()
    latest = versions[-1] if versions else project.version(1)
    print(f"Downloading version {latest.version}...")

    # Download to a temp folder then move the .pt into models/
    latest.download("yolov8", location="rf_download")

    # Find the .pt file
    pt_file = None
    for root, dirs, files in os.walk("rf_download"):
        for f in files:
            if f.endswith(".pt"):
                pt_file = os.path.join(root, f)
                break
        if pt_file:
            break

    if pt_file is None:
        print("ERROR: No .pt file found in download. Check rf_download/ folder manually.")
        sys.exit(1)

    dest = os.path.join("models", "roblox-character.pt")
    shutil.copy2(pt_file, dest)
    shutil.rmtree("rf_download", ignore_errors=True)

    print(f"\nDone! Model saved to: {dest}")
    print("In the app: select 'roblox-character.pt' from the Model dropdown")
    print("and check 'All classes' since this is a custom model (not COCO class 0).")

if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    main()
