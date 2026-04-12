"""Pull and convert video cache files from HongGuo (红果免费短剧) app.

The app caches videos as .mdl files in:
  /sdcard/Android/data/com.phoenix.read/cache/short/

These are standard MP4 (ftypisom) files that can be directly renamed.


  python scripts/pull_cache.py [output_dir]
  python scripts/pull_cache.py --watch       # Watch for new videos
"""

from __future__ import annotations

import os
import shutil
import struct
import subprocess
import sys
import time
from pathlib import Path

from loguru import logger

CACHE_PATH = "/sdcard/Android/data/com.phoenix.read/cache/short"
PACKAGE_NAME = "com.phoenix.read"


def run_adb(args: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    cmd = ["adb"] + args
    env = {**os.environ, "MSYS_NO_PATHCONV": "1"}
    return subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=check, env=env)


def list_remote_mdl() -> list[dict[str, str]]:
    """List .mdl files on device with size and timestamp."""
    result = run_adb(["shell", f"ls -l {CACHE_PATH}/*.mdl"], check=False)
    if result.returncode != 0:
        logger.error("No .mdl files found on device")
        return []

    files = []
    for line in result.stdout.strip().splitlines():
        parts = line.split()
        if len(parts) >= 7 and parts[-1].endswith(".mdl"):
            files.append({
                "name": Path(parts[-1]).name,
                "size": parts[4],
                "date": f"{parts[5]} {parts[6]}",
                "path": parts[-1],
            })
    return files


def get_mp4_duration(filepath: str) -> float:
    """Extract duration from MP4 file header."""
    try:
        with open(filepath, "rb") as f:
            while True:
                header = f.read(8)
                if len(header) < 8:
                    break
                box_size = struct.unpack(">I", header[:4])[0]
                box_type = header[4:8]
                if box_size == 0:
                    break
                if box_type == b"moov":
                    moov_data = f.read(min(box_size - 8, 200))
                    idx = moov_data.find(b"mvhd")
                    if idx >= 0:
                        mvhd = moov_data[idx:]
                        version = mvhd[4]
                        if version == 0:
                            timescale = struct.unpack(">I", mvhd[16:20])[0]
                            dur = struct.unpack(">I", mvhd[20:24])[0]
                        else:
                            timescale = struct.unpack(">I", mvhd[24:28])[0]
                            dur = struct.unpack(">Q", mvhd[28:36])[0]
                        if timescale > 0:
                            return dur / timescale
                    break
                else:
                    f.seek(box_size - 8, 1)
    except Exception:
        pass
    return 0.0


def pull_and_convert(output_dir: str, only_new: bool = True) -> list[str]:
    """Pull .mdl files from device and convert to .mp4."""
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    remote_files = list_remote_mdl()
    if not remote_files:
        return []

    # Check existing files
    existing = {f.stem for f in output_path.glob("*.mp4")}
    if only_new:
        remote_files = [f for f in remote_files if Path(f["name"]).stem not in existing]

    if not remote_files:
        logger.info("No new videos to pull")
        return []

    logger.info(f"Pulling {len(remote_files)} video(s)...")

    pulled = []
    for f in remote_files:
        name = Path(f["name"]).stem
        mp4_path = str(output_path / f"{name}.mp4")

        try:
            run_adb(["pull", f["path"], mp4_path])
            duration = get_mp4_duration(mp4_path)
            size_mb = os.path.getsize(mp4_path) / 1024 / 1024
            logger.info(f"  {name}.mp4  {size_mb:.1f}MB  {duration:.0f}s")
            pulled.append(mp4_path)
        except Exception as e:
            logger.error(f"  Failed to pull {name}: {e}")

    logger.info(f"Pulled {len(pulled)} videos to {output_dir}")
    return pulled


def watch_and_pull(output_dir: str, interval: int = 10) -> None:
    """Watch for new videos and pull them automatically."""
    logger.info(f"Watching for new videos (interval: {interval}s). Press Ctrl+C to stop.")
    seen: set[str] = set()

    # Initial pull
    pulled = pull_and_convert(output_dir, only_new=True)
    for p in pulled:
        seen.add(Path(p).stem)

    try:
        while True:
            time.sleep(interval)
            remote_files = list_remote_mdl()
            new_files = [f for f in remote_files if Path(f["name"]).stem not in seen]

            if new_files:
                logger.info(f"Found {len(new_files)} new video(s)!")
                for f in new_files:
                    name = Path(f["name"]).stem
                    mp4_path = str(Path(output_dir) / f"{name}.mp4")
                    try:
                        run_adb(["pull", f["path"], mp4_path])
                        duration = get_mp4_duration(mp4_path)
                        size_mb = os.path.getsize(mp4_path) / 1024 / 1024
                        logger.info(f"  NEW: {name}.mp4  {size_mb:.1f}MB  {duration:.0f}s")
                        seen.add(name)
                    except Exception as e:
                        logger.error(f"  Failed: {e}")
    except KeyboardInterrupt:
        logger.info(f"Stopped. Total videos: {len(seen)}")


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Pull video cache from HongGuo app")
    parser.add_argument("output", nargs="?", default="./videos/honguo",
                        help="Output directory (default: ./videos/honguo)")
    parser.add_argument("--watch", "-w", action="store_true",
                        help="Watch for new videos and pull automatically")
    parser.add_argument("--interval", "-i", type=int, default=10,
                        help="Watch interval in seconds (default: 10)")
    parser.add_argument("--all", "-a", action="store_true",
                        help="Pull all files, not just new ones")

    args = parser.parse_args()

    if args.watch:
        watch_and_pull(args.output, args.interval)
    else:
        pulled = pull_and_convert(args.output, only_new=not args.all)
        if pulled:
            print(f"\nPulled {len(pulled)} videos to {args.output}")
            print("Files are standard MP4, playable with any video player.")
        else:
            print("No new videos found. Play some videos in the app first.")


if __name__ == "__main__":
    main()
