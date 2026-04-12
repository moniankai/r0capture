## Why

学习安卓应用层抓包技术，并实现"红果免费短剧"App 的视频批量下载功能。当前该应用仅支持在线播放，无法离线观看，且缺乏系统化的抓包分析工具。本项目旨在通过 Frida 和 r0capture 构建完整的视频抓包、分析和下载解决方案，用于个人学习和技术研究。

## What Changes

- 配置完整的 Frida + r0capture 抓包环境（支持小米6/安卓系统）
- 实现实时网络流量捕获和 PCAP 文件生成
- 开发 PCAP 解析器，自动提取视频 URL（M3U8/TS/MP4）
- 实现 HLS 视频下载器，支持 AES 加密解密
- 开发批量下载管理器，支持多线程、断点续传、进度跟踪
- 提供 Frida Hook 脚本，用于绕过复杂加密和反调试
- 生成完整的技术文档和使用指南

## Capabilities

### New Capabilities

- `frida-environment`: Frida Server 安装、配置和验证，支持多安卓版本
- `traffic-capture`: 基于 r0capture 的实时流量捕获和 PCAP 导出
- `pcap-analysis`: PCAP 文件解析，视频 URL 提取和格式识别
- `hls-downloader`: HLS (M3U8/TS) 视频下载，支持 AES-128 解密
- `batch-manager`: 批量下载管理，多线程、去重、进度跟踪
- `frida-hooks`: 高级 Frida Hook 脚本，用于 ExoPlayer、AES 解密、网络请求拦截

### Modified Capabilities

<!-- 无现有功能需要修改 -->

## Impact

- **新增依赖**：frida, frida-tools, scapy, m3u8, pycryptodome, ffmpeg-python
- **新增文件**：
  - `scripts/pcap_parser.py` - PCAP 解析器
  - `scripts/hls_downloader.py` - HLS 下载器
  - `scripts/batch_manager.py` - 批量管理器
  - `frida_hooks/exoplayer_hook.js` - ExoPlayer Hook 脚本
  - `frida_hooks/aes_hook.js` - AES 解密 Hook 脚本
  - `docs/setup_guide.md` - 环境配置指南
  - `docs/usage_guide.md` - 使用指南
- **影响范围**：本项目为独立工具，不影响现有 r0capture 核心功能
- **系统要求**：
  - 已 root 的安卓设备（小米6，安卓7-12）
  - Python 3.6+
  - ADB 工具
  - FFmpeg
