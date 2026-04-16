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


def _wait_and_dump(delay: float = 1.5, retries: int = 3) -> str:
    """等待 UI 稳定后 dump XML，带重试"""
    time.sleep(delay)
    for attempt in range(retries):
        xml = read_ui_xml_from_device()
        if xml:
            return xml
        logger.warning(f"[UI] dump 失败，重试 ({attempt + 1}/{retries})")
        run_adb(["shell", "input", "tap", "540", "960"])
        time.sleep(1.5)
    return ""


def navigate_to_offline_cache() -> bool:
    """从 App 首页导航到离线缓存页面

    路径：首页 → 我的 → 设置 → 离线缓存
    返回 True 表示成功到达离线缓存页
    """
    # 步骤 1：确认在首页
    logger.info("[导航] 等待首页加载...")
    xml = _wait_and_dump(delay=3.0)
    if not xml:
        logger.error("[导航] 无法获取首页 UI")
        return False

    # 步骤 2：点击"我的" Tab
    logger.info("[导航] 点击「我的」...")
    for attempt in range(3):
        bounds = find_text_bounds(xml, "我的")
        if bounds:
            tap_bounds(bounds)
            xml = _wait_and_dump()
            break
        logger.warning(f"[导航] 未找到「我的」，重试 ({attempt + 1}/3)")
        xml = _wait_and_dump()
    else:
        logger.error("[导航] 无法找到「我的」Tab")
        return False

    # 步骤 3：点击设置图标
    logger.info("[导航] 点击「设置」...")
    for attempt in range(3):
        bounds = find_text_bounds(xml, "设置")
        if not bounds:
            bounds = find_content_desc_bounds(xml, "设置")
        if bounds:
            tap_bounds(bounds)
            xml = _wait_and_dump()
            break
        if attempt == 0:
            run_adb(["shell", "input", "tap", "1020", "144"])
            xml = _wait_and_dump()
            if find_text_bounds(xml, "离线缓存"):
                break
        logger.warning(f"[导航] 未找到「设置」，重试 ({attempt + 1}/3)")
        xml = _wait_and_dump()
    else:
        logger.error("[导航] 无法找到设置入口")
        return False

    # 步骤 4：点击"离线缓存"
    logger.info("[导航] 点击「离线缓存」...")
    for attempt in range(3):
        bounds = find_text_bounds(xml, "离线缓存")
        if bounds:
            tap_bounds(bounds)
            xml = _wait_and_dump()
            if find_text_bounds(xml, "已下载") or find_text_bounds(xml, "下载中"):
                logger.info("[导航] 已到达离线缓存页")
                return True
            break
        run_adb(["shell", "input", "swipe", "540", "1500", "540", "800", "300"])
        xml = _wait_and_dump()
    else:
        logger.error("[导航] 无法找到「离线缓存」")
        return False

    return True


def enter_drama_from_cache(drama_name: str) -> bool:
    """在离线缓存页面找到目标剧并点击进入播放器"""
    logger.info(f"[导航] 在缓存中查找《{drama_name}》...")
    for attempt in range(3):
        xml = _wait_and_dump(delay=1.0)
        if not xml:
            continue
        bounds = find_text_contains_bounds(xml, drama_name)
        if bounds:
            tap_bounds(bounds)
            logger.info(f"[导航] 已点击《{drama_name}》")
            time.sleep(3)
            ctx = read_ui_episode()
            if ctx is not None:
                logger.info(f"[导航] 已进入播放器，当前第 {ctx} 集")
                return True
            logger.warning("[导航] 点击后未进入播放器，重试")
        else:
            logger.warning(f"[导航] 未找到《{drama_name}》({attempt + 1}/3)")
    logger.error(f"[导航] 在离线缓存中找不到《{drama_name}》")
    return False


def read_ui_episode() -> int | None:
    """从 UI 读取当前播放集数，带 tap 唤醒重试"""
    for attempt in range(3):
        xml = read_ui_xml_from_device()
        if xml:
            ctx = parse_ui_context(xml)
            if ctx.episode is not None:
                return ctx.episode
        run_adb(["shell", "input", "tap", "540", "960"])
        time.sleep(1.5)
    return None


def read_ui_total_episodes() -> int | None:
    """从 UI 读取总集数"""
    for attempt in range(3):
        xml = read_ui_xml_from_device()
        if xml:
            ctx = parse_ui_context(xml)
            if ctx.total_episodes is not None:
                return ctx.total_episodes
        run_adb(["shell", "input", "tap", "540", "960"])
        time.sleep(1.5)
    return None


def wait_capture(state: HookState, fence_ts: float, timeout: float = 30.0):
    """轮询 HookState，等待围栏后 ref+url+key 三者到齐

    Returns: (ref, url, key) 或 None（超时）
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        ref, url, key = state.get_after_fence(fence_ts)
        if ref and url and key:
            return ref, url, key
        time.sleep(0.5)
    ref, url, key = state.get_after_fence(fence_ts)
    logger.warning(f"[捕获] 超时: ref={'有' if ref else '无'} url={'有' if url else '无'} key={'有' if key else '无'}")
    return None


def download_and_decrypt(url: str, key_hex: str, output_path: str) -> bool:
    """下载加密视频 + CENC 就地解密 + 写文件

    使用临时文件写入，成功后再 rename，避免中断留下损坏文件。
    """
    tmp_path = output_path + ".tmp"
    try:
        logger.info(f"[下载] {url[:80]}...")
        resp = requests.get(
            url,
            headers={"User-Agent": "AVDML_2.1.230.181-novel_ANDROID"},
            timeout=120,
        )
        resp.raise_for_status()

        data = bytearray(resp.content)
        size_mb = len(data) / 1024 / 1024
        logger.info(f"[下载] 完成: {size_mb:.1f}MB")

        key_bytes = bytes.fromhex(key_hex)
        sample_count = decrypt_mp4(data, key_bytes)
        fix_metadata(data)
        logger.info(f"[解密] {sample_count} samples 解密完成")

        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with open(tmp_path, "wb") as f:
            f.write(data)
        os.replace(tmp_path, output_path)
        return True

    except Exception as e:
        logger.error(f"[下载] 失败: {e}")
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        return False


def verify_playable(path: str) -> bool:
    """用 ffprobe 验证文件可播放：有 video+audio stream，duration > 0，文件 > 100KB"""
    if not os.path.exists(path):
        return False
    if os.path.getsize(path) < 100 * 1024:
        logger.warning(f"[验证] 文件太小: {os.path.getsize(path)} bytes")
        return False
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "stream=codec_type", "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=15,
        )
        streams = result.stdout.strip().splitlines()
        has_video = "video" in streams
        has_audio = "audio" in streams
        if not (has_video and has_audio):
            logger.warning(f"[验证] 缺少轨道: video={has_video} audio={has_audio}")
            return False

        result2 = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "format=duration", "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=15,
        )
        duration = float(result2.stdout.strip() or "0")
        if duration <= 0:
            logger.warning(f"[验证] duration={duration}")
            return False

        return True
    except Exception as e:
        logger.warning(f"[验证] ffprobe 失败: {e}")
        return False


def build_plan(output_dir: str, total_eps: int, start_ep: int = 1) -> list[dict]:
    """生成下载计划，glob 已有文件跳过已完成的集数"""
    plan = []
    for ep in range(start_ep, total_eps + 1):
        pattern = os.path.join(output_dir, f"episode_{ep:03d}_*.mp4")
        existing = glob.glob(pattern)
        if existing and os.path.getsize(existing[0]) > 100 * 1024:
            plan.append({"ep": ep, "status": "done", "path": existing[0]})
        else:
            plan.append({"ep": ep, "status": "pending"})
    return plan
