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
from dataclasses import dataclass
from pathlib import Path

import frida
import requests
from loguru import logger
from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.drama_download_common import (
    run_adb,
    read_ui_xml_from_device,
    tap_bounds,
    find_text_bounds,
    find_text_contains_bounds,
    find_content_desc_bounds,
    find_element_by_resource_id,
    parse_ui_context,
    append_jsonl,
    select_episode_from_ui,
    _find_episode_button,
    _select_episode_range,
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
                    matching.sort(key=lambda u: QUALITY_ORDER.get(u.quality, 0))
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
                    matching.sort(key=lambda u: QUALITY_ORDER.get(u.quality, 0))
                    best_url = matching[0].url
            return ref, best_url, key

    def get_complete_by_vid(self, target_vid: str, timeout: float = 12.0
                             ) -> tuple[VideoRef | None, str | None, AESKey | None]:
        """按 video_id 精确匹配 ref 和 URL；key 用"ref 之后到达的第一个 key"，最多等 timeout 秒。

        解决 get_latest 的竞态：refs[-1] 和 keys[-1] 可能不同源。
        """
        deadline = time.time() + timeout
        while True:
            with self.lock:
                ref = next((r for r in self.refs if r.video_id == target_vid), None)
                if not ref:
                    break  # vid 根本没出现，直接返回
                matching = [u for u in self.urls if u.video_id == target_vid]
                best_url = None
                if matching:
                    matching.sort(key=lambda u: QUALITY_ORDER.get(u.quality, 0))
                    best_url = matching[0].url
                # 紧随 ref 之后的第一个 key
                key = next((k for k in self.keys if k.timestamp >= ref.timestamp), None)
            if ref and best_url and key:
                return ref, best_url, key
            if time.time() >= deadline:
                return ref, best_url, key  # 超时也返回当前状态
            time.sleep(0.3)
        return None, None, None

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


def setup_frida(package: str, state: HookState, attach_running: bool = False) -> tuple:
    """加载 COMBINED_HOOK，返回 (session, script, pid)

    Args:
        attach_running: True 时 attach 到已运行 App（不 spawn），用户需手动把 App 调到目标状态
    """
    device = frida.get_usb_device(timeout=10)
    env = {**os.environ, "MSYS_NO_PATHCONV": "1"}

    if attach_running:
        # Attach 到已运行 App
        from scripts.download_drama import select_running_app_pid
        pid = select_running_app_pid(device.enumerate_processes(), package)
        if pid is None:
            raise RuntimeError(f"{package} 未在前台运行，无法 attach。请先在手机上打开 App 并进入全屏播放页")
        session = device.attach(pid)
        script = session.create_script(COMBINED_HOOK)
        script.on("message", create_on_message(state))
        script.load()
        logger.info(f"[Frida] Attached running App, PID={pid}")
        # 等待 Hook 轮询 ffmpegLoaded 的 30 秒 fallback 完成
        logger.info("[Frida] 等待 av_aes_init Hook 就绪（最多 35 秒）...")
        time.sleep(35)
        return session, script, pid

    # Spawn 模式
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
    time.sleep(5)
    subprocess.run(
        ["adb", "shell", "monkey", "-p", package, "-c",
         "android.intent.category.LAUNCHER", "1"],
        capture_output=True, check=False, env=env,
    )
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


def ensure_fullscreen_player() -> bool:
    """确保当前在全屏播放页。若识别为详情页，则 tap 顶部预览进入全屏。

    判定方式：
      - UI dump 失败 → 视频在播放（UIAutomator 拿不到 idle），大概率已全屏
      - UI dump 成功且包含"全N集"/"写评价"/"相关推荐" → 详情页
      - 其他情况保守认为已就绪
    """
    import re as _re
    xml = _wait_and_dump(delay=1.0, retries=1)
    if not xml:
        logger.info("[全屏] UI dump 失败，视为已在全屏播放")
        return True

    is_detail = bool(_re.search(r'全\d+集', xml)) or '写评价' in xml or '相关推荐' in xml
    if not is_detail:
        logger.info("[全屏] 当前不在详情页，继续")
        return True

    logger.info("[全屏] 检测到详情页，tap 顶部预览区进入全屏...")
    run_adb(["shell", "input", "tap", "540", "400"])
    time.sleep(3)
    xml2 = _wait_and_dump(delay=1.0, retries=1)
    if not xml2:
        logger.info("[全屏] tap 后 UI 不可 dump，成功进入全屏")
        return True
    still_detail = bool(_re.search(r'全\d+集', xml2)) and '写评价' in xml2
    if still_detail:
        logger.warning("[全屏] tap 后仍在详情页，尝试下方封面 (195, 400)")
        run_adb(["shell", "input", "tap", "195", "400"])
        time.sleep(3)
        xml3 = _wait_and_dump(delay=1.0, retries=1)
        if not xml3:
            logger.info("[全屏] 第二次 tap 后 UI 不可 dump，成功进入全屏")
            return True
        logger.warning("[全屏] 两次 tap 后仍在详情页")
        return False
    logger.info("[全屏] 已离开详情页")
    return True


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


def verify_playable(path: str, expected_duration: int = 0) -> bool:
    """用 ffprobe 验证文件可播放：有 video+audio stream，duration > 0，文件 > 100KB

    Args:
        expected_duration: Hook 上报的视频时长(秒)。> 0 时会交叉校验，
                          允许 ±5 秒误差。0 表示跳过时长校验。
    """
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

        if expected_duration > 0 and abs(duration - expected_duration) > 5:
            logger.warning(f"[验证] 时长不匹配: 文件={duration:.1f}s Hook={expected_duration}s")
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


# 剧集 duration 合理范围（秒）：短于此为广告/片头，长于此为连播模式
EPISODE_DURATION_MIN = 30
EPISODE_DURATION_MAX = 600


def advance_to_next_episode(state: HookState, prev_video_id: str = "",
                            downloaded_vids: set[str] | None = None,
                            timeout: float = 8.0,
                            max_swipes: int = 2) -> VideoRef | None:
    """上滑切换到下一集，等待合法的新剧集 Hook 数据

    合法判据（全部满足）：
      1. 新 video_id != prev_video_id
      2. 新 video_id 不在 downloaded_vids（不是历史已下过的集）
      3. duration 在 [EPISODE_DURATION_MIN, EPISODE_DURATION_MAX]（排除广告/推荐）

    Returns: 新集的 VideoRef；切集失败返回 None
    """
    downloaded_vids = downloaded_vids or set()

    def _find_legit_ref(start_idx: int) -> VideoRef | None:
        with state.lock:
            candidates = state.refs[start_idx:]
        for ref in candidates:
            if ref.video_id == prev_video_id:
                continue
            if ref.video_id in downloaded_vids:
                continue
            if not (EPISODE_DURATION_MIN <= ref.duration <= EPISODE_DURATION_MAX):
                continue
            return ref
        return None

    for swipe_idx in range(max_swipes):
        with state.lock:
            old_ref_count = len(state.refs)

        # 慢速上滑（800ms）避免惯性跨多集；滑动幅度 1400→600 覆盖半屏
        run_adb(["shell", "input", "swipe", "540", "1400", "540", "600", "800"])

        deadline = time.time() + timeout
        while time.time() < deadline:
            legit = _find_legit_ref(old_ref_count)
            if legit:
                logger.info(f"[切集] 成功 (vid={legit.video_id[:16]}..., dur={legit.duration}s)")
                return legit
            time.sleep(0.3)

        with state.lock:
            new_refs = state.refs[old_ref_count:]
        if not new_refs:
            logger.warning(f"[切集] 第{swipe_idx + 1}次上滑未触发 Hook")
        else:
            reasons = []
            for r in new_refs:
                if r.video_id == prev_video_id:
                    reasons.append(f"同上集({r.duration}s)")
                elif r.video_id in downloaded_vids:
                    reasons.append(f"已下过({r.video_id[-8:]})")
                elif not (EPISODE_DURATION_MIN <= r.duration <= EPISODE_DURATION_MAX):
                    reasons.append(f"dur异常({r.duration}s)")
            logger.warning(f"[切集] 第{swipe_idx + 1}次上滑拿到 {len(new_refs)} 个新 ref，均不合法: {reasons}")

    logger.error(f"[切集] {max_swipes} 次上滑后仍未拿到合法剧集")
    return None


def navigate_to_drama_via_search(drama_name: str, timeout: float = 25.0) -> bool:
    """通过搜索流程把 App 导航回目标剧的播放页。

    适用场景：上一集下载完后 App 自动连播切到了推荐流，需要重新回到本剧。
    流程：home → deeplink 搜索 → 输入剧名 → 点击搜索 → 点击结果卡片剧名
    """
    env = {**os.environ, "MSYS_NO_PATHCONV": "1"}
    logger.info(f"[导航] 重新进入《{drama_name}》...")
    subprocess.run(["adb", "shell", "input", "keyevent", "KEYCODE_HOME"],
                   capture_output=True, check=False, env=env)
    time.sleep(2)
    subprocess.run(
        ["adb", "shell", "am", "start", "-a", "android.intent.action.VIEW",
         "-d", "dragon8662://search", APP_PACKAGE],
        capture_output=True, check=False, env=env,
    )
    time.sleep(5)
    # 聚焦搜索框 + 输入
    run_adb(["shell", "input", "tap", "500", "150"])
    time.sleep(1)
    import base64 as _b64
    b64 = _b64.b64encode(drama_name.encode("utf-8")).decode("ascii")
    subprocess.run(["adb", "shell", "am", "broadcast", "-a", "ADB_INPUT_B64",
                    "--es", "msg", b64],
                   capture_output=True, check=False, env=env)
    time.sleep(2)
    # 点搜索按钮
    run_adb(["shell", "input", "tap", "984", "150"])
    time.sleep(5)
    # 动态定位剧名：dump 搜索结果 XML，找到 text == drama_name 且 y > 250（排除搜索输入框）的节点
    # 策略：在 XML 中先找到剧名节点，再找它所在的卡片容器，筛选条件：
    #   1. 卡片附近有 "万热度" 文本（正片特征；合集卡片没有"热度"只有"播放"）
    #   2. 若多个候选，取 x 最左（主推荐通常在左侧）+ 热度值最大
    import xml.etree.ElementTree as _ET
    import re as _re
    target_bounds = None
    for _dump_try in range(3):
        xml_text = read_ui_xml_from_device()
        if xml_text:
            try:
                root = _ET.fromstring(xml_text)
                # 收集所有剧名 node 的 bounds
                name_nodes = []
                for node in root.iter('node'):
                    if node.get('text', '') != drama_name:
                        continue
                    m = _re.match(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]', node.get('bounds', ''))
                    if not m:
                        continue
                    x1, y1, x2, y2 = map(int, m.groups())
                    if y1 < 250:
                        continue
                    name_nodes.append((x1, y1, x2, y2))
                # 收集所有 "XXX万热度" 节点的 bounds 与热度数值
                heat_nodes = []
                for node in root.iter('node'):
                    t = node.get('text', '')
                    hm = _re.match(r'(\d+)万热度', t)
                    if not hm:
                        continue
                    m = _re.match(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]', node.get('bounds', ''))
                    if not m:
                        continue
                    x1, y1, x2, y2 = map(int, m.groups())
                    heat_nodes.append((x1, y1, x2, y2, int(hm.group(1))))
                # 为每个剧名卡片匹配最近的热度节点（y 差 < 200 且 x 重叠）
                scored = []
                for (nx1, ny1, nx2, ny2) in name_nodes:
                    best_heat = 0
                    for (hx1, hy1, hx2, hy2, heat) in heat_nodes:
                        # 同一卡片：y 差 < 200 且 x 区间重叠
                        if abs(hy2 - ny1) > 200 and abs(hy1 - ny2) > 200:
                            continue
                        if hx2 < nx1 or hx1 > nx2:
                            continue
                        if heat > best_heat:
                            best_heat = heat
                    scored.append((best_heat, nx1, ny1, nx2, ny2))
                # 优先选热度最高；相同热度取 x 最左
                scored.sort(key=lambda s: (-s[0], s[1]))
                if scored:
                    best_heat, x1, y1, x2, y2 = scored[0]
                    target_bounds = (x1, y1, x2, y2)
                    logger.info(f"[导航] 候选卡片: {[(s[0], s[1]) for s in scored]}, 选中热度={best_heat}万")
                    break
            except _ET.ParseError:
                pass
        time.sleep(1.5)
    if target_bounds is None:
        logger.warning(f"[导航] 搜索结果未找到《{drama_name}》卡片，使用固定坐标 fallback")
        run_adb(["shell", "input", "tap", "282", "500"])
    else:
        x_c = (target_bounds[0] + target_bounds[2]) // 2
        y_c = (target_bounds[1] + target_bounds[3]) // 2
        logger.info(f"[导航] 找到《{drama_name}》卡片 bounds={target_bounds}，tap ({x_c}, {y_c})")
        run_adb(["shell", "input", "tap", str(x_c), str(y_c)])
    time.sleep(8)
    logger.info("[导航] 搜索流程完成")
    return True


def select_episode_fast(ep_num: int) -> bool:
    """快速选集：打开选集面板 → 切段 → 点击 ep。不校验高亮（避免 is_target_episode_selected_in_detail 假阴性循环）。"""
    # 1. 尝试打开选集面板（最多 3 次）
    picker_open = False
    _initial_xml = read_ui_xml_from_device()
    if _initial_xml and 'com.phoenix.read:id/ivi' in _initial_xml:
        picker_open = True

    if not picker_open:
        for _ in range(3):
            run_adb(["shell", "input", "tap", "540", "960"])
            time.sleep(1.0)
            _xml = read_ui_xml_from_device()
            joj = None
            if _xml:
                joj = find_element_by_resource_id(_xml, "com.phoenix.read:id/joj")
                if not joj:
                    joj = find_text_bounds(_xml, "选集")
            if joj:
                tap_bounds(joj)
            else:
                # fallback 已知位置
                run_adb(["shell", "input", "tap", "540", "960"])
                time.sleep(0.5)
                run_adb(["shell", "input", "tap", "138", "1836"])
            time.sleep(1.5)
            _peek = read_ui_xml_from_device()
            if _peek and 'com.phoenix.read:id/ivi' in _peek:
                picker_open = True
                break

    if not picker_open:
        logger.warning(f"[快速选集] 选集面板未打开（ep{ep_num}）")
        return False

    # 2. 切段到 ep 所在范围
    xml_text = read_ui_xml_from_device()
    if not xml_text:
        return False
    _select_episode_range(xml_text, ep_num)
    time.sleep(1.0)
    xml_text = read_ui_xml_from_device()
    if not xml_text:
        return False

    # 3. 找到 ep 按钮 tap（最多滚动 6 次）
    for attempt in range(6):
        bounds = _find_episode_button(xml_text, ep_num)
        if bounds:
            tap_bounds(bounds)
            logger.info(f"[快速选集] 已点击第{ep_num}集 ivi 按钮")
            time.sleep(2.0)
            return True
        # 滚动面板
        import re as _re
        _ys = []
        try:
            import xml.etree.ElementTree as _ET
            for _e in _ET.fromstring(xml_text).iter():
                if 'ivi' not in _e.attrib.get('resource-id', ''):
                    continue
                bm = _re.fullmatch(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]', _e.attrib.get('bounds', ''))
                if bm:
                    _ys.append((int(bm.group(2)), int(bm.group(4))))
        except Exception:
            pass
        if _ys:
            y_top, y_bot = _ys[0][0], _ys[-1][1]
            y_ctr = (y_top + y_bot) // 2
            dy = max(60, min(100, (y_bot - y_top) // 4))
            y_a, y_b = max(y_ctr - dy, y_top + 5), min(y_ctr + dy, y_bot - 5)
            # 小 ep 向上滚（手指向下），大 ep 向下滚
            if ep_num <= 15:
                run_adb(["shell", "input", "swipe", "540", str(y_a), "540", str(y_b), "500"])
            else:
                run_adb(["shell", "input", "swipe", "540", str(y_b), "540", str(y_a), "500"])
            time.sleep(0.8)
        new_xml = read_ui_xml_from_device()
        if not new_xml or 'com.phoenix.read:id/ivi' not in new_xml:
            logger.warning(f"[快速选集] 面板已关闭（滚动穿透）")
            return False
        xml_text = new_xml

    logger.warning(f"[快速选集] {6} 次滚动后仍未找到 ep{ep_num} 按钮")
    return False


def select_episode_and_wait(ep_num: int, state: HookState,
                             downloaded_vids: set[str],
                             timeout: float = 15.0) -> VideoRef | None:
    """通过选集面板精确跳到第 ep_num 集，等待 Hook 触发合法新 VideoRef。

    用选集面板（ivi 按钮）代替上滑切集，避免从最后一集/任意集滑出本剧进入推荐流。
    """
    with state.lock:
        old_ref_count = len(state.refs)

    # 用 select_episode_fast（不做"高亮校验"，避免假阴性循环）
    if not select_episode_fast(ep_num):
        logger.warning(f"[选集] 第{ep_num}集 UI 选集失败")
        return None

    deadline = time.time() + timeout
    while time.time() < deadline:
        with state.lock:
            candidates = state.refs[old_ref_count:]
        for ref in candidates:
            if ref.video_id in downloaded_vids:
                continue
            if not (EPISODE_DURATION_MIN <= ref.duration <= EPISODE_DURATION_MAX):
                continue
            logger.info(f"[选集] 第{ep_num}集 Hook 到新 ref (vid={ref.video_id[:16]}..., dur={ref.duration}s)")
            return ref
        time.sleep(0.3)

    logger.warning(f"[选集] 第{ep_num}集选集后 {timeout}s 内未收到合法新 ref")
    return None


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
    parser.add_argument("--attach-running", action="store_true",
                        help="attach 到已运行 App（跳过自动导航）。使用前请手动把 App 调到目标剧的全屏播放页")
    parser.add_argument("--total-episodes", type=int, default=None,
                        help="总集数。attach 模式下必须显式指定")
    parser.add_argument("--bootstrap-navigate", action="store_true",
                        help="attach 后先通过搜索导航到目标剧（首次启动或 App 不在本剧时用）")
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
    session, script, pid = setup_frida(APP_PACKAGE, state, attach_running=args.attach_running)

    # attach 模式：可选先 bootstrap-navigate 到目标剧
    if args.attach_running:
        if args.total_episodes is None:
            logger.error("attach 模式必须通过 --total-episodes 指定总集数")
            return
        if args.bootstrap_navigate:
            logger.info("[attach] bootstrap-navigate: 先通过搜索导航到目标剧")
            navigate_to_drama_via_search(drama_name)
        else:
            logger.info("[attach] 跳过自动导航，假设 App 已在全屏播放页")
        # 等待 Hook 产生第一条 video_ref（播放页应当已在播放）
        deadline = time.time() + 30
        while time.time() < deadline:
            ref, url, key = state.get_latest()
            if ref and url and key:
                logger.info(f"[attach] 检测到 Hook 数据 (vid={ref.video_id[:16]}..., dur={ref.duration}s)")
                break
            time.sleep(1)
        else:
            logger.warning("[attach] 30 秒内未收到 Hook 数据。请确保 App 正在播放视频；若视频暂停请播放一次")
        xml_for_total = None  # attach 模式不从 UI 读总集数
    else:
        # Spawn 模式：自动导航到离线缓存或搜索入口
        logger.info("[1/4] 导航到离线缓存...")
        cache_ok = navigate_to_offline_cache()

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
                search_drama_in_app(drama_name, start_episode=start_ep)
            except Exception as e:
                logger.error(f"搜索入口也失败: {e}")

        if not player_ok:
            logger.info("[2/4] 检查当前状态...")
            ref, url, key = state.get_after_fence(0)
            if ref and url and key:
                logger.info(f"[2/4] Hook 已捕获数据 (vid={ref.video_id[:16]}...)，确认播放器活跃")
                player_ok = True
            else:
                xml = _wait_and_dump(delay=2.0)
                if xml:
                    ctx = parse_ui_context(xml)
                    if ctx.episode is not None:
                        logger.info(f"[2/4] UI 确认在播放器中，当前第 {ctx.episode} 集")
                        player_ok = True
                    elif find_text_contains_bounds(xml, drama_name):
                        logger.info("[2/4] 在详情页，点击封面进入播放器...")
                        run_adb(["shell", "input", "tap", "195", "400"])
                        time.sleep(5)
                        ref2, url2, key2 = state.get_after_fence(0)
                        if ref2 and key2:
                            logger.info("[2/4] 点击封面后 Hook 收到数据，确认进入播放器")
                            player_ok = True

        if not player_ok:
            logger.error("无法进入播放器，退出")
            return

        # 确保在全屏播放页
        import re as _re
        xml_for_total: str | None = None
        xml_probe = _wait_and_dump(delay=1.0, retries=1)
        if xml_probe:
            m_total = _re.search(r'全(\d+)集', xml_probe)
            if m_total:
                xml_for_total = xml_probe
        ensure_fullscreen_player()

    # 4. 获取总集数：attach 模式用 CLI 参数；否则优先用 xml_for_total，再 fallback 到 UI dump
    import re as _re2
    if args.attach_running:
        total_eps = args.total_episodes
    else:
        total_eps = None
        if xml_for_total:
            m = _re2.search(r'全(\d+)集', xml_for_total)
            if m:
                total_eps = int(m.group(1))
        if total_eps is None:
            total_eps = read_ui_total_episodes()
        if total_eps is None:
            logger.warning("无法从 UI 获取总集数，使用默认值 60")
            total_eps = 60
    logger.info(f"[3/4] 总集数: {total_eps}")

    # 5. 生成下载计划
    plan = build_plan(output_dir, total_eps, start_ep)
    done_count = sum(1 for p in plan if p["status"] == "done")
    pending_count = sum(1 for p in plan if p["status"] == "pending")
    logger.info(f"[4/4] 下载计划: {pending_count} 集待下载, {done_count} 集已完成")

    # 6. 逐集下载
    #
    # 策略：播放器已在目标剧中，搜索流程已导航到起始集。
    # - 每集通过 advance_to_next_episode 切集，用 get_latest 获取 Hook 数据后下载
    # - 第一个 pending 集无需切集（播放器已在此集）
    # - done 集也需要 advance 跳过，保持播放器和下载计划同步
    # - 断点续传（start_ep > 1）由搜索流程的 start_episode 参数处理
    #
    manifest_path = os.path.join(output_dir, "session_manifest.jsonl")
    success_count = 0
    failed_eps = []
    last_video_id = ""     # 上一集的 video_id，用于 advance 去重
    downloaded_vids: set[str] = set()  # 所有已下载的 video_id，防止隔集重复

    # 加载已有 manifest 的成功记录，避免跨 session 重复 append；同时填充 downloaded_vids
    recorded_ok: set[tuple[int, str]] = set()
    if os.path.exists(manifest_path):
        with open(manifest_path, encoding="utf-8") as _f:
            for _line in _f:
                try:
                    _rec = json.loads(_line)
                    if _rec.get("status") == "ok":
                        vid = str(_rec.get("video_id", ""))
                        recorded_ok.add((int(_rec["episode"]), vid))
                        if vid:
                            downloaded_vids.add(vid)
                except (json.JSONDecodeError, KeyError, ValueError):
                    continue

    # 首集默认不 advance（假设播放器已对齐 plan[0]）；done 集在循环中会自然推动 advance
    need_advance = False
    current_ref, _, _ = state.get_latest()
    if current_ref:
        last_video_id = current_ref.video_id
    logger.info(f"[启动] downloaded_vids={len(downloaded_vids)} 条, need_advance={need_advance}")

    # 保险：连续失败次数上限，防止跨出本剧后继续空跑
    consecutive_failures = 0
    MAX_CONSECUTIVE_FAILURES = 3

    for task in tqdm(plan, desc="下载进度"):
        ep_num = task["ep"]

        # done 集：无需切集也无需下载
        if task["status"] == "done":
            logger.debug(f"[第{ep_num}集] 已完成，跳过")
            continue

        # 通过选集面板精确跳到第 ep_num 集，失败则重新导航后重试一次
        try:
            target_ref = select_episode_and_wait(ep_num, state, downloaded_vids)
        except frida.InvalidOperationError:
            logger.warning(f"[第{ep_num}集] Frida 断开，恢复...")
            session, script, pid = recover_frida(state, drama_name)
            failed_eps.append(ep_num)
            consecutive_failures += 1
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                logger.error(f"[保险] 连续 {consecutive_failures} 次选集失败，提前终止")
                break
            continue

        if target_ref is None:
            # App 可能自动连播切出本剧。重新导航回本剧播放页后再试一次
            logger.info(f"[第{ep_num}集] 选集失败，重新导航回本剧...")
            try:
                navigate_to_drama_via_search(drama_name)
                target_ref = select_episode_and_wait(ep_num, state, downloaded_vids)
            except frida.InvalidOperationError:
                logger.warning(f"[第{ep_num}集] Frida 断开，恢复...")
                session, script, pid = recover_frida(state, drama_name)

        if target_ref is None:
            logger.warning(f"[第{ep_num}集] 选集失败或未拿到合法 ref")
            failed_eps.append(ep_num)
            consecutive_failures += 1
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                logger.error(f"[保险] 连续 {consecutive_failures} 次选集失败，提前终止")
                break
            continue

        if target_ref.video_id in downloaded_vids:
            logger.warning(f"[第{ep_num}集] 目标 vid={target_ref.video_id[:16]}... 已下载过，跳过")
            failed_eps.append(ep_num)
            continue

        ref, url, key = state.get_complete_by_vid(target_ref.video_id, timeout=12.0)
        if not (ref and url and key):
            logger.warning(f"[第{ep_num}集] 按 vid 配对失败: ref={'有' if ref else '无'} url={'有' if url else '无'} key={'有' if key else '无'}")
            failed_eps.append(ep_num)
            consecutive_failures += 1
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                logger.error(f"[保险] 连续 {consecutive_failures} 次配对失败，疑似跨出本剧或 Hook 失效，提前终止")
                break
            continue

        vid8 = ref.video_id[-8:] if len(ref.video_id) >= 8 else ref.video_id
        output_path = os.path.join(output_dir, f"episode_{ep_num:03d}_{vid8}.mp4")
        expected_dur = ref.duration if ref.duration > 0 else 0

        result = None
        if download_and_decrypt(url, key.key_hex, output_path) \
                and verify_playable(output_path, expected_duration=expected_dur):
            result = {"success": True, "ep": ep_num, "video_id": ref.video_id, "path": output_path}
            last_video_id = ref.video_id
            downloaded_vids.add(ref.video_id)
        else:
            if os.path.exists(output_path):
                os.remove(output_path)
            result = {"success": False, "ep": ep_num, "video_id": "", "path": ""}

        if result["success"]:
            success_count += 1
            consecutive_failures = 0  # 成功时重置连续失败计数
            key_tuple = (result["ep"], result["video_id"])
            if key_tuple not in recorded_ok:
                append_jsonl(manifest_path, {
                    "episode": result["ep"],
                    "video_id": result["video_id"],
                    "path": result["path"],
                    "timestamp": time.time(),
                    "status": "ok",
                })
                recorded_ok.add(key_tuple)
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
