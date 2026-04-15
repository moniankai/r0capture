# External Integrations

**Analysis Date:** 2026-04-15

## APIs & External Services

**CDN 视频服务:**
- 红果短剧 CDN - 视频文件下载（CENC 加密 MP4）
  - SDK/Client: requests
  - Auth: 无（公开 CDN URL，通过 Frida Hook 捕获）
  - URL 模式: `https://<cdn-host>/<path>.mp4`（从 `TTVideoEngine.setVideoModel` 提取）

**GitHub Releases:**
- Frida Server 二进制下载 - `https://github.com/frida/frida/releases/download/{version}/frida-server-{version}-android-{arch}.xz`
  - 实现: `scripts/check_environment.py`
  - 用途: 自动下载匹配设备架构的 Frida Server
- ADBKeyboard APK - `https://github.com/senzhk/ADBKeyBoard/releases`
  - 用途: 中文输入支持（手动下载安装）

## Data Storage

**Databases:**
- 无数据库依赖

**File Storage:**
- 本地文件系统
  - 视频输出: `videos/<剧名>/episode_*.mp4`
  - 元数据: `videos/<剧名>/meta_ep*.json`
  - 会话日志: `videos/<剧名>/session_manifest.jsonl`
  - LLM 预处理: `videos/<剧名>/llm_ready/` (可选)
- Android 设备存储
  - App 缓存: `/data/data/com.phoenix.read/cache/` (通过 ADB 拉取)
  - Frida Server: `/data/local/tmp/frida-server`

**Caching:**
- 无外部缓存服务
- 本地去重机制: `SessionValidationState.seen_video_ids`（内存中）

## Authentication & Identity

**Auth Provider:**
- 无外部认证服务

**Implementation:**
- ADB 连接认证（USB 调试授权）
- Android Root 权限（`su -c` 命令）
- Frida 进程附加（无额外认证）

## Monitoring & Observability

**Error Tracking:**
- 无外部服务

**Logs:**
- loguru 本地日志（控制台输出）
- 会话日志: `session_manifest.jsonl`（JSONL 格式追加写入）

## CI/CD & Deployment

**Hosting:**
- 无托管（本地运行工具）

**CI Pipeline:**
- 无 CI 配置

## Environment Configuration

**Required env vars:**
- `MSYS_NO_PATHCONV=1` - Windows Git Bash/MSYS2 环境下执行 ADB 命令时必需

**Secrets location:**
- 无密钥管理（所有操作基于本地 USB 连接）

## Webhooks & Callbacks

**Incoming:**
- 无

**Outgoing:**
- 无

## Device Integration

**Android Debug Bridge (ADB):**
- 用途: 设备通信、UI 自动化、文件传输
- 关键命令:
  - `adb devices` - 设备检测
  - `adb shell uiautomator dump` - UI XML 提取（`scripts/drama_download_common.py`）
  - `adb shell input tap/swipe` - UI 自动化
  - `adb shell am broadcast` - ADBKeyboard 中文输入
  - `adb pull` - 缓存文件拉取（`scripts/pull_cache.py`）
  - `adb shell su -c` - Root 命令执行

**Frida Dynamic Instrumentation:**
- 协议: Frida RPC（默认端口 27042/27043）
- Hook 目标:
  - Java 层: `com.ss.ttvideoengine.TTVideoEngine.setVideoModel` - 捕获视频 URL
  - Native 层: `libttffmpeg.so!av_aes_init` - 捕获 AES 密钥
  - SSL 层: `libssl.so!SSL_read/SSL_write` - 通用流量拦截
- 实现文件:
  - `frida_hooks/ttengine_all.js` - TTVideoEngine Hook
  - `frida_hooks/aes_hook.js` - AES 密钥捕获
  - `frida_hooks/okhttp_hook.js` - HTTP 日志
  - `frida_hooks/anti_detection.js` - 反检测

**mitmproxy (实验性):**
- 模式: WireGuard VPN + SSL 拦截
- 配置: `scripts/mitm_capture.py`
- 用途: 绕过 SSL Pinning（需 Root + 系统证书安装）

## FFmpeg Integration

**用途:**
- 视频时长检测（`scripts/preprocess_video.py`）
- 关键帧提取（场景变化检测）
- 音频流提取（ASR 预处理）

**获取方式:**
- `imageio-ffmpeg.get_ffmpeg_exe()` - 自动下载 FFmpeg 二进制
- 回退: 系统 PATH 中的 `ffmpeg` 命令

**调用方式:**
- `subprocess.run([ffmpeg, ...])` - 直接调用二进制
- `ffmpeg-python` - Python 封装（部分脚本）

## Whisper ASR (可选)

**模型:**
- faster-whisper - OpenAI Whisper 优化实现
- 默认模型: `base`（可通过 `--model` 指定 `large-v3` 等）

**用途:**
- 视频转录（`scripts/preprocess_video.py --preprocess`）
- 输出格式: SRT 字幕 + 纯文本

---

*Integration audit: 2026-04-15*
