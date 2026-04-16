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
from scripts.download_drama import COMBINED_HOOK

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

    def get_latest(self) -> tuple[VideoRef | None, str | None, AESKey | None]:
        """返回最新的 ref、对应的最高画质 URL、最新的 key"""
        with self.lock:
            ref = self.refs[-1] if self.refs else None
            key = self.keys[-1] if self.keys else None
            best_url = None
            if ref:
                matching = [u for u in self.urls if u.video_id == ref.video_id]
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
    device = frida.get_usb_device(timeout=10)

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

    # 等待 Hook 就绪
    time.sleep(5)

    # 确保 App 在前台（spawn 后可能在后台）
    subprocess.run(
        ["adb", "shell", "monkey", "-p", package, "-c",
         "android.intent.category.LAUNCHER", "1"],
        capture_output=True, check=False, env=env,
    )

    # 等待 App 首页完全加载
    logger.info("[Frida] 等待 App 首页加载...")
    time.sleep(10)

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
    # 步骤 1：暂停首页视频（首页有自动播放视频，会阻止 uiautomator dump）
    logger.info("[导航] 暂停首页视频...")
    run_adb(["shell", "input", "tap", "540", "400"])
    time.sleep(1)
    run_adb(["shell", "input", "tap", "540", "400"])
    time.sleep(1)

    xml = _wait_and_dump(delay=2.0)

    # 步骤 2：点击"我的" Tab（右下角）
    logger.info("[导航] 点击「我的」...")
    found_mine = False
    if xml:
        bounds = find_text_bounds(xml, "我的")
        if bounds:
            tap_bounds(bounds)
            found_mine = True
    if not found_mine:
        # fallback：底部右下角 Tab 坐标（1080 宽屏幕，5 个 Tab，"我的"是最后一个）
        logger.info("[导航] 用坐标 fallback 点击「我的」(972, 1890)")
        run_adb(["shell", "input", "tap", "972", "1890"])
    xml = _wait_and_dump(delay=2.0)

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


def advance_to_next_episode(state: HookState, timeout: float = 20.0) -> bool:
    """尝试多种方式切换到下一集，等待 Hook 捕获新数据确认成功

    策略优先级：
    1. 上滑手势（竖屏短剧标准操作）
    2. 媒体键 NEXT
    3. 点击播放器控制层的"下一集"区域

    Returns: True 表示检测到新 Hook 数据（说明换集成功）
    """
    old_ref_count = len(state.refs)

    methods = [
        # (名称, 命令列表, 等待秒数)
        ("上滑(快)", [["shell", "input", "swipe", "540", "1600", "540", "300", "250"]], 5),
        ("上滑(慢)", [["shell", "input", "swipe", "540", "1700", "540", "200", "500"]], 5),
        ("媒体键NEXT", [["shell", "input", "keyevent", "87"]], 5),
        ("tap控制层+下一集", [
            ["shell", "input", "tap", "540", "960"],  # 唤醒控制层
        ], 1),
        ("tap下一集按钮", [
            ["shell", "input", "tap", "960", "960"],  # 右侧区域常见"下一集"位置
        ], 5),
    ]

    for name, cmds, wait_sec in methods:
        for cmd in cmds:
            run_adb(cmd)
            time.sleep(0.3)
        logger.info(f"[切集] 尝试: {name}")

        # 轮询等待新 Hook 数据
        deadline = time.time() + wait_sec
        while time.time() < deadline:
            if len(state.refs) > old_ref_count:
                new_ref = state.refs[-1]
                logger.info(f"[切集] 成功! 新视频: {new_ref.video_id[:16]}... (方式: {name})")
                return True
            time.sleep(0.5)

    logger.warning("[切集] 所有方式均未触发新 Hook 数据")
    return False


def download_current_episode(
    ep_num: int, state: HookState, output_dir: str, fence_ts: float
) -> dict:
    """下载当前正在播放的集数（围栏式）

    假设播放器已经在播放目标集，通过 Hook 围栏获取 URL/Key 后下载。

    Returns: {"success": bool, "ep": int, "video_id": str, "reason": str, "path": str}
    """
    MAX_RETRIES = 3

    for attempt in range(MAX_RETRIES):
        logger.info(f"[第{ep_num}集] 下载当前播放 (尝试 {attempt + 1}/{MAX_RETRIES})")

        # 1. 等待 Hook 围栏后数据到齐
        result = wait_capture(state, fence_ts, timeout=30)
        if result is None:
            # 可能播放器卡住了，tap 一下触发
            logger.warning(f"[第{ep_num}集] 等待捕获超时，tap 触发")
            run_adb(["shell", "input", "tap", "540", "960"])
            time.sleep(3)
            fence_ts = time.time() - 5  # 回退 5 秒的围栏
            continue
        ref, url, key = result

        # 2. 下载 + 解密
        vid8 = ref.video_id[-8:] if len(ref.video_id) >= 8 else ref.video_id
        output_path = os.path.join(output_dir, f"episode_{ep_num:03d}_{vid8}.mp4")

        ok = download_and_decrypt(url, key.key_hex, output_path)
        if not ok:
            logger.warning(f"[第{ep_num}集] 下载/解密失败，重试")
            continue

        # 3. 验证
        if not verify_playable(output_path):
            logger.warning(f"[第{ep_num}集] 验证失败，删除文件并重试")
            if os.path.exists(output_path):
                os.remove(output_path)
            continue

        logger.info(f"[第{ep_num}集] 下载成功: {output_path}")
        return {
            "success": True,
            "ep": ep_num,
            "video_id": ref.video_id,
            "path": output_path,
            "reason": "ok",
        }

    logger.error(f"[第{ep_num}集] {MAX_RETRIES} 次重试均失败")
    return {
        "success": False,
        "ep": ep_num,
        "video_id": "",
        "path": "",
        "reason": "max_retries_exceeded",
    }


def recover_frida(state: HookState, drama_name: str) -> tuple:
    """Frida 断连后完整恢复

    Returns: (session, script, pid)
    """
    logger.info("[恢复] Frida session 断开，重新初始化...")
    state.clear()

    session, script, pid = setup_frida(APP_PACKAGE, state)

    if not navigate_to_offline_cache():
        logger.error("[恢复] 导航到离线缓存失败")
        return session, script, pid

    if not enter_drama_from_cache(drama_name):
        logger.warning("[恢复] 在缓存中找不到剧，尝试搜索入口")
        try:
            from scripts.download_drama import search_drama_in_app
            search_drama_in_app(drama_name)
        except Exception as e:
            logger.error(f"[恢复] 搜索入口也失败: {e}")

    return session, script, pid


def main():
    parser = argparse.ArgumentParser(description="红果短剧全集精准下载器")
    parser.add_argument("-n", "--name", required=True, help="短剧名称")
    parser.add_argument("-e", "--start-episode", type=int, default=1, help="起始集数")
    parser.add_argument("--output", default="./videos", help="输出根目录")
    args = parser.parse_args()

    drama_name = args.name
    start_ep = args.start_episode
    output_dir = os.path.join(args.output, drama_name)
    os.makedirs(output_dir, exist_ok=True)

    # 配置日志
    log_file = os.path.join(output_dir, "download.log")
    logger.add(log_file, rotation="10 MB", encoding="utf-8",
               format="{time:YYYY-MM-DD HH:mm:ss} | {level:<8} | {message}", level="INFO")

    logger.info(f"{'=' * 60}")
    logger.info(f"  红果短剧下载器")
    logger.info(f"  目标: 《{drama_name}》 从第{start_ep}集开始")
    logger.info(f"  输出: {output_dir}")
    logger.info(f"{'=' * 60}")

    # 1. 初始化 Frida
    state = HookState()
    session, script, pid = setup_frida(APP_PACKAGE, state)

    # 2. 导航到离线缓存
    logger.info("[1/4] 导航到离线缓存...")
    cache_ok = navigate_to_offline_cache()

    # 3. 进入剧
    player_ok = False
    if cache_ok:
        logger.info("[2/4] 进入播放器...")
        player_ok = enter_drama_from_cache(drama_name)

    if not player_ok:
        logger.warning("[2/4] 离线缓存入口失败，尝试搜索入口...")
        try:
            from scripts.download_drama import search_drama_in_app, set_adapter
            from scripts.app_adapter import create_adapter
            set_adapter(create_adapter("honguo"))
            player_ok = search_drama_in_app(drama_name, start_episode=start_ep)
        except Exception as e:
            logger.error(f"搜索入口也失败: {e}")

    # 即使 search_drama_in_app 返回 False，通过多种方式确认播放器状态
    if not player_ok:
        logger.info("[2/4] 检查当前状态...")
        # 方式 1：检查 Hook 是否已收到数据（说明播放器已在播放）
        ref, url, key = state.get_after_fence(0)
        if ref and url and key:
            logger.info(f"[2/4] Hook 已捕获数据 (vid={ref.video_id[:16]}...)，确认播放器活跃")
            player_ok = True
        else:
            # 方式 2：检查 UI
            xml = _wait_and_dump(delay=2.0)
            if xml:
                ctx = parse_ui_context(xml)
                if ctx.episode is not None:
                    logger.info(f"[2/4] UI 确认在播放器中，当前第 {ctx.episode} 集")
                    player_ok = True
                elif find_text_contains_bounds(xml, drama_name):
                    # 在详情页，点击封面进入播放器
                    logger.info("[2/4] 在详情页，点击封面进入播放器...")
                    run_adb(["shell", "input", "tap", "195", "400"])
                    time.sleep(5)
                    # 再检查 Hook
                    ref2, url2, key2 = state.get_after_fence(0)
                    if ref2 and key2:
                        logger.info("[2/4] 点击封面后 Hook 收到数据，确认进入播放器")
                        player_ok = True

    if not player_ok:
        logger.error("无法进入播放器，退出")
        return

    # 4. 获取总集数：先从详情页解析"全XX集"，再从播放器 UI 获取
    total_eps = read_ui_total_episodes()
    if total_eps is None:
        # 尝试从之前 dump 的 XML 解析"全XX集"
        if xml:
            import re as _re
            m = _re.search(r'全(\d+)集', xml)
            if m:
                total_eps = int(m.group(1))
    if total_eps is None:
        logger.warning("无法从 UI 获取总集数，使用默认值 60")
        total_eps = 60
    logger.info(f"[3/4] 总集数: {total_eps}")

    # 5. 生成下载计划
    plan = build_plan(output_dir, total_eps, start_ep)
    done_count = sum(1 for p in plan if p["status"] == "done")
    pending_count = sum(1 for p in plan if p["status"] == "pending")
    logger.info(f"[4/4] 下载计划: {pending_count} 集待下载, {done_count} 集已完成")

    # 6. 逐集下载（滑动前进策略）
    #
    # 策略：播放器已在目标剧中，搜索流程已导航到起始集附近。
    # - 首个待下载集：直接下载当前播放的内容（不操作选集面板）
    # - 后续集：上滑切换到下一集 → 等 Hook 捕获 → 下载
    # - 跳过已完成的集时也需要滑动前进以保持同步
    #
    manifest_path = os.path.join(output_dir, "session_manifest.jsonl")
    success_count = 0
    failed_eps = []
    is_first_pending = True  # 第一个待下载集不需要滑动

    for task in tqdm(plan, desc="下载进度"):
        if task["status"] == "done":
            if not is_first_pending:
                logger.info(f"[第{task['ep']}集] 已完成，切集跳过")
                advance_to_next_episode(state)
            continue

        ep_num = task["ep"]
        result = None

        if is_first_pending:
            # 首集：搜索流程已导航到播放器，Hook 数据已在搜索阶段捕获
            is_first_pending = False
            ref, url, key = state.get_latest()
            if ref and url and key:
                logger.info(f"[第{ep_num}集] 使用已捕获数据 (vid={ref.video_id[:16]}...)")
                vid8 = ref.video_id[-8:] if len(ref.video_id) >= 8 else ref.video_id
                output_path = os.path.join(output_dir, f"episode_{ep_num:03d}_{vid8}.mp4")
                if download_and_decrypt(url, key.key_hex, output_path) and verify_playable(output_path):
                    result = {"success": True, "ep": ep_num, "video_id": ref.video_id, "path": output_path}
                else:
                    if os.path.exists(output_path):
                        os.remove(output_path)
            if result is None:
                logger.warning(f"[第{ep_num}集] 已捕获数据不可用，尝试围栏捕获")
                fence_ts = time.time() - 60
                try:
                    result = download_current_episode(ep_num, state, output_dir, fence_ts)
                except frida.InvalidOperationError:
                    session, script, pid = recover_frida(state, drama_name)
                    result = download_current_episode(ep_num, state, output_dir, time.time())
        else:
            # 后续集：多策略切集 → 围栏捕获 → 下载
            try:
                advanced = advance_to_next_episode(state)
                if advanced:
                    # 切集成功，用最新 Hook 数据下载
                    ref, url, key = state.get_latest()
                    if ref and url and key:
                        vid8 = ref.video_id[-8:] if len(ref.video_id) >= 8 else ref.video_id
                        output_path = os.path.join(output_dir, f"episode_{ep_num:03d}_{vid8}.mp4")
                        if download_and_decrypt(url, key.key_hex, output_path) and verify_playable(output_path):
                            result = {"success": True, "ep": ep_num, "video_id": ref.video_id, "path": output_path}
                        else:
                            if os.path.exists(output_path):
                                os.remove(output_path)
                if result is None:
                    # 切集失败或下载失败，尝试围栏捕获
                    fence_ts = time.time() - 5
                    result = download_current_episode(ep_num, state, output_dir, fence_ts)
            except frida.InvalidOperationError:
                logger.warning(f"[第{ep_num}集] Frida session 断开，恢复...")
                session, script, pid = recover_frida(state, drama_name)
                result = download_current_episode(ep_num, state, output_dir, time.time())

        if result is None:
            result = {"success": False, "ep": ep_num, "video_id": "", "path": ""}

        if result["success"]:
            success_count += 1
            append_jsonl(manifest_path, {
                "episode": result["ep"],
                "video_id": result["video_id"],
                "path": result["path"],
                "timestamp": time.time(),
                "status": "ok",
            })
        else:
            failed_eps.append(ep_num)

    # 7. 最终报告
    total_done = done_count + success_count
    logger.info(f"\n{'=' * 60}")
    logger.info(f"  下载完成!")
    logger.info(f"  成功: {total_done}/{total_eps} 集")
    if failed_eps:
        logger.warning(f"  失败: {len(failed_eps)} 集 → {failed_eps}")
    logger.info(f"  输出: {output_dir}")
    logger.info(f"{'=' * 60}")


if __name__ == "__main__":
    main()
