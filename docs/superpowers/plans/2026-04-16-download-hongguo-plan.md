# download_hongguo.py Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a reliable full-season downloader for HongGuo (红果) short drama app that guarantees episode accuracy and completeness.

**Architecture:** Single new script `scripts/download_hongguo.py` with fence-based capture mechanism. Reuses existing ADB/UI tools from `drama_download_common.py`, CENC decryption from `decrypt_video.py`, and `COMBINED_HOOK` from `download_drama.py`. Three-layer design: orchestrator → episode pipeline → infrastructure.

**Tech Stack:** Python 3.10+, Frida 16.5.9, ADB, ffprobe, requests, pycryptodome, loguru

---

## File Structure

- **Create:** `scripts/download_hongguo.py` — The full new script (~700 lines)
- **Create:** `tests/test_download_hongguo.py` — Unit tests for pure functions
- **Read (not modify):**
  - `scripts/drama_download_common.py` — ADB/UI tools to import
  - `scripts/decrypt_video.py` — CENC decryption to import
  - `scripts/download_drama.py` — COMBINED_HOOK constant + search_drama_in_app to import

---

### Task 1: Data Classes and HookState

**Files:**
- Create: `scripts/download_hongguo.py`
- Create: `tests/test_download_hongguo.py`

- [ ] **Step 1: Write the failing test for HookState**

```python
# tests/test_download_hongguo.py
"""Tests for download_hongguo.py"""
import sys
import time
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_hookstate_get_after_fence_empty():
    from scripts.download_hongguo import HookState
    state = HookState()
    ref, url, key = state.get_after_fence(time.time())
    assert ref is None
    assert url is None
    assert key is None


def test_hookstate_get_after_fence_filters_old_data():
    from scripts.download_hongguo import HookState, VideoRef, VideoURL, AESKey
    state = HookState()
    old_ts = time.time() - 10
    with state.lock:
        state.refs.append(VideoRef(video_id="old_vid", duration=100, timestamp=old_ts))
        state.urls.append(VideoURL(video_id="old_vid", url="http://old", quality="720p", kid="aaa", timestamp=old_ts))
        state.keys.append(AESKey(key_hex="0" * 32, bits=128, timestamp=old_ts))

    fence_ts = time.time() - 5  # 围栏在 old_ts 之后
    ref, url, key = state.get_after_fence(fence_ts)
    assert ref is None  # old data should be filtered
    assert url is None
    assert key is None


def test_hookstate_get_after_fence_returns_new_data():
    from scripts.download_hongguo import HookState, VideoRef, VideoURL, AESKey
    state = HookState()
    fence_ts = time.time() - 1
    new_ts = time.time()
    with state.lock:
        state.refs.append(VideoRef(video_id="new_vid", duration=60, timestamp=new_ts))
        state.urls.append(VideoURL(video_id="new_vid", url="http://new/1080p", quality="1080p", kid="bbb", timestamp=new_ts))
        state.urls.append(VideoURL(video_id="new_vid", url="http://new/360p", quality="360p", kid="bbb", timestamp=new_ts))
        state.keys.append(AESKey(key_hex="a" * 32, bits=128, timestamp=new_ts))

    ref, url, key = state.get_after_fence(fence_ts)
    assert ref is not None
    assert ref.video_id == "new_vid"
    assert url == "http://new/1080p"  # 最高画质
    assert key is not None
    assert key.key_hex == "a" * 32


def test_hookstate_quality_ordering():
    from scripts.download_hongguo import HookState, VideoRef, VideoURL, AESKey
    state = HookState()
    fence_ts = time.time() - 1
    ts = time.time()
    with state.lock:
        state.refs.append(VideoRef(video_id="vid1", duration=60, timestamp=ts))
        state.urls.append(VideoURL(video_id="vid1", url="http://360", quality="360p", kid="k", timestamp=ts))
        state.urls.append(VideoURL(video_id="vid1", url="http://720", quality="720p", kid="k", timestamp=ts))
        state.urls.append(VideoURL(video_id="vid1", url="http://480", quality="480p", kid="k", timestamp=ts))
        state.keys.append(AESKey(key_hex="b" * 32, bits=128, timestamp=ts))

    ref, url, key = state.get_after_fence(fence_ts)
    assert url == "http://720"  # 720p 是最高画质
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_download_hongguo.py -v`
Expected: FAIL with "ModuleNotFoundError" or "ImportError"

- [ ] **Step 3: Write data classes and HookState**

```python
# scripts/download_hongguo.py
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
    select_episode_from_ui,
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

    def clear(self):
        with self.lock:
            self.current_video_id = ""
            self.refs.clear()
            self.urls.clear()
            self.keys.clear()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_download_hongguo.py -v`
Expected: 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/download_hongguo.py tests/test_download_hongguo.py
git commit -m "feat: add data classes and HookState for download_hongguo"
```

---

### Task 2: on_message callback and setup_frida

**Files:**
- Modify: `scripts/download_hongguo.py`
- Modify: `tests/test_download_hongguo.py`

- [ ] **Step 1: Write the failing test for on_message**

```python
# tests/test_download_hongguo.py — append these tests

def test_on_message_video_ref():
    from scripts.download_hongguo import HookState, create_on_message
    state = HookState()
    handler = create_on_message(state)
    handler({"type": "send", "payload": {
        "t": "video_ref",
        "data": {"mVideoId": "v_test_123", "mVideoDuration": "90"},
        "episode_number": 3,
    }}, None)
    assert len(state.refs) == 1
    assert state.refs[0].video_id == "v_test_123"
    assert state.refs[0].duration == 90
    assert state.current_video_id == "v_test_123"


def test_on_message_video_info():
    from scripts.download_hongguo import HookState, create_on_message
    state = HookState()
    handler = create_on_message(state)
    # 先发 video_ref 设置 current_video_id
    handler({"type": "send", "payload": {
        "t": "video_ref",
        "data": {"mVideoId": "v_abc", "mVideoDuration": "60"},
    }}, None)
    # 再发 video_info
    handler({"type": "send", "payload": {
        "t": "video_info",
        "idx": 0,
        "data": {"mMainUrl": "https://cdn/video.mp4", "mResolution": "1080p", "mKid": "kid123"},
    }}, None)
    assert len(state.urls) == 1
    assert state.urls[0].video_id == "v_abc"
    assert state.urls[0].url == "https://cdn/video.mp4"
    assert state.urls[0].quality == "1080p"


def test_on_message_aes_key():
    from scripts.download_hongguo import HookState, create_on_message
    state = HookState()
    handler = create_on_message(state)
    handler({"type": "send", "payload": {
        "t": "AES_KEY",
        "key": "abcd1234abcd1234abcd1234abcd1234",
        "bits": 128,
        "dec": 0,
        "episode_number": 1,
    }}, None)
    assert len(state.keys) == 1
    assert state.keys[0].key_hex == "abcd1234abcd1234abcd1234abcd1234"
    assert state.keys[0].bits == 128


def test_on_message_ignores_non_send():
    from scripts.download_hongguo import HookState, create_on_message
    state = HookState()
    handler = create_on_message(state)
    handler({"type": "error", "description": "something"}, None)
    assert len(state.refs) == 0
    assert len(state.keys) == 0


def test_on_message_video_info_empty_url_ignored():
    from scripts.download_hongguo import HookState, create_on_message
    state = HookState()
    handler = create_on_message(state)
    handler({"type": "send", "payload": {
        "t": "video_ref",
        "data": {"mVideoId": "v1"},
    }}, None)
    handler({"type": "send", "payload": {
        "t": "video_info",
        "idx": 0,
        "data": {"mMainUrl": "", "mResolution": "360p"},
    }}, None)
    assert len(state.urls) == 0  # 空 URL 被忽略
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_download_hongguo.py::test_on_message_video_ref -v`
Expected: FAIL with "cannot import name 'create_on_message'"

- [ ] **Step 3: Implement create_on_message and setup_frida**

Append to `scripts/download_hongguo.py`:

```python
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
            logger.info(f"[Hook] libttffmpeg.so 已加载")
        elif t == "aes_hooked":
            logger.info(f"[Hook] av_aes_init 已挂钩")
        elif t == "java_ready":
            logger.info(f"[Hook] Java Hook 就绪")

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

    # 等待 libttffmpeg 加载和 Hook 就绪
    for _ in range(30):
        time.sleep(1)
        with state.lock:
            if state.keys or any(True for _ in []):  # 只等时间
                pass
        # 检查 Hook 状态通过日志确认，这里只等待足够时间
    time.sleep(5)  # 额外等待确保 App 首页加载完成

    return session, script, pid
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_download_hongguo.py -v`
Expected: 9 tests PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/download_hongguo.py tests/test_download_hongguo.py
git commit -m "feat: add on_message callback and setup_frida"
```

---

### Task 3: Navigation Functions (offline cache entry)

**Files:**
- Modify: `scripts/download_hongguo.py`

- [ ] **Step 1: Implement navigate_to_offline_cache**

Append to `scripts/download_hongguo.py`:

```python
def _wait_and_dump(delay: float = 1.5, retries: int = 3) -> str:
    """等待 UI 稳定后 dump XML，带重试"""
    time.sleep(delay)
    for attempt in range(retries):
        xml = read_ui_xml_from_device()
        if xml:
            return xml
        logger.warning(f"[UI] dump 失败，重试 ({attempt + 1}/{retries})")
        run_adb(["shell", "input", "tap", "540", "960"])  # tap 唤醒
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
            # fallback：点击右上角坐标
            run_adb(["shell", "input", "tap", "1020", "144"])
            xml = _wait_and_dump()
            if find_text_bounds(xml, "离线缓存"):
                break  # 直接到了设置页
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
            # 确认到达离线缓存页
            if find_text_bounds(xml, "已下载") or find_text_bounds(xml, "下载中"):
                logger.info("[导航] 已到达离线缓存页")
                return True
            break
        # 可能需要滚动
        run_adb(["shell", "input", "swipe", "540", "1500", "540", "800", "300"])
        xml = _wait_and_dump()
    else:
        logger.error("[导航] 无法找到「离线缓存」")
        return False

    return True


def enter_drama_from_cache(drama_name: str) -> bool:
    """在离线缓存页面找到目标剧并点击进入播放器

    返回 True 表示成功进入播放器
    """
    logger.info(f"[导航] 在缓存中查找《{drama_name}》...")
    for attempt in range(3):
        xml = _wait_and_dump(delay=1.0)
        if not xml:
            continue
        bounds = find_text_contains_bounds(xml, drama_name)
        if bounds:
            tap_bounds(bounds)
            logger.info(f"[导航] 已点击《{drama_name}》")
            # 等待播放器加载
            time.sleep(3)
            # 确认进入播放器
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
        # tap 唤醒控制层后重试
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
```

- [ ] **Step 2: Verify syntax**

Run: `python -c "import scripts.download_hongguo; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add scripts/download_hongguo.py
git commit -m "feat: add offline cache navigation and UI helpers"
```

---

### Task 4: wait_capture, download_and_decrypt, verify_playable

**Files:**
- Modify: `scripts/download_hongguo.py`
- Modify: `tests/test_download_hongguo.py`

- [ ] **Step 1: Write tests for wait_capture and verify_playable**

```python
# tests/test_download_hongguo.py — append

def test_wait_capture_returns_none_on_empty_state():
    from scripts.download_hongguo import HookState, wait_capture
    state = HookState()
    result = wait_capture(state, time.time(), timeout=1)
    assert result is None


def test_wait_capture_returns_data_when_available():
    from scripts.download_hongguo import HookState, VideoRef, VideoURL, AESKey, wait_capture
    state = HookState()
    fence_ts = time.time()
    # 模拟 Hook 数据延迟到达
    def add_data():
        time.sleep(0.3)
        ts = time.time()
        with state.lock:
            state.refs.append(VideoRef(video_id="vid1", duration=60, timestamp=ts))
            state.urls.append(VideoURL(video_id="vid1", url="http://test", quality="720p", kid="k1", timestamp=ts))
            state.keys.append(AESKey(key_hex="a" * 32, bits=128, timestamp=ts))
    t = threading.Thread(target=add_data)
    t.start()
    result = wait_capture(state, fence_ts, timeout=5)
    t.join()
    assert result is not None
    ref, url, key = result
    assert ref.video_id == "vid1"
    assert url == "http://test"
    assert key.key_hex == "a" * 32


def test_build_plan_skips_existing():
    from scripts.download_hongguo import build_plan
    import tempfile, os
    with tempfile.TemporaryDirectory() as tmpdir:
        # 创建一个已存在的 episode 文件（大于 100KB）
        ep_file = Path(tmpdir) / "episode_003_abcd1234.mp4"
        ep_file.write_bytes(b"\x00" * 200_000)
        plan = build_plan(tmpdir, total_eps=5, start_ep=1)
        statuses = {p["ep"]: p["status"] for p in plan}
        assert statuses[3] == "done"
        assert statuses[1] == "pending"
        assert statuses[5] == "pending"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_download_hongguo.py::test_wait_capture_returns_none_on_empty_state -v`
Expected: FAIL with "cannot import name 'wait_capture'"

- [ ] **Step 3: Implement wait_capture, download_and_decrypt, verify_playable, build_plan**

Append to `scripts/download_hongguo.py`:

```python
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
    # 超时，输出调试信息
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
            stream=True,
        )
        resp.raise_for_status()

        # 下载到内存
        data = bytearray(resp.content)
        size_mb = len(data) / 1024 / 1024
        logger.info(f"[下载] 完成: {size_mb:.1f}MB")

        # CENC 解密（就地修改）
        key_bytes = bytes.fromhex(key_hex)
        sample_count = decrypt_mp4(data, key_bytes)
        fix_metadata(data)
        logger.info(f"[解密] {sample_count} samples 解密完成")

        # 先写临时文件，再 rename
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

        # 检查 duration
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
        # 检查是否已存在（glob 匹配 episode_NNN_*.mp4）
        pattern = os.path.join(output_dir, f"episode_{ep:03d}_*.mp4")
        existing = glob.glob(pattern)
        if existing and os.path.getsize(existing[0]) > 100 * 1024:
            plan.append({"ep": ep, "status": "done", "path": existing[0]})
        else:
            plan.append({"ep": ep, "status": "pending"})
    return plan
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_download_hongguo.py -v`
Expected: 12 tests PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/download_hongguo.py tests/test_download_hongguo.py
git commit -m "feat: add wait_capture, download_and_decrypt, verify_playable, build_plan"
```

---

### Task 5: download_episode (fence-based single episode pipeline)

**Files:**
- Modify: `scripts/download_hongguo.py`

- [ ] **Step 1: Implement download_episode**

Append to `scripts/download_hongguo.py`:

```python
def download_episode(ep_num: int, state: HookState, output_dir: str, drama_name: str) -> dict:
    """围栏式单集下载管线

    Returns: {"success": bool, "ep": int, "video_id": str, "reason": str}
    """
    MAX_RETRIES = 3

    for attempt in range(MAX_RETRIES):
        logger.info(f"[第{ep_num}集] 开始下载 (尝试 {attempt + 1}/{MAX_RETRIES})")

        # 1. 设置围栏
        fence_ts = time.time()

        # 2. 选集
        ok = select_episode_from_ui(ep_num)
        if not ok:
            logger.warning(f"[第{ep_num}集] 选集失败，重试")
            time.sleep(2)
            continue

        # 3. 等待 Hook 数据
        result = wait_capture(state, fence_ts, timeout=30)
        if result is None:
            logger.warning(f"[第{ep_num}集] 等待捕获超时，重试")
            continue
        ref, url, key = result

        # 4. UI 校验集数
        ui_ep = read_ui_episode()
        if ui_ep is not None and ui_ep != ep_num:
            logger.warning(f"[第{ep_num}集] UI 显示第{ui_ep}集，不一致，重试")
            continue

        # 5. 下载 + 解密
        vid8 = ref.video_id[-8:] if len(ref.video_id) >= 8 else ref.video_id
        output_path = os.path.join(output_dir, f"episode_{ep_num:03d}_{vid8}.mp4")

        ok = download_and_decrypt(url, key.key_hex, output_path)
        if not ok:
            logger.warning(f"[第{ep_num}集] 下载/解密失败，重试")
            continue

        # 6. 验证
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

    # 所有重试用尽
    logger.error(f"[第{ep_num}集] {MAX_RETRIES} 次重试均失败")
    return {
        "success": False,
        "ep": ep_num,
        "video_id": "",
        "reason": "max_retries_exceeded",
    }
```

- [ ] **Step 2: Verify syntax**

Run: `python -c "from scripts.download_hongguo import download_episode; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add scripts/download_hongguo.py
git commit -m "feat: add download_episode fence-based pipeline"
```

---

### Task 6: recover_frida and main orchestrator

**Files:**
- Modify: `scripts/download_hongguo.py`

- [ ] **Step 1: Implement recover_frida and main**

Append to `scripts/download_hongguo.py`:

```python
def recover_frida(state: HookState, drama_name: str) -> tuple:
    """Frida 断连后完整恢复

    Returns: (session, script, pid)
    """
    logger.info("[恢复] Frida session 断开，重新初始化...")
    state.clear()

    session, script, pid = setup_frida(APP_PACKAGE, state)

    # 重新导航到离线缓存并进入剧
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
            from scripts.download_drama import search_drama_in_app
            player_ok = search_drama_in_app(drama_name, start_episode=start_ep)
        except Exception as e:
            logger.error(f"搜索入口也失败: {e}")

    if not player_ok:
        logger.error("无法进入播放器，退出")
        return

    # 4. 获取总集数
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
    manifest_path = os.path.join(output_dir, "session_manifest.jsonl")
    success_count = 0
    failed_eps = []

    for task in tqdm(plan, desc="下载进度"):
        if task["status"] == "done":
            continue

        ep_num = task["ep"]
        try:
            result = download_episode(ep_num, state, output_dir, drama_name)
        except frida.InvalidOperationError:
            logger.warning(f"[第{ep_num}集] Frida session 断开，恢复...")
            session, script, pid = recover_frida(state, drama_name)
            result = download_episode(ep_num, state, output_dir, drama_name)

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
```

- [ ] **Step 2: Verify CLI help works**

Run: `python scripts/download_hongguo.py --help`
Expected: Shows usage with `-n`, `-e`, `--output` arguments

- [ ] **Step 3: Commit**

```bash
git add scripts/download_hongguo.py
git commit -m "feat: add main orchestrator with recover_frida and CLI"
```

---

### Task 7: Run all tests and final verification

**Files:**
- Existing tests only

- [ ] **Step 1: Run full test suite**

Run: `pytest tests/test_download_hongguo.py -v`
Expected: All 12+ tests PASS

- [ ] **Step 2: Run existing project tests to ensure no regressions**

Run: `pytest tests/ -v --ignore=tests/test_download_hongguo.py -x`
Expected: No new failures

- [ ] **Step 3: Verify CLI interface**

Run: `python scripts/download_hongguo.py --help`
Expected: Clean help output with all arguments

- [ ] **Step 4: Verify imports work end-to-end**

Run: `python -c "from scripts.download_hongguo import main, download_episode, HookState, wait_capture, build_plan, verify_playable; print('All imports OK')"`
Expected: `All imports OK`

- [ ] **Step 5: Final commit**

```bash
git add -A
git commit -m "feat: download_hongguo.py — 红果短剧全集精准下载器

围栏式捕获机制解决 Hook 数据污染问题：
- 每集下载前设置时间围栏，过滤旧 Hook 数据
- UI 集数作为唯一真相，不依赖 Hook episode_number
- 离线缓存入口优先，减少搜索预览污染
- 临时文件 + rename 避免中断留下损坏文件
- 支持断点续传，glob 检测已完成集数"
```
