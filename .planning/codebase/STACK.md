# Technology Stack

**Analysis Date:** 2026-04-15

## Languages

**Primary:**
- Python 3.x - 所有核心脚本、Frida Hook 编排、视频解密、UI 自动化

**Secondary:**
- JavaScript (ES6+) - Frida Hook 脚本（Java/Native 层拦截）

## Runtime

**Environment:**
- Python 3.7+ (测试环境使用 Python 3.14.3)
- Node.js/JavaScript - Frida 运行时（通过 frida-tools 调用）

**Package Manager:**
- pip - Python 依赖管理
- Lockfile: 不存在（仅 requirements.txt）

## Frameworks

**Core:**
- Frida 16.5.9 - 动态插桩框架（Android 9 兼容性约束）
- frida-tools - Frida CLI 工具集

**Testing:**
- unittest - Python 标准库测试框架（`tests/test_download_drama.py`, `tests/test_audit_drama_downloads.py`）

**Build/Dev:**
- loguru - 结构化日志
- click - CLI 参数解析（部分脚本使用 argparse）

## Key Dependencies

**Critical:**
- frida==16.5.9 - 必须使用此版本以兼容 Android 9 Java bridge（17.x 在 Android 9 上不可用）
- pycryptodome - AES-CTR-128 CENC 视频解密（`Crypto.Cipher.AES`）
- requests - HTTP 下载 CDN 视频文件
- tqdm - 下载进度条

**Infrastructure:**
- scapy - PCAP 文件解析（SSL 流量分析）
- m3u8 - HLS 流媒体下载
- ffmpeg-python - 视频处理封装
- imageio-ffmpeg - FFmpeg 二进制获取（可选，用于 `--preprocess` 模式）
- faster-whisper - ASR 转录（可选，用于 LLM 预处理）
- watchdog - 文件系统监控（缓存模式实时监控）
- tenacity - 重试机制
- blackboxprotobuf - Protobuf 解码
- mitmproxy - WireGuard 模式 SSL 拦截（实验性）
- hexdump - 二进制数据可视化

**Windows Support:**
- win_inet_pton - Windows 平台网络地址转换（`sys_platform == "win32"`）

## Configuration

**Environment:**
- 无 `.env` 文件依赖
- 通过命令行参数配置（`argparse`/`click`）
- ADB 命令需设置 `MSYS_NO_PATHCONV=1` 环境变量（Git Bash/MSYS2 路径转换问题）

**Build:**
- `requirements.txt` - 依赖声明
- 无 `setup.py` 或 `pyproject.toml`（非打包项目）

## Platform Requirements

**Development:**
- Python 3.7+
- Android SDK Platform-Tools (ADB)
- Frida Server（需与 PC 端 frida 版本完全匹配）
- Root 权限的 Android 设备（Android 7-13 测试通过）
- ADBKeyboard APK（可选，用于中文搜索自动化）

**Production:**
- 非服务端部署项目
- 运行环境：Windows/Linux/macOS + USB 连接的 Android 设备
- Frida Server 需在设备端以 root 权限运行：`adb shell su -c "/data/local/tmp/frida-server &"`

---

*Stack analysis: 2026-04-15*
