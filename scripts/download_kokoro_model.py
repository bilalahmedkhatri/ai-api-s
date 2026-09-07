"""
scripts/download_kokoro_model.py — Utility to download Kokoro-82M ONNX model & voice files reliably.

Usage:
  python scripts/download_kokoro_model.py
"""

import os
import sys
import urllib.request
from pathlib import Path

MODEL_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/kokoro-v1.0.onnx"
VOICES_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/voices-v1.0.bin"

MODEL_DEST = Path("kokoro-v1_0.onnx")
VOICES_DEST = Path("voices-v1_0.bin")


def download_file(url: str, dest_path: Path):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    
    # Check expected content length
    with urllib.request.urlopen(req) as resp:
        expected_size = int(resp.headers.get("content-length", 0))

    if dest_path.exists() and expected_size > 0:
        actual_size = dest_path.stat().st_size
        if actual_size >= expected_size:
            print(f" File '{dest_path.name}' is already fully downloaded ({actual_size / (1024*1024):.1f} MB). Skipping.")
            return
        else:
            print(f" Partial file '{dest_path.name}' detected ({actual_size / (1024*1024):.1f} MB / {expected_size / (1024*1024):.1f} MB). Re-downloading...")
            dest_path.unlink(missing_ok=True)

    print(f" Downloading '{dest_path.name}' from {url}...")
    tmp_path = dest_path.with_suffix(dest_path.suffix + ".tmp")
    
    with urllib.request.urlopen(req) as resp, open(tmp_path, "wb") as f:
        total = int(resp.headers.get("content-length", 0))
        downloaded = 0
        block_size = 1024 * 1024  # 1MB chunk

        while True:
            buffer = resp.read(block_size)
            if not buffer:
                break
            downloaded += len(buffer)
            f.write(buffer)
            if total > 0:
                percent = downloaded / total * 100
                print(f" Progress: {downloaded / (1024*1024):.1f} MB / {total / (1024*1024):.1f} MB ({percent:.1f}%)", end="\r")

    # Rename tmp to final destination after complete download
    tmp_path.replace(dest_path)
    print(f"\n Successfully downloaded '{dest_path.name}' ({dest_path.stat().st_size / (1024*1024):.1f} MB).")


def main():
    print("\n=======================================================")
    print("      Downloading Kokoro-82M ONNX Model Files          ")
    print("=======================================================")
    download_file(MODEL_URL, MODEL_DEST)
    download_file(VOICES_URL, VOICES_DEST)
    print("=======================================================")
    print(" Kokoro TTS Model Files Fully Ready!")
    print("=======================================================\n")


if __name__ == "__main__":
    main()
