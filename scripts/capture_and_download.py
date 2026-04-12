"""
Capture video URLs from HongGuo app via Frida SSL hooks, then download directly from CDN.

The CDN serves unencrypted MP4 files. The CENC encryption is only applied locally by the app.


  python scripts/capture_and_download.py                # 处理 App
  python scripts/capture_and_download.py --spawn        # App
  python scripts/capture_and_download.py --duration 120  # 2 
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import frida
import requests
from loguru import logger
from tqdm import tqdm

APP_PACKAGE = "com.phoenix.read"
CDN_PATTERN = re.compile(
    r"GET\s+(/[^\s]+/video/tos/[^\s]+)\s+HTTP",
)
HOST_PATTERN = re.compile(r"Host:\s*(\S+)", re.IGNORECASE)
RANGE_PATTERN = re.compile(r"Range:\s*bytes=(\d+)-", re.IGNORECASE)


def extract_video_urls(packets: list[dict]) -> list[dict]:
    """Extract unique video download URLs from captured SSL packets."""
    urls: list[dict] = []
    seen_paths: set[str] = set()

    for pkt in packets:
        text = pkt.get("text", "")
        if not text.startswith("GET "):
            continue

        path_match = CDN_PATTERN.search(text)
        if not path_match:
            continue

        path = path_match.group(1)
        host_match = HOST_PATTERN.search(text)
        host = host_match.group(1) if host_match else ""

        if not host or "video" not in path:
            continue

        # 处理回退 query 
        base_path = path.split("?")[0]
        if base_path in seen_paths:
            continue
        seen_paths.add(base_path)

        # 处理Range: bytes=0-处理
        range_match = RANGE_PATTERN.search(text)
        if range_match and int(range_match.group(1)) > 0:
            continue

        full_url = f"https://{host}{path}"
        urls.append({"url": full_url, "host": host, "path": base_path})

    return urls


def download_video(url: str, output_path: str, headers: dict | None = None) -> bool:
    """Download a video file from CDN."""
    default_headers = {
        "User-Agent": "AVDML_2.1.230.181-novel_ANDROID,ShortPlay,MDLTaskPreload",
    }
    if headers:
        default_headers.update(headers)

    try:
        resp = requests.get(url, headers=default_headers, stream=True, timeout=30)
        resp.raise_for_status()
        total = int(resp.headers.get("content-length", 0))

        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

        downloaded = 0
        with open(output_path, "wb") as f:
            with tqdm(total=total, desc=Path(output_path).name, unit="B", unit_scale=True) as pbar:
                for chunk in resp.iter_content(8192):
                    f.write(chunk)
                    downloaded += len(chunk)
                    pbar.update(len(chunk))

        logger.info(f"Downloaded: {output_path} ({downloaded / 1024 / 1024:.1f} MB)")
        return True
    except Exception as e:
        logger.error(f"Download failed: {e}")
        return False


def run_capture(spawn: bool = False, duration: int = 60) -> list[dict]:
    """Run Frida SSL hook capture and return text packets."""
    device = frida.get_usb_device()

    if spawn:
        logger.info(f"Spawning {APP_PACKAGE}...")
        pid = device.spawn([APP_PACKAGE])
        session = device.attach(pid)
        device.resume(pid)
        logger.info(f"PID: {pid}, waiting 8s for app to load...")
        time.sleep(8)
    else:
        # 处理
        procs = device.enumerate_processes()
        pid = None
        for p in procs:
            if p.identifier == APP_PACKAGE:
                pid = p.pid
                break
        if pid is None:
            logger.error(f"{APP_PACKAGE} not running. Use --spawn or open the app first.")
            return []
        logger.info(f"Attaching to PID {pid}")
        session = device.attach(pid)

    hook = (
        'var resolver = new ApiResolver("module"); var n = 0;'
        'function hookSSL(pat, tag) {'
        '  var rm = resolver.enumerateMatches(pat + "!SSL_read");'
        '  var wm = resolver.enumerateMatches(pat + "!SSL_write");'
        '  if (rm.length > 0) { Interceptor.attach(rm[0].address, {'
        '    onEnter: function(a) { this.buf = a[1]; },'
        '    onLeave: function(ret) {'
        '      var len = ret.toInt32();'
        '      if (len > 0) { n++; send({f: tag + "_R", n: n, l: len}, this.buf.readByteArray(len)); }'
        '    }'
        '  }); }'
        '  if (wm.length > 0) { Interceptor.attach(wm[0].address, {'
        '    onEnter: function(a) {'
        '      var len = a[2].toInt32();'
        '      if (len > 0) { n++; send({f: tag + "_W", n: n, l: len}, a[1].readByteArray(len)); }'
        '    }'
        '  }); }'
        '  send({s: tag + " hooked"});'
        '}'
        'hookSSL("exports:*libttboringssl*", "tt");'
        'hookSSL("exports:*libssl.so*", "sys");'
        'send({s: "READY"});'
    )

    text_packets: list[dict] = []

    def on_message(msg, data):
        if msg["type"] != "send":
            return
        p = msg["payload"]
        if "s" in p:
            logger.info(p["s"])
            return
        if data is None:
            return

        try:
            text = data.decode("utf-8", errors="replace")
            printable = sum(1 for c in text if c.isprintable() or c in "\r\n\t") / max(len(text), 1)
            if printable > 0.6:
                text_packets.append({"n": p["n"], "f": p["f"], "len": p["l"], "text": text})
        except Exception:
            pass

    script = session.create_script(hook)
    script.on("message", on_message)
    script.load()

    logger.info(f"Capturing for {duration}s... Play/switch videos on your phone!")
    time.sleep(duration)

    logger.info(f"Captured {len(text_packets)} text packets")
    session.detach()
    return text_packets


def main():
    parser = argparse.ArgumentParser(description="Capture and download HongGuo videos")
    parser.add_argument("--spawn", "-f", action="store_true", help="Spawn app (recommended for first run)")
    parser.add_argument("--duration", "-d", type=int, default=60, help="Capture duration in seconds")
    parser.add_argument("--output", "-o", default="./videos/honguo_direct", help="Output directory")
    parser.add_argument("--no-download", action="store_true", help="Only capture URLs, don't download")
    args = parser.parse_args()

    logger.info("=== HongGuo Video Capture & Download ===")

    # 1 SSL 
    packets = run_capture(spawn=args.spawn, duration=args.duration)
    if not packets:
        logger.error("No packets captured")
        return

    # 2回退 URL
    video_urls = extract_video_urls(packets)
    logger.info(f"Found {len(video_urls)} unique video URLs")

    if not video_urls:
        logger.warning("No video URLs found. Make sure you played videos during capture.")
        return

    for i, v in enumerate(video_urls):
        logger.info(f"  [{i + 1}] {v['url'][:200]}")

    # URLs
    os.makedirs(args.output, exist_ok=True)
    urls_file = os.path.join(args.output, "video_urls.json")
    with open(urls_file, "w", encoding="utf-8") as f:
        json.dump(video_urls, f, indent=2, ensure_ascii=False)
    logger.info(f"URLs saved to {urls_file}")

    if args.no_download:
        return

# 步骤 3：下载视频
    logger.info("Downloading videos from CDN...")
    success_count = 0
    for i, v in enumerate(video_urls):
        output_path = os.path.join(args.output, f"episode_{i + 1:03d}.mp4")
        if download_video(v["url"], output_path):
            success_count += 1

    logger.info(f"Downloaded {success_count}/{len(video_urls)} videos to {args.output}")


if __name__ == "__main__":
    main()
