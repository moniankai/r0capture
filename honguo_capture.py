"""
honguo_capture - 红果免费短剧 视频抓包与下载工具

四种运行模式：
  1. 缓存模式 (cache)：直接从 App 缓存拉取已播放的视频（推荐）
  2. 实时模式 (live)：启动 r0capture 抓包，实时解析并下载视频
  3. 离线模式 (offline)：分析已有 PCAP 文件，提取并下载视频
  4. Hook 模式 (hook)：使用 Frida Hook 脚本直接拦截视频 URL

使用示例：
  python honguo_capture.py cache                    # 拉取已播放的视频
  python honguo_capture.py cache --watch            # 实时监控新视频
  python honguo_capture.py live --app com.phoenix.read
  python honguo_capture.py offline --pcap capture.pcap
  python honguo_capture.py hook --app com.phoenix.read --hooks exoplayer,okhttp
  python honguo_capture.py setup
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from loguru import logger


def cmd_setup(args: argparse.Namespace) -> None:
    """Setup environment: detect device, install Frida, verify connection."""
    from scripts.check_environment import setup_environment
    setup_environment()


def cmd_sniff(args: argparse.Namespace) -> None:
    """Sniff mode: capture video URLs via SSL hooks and download from CDN."""
    from scripts.capture_and_download import run_capture, extract_video_urls, download_video

    packets = run_capture(spawn=args.spawn, duration=args.duration)
    video_urls = extract_video_urls(packets)
    logger.info(f"Found {len(video_urls)} video URLs")

    if not video_urls:
        logger.warning("No video URLs found. Make sure you played/switched videos.")
        return

    os.makedirs(args.output, exist_ok=True)

    for i, v in enumerate(video_urls):
        output_path = os.path.join(args.output, f"episode_{i + 1:03d}.mp4")
        download_video(v["url"], output_path)

    logger.info(f"Done! Videos saved to {args.output}")


def cmd_cache(args: argparse.Namespace) -> None:
    """Cache mode: pull cached videos directly from app storage."""
    from scripts.pull_cache import pull_and_convert, watch_and_pull

    if args.watch:
        watch_and_pull(args.output, args.interval)
    else:
        pulled = pull_and_convert(args.output, only_new=not args.all)
        if pulled:
            logger.info(f"Pulled {len(pulled)} videos to {args.output}")
        else:
            logger.info("No new videos. Play some videos in the app first.")


def cmd_live(args: argparse.Namespace) -> None:
    """Live capture mode: run r0capture, parse PCAP in real-time, download videos."""
    from scripts.pcap_parser import parse_pcap, save_report
    from scripts.batch_manager import BatchManager

    app = args.app
    output_dir = args.output
    pcap_path = args.pcap or f"{app.replace('.', '_')}_{int(time.time())}.pcap"

    logger.info(f"Live capture mode: {app}")
    logger.info(f"PCAP output: {pcap_path}")

    # Build r0capture command
    r0capture_path = str(Path(__file__).parent / "r0capture.py")
    cmd = [
        sys.executable, r0capture_path,
        "-U", "-f",
        "-p", pcap_path,
        app,
    ]

    if args.wait:
        cmd.extend(["-w", str(args.wait)])

    logger.info(f"Starting r0capture: {' '.join(cmd)}")
    logger.info("Press Ctrl+C to stop capture and start downloading...")

    # Run r0capture as subprocess
    proc = subprocess.Popen(cmd)

    def on_interrupt(signum, frame):
        logger.info("Stopping capture...")
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

    signal.signal(signal.SIGINT, on_interrupt)
    signal.signal(signal.SIGTERM, on_interrupt)

    try:
        proc.wait()
    except KeyboardInterrupt:
        on_interrupt(None, None)

    # Parse captured PCAP
    if not Path(pcap_path).exists():
        logger.error(f"PCAP file not found: {pcap_path}")
        return

    logger.info("Analyzing captured traffic...")
    report = parse_pcap(pcap_path)

    report_path = pcap_path.replace(".pcap", "_report.json")
    save_report(report, report_path)

    if report.videos_found == 0:
        logger.warning("No video URLs found in captured traffic.")
        logger.info("Tips: Make sure the app was playing videos during capture.")
        return

    # Download found videos
    logger.info(f"Found {report.videos_found} video URLs. Starting download...")
    manager = BatchManager(output_dir=output_dir, max_concurrent=args.concurrent)
    manager.add_urls(report.m3u8_urls + report.mp4_urls)
    manager.run()

    print(manager.get_report())
    if manager.state.failed > 0:
        manager.export_failed()


def cmd_offline(args: argparse.Namespace) -> None:
    """Offline mode: analyze existing PCAP and download found videos."""
    from scripts.pcap_parser import parse_pcap, save_report
    from scripts.batch_manager import BatchManager

    pcap_path = args.pcap
    output_dir = args.output

    if not Path(pcap_path).exists():
        logger.error(f"PCAP file not found: {pcap_path}")
        return

    logger.info(f"Offline analysis: {pcap_path}")
    report = parse_pcap(pcap_path)

    report_path = pcap_path.replace(".pcap", "_report.json")
    save_report(report, report_path)
    logger.info(f"Report saved: {report_path}")

    if report.videos_found == 0:
        logger.warning("No video URLs found.")
        return

    # Print summary
    print(f"\nFound {report.videos_found} video URLs:")
    for v in report.m3u8_urls:
        print(f"  [M3U8] {v.url[:100]}...")
    for v in report.mp4_urls:
        print(f"  [MP4]  {v.url[:100]}...")

    if not args.download:
        logger.info("Use --download to start downloading, or process the report manually.")
        return

    # Download
    manager = BatchManager(output_dir=output_dir, max_concurrent=args.concurrent)
    manager.add_urls(report.m3u8_urls + report.mp4_urls)
    manager.run()

    print(manager.get_report())
    if manager.state.failed > 0:
        manager.export_failed()


def cmd_hook(args: argparse.Namespace) -> None:
    """Hook mode: use Frida scripts to intercept video URLs directly."""
    import frida

    app = args.app
    output_dir = args.output
    hook_names = [h.strip() for h in args.hooks.split(",")]

    hooks_dir = Path(__file__).parent / "frida_hooks"
    hook_files = {
        "exoplayer": "exoplayer_hook.js",
        "aes": "aes_hook.js",
        "okhttp": "okhttp_hook.js",
        "anti": "anti_detection.js",
        "java_fix": "java_bridge_fix.js",
        "aes_hw": "aes_hw_hook.js",
    }

    # Load hook scripts
    combined_script = ""
    for name in hook_names:
        filename = hook_files.get(name)
        if not filename:
            logger.warning(f"Unknown hook: {name}. Available: {', '.join(hook_files.keys())}")
            continue
        path = hooks_dir / filename
        if not path.exists():
            logger.warning(f"Hook file not found: {path}")
            continue
        with open(path, "r", encoding="utf-8") as f:
            combined_script += f"\n// === {name} ===\n" + f.read() + "\n"
        logger.info(f"Loaded hook: {name}")

    if not combined_script:
        logger.error("No valid hooks loaded")
        return

    # Also load the original r0capture script for SSL capture
    r0_script_path = Path(__file__).parent / "script.js"
    if r0_script_path.exists() and args.ssl:
        with open(r0_script_path, "r", encoding="utf-8") as f:
            combined_script = f.read() + "\n" + combined_script
        logger.info("SSL capture script loaded")

    # Captured URLs
    captured_urls: list[dict] = []

    def on_message(message, data):
        if message["type"] == "error":
            logger.error(f"Frida error: {message.get('description', message)}")
            return
        if message["type"] == "send":
            payload = message["payload"]
            msg_type = payload.get("type", "")

            if msg_type in ("exoplayer_url", "exoplayer_media_item", "okhttp_request"):
                url = payload.get("url", "")
                if any(ext in url.lower() for ext in [".m3u8", ".ts", ".mp4", "/hls/", "/video/"]):
                    captured_urls.append(payload)
                    logger.info(f"[Captured] {url[:120]}")

            elif msg_type in ("aes_init", "aes_key_created"):
                key = payload.get("key", "")
                iv = payload.get("iv", "")
                logger.info(f"[AES] Key: {key} | IV: {iv}")

    # Connect to device
    try:
        device = frida.get_usb_device()
    except Exception:
        device = frida.get_remote_device()

    logger.info(f"Attaching to: {app}")

    if args.spawn:
        pid = device.spawn([app])
        session = device.attach(pid)
        device.resume(pid)
    else:
        session = device.attach(app)

    if args.wait:
        logger.info(f"Waiting {args.wait}s for app to load...")
        time.sleep(args.wait)

    script = session.create_script(combined_script)
    script.on("message", on_message)
    script.load()

    logger.info("Hooks active. Play videos in the app, then press Ctrl+C to stop.")

    def on_stop(signum, frame):
        logger.info("Stopping hooks...")
        session.detach()

        if captured_urls:
            # Save captured URLs
            output_path = os.path.join(output_dir, "captured_urls.json")
            os.makedirs(output_dir, exist_ok=True)
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(captured_urls, f, indent=2, ensure_ascii=False)
            logger.info(f"Saved {len(captured_urls)} URLs to: {output_path}")

            # Download if requested
            if args.download:
                from scripts.pcap_parser import VideoURL
                from scripts.batch_manager import BatchManager

                video_urls = []
                seen = set()
                for item in captured_urls:
                    url = item.get("url", "")
                    if url in seen:
                        continue
                    seen.add(url)
                    fmt = "m3u8" if ".m3u8" in url else "mp4"
                    video_urls.append(VideoURL(url=url, format=fmt, headers=item.get("headers", {})))

                manager = BatchManager(output_dir=output_dir, max_concurrent=args.concurrent)
                manager.add_urls(video_urls)
                manager.run()
                print(manager.get_report())

        sys.exit(0)

    signal.signal(signal.SIGINT, on_stop)
    signal.signal(signal.SIGTERM, on_stop)
    sys.stdin.read()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="honguo_capture - 红果免费短剧视频抓包与下载工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", help="运行模式")

    # setup
    sp_setup = subparsers.add_parser("setup", help="环境配置：安装 Frida Server 并验证")

    # sniff (recommended)
    sp_sniff = subparsers.add_parser("sniff", help="嗅探模式：抓取视频 URL 并从 CDN 直接下载明文视频（推荐）")
    sp_sniff.add_argument("--output", "-o", default="./videos/honguo_direct", help="视频输出目录")
    sp_sniff.add_argument("--spawn", "-f", action="store_true", help="以 spawn 模式启动 App")
    sp_sniff.add_argument("--duration", "-d", type=int, default=60, help="捕获时长（秒）")

    # cache
    sp_cache = subparsers.add_parser("cache", help="缓存模式：拉取已播放的视频（加密，需解密）")
    sp_cache.add_argument("--output", "-o", default="./videos/honguo", help="视频输出目录")
    sp_cache.add_argument("--watch", "-w", action="store_true", help="实时监控新视频")
    sp_cache.add_argument("--interval", "-i", type=int, default=10, help="监控间隔秒数")
    sp_cache.add_argument("--all", "-a", action="store_true", help="拉取全部文件（含已拉取的）")

    # live
    sp_live = subparsers.add_parser("live", help="实时模式：抓包 + 解析 + 下载")
    sp_live.add_argument("--app", "-a", default="com.phoenix.read", help="目标应用包名")
    sp_live.add_argument("--output", "-o", default="./videos", help="视频输出目录")
    sp_live.add_argument("--pcap", "-p", help="PCAP 输出文件路径")
    sp_live.add_argument("--concurrent", "-c", type=int, default=2, help="并发下载数 (1-10)")
    sp_live.add_argument("--wait", "-w", type=int, default=0, help="等待应用启动的秒数")

    # offline
    sp_offline = subparsers.add_parser("offline", help="离线模式：分析已有 PCAP 文件")
    sp_offline.add_argument("--pcap", "-p", required=True, help="PCAP 文件路径")
    sp_offline.add_argument("--output", "-o", default="./videos", help="视频输出目录")
    sp_offline.add_argument("--download", "-d", action="store_true", help="自动下载找到的视频")
    sp_offline.add_argument("--concurrent", "-c", type=int, default=2, help="并发下载数 (1-10)")

    # hook
    sp_hook = subparsers.add_parser("hook", help="Hook 模式：Frida 直接拦截视频 URL")
    sp_hook.add_argument("--app", "-a", default="com.phoenix.read", help="目标应用包名")
    sp_hook.add_argument("--output", "-o", default="./videos", help="视频输出目录")
    sp_hook.add_argument("--hooks", default="anti,exoplayer,okhttp",
                         help="Hook 脚本 (逗号分隔): exoplayer,aes,okhttp,anti,java_fix,aes_hw")
    sp_hook.add_argument("--spawn", "-f", action="store_true", help="以 spawn 模式启动应用")
    sp_hook.add_argument("--ssl", action="store_true", help="同时启用 SSL 流量捕获")
    sp_hook.add_argument("--download", "-d", action="store_true", help="停止后自动下载")
    sp_hook.add_argument("--concurrent", "-c", type=int, default=2, help="并发下载数 (1-10)")
    sp_hook.add_argument("--wait", "-w", type=int, default=3, help="等待应用启动的秒数")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        print("\n示例:")
        print("  python honguo_capture.py sniff --spawn      # 嗅探 URL 并下载明文视频（推荐）")
        print("  python honguo_capture.py sniff -d 120       # 捕获 2 分钟")
        print("  python honguo_capture.py cache              # 拉取缓存视频（加密）")
        print("  python honguo_capture.py setup              # 配置 Frida 环境")
        return

    # Configure logging
    logger.add(
        f"honguo_capture_{int(time.time())}.log",
        rotation="50MB", encoding="utf-8", enqueue=True, retention="7 days",
    )

    commands = {
        "setup": cmd_setup,
        "sniff": cmd_sniff,
        "cache": cmd_cache,
        "live": cmd_live,
        "offline": cmd_offline,
        "hook": cmd_hook,
    }

    handler = commands.get(args.command)
    if handler:
        handler(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
