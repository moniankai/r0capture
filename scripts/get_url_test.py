"""
简单测试脚本：获取指定短剧第一集的下载 URL

用法:
    python scripts/get_url_test.py

功能:
    1. 连接手机并注入 Frida Hook
    2. 自动搜索"西游，错把玉帝当亲爹"
    3. 选择第 1 集
    4. 捕获视频 CDN URL 和 AES 密钥
    5. 输出到 videos/西游，错把玉帝当亲爹/get_url.txt
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import frida
from loguru import logger

# 添加项目根目录到 Python 路径
sys.path.insert(0, str(Path(__file__).parent.parent))

# 配置日志输出到文件
logger.add(
    "get_url_test.log",
    rotation="10 MB",
    retention="7 days",
    encoding="utf-8",
    level="DEBUG"
)

from scripts.drama_download_common import (
    read_ui_xml_from_device,
    run_adb,
    sanitize_drama_name,
    select_episode_from_ui,
)

APP_PACKAGE = "com.phoenix.read"
TARGET_DRAMA = "西游，错把玉帝当亲爹"
TARGET_EPISODE = 1

# Frida Hook 脚本（修复版：使用正确的类名和 Hook 方式）
HOOK_SCRIPT = r"""
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

    function findFieldInHierarchy(obj, name) {
        var cls = obj.getClass();
        while (cls !== null) {
            try {
                var field = cls.getDeclaredField(name);
                field.setAccessible(true);
                return field;
            } catch(e) {
                try { cls = cls.getSuperclass(); } catch(e2) { break; }
            }
        }
        return null;
    }

    // 使用正确的类名（与 download_drama.py 一致）
    var Engine = Java.use("com.ss.ttvideoengine.TTVideoEngine");

    // 使用 overloads.forEach 处理方法重载
    Engine.setVideoModel.overloads.forEach(function(ov) {
        ov.implementation = function(model) {
            try {
                // 导出 model 层字段
                send({t: "video_model", data: dumpObj(model)});

                var refField = findFieldInHierarchy(model, "vodVideoRef");
                if (refField) {
                    var ref = refField.get(model);
                    if (ref) {
                        var refData = dumpObj(ref);
                        send({t: "video_ref", data: refData});

                        // 提取 mVideoList
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

// Native Hook：av_aes_init
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
                if (bits === 128 || bits === 256) {
                    send({t: "AES_KEY", bits: bits, key: bh(args[1], bits / 8)});
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


class SimpleCaptureState:
    """简化的捕获状态"""
    def __init__(self):
        self.video_refs = []
        self.video_infos = []
        self.aes_keys = []
        self.lock = threading.Lock()
        self.java_ready = False
        self.aes_hooked = False

    @property
    def has_data(self):
        # 只要有视频信息就算有数据（AES 密钥是可选的，只有播放时才会触发）
        return len(self.video_infos) > 0


def on_message(message, data):
    """Frida 消息回调"""
    if message["type"] == "send":
        payload = message.get("payload", {})
        msg_type = payload.get("t", "")

        if msg_type == "ready":
            logger.info("[Frida] Hook 脚本已就绪")
        elif msg_type == "java_ready":
            capture_state.java_ready = True
            logger.info("[Frida] Java Hook 已就绪")
        elif msg_type == "aes_hooked":
            capture_state.aes_hooked = True
            logger.info("[Frida] AES Hook 已就绪")
        elif msg_type == "lib_loaded":
            logger.info(f"[Frida] 检测到库加载: {payload.get('path', '')}")
        elif msg_type == "video_ref":
            with capture_state.lock:
                capture_state.video_refs.append(payload)
            logger.info("[捕获] 视频引用数据")
        elif msg_type == "video_info":
            with capture_state.lock:
                capture_state.video_infos.append(payload)
            logger.info(f"[捕获] 视频信息 (索引 {payload.get('idx', '?')})")
        elif msg_type == "AES_KEY":
            with capture_state.lock:
                capture_state.aes_keys.append(payload)
            logger.info(f"[捕获] AES 密钥: {payload.get('key', '')[:16]}...")
        elif msg_type == "err":
            logger.error(f"[Hook Error] {payload.get('e', '')}")
    elif message["type"] == "error":
        logger.error(f"[Frida Error] {message.get('description', message)}")


def search_drama(drama_name: str) -> bool:
    """在 App 内搜索短剧"""
    logger.info(f"开始搜索短剧: {drama_name}")

    # 确保在主页（点击底部导航栏"首页"）
    logger.info("返回主页...")
    run_adb(["shell", "input", "tap", "135", "2100"])
    time.sleep(1.5)

    # 点击搜索图标
    logger.info("点击搜索图标...")
    run_adb(["shell", "input", "tap", "972", "132"])
    time.sleep(1.5)

    # 使用 ADBKeyboard 输入剧名
    logger.info(f"输入剧名: {drama_name}")
    run_adb(["shell", "am", "broadcast", "-a", "ADB_INPUT_TEXT", "--es", "msg", drama_name])
    time.sleep(1.0)

    # 点击搜索按钮
    logger.info("点击搜索按钮...")
    run_adb(["shell", "input", "tap", "1026", "132"])
    time.sleep(3.0)

    # 点击第一个搜索结果
    xml_text = read_ui_xml_from_device()
    if not xml_text:
        logger.error("无法读取 UI XML")
        return False

    logger.info("点击第一个搜索结果...")
    run_adb(["shell", "input", "tap", "540", "600"])
    time.sleep(3.0)

    logger.info("搜索完成，已进入短剧详情页")
    return True


def main():
    global capture_state
    capture_state = SimpleCaptureState()

    logger.info("=" * 60)
    logger.info("红果短剧 URL 捕获测试")
    logger.info(f"目标剧名: {TARGET_DRAMA}")
    logger.info(f"目标集数: 第 {TARGET_EPISODE} 集")
    logger.info("=" * 60)

    # 1. 连接设备
    logger.info("正在连接 Frida 设备...")
    run_adb(["devices"])
    try:
        device = frida.get_usb_device(timeout=5)
    except Exception as e:
        logger.error(f"无法连接 Frida 设备: {e}")
        logger.error("请确认:")
        logger.error("  1. 手机已通过 USB 连接")
        logger.error("  2. 已开启 USB 调试")
        logger.error("  3. frida-server 已在手机上运行")
        return

    # 2. 重启 App 以确保干净状态
    logger.info(f"重启 {APP_PACKAGE}...")
    run_adb(["shell", "am", "force-stop", APP_PACKAGE])
    time.sleep(1.0)
    # 使用正确的 Activity 名称
    run_adb(["shell", "am", "start", "-n", f"{APP_PACKAGE}/com.dragon.read.pages.splash.SplashActivity"])
    time.sleep(5.0)

    # 3. 获取 PID 并附加 Frida（使用更可靠的方法）
    max_retries = 3
    pid = None
    for retry in range(max_retries):
        try:
            result = subprocess.run(
                ["adb", "shell", "pidof", APP_PACKAGE],
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
                env={**os.environ, "MSYS_NO_PATHCONV": "1"}
            )
            if result.returncode == 0 and result.stdout.strip():
                pid = int(result.stdout.strip().split()[0])
                break
        except Exception as e:
            logger.debug(f"尝试 {retry + 1}/{max_retries} 获取 PID 失败: {e}")

        if retry < max_retries - 1:
            logger.info(f"等待 App 启动... ({retry + 1}/{max_retries})")
            time.sleep(2.0)

    if not pid:
        logger.error("App 启动失败，未找到进程")
        logger.error("请手动启动 App 后重试")
        return

    logger.info(f"找到 App 进程 PID: {pid}")

    logger.info("正在附加 Frida...")
    try:
        session = device.attach(pid)
        script = session.create_script(HOOK_SCRIPT)
        script.on("message", on_message)
        script.load()
        logger.info("Frida Hook 已注入")
    except Exception as e:
        logger.error(f"Frida 附加失败: {e}")
        return

    # 等待 Hook 就绪
    logger.info("等待 Hook 就绪...")
    for _ in range(20):
        if capture_state.java_ready:
            break
        time.sleep(0.5)

    if not capture_state.java_ready:
        logger.error("Java Hook 未就绪，退出")
        logger.error("可能原因:")
        logger.error("  1. TTVideoEngine 类名已变更")
        logger.error("  2. App 版本不兼容")
        logger.error("  3. 需要等待更长时间让类加载")
        return

    # 4. 搜索短剧
    if not search_drama(TARGET_DRAMA):
        logger.error("搜索短剧失败")
        return

    # 5. 选择第 1 集
    logger.info(f"正在选择第 {TARGET_EPISODE} 集...")
    if not select_episode_from_ui(TARGET_EPISODE):
        logger.error(f"选择第 {TARGET_EPISODE} 集失败")
        return

    # 6. 等待数据捕获
    logger.info("等待捕获视频信息...")
    timeout = 15  # 减少超时时间，因为视频信息很快就会捕获
    start_time = time.time()

    while time.time() - start_time < timeout:
        if capture_state.has_data:
            logger.info("✓ 视频信息捕获完成")
            break
        time.sleep(0.5)

    if not capture_state.has_data:
        logger.error("超时：未能捕获视频信息")
        logger.error(f"  视频引用数量: {len(capture_state.video_refs)}")
        logger.error(f"  视频信息数量: {len(capture_state.video_infos)}")
        return

    # 等待 AES 密钥（可选，如果视频开始播放会捕获到）
    if len(capture_state.aes_keys) == 0:
        logger.info("等待 AES 密钥（视频需要开始播放才会触发）...")
        aes_timeout = 10
        aes_start = time.time()
        while time.time() - aes_start < aes_timeout:
            if len(capture_state.aes_keys) > 0:
                logger.info("✓ AES 密钥捕获完成")
                break
            time.sleep(0.5)

        if len(capture_state.aes_keys) == 0:
            logger.warning("未捕获到 AES 密钥（视频可能未开始播放，这是正常的）")

    # 7. 输出结果
    output_dir = Path("videos") / sanitize_drama_name(TARGET_DRAMA)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / "get_url.txt"

    with open(output_file, "w", encoding="utf-8") as f:
        f.write(f"短剧名称: {TARGET_DRAMA}\n")
        f.write(f"集数: 第 {TARGET_EPISODE} 集\n")
        f.write("=" * 60 + "\n\n")

        f.write("视频引用信息:\n")
        f.write("-" * 60 + "\n")
        for i, ref_data in enumerate(capture_state.video_refs, 1):
            f.write(f"\n[引用 {i}]\n")
            data = ref_data.get('data', {})
            for key, value in data.items():
                f.write(f"{key}: {value}\n")

        f.write("\n" + "=" * 60 + "\n\n")
        f.write("视频详细信息:\n")
        f.write("-" * 60 + "\n")
        for info in capture_state.video_infos:
            idx = info.get('idx', '?')
            data = info.get('data', {})
            f.write(f"\n[视频 {idx}]\n")

            # 提取关键字段
            if 'mVideoUrl' in data:
                f.write(f"CDN URL: {data['mVideoUrl']}\n")
            if 'mVideoId' in data:
                f.write(f"Video ID: {data['mVideoId']}\n")
            if 'mResolution' in data:
                f.write(f"分辨率: {data['mResolution']}\n")
            if 'mCodec' in data:
                f.write(f"编码: {data['mCodec']}\n")

            # 输出所有字段
            f.write("\n完整字段:\n")
            for key, value in data.items():
                f.write(f"  {key}: {value}\n")

        f.write("\n" + "=" * 60 + "\n\n")
        f.write("AES 密钥信息:\n")
        f.write("-" * 60 + "\n")
        for i, key_data in enumerate(capture_state.aes_keys, 1):
            f.write(f"\n[密钥 {i}]\n")
            f.write(f"密钥 (hex): {key_data.get('key', 'N/A')}\n")
            f.write(f"位数: {key_data.get('bits', 'N/A')}\n")

        f.write("\n" + "=" * 60 + "\n")
        f.write("原始 JSON 数据:\n")
        f.write("-" * 60 + "\n")
        f.write(json.dumps({
            "video_refs": capture_state.video_refs,
            "video_infos": capture_state.video_infos,
            "aes_keys": capture_state.aes_keys
        }, ensure_ascii=False, indent=2))

    logger.info(f"✓ 结果已保存到: {output_file}")
    logger.info(f"  捕获到 {len(capture_state.video_refs)} 个视频引用")
    logger.info(f"  捕获到 {len(capture_state.video_infos)} 个视频信息")
    logger.info(f"  捕获到 {len(capture_state.aes_keys)} 个 AES 密钥")
    logger.info(f"  日志文件: get_url_test.log")

    # 清理
    script.unload()
    session.detach()
    logger.info("测试完成")


if __name__ == "__main__":
    main()
