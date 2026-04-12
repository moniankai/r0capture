# CLAUDE.md

本文件为 Claude Code (claude.ai/code) 在此仓库中工作时提供指引。

## 项目概述

r0capture 是一个安卓 SSL 流量抓包框架，扩展支持红果短剧 App（`com.phoenix.read`）CENC 加密视频的一键下载与解密。项目融合了 Frida Hook 注入、ADB UI 自动化、MP4 CENC 解密以及可选的 LLM 预处理能力。

## 常用命令

### 环境安装
```bash
pip install -r requirements.txt
# 可选，用于 --preprocess 模式：
pip install imageio-ffmpeg faster-whisper
```

### 短剧下载器（主要工作流）
```bash
# 手动模式 — 用户在手机上操作，脚本自动捕获
python scripts/download_drama.py

# 搜索模式 — 全自动（需 ADBKeyboard）
python scripts/download_drama.py -n "剧名" --search -e 1 -b 10

# 挂载到已运行的 App
python scripts/download_drama.py --attach-running -n "剧名"
```

### 通用 SSL 抓包（原始 r0capture）
```bash
python r0capture.py -U -f com.coolapk.market -v
python r0capture.py -U 酷安 -v -p output.pcap
```

### 独立解密工具
```bash
python scripts/decrypt_video.py --key <32位hex密钥> --input encrypted.mp4 --output decrypted.mp4
```

### 下载审计
```bash
python scripts/audit_drama_downloads.py videos/剧名 --expected-total 80
```

### 测试
```bash
pytest tests/
pytest tests/test_download_drama.py -v
pytest tests/test_audit_drama_downloads.py -v
```

## 架构

### 四种运行模式

1. **通用抓包**（`r0capture.py`）— 通过 Frida Hook `SSL_read`/`SSL_write` 实现任意安卓 App 的 SSL/TLS 流量拦截。
2. **短剧下载**（`scripts/download_drama.py`）— 主工作流：Frida Hook → 捕获 CDN URL + AES 密钥 → 下载 → CENC 解密 → 可播放 MP4。
3. **缓存提取**（`scripts/pull_cache.py`）— 通过 ADB 从 App 本地存储中提取已缓存的视频文件。
4. **实时捕获**（`honguo_capture.py`）— 多模式编排器，支持 `cache|live|offline|hook|setup` 子命令。

### 三阶段视频管线

```
捕获 (Frida)            →  下载 (requests)        →  解密 (pycryptodome)
├─ Java: TTVideoEngine     ├─ 从 CDN 获取 CENC MP4    ├─ 解析 stsz/stco/stsc/senc
│  .setVideoModel → URL    └─ tqdm 显示进度            ├─ AES-CTR-128 逐 sample 解密
└─ Native: av_aes_init                                 └─ 修复 encv→hvc1, enca→mp4a
   (libttffmpeg) → 密钥
```

### 核心模块

| 模块 | 职责 |
|------|------|
| `scripts/download_drama.py` | 主入口：编排 Hook、捕获、下载、解密全流程 |
| `scripts/drama_download_common.py` | 共享工具：UI XML 解析、文件名生成、会话校验 |
| `scripts/decrypt_video.py` | MP4 CENC 解密（AES-CTR-128，视频+音频双轨） |
| `scripts/batch_manager.py` | 多线程批量下载队列，支持去重与断点续传 |
| `scripts/preprocess_video.py` | LLM 预处理：关键帧提取 + Whisper ASR 转录 |
| `scripts/audit_drama_downloads.py` | 离线审计：缺集检测、重复识别、重命名规划 |
| `scripts/check_environment.py` | 设备环境校验：ADB、Frida Server 检测 |
| `scripts/pcap_parser.py` | PCAP 文件解析，提取视频 URL |
| `scripts/capture_key.py` | 专用 Frida Hook，捕获 AES 解密密钥 |
| `honguo_capture.py` | 多模式编排器（cache/live/offline/hook） |

### Frida Hook 体系（`frida_hooks/`）

短剧下载器依赖两个核心 Hook：
- **`ttengine_all.js`** — Java 层：Hook `TTVideoEngine.setVideoModel`，提取视频 CDN URL 和元数据。
- **`aes_hook.js`** — Native 层：监控 `dlopen` 等待 `libttffmpeg.so` 加载，随后 Hook `av_aes_init` 捕获 16 字节 AES 密钥。

辅助 Hook：`okhttp_hook.js`（HTTP 日志）、`anti_detection.js`（绕过 Frida 检测）、`exoplayer_hook.js`、`mediacodec_java.js`、以及各种 trace/dump 工具。

### 会话与校验机制

- **UIContext** — 通过 `adb shell uiautomator dump` 解析 App 界面 XML，提取剧名、当前集数、总集数。
- **SessionValidationState** — 防止剧名漂移（首次捕获后锁定）、video_id 去重、集数单调递增校验。
- **session_manifest.jsonl** — 每次会话的下载日志（集数、video_id、分辨率、成功状态）。

### 输出目录约定

```
videos/<剧名>/
├── episode_001_<8位vid>.mp4    # 解密后可直接播放
├── meta_ep001_<8位vid>.json    # 捕获元数据
├── session_manifest.jsonl      # 会话日志
└── llm_ready/                  # --preprocess 时生成
```

8 位后缀取自 video_id 的末 8 个字符。

## 关键约束

- **Frida 版本**：必须使用 frida 16.5.9 以兼容 Android 9。17.x 版本在 Android 9 上 Java bridge 不可用。PC 端 pip 包与设备端 frida-server 版本必须完全匹配。
- **libttffmpeg 延迟加载**：该 Native 库按需加载，Hook 必须先监控 `dlopen` 再挂载 `av_aes_init`。
- **双轨解密**：MP4 包含独立的视频轨（encv）和音频轨（enca），需分别使用各自 senc 中的 IV 进行解密。
- **Windows 路径**：在 Git Bash/MSYS2 下执行 ADB 命令需加 `MSYS_NO_PATHCONV=1` 前缀，防止路径被自动转换。
- **中文处理**：UI 解析涉及 CJK 字符；文件名清理和 ADB 输入（通过 ADBKeyboard 广播）必须正确处理 UTF-8 编码。

## 语言

- 默认使用简体中文回复。
- 代码注释和变量名遵循项目既有规范（注释时默认使用中文为主，中文注释可接受）。
