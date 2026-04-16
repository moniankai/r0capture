"""红果短剧全集精准下载器

给定剧名，全自动下载全集视频。使用围栏式捕获机制解决 Hook 数据污染问题。

用法:
  python scripts/download_hongguo.py -n "西游，错把玉帝当亲爹"
  python scripts/download_hongguo.py -n "西游，错把玉帝当亲爹" -e 5
  python scripts/download_hongguo.py -n "西游，错把玉帝当亲爹" --output videos
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import frida
import requests
from loguru import logger
from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.drama_download_common import (
    UIContext,
    run_adb,
    read_ui_xml_from_device,
    tap_bounds,
    bounds_center,
    find_text_bounds,
    find_text_contains_bounds,
    find_content_desc_bounds,
    find_element_by_resource_id,
    parse_ui_context,
    append_jsonl,
)
from scripts.decrypt_video import decrypt_mp4, fix_metadata
from scripts.download_drama import COMBINED_HOOK, select_episode_from_ui

APP_PACKAGE = "com.phoenix.read"
QUALITY_ORDER = {"1080p": 5, "720p": 4, "540p": 3, "480p": 2, "360p": 1}


@dataclass
class VideoRef:
    video_id: str
    duration: int
    timestamp: float


@dataclass
class VideoURL:
    video_id: str
    url: str
    quality: str
    kid: str
    timestamp: float


@dataclass
class AESKey:
    key_hex: str
    bits: int
    timestamp: float


class HookState:
    """线程安全的 Hook 数据容器，支持围栏式过滤"""

    def __init__(self):
        self.lock = threading.Lock()
        self.current_video_id: str = ""
        self.refs: list[VideoRef] = []
        self.urls: list[VideoURL] = []
        self.keys: list[AESKey] = []

    def get_after_fence(self, fence_ts: float) -> tuple[VideoRef | None, str | None, AESKey | None]:
        """返回围栏之后的第一个 ref、对应的最高画质 URL、第一个 key"""
        with self.lock:
            ref = next((r for r in self.refs if r.timestamp > fence_ts), None)
            key = next((k for k in self.keys if k.timestamp > fence_ts), None)
            best_url = None
            if ref:
                matching = [u for u in self.urls
                            if u.video_id == ref.video_id and u.timestamp > fence_ts]
                if matching:
                    matching.sort(key=lambda u: QUALITY_ORDER.get(u.quality, 0), reverse=True)
                    best_url = matching[0].url
            return ref, best_url, key

    def clear(self):
        with self.lock:
            self.current_video_id = ""
            self.refs.clear()
            self.urls.clear()
            self.keys.clear()


def create_on_message(state: HookState):
    """创建 on_message 回调闭包，解析 video_ref / video_info / AES_KEY 消息"""

    def on_message(msg, data):
        if msg.get("type") != "send":
            return
        p = msg.get("payload", {})
        ts = time.time()
        t = p.get("t", "")

        if t == "video_ref":
            vid = p.get("data", {}).get("mVideoId", "")
            try:
                dur = int(p.get("data", {}).get("mVideoDuration", 0))
            except (ValueError, TypeError):
                dur = 0
            with state.lock:
                state.current_video_id = vid
                state.refs.append(VideoRef(video_id=vid, duration=dur, timestamp=ts))
            logger.info(f"[Hook] video_ref: {vid} ({dur}s)")

        elif t == "video_info":
            d = p.get("data", {})
            url = d.get("mMainUrl", "")
            if url:
                with state.lock:
                    state.urls.append(VideoURL(
                        video_id=state.current_video_id,
                        url=url,
                        quality=d.get("mResolution", ""),
                        kid=d.get("mKid", ""),
                        timestamp=ts,
                    ))

        elif t == "AES_KEY":
            with state.lock:
                state.keys.append(AESKey(
                    key_hex=p["key"],
                    bits=p.get("bits", 128),
                    timestamp=ts,
                ))
            logger.info(f"[Hook] AES_KEY: {p['key'][:8]}... ({p.get('bits')}bit)")

        elif t == "lib_loaded":
            logger.info("[Hook] libttffmpeg.so 已加载")
        elif t == "aes_hooked":
            logger.info("[Hook] av_aes_init 已挂钩")
        elif t == "java_ready":
            logger.info("[Hook] Java Hook 就绪")

    return on_message


def setup_frida(package: str, state: HookState) -> tuple:
    """Spawn App + 加载 COMBINED_HOOK，返回 (session, script, pid)"""
    device = frida.get_usb_device()

    # 先停止 App
    env = {**os.environ, "MSYS_NO_PATHCONV": "1"}
    subprocess.run(["adb", "shell", "am", "force-stop", package],
                   capture_output=True, check=False, env=env)
    time.sleep(1)

    pid = device.spawn([package])
    session = device.attach(pid)

    script = session.create_script(COMBINED_HOOK)
    script.on("message", create_on_message(state))
    script.load()

    device.resume(pid)
    logger.info(f"[Frida] App spawned, PID={pid}")

    # 等待 Hook 就绪 + App 首页加载
    time.sleep(15)

    return session, script, pid
