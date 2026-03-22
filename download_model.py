"""
download_model.py — Downloads a model from a Roboflow workspace and exports it to ONNX.

Usage:
    python download_model.py YOUR_API_KEY WORKSPACE PROJECT

Example:
    python download_model.py abc123 my-workspace roblox-character

Get your free API key at: https://app.roboflow.com/settings/api
"""

import sys
import os
import shutil

def main():
    if len(sys.argv) < 4:
        print("Usage: python download_model.py YOUR_API_KEY WORKSPACE PROJECT")
        print("Example: python download_model.py abc123 my-workspace roblox-character")
        print("Get your free key at: https://app.roboflow.com/settings/api")
        sys.exit(1)

    api_key   = sys.argv[1].strip()
    workspace = sys.argv[2].strip()
    project_name = sys.argv[3].strip()

    try:
        from roboflow import Roboflow
    except ImportError:
        print("Installing roboflow package...")
        import subprocess
        subprocess.check_call([sys.executable, "-m", "pip", "install", "roboflow"])
        from roboflow import Roboflow

    print("Connecting to Roboflow...")
    rf = Roboflow(api_key=api_key)
    project = rf.workspace(workspace).project(project_name)

    # Get latest version
    versions = project.versions()
    latest = versions[-1] if versions else project.version(1)
    print(f"Downloading version {latest.version}...")

    # Download ONNX format directly
    latest.download("onnx", location="rf_download")

    # Find the .onnx file
    onnx_file = None
    for root, dirs, files in os.walk("rf_download"):
        for f in files:
            if f.endswith(".onnx"):
                onnx_file = os.path.join(root, f)
                break
        if onnx_file:
            break

    if onnx_file is None:
        print("ERROR: No .onnx file found in download. Check rf_download/ folder manually.")
        sys.exit(1)

    dest = os.path.join("models", f"{project_name}.onnx")
    shutil.copy2(onnx_file, dest)
    shutil.rmtree("rf_download", ignore_errors=True)

    print(f"\nDone! Model saved to: {dest}")
    print(f"In the app: select '{os.path.basename(dest)}' from the Model dropdown.")

if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    main()
