"""
红果免费短剧 一键下载工具

功能流程:
  1. 启动 App 并注入 Frida 双 Hook（Java 层 URL + Native 层 AES 密钥）
  2. 用户在手机上播放目标短剧
  3. 自动捕获视频 CDN 地址 + AES-128 解密密钥
  4. 下载 CENC 加密的 MP4
  5. 解密视频+音频轨道（AES-CTR-128）
  6. 输出可播放的 MP4 文件

用法:
  python scripts/download_drama.py                         # 自动识别剧名，下载当前播放集
  python scripts/download_drama.py -b 5                    # 连续下载 5 集（自动上滑翻集）
  python scripts/download_drama.py -e 3 -b 10              # 从第 3 集开始连续下载 10 集
  python scripts/download_drama.py -n "剧名" -q 720p       # 手动指定输出文件夹名和画质
  python scripts/download_drama.py -n "剧名" --search      # 自动在 App 内搜索并打开该剧
  python scripts/download_drama.py -n "剧名" --search -e 5 # 搜索并直接跳到第 5 集开始下载
  python scripts/download_drama.py -n "剧名" --search -b 0 # 搜索后连续下载全集

注意:
  - --search 需要在手机上安装 ADBKeyboard 以支持中文输入
    安装: adb install ADBKeyboard.apk
    启用: adb shell ime enable com.android.adbkeyboard/.AdbIME
         adb shell ime set com.android.adbkeyboard/.AdbIME
  - 不加 --search 时 -n 仅用于覆盖输出文件夹名，不在 App 内搜索
  - 不加 --search 时需在手机上手动打开目标剧并播放，脚本自动捕获数据
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

import frida
import requests
from loguru import logger
from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).parent.parent))
from scripts.decrypt_video import decrypt_mp4, fix_metadata
from scripts.preprocess_video import process_episode
from scripts.drama_download_common import (
    UIContext,
    SessionValidationState,
    apply_valid_round,
    append_jsonl,
    bounds_center,
    build_episode_paths,
    find_element_by_class,
    find_element_by_resource_id,
    find_text_bounds,
    find_text_contains_bounds,
    parse_ui_context,
    sanitize_drama_name,
    validate_round,
)

APP_PACKAGE = "com.phoenix.read"

# 组合 Frida Hook：Java 层 TTVideoEngine URL + Native 层 av_aes_init key
COMBINED_HOOK = r"""
var resolver = new ApiResolver("module");
var ffmpegLoaded = false;

// 监听 dlopen，等待 libttffmpeg.so 加载
var dlopen = resolver.enumerateMatches("exports:*!android_dlopen_ext");
if (dlopen.length > 0) {
    Interceptor.attach(dlopen[0].address, {
        onEnter: function(args) {
            try {
                var path = args[0].readUtf8String();
                if (path && path.indexOf("ttffmpeg") !== -1) {
                    send({t: "lib_loaded", path: path});
                    ffmpegLoaded = true;
                }
            } catch(e) {}
        }
    });
}

// Java Hook：TTVideoEngine.setVideoModel
Java.perform(function() {
    function dumpObj(obj) {
        var result = {};
        try {
            var cls = obj.getClass();
            while (cls != null) {
                var fields = cls.getDeclaredFields();
                for (var i = 0; i < fields.length; i++) {
                    var name = fields[i].getName();
                    if (result.hasOwnProperty(name)) continue;
                    fields[i].setAccessible(true);
                    try {
                        var val = fields[i].get(obj);
                        if (val !== null) {
                            var s = val.toString();
                            if (s.length > 0 && s.length < 5000)
                                result[name] = s;
                        }
                    } catch(e) {}
                }
                try { cls = cls.getSuperclass(); } catch(e) { break; }
            }
        } catch(e) {}
        return result;
    }

    // 沿类继承链向上查找字段；getDeclaredField 只能找到当前类声明的字段
    // 继承字段需要逐层向父类查找
    function findFieldInHierarchy(obj, name) {
        var cls = obj.getClass();
        while (cls !== null) {
            try {
                var f = cls.getDeclaredField(name);
                f.setAccessible(true);
                return f;
            } catch(e) {}
            try { cls = cls.getSuperclass(); } catch(e) { break; }
        }
        return null;
    }

    var Engine = Java.use("com.ss.ttvideoengine.TTVideoEngine");
    Engine.setVideoModel.overloads.forEach(function(ov) {
        ov.implementation = function(model) {
            try {
                // 导出 model 层字段，包含标题和短剧信息
                send({t: "video_model", data: dumpObj(model)});

                var refField = findFieldInHierarchy(model, "vodVideoRef");
                if (refField) {
                    var ref = refField.get(model);
                    if (ref) {
                        var refData = dumpObj(ref);
                        send({t: "video_ref", data: refData});
                        // mVideoList 可能声明在父类上，需要沿继承链查找
                        var listField = findFieldInHierarchy(ref, "mVideoList");
                        if (listField) {
                            var list = Java.cast(listField.get(ref), Java.use("java.util.List"));
                            for (var i = 0; i < list.size(); i++) {
                                send({t: "video_info", idx: i, data: dumpObj(list.get(i))});
                            }
                        }
                    }
                }
            } catch(e) {
                send({t: "err", e: e.toString()});
            }
            return ov.apply(this, arguments);
        };
    });
    send({t: "java_ready"});
});

// Native Hook：av_aes_init，延迟到 libttffmpeg 加载后执行
function hookAesInit() {
    function bh(p, l) {
        var h = "";
        try { for (var i = 0; i < l; i++) {
            var b = (p.add(i).readU8() & 0xFF).toString(16);
            h += (b.length === 1 ? "0" : "") + b;
        }} catch(e) {}
        return h;
    }
    var r = new ApiResolver("module");
    var m = r.enumerateMatches("exports:*libttffmpeg*!av_aes_init");
    if (m.length > 0) {
        Interceptor.attach(m[0].address, {
            onEnter: function(args) {
                var bits = args[2].toInt32();
                var dec = args[3].toInt32();
                if (bits === 128 || bits === 256) {
                    send({t: "AES_KEY", bits: bits, dec: dec, key: bh(args[1], bits / 8)});
                }
            }
        });
        send({t: "aes_hooked"});
    }
}

var pollCount = 0;
var pollTimer = setInterval(function() {
    pollCount++;
    if (ffmpegLoaded) { clearInterval(pollTimer); setTimeout(hookAesInit, 2000); }
    else if (pollCount > 60) { clearInterval(pollTimer); hookAesInit(); }
}, 500);

send({t: "ready"});
"""


class CaptureState:
    def __init__(self) -> None:
        self.video_urls: list[dict] = []
        self.aes_keys: list[str] = []
        self.video_refs: list[dict] = []
        self.video_models: list[dict] = []
        self.drama_title: str = ""
        self.captured_episodes: dict[str, int] = {}  # video_id → 集号（从 Hook 数据提取）
        self._current_video_id: str = ""
        self.java_ready = False
        self.aes_hooked = False
        self.capture_round = 0
        self.lock = threading.Lock()

    def clear(self) -> None:
        with self.lock:
            self.video_urls.clear()
            self.aes_keys.clear()
            self.video_refs.clear()
            self.video_models.clear()
            self.captured_episodes.clear()
            self.capture_round += 1

    @property
    def has_data(self) -> bool:
        return len(self.video_urls) > 0 and len(self.aes_keys) > 0

    def best_video(self, quality: str, video_id: str = "") -> dict | None:
        """选择最合适的视频 URL 条目。

        参数：
            quality: 期望分辨率，例如 "1080p"
            video_id: 如果传入，则只考虑该视频 ID 对应的 URL
        """
        urls = self.video_urls
        if video_id:
            urls = [v for v in urls if v.get("video_id") == video_id]
        if not urls:
            urls = self.video_urls  # 筛选无结果时回退到全部 URL
        if not urls:
            return None

        RES_RANK = {"1080p": 5, "720p": 4, "540p": 3, "480p": 2, "360p": 1}

        def _is_bytevc2(v: dict) -> bool:
            return "bytevc2" in v.get("codec", "").lower()

        def _codec_score(v: dict) -> int:
            c = v.get("codec", "").lower()
            for key, score in {"h264": 4, "h265": 3, "bytevc1": 2}.items():
                if key in c:
                    return score
            return 1

        playable = [v for v in urls if not _is_bytevc2(v)]
        pool = playable or list(urls)

        at_quality = [v for v in pool if v.get("resolution") == quality]
        candidates = at_quality or pool

        candidates.sort(
            key=lambda v: (
                RES_RANK.get(v.get("resolution", ""), 0),
                _codec_score(v),
            ),
            reverse=True,
        )

        best = candidates[0]
        if _is_bytevc2(best):
            logger.warning("仅有 bytevc2 编码可用，PotPlayer 可能无法播放")
        return best


def run_adb(args: list[str]) -> None:
    env = {**os.environ, "MSYS_NO_PATHCONV": "1"}
    subprocess.run(["adb"] + args, capture_output=True, check=False, env=env)


def get_frida_usb_device():
    run_adb(["devices"])
    try:
        return frida.get_usb_device(timeout=5)
    except (frida.InvalidArgumentError, frida.TimedOutError) as exc:
        logger.error(f"未找到 Frida USB 设备: {exc}")
        logger.error("请确认手机已连接并开启 USB 调试，`adb devices` 能看到设备，且手机端 frida-server 已启动。")
        logger.error('启动示例: adb shell su -c "/data/local/tmp/frida-server &"')
        return None


def swipe_next_episode() -> None:
    """在手机上上滑，切换到下一集。"""
    run_adb(["shell", "input", "swipe", "540", "1500", "540", "400", "300"])
    logger.info("[ADB] 已上滑切换下一集")


def click_next_episode_button() -> bool:
    """点击短剧播放器 UI 中的“下一集”按钮。

    先被动读取 XML，不点击屏幕，避免误触播放/暂停。
    如果按钮不可见，再点击一次唤出控制层后重试。
    """
    # 先被动检查，避免不必要的中心点点击切换播放/暂停
    xml_text = read_ui_xml_from_device()
    if xml_text:
        bounds = find_text_bounds(xml_text, "下一集")
        if bounds:
            tap_bounds(bounds)
            logger.info("[ADB] 已点击「下一集」按钮")
            return True

    # 控制层被隐藏时，先唤出播放器控件再重试一次
    run_adb(["shell", "input", "tap", "540", "960"])
    time.sleep(0.5)  # 200-300ms 处理 dump
    xml_text = read_ui_xml_from_device()
    if not xml_text:
        return False
    bounds = find_text_bounds(xml_text, "下一集")
    if not bounds:
        logger.debug("[ADB] 未在 UI 中找到「下一集」按钮")
        return False
    tap_bounds(bounds)
    logger.info("[ADB] 已点击「下一集」按钮")
    return True


def tap_bounds(bounds: tuple[int, int, int, int]) -> None:
    x, y = bounds_center(bounds)
    run_adb(["shell", "input", "tap", str(x), str(y)])


def _find_episode_button(xml_text: str, ep_num: int) -> tuple | None:
    """在选集网格中查找指定集数按钮的 bounds。

    只匹配 resource-id 为 ``ivi`` 的选集按钮，避免误命中
    分段按钮（如 "1-30"）或总集数文案等元素。
    """
    import xml.etree.ElementTree as _ET

    target = str(ep_num)
    try:
        root = _ET.fromstring(xml_text)
        for elem in root.iter():
            rid = elem.attrib.get('resource-id', '')
            text = (elem.attrib.get('text') or '').strip()
            if rid == 'com.phoenix.read:id/ivi' and text == target:
                bounds_str = elem.attrib.get('bounds', '')
                match = re.fullmatch(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]', bounds_str)
                if match:
                    return tuple(int(p) for p in match.groups())
    except _ET.ParseError:
        pass
    return None


def _select_episode_range(xml_text: str, ep_num: int) -> bool:
    """目标集不在当前可见范围时，点击正确的分段页签。

    例如目标为第 35 集时点击 "31-60"。如果点击了分段按钮则返回 True。
    """
    import xml.etree.ElementTree as _ET

    try:
        root = _ET.fromstring(xml_text)
        for elem in root.iter():
            rid = elem.attrib.get('resource-id', '')
            text = (elem.attrib.get('text') or '').strip()
            if rid != 'com.phoenix.read:id/gi1' or not text:
                continue
            # 解析类似 "1-30"、"31-60" 的分段文本
            m = re.fullmatch(r'(\d+)-(\d+)', text)
            if not m:
                continue
            lo, hi = int(m.group(1)), int(m.group(2))
            if lo <= ep_num <= hi:
                bounds_str = elem.attrib.get('bounds', '')
                bm = re.fullmatch(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]', bounds_str)
                if bm:
                    tap_bounds(tuple(int(p) for p in bm.groups()))
                    logger.info(f"[ADB] 已切换到范围 {text}")
                    time.sleep(1.0)
                    return True
    except _ET.ParseError:
        pass
    return False


def select_episode_from_ui(ep_num: int, max_attempts: int = 8) -> bool:
    """打开选集面板并点击指定集数。

    使用 resource-id ``ivi`` 精确匹配选集网格按钮，并处理超过 30 集时的
    分段切换（例如 "31-60"）。

    上下文处理：
    - 短剧详情页：``ivi`` 选集网格已经可见，跳过 (540,960) 的唤醒点击，
      避免误点详情页内容区触发播放。
    - 播放器页：``ivi`` 不可见，先唤出控制层，再打开选集面板并查找集数。
    """
    import xml.etree.ElementTree as _ET_sel

    def _has_ivi(xml_text: str) -> bool:
        try:
            root = _ET_sel.fromstring(xml_text)
            return any('ivi' in e.attrib.get('resource-id', '') for e in root.iter())
        except _ET_sel.ParseError:
            return False

    # 先检查是否已经在详情页选集网格中，避免误触播放器区域
    _initial_xml = read_ui_xml_from_device()
    picker_open = _initial_xml is not None and _has_ivi(_initial_xml)
    if picker_open:
        logger.debug("[ADB] 剧情详情页集数网格已可见，跳过打开选集弹窗")

    if not picker_open:
        # 最多尝试 3 次打开选集面板：先唤醒控制层，再点击“选集”按钮
        # 优先通过 resource-id "joj" 或文本“选集”动态定位，失败时回退到坐标 (450,1835)
        for _open_try in range(3):
            # 唤醒播放器控制层
            run_adb(["shell", "input", "tap", "540", "960"])
            time.sleep(0.5)  # 200-300ms 处理 dump

            # 读取控制层 XML 并动态定位“选集”按钮
            _overlay_xml = read_ui_xml_from_device()
            _joj_bounds = None
            if _overlay_xml:
                _joj_bounds = find_element_by_resource_id(_overlay_xml, "com.phoenix.read:id/joj")
                if not _joj_bounds:
                    _joj_bounds = find_text_bounds(_overlay_xml, "选集")

            if _joj_bounds:
                logger.debug(f"[ADB] 找到选集按钮 bounds={_joj_bounds}，点击")
                tap_bounds(_joj_bounds)
            else:
                logger.debug("[ADB] 未找到选集按钮，使用备用坐标 (450,1835)")
                run_adb(["shell", "input", "tap", "450", "1835"])
            time.sleep(1.5)

            # 只要 XML 中出现任意 ivi 元素，就说明选集面板已经打开
            _peek_xml = read_ui_xml_from_device()
            if _peek_xml and _has_ivi(_peek_xml):
                picker_open = True
                break
            if _open_try < 2:
                logger.debug(f"[ADB] 选集面板未打开，重试 ({_open_try + 1}/3)")

    range_switched = False
    for attempt in range(max_attempts):
        xml_text = read_ui_xml_from_device()
        if not xml_text:
            time.sleep(1)
            continue

        # 必要时切换到正确分段页签，例如第 35 集对应 "31-60"
        if not range_switched and ep_num > 30:
            if _select_episode_range(xml_text, ep_num):
                range_switched = True
                xml_text = read_ui_xml_from_device()
                if not xml_text:
                    time.sleep(1)
                    continue

        # 通过 resource-id ivi 精确查找集数按钮
        target_bounds = _find_episode_button(xml_text, ep_num)
        if target_bounds:
            tap_bounds(target_bounds)
            logger.info(f"[ADB] 已在选集面板点击第{ep_num}集")
            time.sleep(2.5)
            return True

        # 逻辑
        if attempt < max_attempts // 2:
            run_adb(["shell", "input", "swipe", "540", "1780", "540", "1540", "300"])
        else:
            run_adb(["shell", "input", "swipe", "540", "1540", "540", "1780", "300"])
        time.sleep(0.8)

    logger.error(f"[ADB] 未能在选集面板找到第{ep_num}集")
    # 处理 XML 处理
    try:
        _debug_xml = read_ui_xml_from_device()
        if _debug_xml:
            # ivi XML 1500 处理
            import xml.etree.ElementTree as _ET
            try:
                _root = _ET.fromstring(_debug_xml)
                _ivi_texts = [
                    (e.attrib.get('resource-id',''), e.attrib.get('text',''))
                    for e in _root.iter()
                    if 'ivi' in e.attrib.get('resource-id','') or e.attrib.get('text','').strip().isdigit()
                ][:20]
                logger.debug(f"[ADB] picker XML ivi/digit elements: {_ivi_texts}")
            except Exception:
                pass
            logger.debug(f"[ADB] picker XML (first 1500): {_debug_xml[:1500]}")
    except Exception:
        pass
    return False


def read_ui_xml_from_device() -> str:
    """dump 并返回手机当前 UI XML。

    两步命令均加了 timeout，避免设备渲染视频时 uiautomator dump 无限挂起。
    dump 失败（非零退出码）时先删除旧文件再返回空串，防止 cat 读到陈旧数据。
    """
    env = {**os.environ, "MSYS_NO_PATHCONV": "1"}
    try:
        dump_result = subprocess.run(
            ["adb", "shell", "uiautomator", "dump", "/sdcard/_ui.xml"],
            capture_output=True, check=False, env=env, timeout=12,
        )
    except subprocess.TimeoutExpired:
        logger.debug("[ADB] uiautomator dump 超时 (12s)")
        return ""
    if dump_result.returncode != 0:
        logger.debug(f"[ADB] uiautomator dump 失败 (rc={dump_result.returncode})")
        return ""
    try:
        result = subprocess.run(
            ["adb", "shell", "cat", "/sdcard/_ui.xml"],
            capture_output=True, check=False, env=env, timeout=8,
        )
    except subprocess.TimeoutExpired:
        logger.debug("[ADB] cat _ui.xml 超时 (8s)")
        return ""
    if result.returncode != 0 or not result.stdout:
        return ""
    return result.stdout.decode("utf-8", errors="replace")


def get_current_activity() -> str:
    """通过 dumpsys window 返回当前前台 Activity 名称。

    相比 uiautomator dump，即使 App 中的全屏视频或动画阻塞了 UI 层级捕获，
    这个方式也更稳定。

    返回 Activity 短名，例如 'MainFragmentActivity'；无法识别时返回空字符串。
    """
    env = {**os.environ, "MSYS_NO_PATHCONV": "1"}
    try:
        result = subprocess.run(
            ["adb", "shell", "dumpsys", "window", "displays"],
            capture_output=True, check=False, env=env, timeout=10,
        )
    except subprocess.TimeoutExpired:
        return ""
    if result.returncode != 0 or not result.stdout:
        return ""
    output = result.stdout.decode("utf-8", errors="replace")
    for line in output.splitlines():
        if "mCurrentFocus" not in line and "mFocusedApp" not in line:
            continue
        match = re.search(r'com\.phoenix\.read/[\w.]+\.(\w+Activity)\b', line)
        if match:
            return match.group(1)
    return ""


def open_search_via_deeplink() -> bool:
    """使用 App 的 deeplink scheme 打开搜索页。

    跳转后如果检测到搜索 EditText，则返回 True。
    """
    env = {**os.environ, "MSYS_NO_PATHCONV": "1"}
    subprocess.run(
        ["adb", "shell", "am", "start", "-a", "android.intent.action.VIEW",
         "-d", "dragon8662://search", APP_PACKAGE],
        capture_output=True, check=False, env=env,
    )
    time.sleep(2.0)

    xml_text = read_ui_xml_from_device()
    if not xml_text:
        return False
    if find_element_by_resource_id(xml_text, "com.phoenix.read:id/h7h"):
        logger.info("[搜索] 深度链接成功打开搜索页")
        return True
    if find_element_by_class(xml_text, "android.widget.EditText"):
        logger.info("[搜索] 深度链接成功打开搜索页 (EditText)")
        return True
    return False


def detect_ui_context_from_device() -> UIContext:
    """从手机当前 UI 解析剧名和集数信息。"""
    xml_text = read_ui_xml_from_device()
    if not xml_text:
        return UIContext()

    context = parse_ui_context(xml_text)
    if context.title:
        logger.info(f"[UI] Title: {context.title}")
    if context.episode is not None:
        total = f"/{context.total_episodes}" if context.total_episodes else ""
        logger.info(f"[UI] Episode: {context.episode}{total}")
    return context


def detect_drama_title_from_ui() -> str:
    """从手机当前 UI 提取剧名。"""
    return detect_ui_context_from_device().title


def adb_type_text(text: str) -> bool:
    """向当前聚焦输入框输入文本。

    优先使用 ADBKeyboard 广播（支持中文和 Unicode），失败时仅对 ASCII 文本
    回退到 adb input。

    中文输入需要 ADBKeyboard：
      1. 下载: https://github.com/senzhk/ADBKeyBoard/releases
      2. 安装: adb install ADBKeyboard.apk
      3. 启用: adb shell ime enable com.android.adbkeyboard/.AdbIME
      4. 设置: adb shell ime set com.android.adbkeyboard/.AdbIME
    """
    env = {**os.environ, "MSYS_NO_PATHCONV": "1"}

    # 方法 1：ADBKeyboard 广播，支持中文
    result = subprocess.run(
        ["adb", "shell", "am", "broadcast", "-a", "ADB_INPUT_TEXT", "--es", "msg", text],
        capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
    )
    if "Broadcast completed: result=0" in result.stdout:
        logger.info(f"[输入] ADBKeyboard 输入成功: {text}")
        time.sleep(0.5)
        return True

    # 方法 2：直接 adb input，仅支持 ASCII
    if all(ord(c) < 128 for c in text):
        subprocess.run(
            ["adb", "shell", "input", "text", text],
            capture_output=True, env=env,
        )
        logger.info(f"[输入] 直接输入: {text}")
        return True

    logger.error(
        "[输入] 无法输入中文！请安装 ADBKeyboard 并设为默认输入法:\n"
        "  1. 下载: https://github.com/senzhk/ADBKeyBoard/releases\n"
        "  2. 安装: adb install ADBKeyboard.apk\n"
        "  3. 启用: adb shell ime enable com.android.adbkeyboard/.AdbIME\n"
        "  4. 设置: adb shell ime set com.android.adbkeyboard/.AdbIME"
    )
    return False


def _exit_to_home_screen(max_attempts: int = 4) -> bool:
    """返回 App 主信息流页面。

    先按 Android HOME 键回到系统桌面，再通过后续 deeplink 或点搜索图标进入 App。
    这里不使用 BACK 键，因为该 App 的播放器页不会可靠地通过 BACK 返回主页。
    优先用 ``dumpsys window`` 判断前台 Activity，必要时回退到 UI dump。
    """
    # HOME 键无论当前是否在播放器中，都能退回 Android 桌面
    run_adb(["shell", "input", "keyevent", "KEYCODE_HOME"])
    time.sleep(1.5)

    for _ in range(max_attempts):
        activity = get_current_activity()
        if activity:
            if "MainFragmentActivity" in activity:
                logger.info("[搜索] 已确认在主页 (MainFragmentActivity)")
                return True

        xml_text = read_ui_xml_from_device()
        if xml_text:
            # 已经在 Android 桌面，不在 App 内；这对后续 deeplink 是可接受状态
            if APP_PACKAGE not in xml_text:
                logger.info("[搜索] 已确认在 Android 桌面")
                return True
            if APP_PACKAGE in xml_text:
                has_home_tabs = "首页" in xml_text or "剧场" in xml_text
                in_player = "全屏观看" in xml_text or "倍速" in xml_text
                if has_home_tabs and not in_player:
                    logger.info("[搜索] 已确认在主页 (UI dump)")
                    return True

        # 如果仍在播放器中，尝试按一次 BACK；部分播放器 Activity 会响应
        run_adb(["shell", "input", "keyevent", "KEYCODE_BACK"])
        time.sleep(1.0)

    logger.warning("[搜索] 未确认回到主页，继续...")
    return False


def _is_search_results_page(xml_text: str) -> bool:
    """判断当前页面是否为搜索结果网格，而不是自动补全页。

    同时兼容旧布局（综合/漫剧页签）和新卡片布局（万热度标识）。
    """
    # 旧布局：可见分类页签
    if "综合" in xml_text and ("漫剧" in xml_text or "影视" in xml_text):
        return True
    # 新卡片布局：结果卡片中存在热度标识
    if "万热度" in xml_text:
        return True
    return False


def _name_search_keys(name: str) -> list[str]:
    """返回用于匹配搜索结果剧名的一组候选子串。

    用于处理 App 把阿拉伯数字转成中文数字的情况，例如 "18" → "十八"。
    """
    keys = [name]
    # 去掉开头的阿拉伯数字，例如 "18岁太奶奶" → "岁太奶奶"
    stripped = name.lstrip("0123456789")
    if stripped and stripped != name:
        keys.append(stripped)
        if len(stripped) >= 4:
            keys.append(stripped[:6])
    if len(name) >= 4:
        keys.append(name[:4])
    return keys


def _find_search_result(xml_text: str, name: str) -> tuple | None:
    """在搜索相关页面中查找匹配剧名元素的 bounds。

    兼容自动补全建议、旧版页签布局和新版卡片布局。
    防护条件：元素 y 坐标必须大于 150（位于搜索框下方），并跳过搜索输入框本身。
    优先返回非续集结果，例如优先选第一部而不是第四部。
    """
    import xml.etree.ElementTree as _ET

    _SEARCH_INPUT_IDS = {
        'com.phoenix.read:id/h7h',  # 搜索输入框
    }
    # 表示续集的关键词；优先选择不包含这些关键词的结果
    _SEQUEL_MARKERS = ('第二部', '第三部', '第四部', '第五部', '第六部',
                       '第七部', '第八部', '第九部', '第十部', '续集')

    def _elem_bounds(elem) -> tuple | None:
        bounds_str = elem.attrib.get('bounds', '')
        m = re.fullmatch(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]', bounds_str)
        if not m:
            return None
        t = tuple(int(p) for p in m.groups())
        # 顶部 y 坐标必须大于 150，且元素不能是搜索输入框
        if t[1] <= 150:
            return None
        if elem.attrib.get('resource-id', '') in _SEARCH_INPUT_IDS:
            return None
        return t

    keys = _name_search_keys(name)
    matched_non_sequel: tuple | None = None  # 首个不包含续集标记的匹配项
    matched_any: tuple | None = None    # 首个匹配项，不区分是否为续集

    try:
        root = _ET.fromstring(xml_text)
        for elem in root.iter():
            rid = elem.attrib.get('resource-id', '')
            text = (elem.attrib.get('text') or '').strip()
            if not text:
                continue

            matches = False
            # 优先匹配已知结果卡片 resource-id
            if rid == 'com.phoenix.read:id/jy3':
                if text == name or name in text or any(k in text for k in keys):
                    matches = True
            # 通用匹配：任意命中候选关键词的文本元素
            if not matches:
                for key in keys:
                    if key in text:
                        matches = True
                        break

            if matches:
                t = _elem_bounds(elem)
                if t:
                    is_sequel = any(marker in text for marker in _SEQUEL_MARKERS)
                    if not is_sequel and matched_non_sequel is None:
                        matched_non_sequel = t
                    elif is_sequel and matched_any is None:
                        matched_any = t
                    # 一旦找到非续集匹配项就提前结束
                    if matched_non_sequel is not None:
                        break
    except _ET.ParseError:
        pass

    return matched_non_sequel or matched_any


def _try_start_episode_on_drama_page(ep_num: int) -> bool:
    """进入短剧详情页或选集页后，点击目标集。

    如果已经在播放器页（会自动播放），或成功点击集数按钮触发播放，则返回 True。
    如果页面既不像播放器页也不像选集页，则返回 False。
    """
    import xml.etree.ElementTree as _ET

    xml_text = read_ui_xml_from_device()
    if not xml_text:
        return False

    # 播放器页检测：
    # - jjj：集数指示（如“第1集”），只出现在播放器页
    # - joj：“选集”按钮，播放器页和详情页都会出现，不能单独作为判断依据
    # - 只有当 ivi（选集网格按钮）不存在时，才结合 joj 判断播放器页
    # - “全屏观看”/“倍速”：播放器控制项
    has_joj = 'com.phoenix.read:id/joj' in xml_text
    has_ivi = 'com.phoenix.read:id/ivi' in xml_text
    is_player_page = (
        'com.phoenix.read:id/jjj' in xml_text          # 集数指示，只在播放器页出现
        or (has_joj and not has_ivi)                    # 有“选集”但无选集网格，视为播放器页
        or '全屏观看' in xml_text
        or '倍速' in xml_text
    )
    if is_player_page:
        # 检测播放器当前实际集号，不能假设是第1集
        current_playing_ep = None
        try:
            _root = _ET.fromstring(xml_text)
            for _elem in _root.iter():
                if _elem.attrib.get('resource-id') == 'com.phoenix.read:id/jjj':
                    _jjj_text = (_elem.attrib.get('text') or '').strip()
                    _ep_m = re.search(r'第\s*(\d+)\s*[集话]', _jjj_text)
                    if _ep_m:
                        current_playing_ep = int(_ep_m.group(1))
                    break
        except _ET.ParseError:
            pass
        if current_playing_ep is not None and current_playing_ep == ep_num:
            logger.info(f"[搜索] 播放器已在第{current_playing_ep}集，与目标一致")
            return True
        # 需要切集：检查 select_episode_from_ui 返回值
        if current_playing_ep is not None:
            logger.info(f"[搜索] 播放器当前在第{current_playing_ep}集，需切换到第 {ep_num} 集")
        else:
            logger.warning(f"[搜索] 播放器页面无法识别当前集号，尝试导航到第 {ep_num} 集")
        if not select_episode_from_ui(ep_num):
            logger.error(f"[搜索] 选集面板未能切换到第 {ep_num} 集")
            return False
        return True

    # 在短剧详情页中查找可见的选集按钮（resource-id ivi）
    try:
        root = _ET.fromstring(xml_text)
        for elem in root.iter():
            rid = elem.attrib.get('resource-id', '')
            text = (elem.attrib.get('text') or '').strip()
            if rid == 'com.phoenix.read:id/ivi' and text == str(ep_num):
                bounds_str = elem.attrib.get('bounds', '')
                m = re.fullmatch(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]', bounds_str)
                if m:
                    tap_bounds(tuple(int(p) for p in m.groups()))
                    logger.info(f"[搜索] 已在剧情详情页点击第 {ep_num} 集")
                    time.sleep(2.5)
                    return True
    except _ET.ParseError:
        pass

    # 目标集超过 30 时，先切换分段页签
    if ep_num > 30:
        _select_episode_range(xml_text, ep_num)
        xml_text = read_ui_xml_from_device()
        if xml_text:
            bounds = _find_episode_button(xml_text, ep_num)
            if bounds:
                tap_bounds(bounds)
                logger.info(f"[搜索] 已切换范围并点击第 {ep_num} 集")
                time.sleep(2.5)
                return True

    # 尝试点击短剧详情页上的播放按钮
    for play_text in ('立即播放', '继续播放', '播放'):
        b = find_text_bounds(xml_text, play_text)
        if b and b[1] > 150:
            tap_bounds(b)
            logger.info(f"[搜索] 已点击 '{play_text}' 按钮")
            time.sleep(2.5)
            return True

    return False


def search_drama_in_app(name: str, start_episode: int = 1) -> bool:
    """在红果 App 内按剧名搜索，并导航到目标剧集。

    策略（每步都有主路径和回退路径）：
      1. 通过 deeplink ``dragon8662://search`` 打开搜索页；
         回退路径：返回主页后点击坐标 (1035, 80) 的搜索图标。
      2. 通过 resource-id ``h7h`` 查找 EditText，清空并用 ADBKeyboard 输入。
      3. 通过 resource-id ``h96`` 点击“搜索”按钮；回退路径：发送 ENTER。
      4. 通过 resource-id ``jy3`` 查找结果；回退路径：文本匹配。
      5. 如果 start_episode > 1，则进入对应集。

    成功返回 True，失败返回 False。
    """
    logger.info(f"\n[搜索] ===== 自动搜索: 《{name}》 =====")

    # ---- 步骤 1：打开搜索页 ----
    search_page_ready = False

    # 先按 HOME 退出可能存在的播放器页；该 App 中 BACK 不会可靠退出播放器，
    # 从播放器上下文发送 deeplink 会被拦截。HOME 会回到 Android 桌面，使 deeplink 更稳定。
    run_adb(["shell", "input", "keyevent", "KEYCODE_HOME"])
    time.sleep(2.0)  # 等待桌面稳定后再发送 deeplink

    # 方法 A：deeplink；最多尝试 2 次，规避播放器退出后的偶发时序问题
    for attempt in range(2):
        logger.info(f"[搜索] 尝试通过深度链接打开搜索页 (第{attempt+1}次)...")
        search_page_ready = open_search_via_deeplink()
        if search_page_ready:
            break
        if attempt == 0:
            # 第一次失败后，再按一次 HOME 并稍等更久后重试
            logger.info("[搜索] 深度链接第1次失败，稍等后重试...")
            run_adb(["shell", "input", "keyevent", "KEYCODE_HOME"])
            time.sleep(2.5)

    # 方法 B：通过 am start 启动 App，再点击搜索图标
    if not search_page_ready:
        logger.info("[搜索] 深度链接均失败，尝试直接启动App后点击搜索图标...")
        env = {**os.environ, "MSYS_NO_PATHCONV": "1"}
        subprocess.run(
            ["adb", "shell", "am", "start", "-n",
             f"{APP_PACKAGE}/{APP_PACKAGE}.activity.MainFragmentActivity"],
            capture_output=True, check=False, env=env,
        )
        time.sleep(3.0)  # 等待 App 主界面加载
        run_adb(["shell", "input", "tap", "1035", "80"])
        logger.info("[搜索] 已点击搜索图标 (1035, 80)")
        time.sleep(1.5)

        xml_text = read_ui_xml_from_device()
        if xml_text:
            if find_element_by_resource_id(xml_text, "com.phoenix.read:id/h7h"):
                search_page_ready = True
            elif find_element_by_class(xml_text, "android.widget.EditText"):
                search_page_ready = True

    if not search_page_ready:
        logger.error("[搜索] 无法打开搜索页，搜索失败")
        return False

    # ---- 步骤 2：清空并输入剧名 ----
    logger.info("[搜索] 搜索页已就绪，输入剧名...")

    xml_text = read_ui_xml_from_device()
    search_input = None
    if xml_text:
        search_input = find_element_by_resource_id(xml_text, "com.phoenix.read:id/h7h")
        if not search_input:
            search_input = find_element_by_class(xml_text, "android.widget.EditText")
    if not search_input:
        logger.error("[搜索] 未找到搜索输入框")
        return False

    tap_bounds(search_input)
    time.sleep(0.5)

    # 清空：优先点击 ✕ 按钮，失败时回退到全选并删除
    clear_btn = None
    xml_text = read_ui_xml_from_device()
    if xml_text:
        clear_btn = find_element_by_resource_id(xml_text, "com.phoenix.read:id/h7b")
    if clear_btn:
        tap_bounds(clear_btn)
        time.sleep(0.3)
    else:
        run_adb(["shell", "input", "keyevent", "KEYCODE_MOVE_END"])
        time.sleep(0.1)
        run_adb(["shell", "input", "keyevent", "KEYCODE_CTRL_A"])
        time.sleep(0.2)
        run_adb(["shell", "input", "keyevent", "KEYCODE_DEL"])
        time.sleep(0.2)

    if not adb_type_text(name):
        return False
    time.sleep(1.5)

    # ---- 步骤 3：提交搜索 ----
    # 这里刻意跳过自动补全建议；点击补全项只会填充搜索框并提交搜索，
    # 不会直接进入短剧播放器。步骤 3 和步骤 4 负责提交搜索并点击真实结果卡片。
    xml_text = read_ui_xml_from_device()
    search_btn = None
    if xml_text:
        search_btn = find_element_by_resource_id(xml_text, "com.phoenix.read:id/h96")
    if search_btn:
        tap_bounds(search_btn)
        logger.info("[搜索] 已点击搜索按钮")
    else:
        run_adb(["shell", "input", "keyevent", "KEYCODE_ENTER"])
        logger.info("[搜索] 已按回车提交搜索")
    time.sleep(3)

    # ---- 步骤 4：查找并点击匹配结果 ----
    found = False
    for attempt in range(8):
        xml_text = read_ui_xml_from_device()
        if xml_text:
            bounds = _find_search_result(xml_text, name)
            if bounds:
                tap_bounds(bounds)
                logger.info(f"[搜索] 已点击搜索结果: 《{name}》")
                time.sleep(3)
                found = True
                break
        logger.info(f"[搜索] 等待搜索结果... ({attempt + 1}/8)")
        time.sleep(2)

    if not found:
        logger.error(f"[搜索] 未在搜索结果中找到: 《{name}》")
        return False

    # ---- 步骤 5：等待短剧详情页加载完成 ----
    # 点击搜索结果后，选集网格（ivi 按钮）需要几秒钟才能渲染。
    # 在 ivi 可见前交互可能误触播放器跳转，进入不会触发 video_info 的缓存 URL 路径。
    # 继续前最多等待 15 秒，直到 ivi 出现。
    logger.info("[搜索] 等待剧情详情页加载完成 (ivi 出现)...")
    for _wait_idx in range(10):
        time.sleep(1.5)
        _xml_wait = read_ui_xml_from_device()
        if _xml_wait and 'com.phoenix.read:id/ivi' in _xml_wait:
            logger.info(f"[搜索] 详情页已加载 (ivi 可见，用时约 {(_wait_idx + 1) * 1.5:.0f}s)")
            break
    else:
        logger.warning("[搜索] 等待15s后 ivi 仍未出现，继续尝试")

    # ---- 步骤 6：开始播放目标集 ----
    # 短剧详情页不会自动播放，必须显式点击集数按钮。
    if not _try_start_episode_on_drama_page(start_episode):
        # 回退方案：已在播放器中时使用播放器内选集面板
        if start_episode > 1:
            if not select_episode_from_ui(start_episode):
                logger.warning(f"[搜索] 无法自动选集，请手动选择第 {start_episode} 集")

    logger.info("[搜索] 导航完成，等待视频数据捕获...")
    return True


def download_file(url: str, path: str) -> bool:
    headers = {"User-Agent": "AVDML_2.1.230.181-novel_ANDROID,ShortPlay,MDLTaskPreload"}
    try:
        resp = requests.get(url, headers=headers, stream=True, timeout=60)
        resp.raise_for_status()
        total = int(resp.headers.get("content-length", 0))
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "wb") as f:
            with tqdm(total=total, desc="下载中", unit="B", unit_scale=True) as pbar:
                for chunk in resp.iter_content(8192):
                    f.write(chunk)
                    pbar.update(len(chunk))
        return True
    except Exception as e:
        logger.error(f"下载失败: {e}")
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="红果短剧一键下载（URL + Key + 解密）")
    parser.add_argument("--name", "-n", default="", help="剧名（留空则自动识别）")
    parser.add_argument("--name-file", default="", help="从 UTF-8 文本文件读取目标剧名，避免命令行编码问题")
    parser.add_argument("--episode", "-e", type=int, default=1, help="起始集数")
    parser.add_argument("--output", "-o", default="./videos", help="输出根目录")
    parser.add_argument("--quality", "-q", default="1080p",
                        choices=["360p", "480p", "540p", "720p", "1080p"], help="画质")
    parser.add_argument("--timeout", "-t", type=int, default=180, help="等待超时（秒）")
    parser.add_argument("--skip-initial", type=int, default=15,
                        help="跳过启动视频的等待秒数")
    parser.add_argument("--attach-running", action="store_true",
                        help="挂到当前正在运行的 App，不 force-stop/spawn；用于已手动定位到目标剧时继续下载")
    parser.add_argument("--batch", "-b", type=int, nargs="?", const=0, default=None,
                        metavar="N",
                        help="连续下载 N 集 (省略N=不限集数)，自动上滑切换下一集")
    parser.add_argument("--search", action="store_true",
                        help="自动在 App 内搜索 --name 指定的剧并打开（需安装 ADBKeyboard 以支持中文输入）")
    parser.add_argument("--preprocess", action="store_true",
                        help="额外生成 LLM 预处理素材包（关键帧 + ASR 字幕），作为 fallback")
    parser.add_argument("--whisper-model", default="large-v3",
                        help="Whisper ASR 模型 (tiny/base/small/medium/large-v3)")
    args = parser.parse_args()

    if args.name_file:
        args.name = Path(args.name_file).read_text(encoding="utf-8").strip()

    expected_drama_name = args.name
    drama_name = args.name  # 可能为空，首次捕获后会自动识别
    output_root = args.output

    state = CaptureState()
    session_state = SessionValidationState()

    def on_message(msg, data):
        if msg["type"] != "send":
            return
        p = msg["payload"]
        t = p.get("t", "")

        if t == "java_ready":
            state.java_ready = True
        elif t == "aes_hooked":
            state.aes_hooked = True
            logger.info("[Hook] av_aes_init 已挂钩")
        elif t == "lib_loaded":
            logger.info(f"[Hook] libttffmpeg.so 已加载")
        elif t == "ready":
            logger.info("[Hook] 所有 Hook 就绪")
        elif t == "video_model":
            d = p["data"]
            with state.lock:
                state.video_models.append(d)
            # 导出所有 model 字段用于识别剧名
            logger.debug(f"[Model] fields: {list(d.keys())}")
            # 处理
            for key in ("mTitle", "title", "mVideoTitle", "mDramaTitle",
                        "mName", "mGroupTitle", "mEpisodeTitle",
                        "mMediaTitle", "mCaption", "mDesc"):
                val = d.get(key, "")
                if val and len(val) > 1:
                    with state.lock:
                        state.drama_title = val
                    logger.info(f"[捕获] 剧名: {val} (from {key})")
                    break
            else:
                # 没有匹配到已知字段时，记录所有非空字符串字段用于调试
                title_candidates = {k: v for k, v in d.items()
                                    if isinstance(v, str) and 2 < len(v) < 100
                                    and not v.startswith("http") and not v.startswith("v0")}
                if title_candidates:
                    logger.info(f"[Model] 可能的标题字段: {title_candidates}")
            # 从 model 语义明确的字段中提取集号
            # 只使用 episode 专用字段，避免描述字段中 "更新至第40集" 误匹配
            _ep_from_hook = None
            # 第一优先级：纯 episode 字段（仅接受纯数字）
            for _fk in ("mEpisodeNumber", "episodeNumber", "mEpisode", "episode"):
                _fv = d.get(_fk, "").strip()
                if _fv and _fv.isdigit() and 0 < int(_fv) < 10000:
                    _ep_from_hook = int(_fv)
                    break
            # 第二优先级：标题字段中提取 "第X集" 模式
            if _ep_from_hook is None:
                for _fk in ("mEpisodeTitle", "mTitle", "mVideoTitle"):
                    _fv = d.get(_fk, "")
                    if not _fv:
                        continue
                    _ep_m = re.search(r'第\s*(\d+)\s*[集话]', _fv)
                    if _ep_m:
                        _ep_from_hook = int(_ep_m.group(1))
                        break
            if _ep_from_hook is not None:
                with state.lock:
                    _vid_for_ep = state._current_video_id or "unknown"
                    state.captured_episodes[_vid_for_ep] = _ep_from_hook
                logger.info(f"[Hook] 检测到集号: 第{_ep_from_hook}集 (vid={_vid_for_ep})")
        elif t == "video_ref":
            vid = p["data"].get("mVideoId", "?")
            dur = p["data"].get("mVideoDuration", "?")
            logger.info(f"[捕获] 视频: {vid} ({dur}s)")
            with state.lock:
                state.video_refs.append(p["data"])
                state._current_video_id = vid
        elif t == "video_info":
            d = p["data"]
            res = d.get("mResolution", "?")
            url = d.get("mMainUrl", "")
            kid = d.get("mKid", "")
            if url:
                with state.lock:
                    state.video_urls.append({
                        "video_id": state._current_video_id,
                        "resolution": res,
                        "main_url": url,
                        "backup_url": d.get("mBackupUrl1", ""),
                        "kid": kid,
                        "codec": d.get("mCodecType", ""),
                        "size": d.get("mSize", ""),
                    })
            logger.info(f"  [{p['idx']}] {res} kid={kid[:16]}...")
        elif t == "AES_KEY":
            key = p["key"]
            with state.lock:
                if key not in state.aes_keys:
                    state.aes_keys.append(key)
            logger.info(f"  >>> AES KEY: {key} ({p['bits']}bit)")

    # output_dir 在首次捕获后解析，因为 drama_name 可能需要自动识别
    output_dir = ""
    session_manifest_path = ""

    def resolve_output_dir(ui_context: UIContext | None = None) -> str:
        """确定输出目录，必要时自动识别并清洗剧名。"""
        nonlocal drama_name, output_dir, session_manifest_path
        if expected_drama_name:
            drama_name = expected_drama_name
        elif ui_context and ui_context.title:
            drama_name = ui_context.title
        elif not drama_name and state.drama_title:
            drama_name = state.drama_title
        if not drama_name:
            detected_context = detect_ui_context_from_device()
            if detected_context.title:
                drama_name = detected_context.title
        if not drama_name:
            drama_name = "unknown_drama"
        safe_name = sanitize_drama_name(drama_name)
        output_dir = os.path.join(output_root, safe_name)
        os.makedirs(output_dir, exist_ok=True)
        session_manifest_path = os.path.join(output_dir, "session_manifest.jsonl")
        return output_dir

    # === 步骤 1：启动 App + Hook ===
    logger.info("=" * 55)
    logger.info(f"  红果短剧下载器")
    logger.info(f"  目标: {drama_name or '(自动识别)'} 第{args.episode}集")
    logger.info(f"  画质: {args.quality}")
    logger.info("=" * 55)

    device = get_frida_usb_device()
    if device is None:
        return
    if args.attach_running:
        logger.info("\n[1/5] Attach to running App...")
        pid = None
        fallback_pid = None
        for proc in device.enumerate_processes():
            proc_identifier = getattr(proc, "identifier", "") or getattr(proc, "name", "")
            if proc_identifier == APP_PACKAGE:
                pid = proc.pid
                break
            if fallback_pid is None and proc_identifier.startswith(APP_PACKAGE + ":"):
                fallback_pid = proc.pid
        if pid is None and fallback_pid is not None:
            pid = fallback_pid
        if pid is None:
            logger.error(f"{APP_PACKAGE} is not running; cannot attach")
            return
        logger.info("[2/5] Attach App + 安装 Frida 双 Hook...")
    else:
        logger.info("\n[1/5] 停止现有 App...")
        run_adb(["shell", "su", "-c", f"am force-stop {APP_PACKAGE}"])
        time.sleep(1)

        logger.info("[2/5] 启动 App + 安装 Frida 双 Hook...")
        pid = device.spawn([APP_PACKAGE])
    session = device.attach(pid)
    script = session.create_script(COMBINED_HOOK)
    script.on("message", on_message)
    script.load()
    if not args.attach_running:
        device.resume(pid)
    logger.info(f"  PID: {pid}")

    # 等待 Hook 就绪
    for _ in range(10):
        if state.java_ready:
            break
        time.sleep(1)

    # === 步骤 2：跳过启动视频 / 自动搜索 ===
    if args.search:
        if not args.name:
            logger.error("--search 需要同时指定 --name")
            try:
                session.detach()
            except Exception:
                pass
            return
        wait_time = max(3, args.skip_initial)
        logger.info(f"\n[3/5] 等待 App 启动 ({wait_time}s) 后自动搜索...")
        time.sleep(wait_time)
        state.clear()
        logger.info("  已清除启动数据，开始自动搜索...")
        search_ok = search_drama_in_app(args.name, start_episode=args.episode)
        if not search_ok:
            logger.warning("  自动搜索失败，请手动在手机上打开目标剧集，脚本将继续等待")
    elif args.skip_initial > 0:
        logger.info(f"\n[3/5] Waiting {args.skip_initial}s to skip startup recommendation data...")
        time.sleep(args.skip_initial)
        state.clear()
        logger.info("  Cleared startup capture data")
    else:
        logger.info("\n[3/5] skip-initial=0, keeping capture data from app startup")

    # === 步骤 3：等待 + 下载循环 ===
    current_ep = args.episode

    def wait_for_capture() -> bool:
        """等待捕获 URL 和 AES key；超时时返回 False。"""
        start_wait = time.time()
        while time.time() - start_wait < args.timeout:
            if state.has_data:
                time.sleep(1)  # 等待剩余分辨率 URL 到达；缩短窗口可减少下一集预加载污染
                logger.info("  URL + Key 均已捕获!")
                return True
            elapsed = int(time.time() - start_wait)
            if elapsed > 0 and elapsed % 15 == 0:
                parts = []
                if state.video_urls:
                    parts.append(f"URL: {len(state.video_urls)}个")
                if state.aes_keys:
                    parts.append(f"Key: {len(state.aes_keys)}个")
                logger.info(f"  [{elapsed}s] {', '.join(parts) or '等待中...'}")
            time.sleep(1)
        logger.error(f"超时 {args.timeout}s，未捕获到完整数据")
        return False

    def _titles_match(expected: str, actual: str) -> bool:
        """处理阿拉伯数字到中文数字转换的宽松标题匹配。

        E.g. "18岁太奶奶驾到，重整家族荣耀" should match "十八岁太奶奶驾到，重整家族荣耀".
        """
        if sanitize_drama_name(expected) == sanitize_drama_name(actual):
            return True
        # 逻辑逻辑
        stripped = expected.lstrip("0123456789").strip()
        if stripped and stripped != expected and stripped in actual:
            return True
        return False

    def download_and_decrypt(ep_num: int) -> dict:
        """在 UI 校验后下载并解密当前捕获的视频。"""
        ui_context = detect_ui_context_from_device()
        logger.debug(
            f"[validate] ep={ep_num} ui_title={ui_context.title!r} "
            f"locked={session_state.locked_title!r}"
        )
        if expected_drama_name and ui_context.title:
            if not _titles_match(expected_drama_name, ui_context.title):
                logger.error(f"UI title mismatch: expected {expected_drama_name}, actual {ui_context.title}")
                return {"success": False, "reason": "title_mismatch"}

        resolve_output_dir(ui_context)

        if not _snap_keys:
            logger.error("Missing AES key")
            return {"success": False, "reason": "missing_key"}

        # 在锁内拍一致性快照，避免 Frida 回调线程并发修改
        with state.lock:
            _snap_refs = list(state.video_refs)
            _snap_keys = list(state.aes_keys)
            _snap_episodes = dict(state.captured_episodes)
        vid = _snap_refs[-1].get("mVideoId", "unknown") if _snap_refs else "unknown"
        # 把实际 UI 标题传给 validate_round，避免 "18岁" 与 "十八岁" 这类数字形式差异阻塞校验。
        # 标题正确性已在上面的 _titles_match 中验证。
        # UI 逻辑处理
        # 逻辑逻辑
        effective_expected = (
            ui_context.title
            or session_state.locked_title
            or expected_drama_name
            or ""
        )
        ok, reason = validate_round(
            session_state,
            ui_context,
            vid,
            expected_title=effective_expected,
            fallback_episode=ep_num,
        )
        if not ok:
            logger.error(f"Round validation failed: {reason}")
            return {"success": False, "reason": reason, "episode": ui_context.episode}

        # 集号优先级：UI 检测 > Hook 提取（按 video_id 绑定）> 计数器（附警告）
        _hook_ep = _snap_episodes.get(vid)
        if ui_context.episode is not None:
            actual_episode = ui_context.episode
        elif _hook_ep is not None:
            actual_episode = _hook_ep
            logger.info(f"[集号] UI 未检测到集号，使用 Hook 捕获值: 第{actual_episode}集 (vid={vid})")
        else:
            actual_episode = ep_num
            logger.warning(
                f"[集号] UI 和 Hook 均未检测到集号，回退使用计数器值: {ep_num}（可能不准确！）"
            )
        key_hex = _snap_keys[-1]

        best = state.best_video(args.quality, video_id=vid)
        if not best:
            logger.error("No playable video")
            return {"success": False, "reason": "missing_video"}

        dec_path, meta_path = build_episode_paths(output_dir, actual_episode, vid, drama_name=drama_name)
        meta_payload = {
            "drama": ui_context.title or drama_name,
            "folder_drama": os.path.basename(output_dir),
            "episode": actual_episode,
            "ui_total_episodes": ui_context.total_episodes,
            "video_id": vid,
            "resolution": best["resolution"],
            "codec": best.get("codec", ""),
            "aes_key": key_hex,
            "kid": best.get("kid", ""),
            "captured_video_url_count": len(state.video_urls),
            "captured_key_count": len(state.aes_keys),
            "selected_url": best.get("main_url", ""),
            "timestamp": int(time.time()),
        }

        if os.path.exists(dec_path) and os.path.getsize(dec_path) > 0:
            logger.info(f"  Episode {actual_episode} already exists, skipping: {dec_path}")
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(meta_payload, f, indent=2, ensure_ascii=False)
            append_jsonl(session_manifest_path, {**meta_payload, "video_path": dec_path, "meta_path": meta_path, "status": "skipped_existing"})
            # 传入空 video_id，避免把当前捕获 ID 加入 seen_video_ids；
            # 当前捕获的视频可能不同于已存文件。
            apply_valid_round(
                session_state,
                ui_context,
                '',
                expected_title=ui_context.title or expected_drama_name or "",
                fallback_episode=actual_episode,
            )
            return {"success": True, "episode": actual_episode, "total_episodes": ui_context.total_episodes, "skipped": True}

        logger.info(f"\n  Download + decrypt episode {actual_episode}")
        logger.info(f"  Video ID: {vid}")
        logger.info(f"  Quality: {best['resolution']} {best.get('codec', '')}")
        logger.info(f"  Size: {int(best.get('size', 0)) / 1024 / 1024:.1f} MB")
        logger.info(f"  Key: {key_hex}")

        enc_path = os.path.join(output_dir, f"_ep{actual_episode:03d}_{vid[:8]}_enc.mp4")

        url = best["main_url"]
        if not download_file(url, enc_path):
            backup = best.get("backup_url", "")
            if not backup or not download_file(backup, enc_path):
                logger.error("CDN download failed")
                return {"success": False, "reason": "download_failed", "episode": actual_episode}

        logger.info("Decrypting...")
        with open(enc_path, "rb") as f:
            raw = bytearray(f.read())

        key_bytes = bytes.fromhex(key_hex)
        total = decrypt_mp4(raw, key_bytes)
        fix_metadata(raw)

        with open(dec_path, "wb") as f:
            f.write(raw)

        try:
            os.remove(enc_path)
        except OSError:
            pass

        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta_payload, f, indent=2, ensure_ascii=False)

        append_jsonl(
            session_manifest_path,
            {
                **meta_payload,
                "video_path": dec_path,
                "meta_path": meta_path,
                "status": "downloaded",
                "sample_count": total,
            },
        )
        apply_valid_round(
            session_state,
            ui_context,
            vid,
            expected_title=expected_drama_name or "",
            fallback_episode=actual_episode,
        )

        logger.info(f"  --> {dec_path} ({len(raw) / 1024 / 1024:.1f}MB, {total} samples)")

        if args.preprocess:
            logger.info("  Preparing LLM assets...")
            llm_dir = os.path.join(output_dir, "llm_ready")
            try:
                process_episode(
                    dec_path, llm_dir,
                    model_size=args.whisper_model,
                )
            except Exception as e:
                logger.warning(f"  LLM asset preparation failed: {e}")

        return {"success": True, "episode": actual_episode, "total_episodes": ui_context.total_episodes}

    # 首集：如果文件已存在则跳过
    first_ep_path, _ = build_episode_paths(
        output_dir, current_ep, '', drama_name=drama_name,
    )
    if os.path.exists(first_ep_path) and os.path.getsize(first_ep_path) > 0:
        logger.info(f"\n  第{current_ep}集已存在，跳过: {os.path.basename(first_ep_path)}")
        first_result = {"success": True, "episode": current_ep}
    else:
        if args.search:
            logger.info(f"\n等待第 {current_ep} 集数据捕获（视频应已自动开始）...")
        else:
            logger.info(f"\n请在手机上播放第{current_ep}集...")
        logger.info("  当检测到视频 URL + AES 密钥后将自动开始下载\n")

        if not wait_for_capture():
            try:
                session.detach()
            except Exception:
                pass
            return

        first_result = download_and_decrypt(current_ep)
        while not first_result.get("success") and first_result.get("reason") == "title_mismatch":
            logger.warning("Captured a non-target drama during startup; clear state and keep waiting.")
            state.clear()
            if not wait_for_capture():
                break
            first_result = download_and_decrypt(current_ep)

    if not first_result.get("success"):
        try:
            session.detach()
        except Exception:
            pass
        return
    current_ep = first_result.get("episode", current_ep)
    total_eps = first_result.get("total_episodes")  # 跨循环保留总集数

    # 逻辑
    if args.batch is not None:
        max_eps = args.batch if args.batch > 0 else 999
        downloaded = 1
        if total_eps:
            logger.info(f"\n  检测到总集数: {total_eps}")
        logger.info(f"\n{'=' * 55}")
        logger.info(f"  连续模式 — 将自动上滑切换下一集")
        if args.batch > 0:
            logger.info(f"  计划下载: {max_eps} 集 (从第{args.episode}集开始)")
        logger.info("  按 Ctrl+C 随时退出")
        logger.info(f"{'=' * 55}")

        try:
            while downloaded < max_eps:
                expected_ep = current_ep + 1

                # 逻辑
                if total_eps and expected_ep > total_eps:
                    logger.info(f"已到达最后一集（共{total_eps}集），停止下载")
                    break

                # 逻辑
                ep_path, _ = build_episode_paths(
                    output_dir, expected_ep, '', drama_name=drama_name,
                )
                if os.path.exists(ep_path) and os.path.getsize(ep_path) > 0:
                    logger.info(f"\n  第{expected_ep}集已存在，跳过: {os.path.basename(ep_path)}")
                    downloaded += 1
                    current_ep = expected_ep
                    continue

                state.clear()

                # 逻辑处理
                # 通过完整搜索进入短剧详情页，再导航到下一集。
                # 在这个 App 中，从播放器按 BACK 不会返回详情页。
                # _try_start_episode_on_drama_page 在播放器页也可能返回 True，回退逻辑不可靠。
                # 只有 search_drama_in_app 能稳定到达详情页；在详情页点击集数按钮会触发
                # setVideoModel 和完整 URL 解析，随后 video_info 触发、video_urls 填充，捕获成功。
                time.sleep(1)
                # 导航最多重试 2 次，避免单次 deeplink 时序抖动中断整个批量流程
                # 处理回退
                nav_ok = False
                for nav_attempt in range(2):
                    nav_ok = search_drama_in_app(drama_name, expected_ep)
                    if nav_ok:
                        break
                    if nav_attempt == 0:
                        logger.warning(f"[批量] 第{expected_ep}集导航第1次失败，10s后重试...")
                        time.sleep(10)

                if not nav_ok:
                    logger.error(f"Unable to navigate to episode {expected_ep}, leaving batch mode")
                    break
                time.sleep(3)

                logger.info(f"\nWaiting for episode {expected_ep} data... ({downloaded}/{max_eps})")
                if not wait_for_capture():
                    # 预缓冲死锁恢复：先跳回 N-1，再重新跳到 N，强制 app 重新触发 setVideoModel
                    prev_ep = expected_ep - 1
                    if prev_ep >= 1:
                        logger.warning(
                            f"  [恢复] 第{expected_ep}集超时，尝试 N-1→N 恢复策略..."
                        )
                        state.clear()
                        logger.info(f"  [恢复] 1/4 导航到第{prev_ep}集...")
                        select_episode_from_ui(prev_ep)
                        time.sleep(3)
                        state.clear()
                        logger.info(f"  [恢复] 2/4 再次导航到第{expected_ep}集...")
                        select_episode_from_ui(expected_ep)
                        time.sleep(3)
                        logger.info(f"  [恢复] 3/4 重新等待第{expected_ep}集数据...")
                        if not wait_for_capture():
                            logger.error(
                                f"  [恢复] 4/4 第{expected_ep}集再次超时，退出批量模式"
                            )
                            break
                        logger.info(f"  [恢复] 4/4 第{expected_ep}集数据已捕获！")
                    else:
                        logger.info("Timed out waiting for capture, leaving batch mode")
                        break

                result = download_and_decrypt(expected_ep)
                if result.get("success"):
                    downloaded += 1
                    current_ep = result.get("episode", expected_ep)
                    # 保留 total_eps，不要用 None 覆盖
                    new_total = result.get("total_episodes")
                    if new_total:
                        total_eps = new_total
                    if total_eps and current_ep >= total_eps:
                        logger.info(f"已到达最后一集（共{total_eps}集），停止下载")
                        break
                else:
                    reason = result.get('reason', 'unknown_error')
                    if reason == 'duplicate_video_id':
                        logger.warning(f"  第{expected_ep}集视频重复（可能预加载缓存），跳过继续")
                        current_ep = expected_ep
                        downloaded += 1
                        continue
                    logger.error(f"Episode {expected_ep} failed: {reason}")
                    break
        except KeyboardInterrupt:
            logger.info("\nUser interrupted, leaving batch mode")

    try:
        session.detach()
    except Exception:
        pass

    logger.info(f"\n{'=' * 55}")
    logger.info(f"  全部完成! 共下载 {current_ep - args.episode + 1} 集")
    logger.info(f"  剧名: {drama_name}")
    logger.info(f"  目录: {output_dir or output_root}")
    logger.info(f"{'=' * 55}")


if __name__ == "__main__":
    main()
