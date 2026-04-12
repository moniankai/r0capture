# Repository Guidelines（仓库指南）

## 项目结构与模块组织

本仓库是面向 Android SSL 抓包、流量分析和红果短剧下载流程的 Python/Frida 工具集。根目录下的 `r0capture.py`、`honguo_capture.py`、`myhexdump.py` 和 `script.js` 是主要入口或历史入口。可复用的 Python 工具位于 `scripts/`；Frida Hook 脚本位于 `frida_hooks/`；单元测试位于 `tests/`；设计说明、实施计划和 OpenSpec 变更位于 `docs/` 与 `openspec/`。`videos/`、`videos_redownload*/`、`app_data/`、`.tmp/`、日志、pcap 和截图通常是运行产物或大文件，除非明确作为脱敏 fixture，否则不要当作源码提交。

## 构建、测试与本地开发命令

建议先创建虚拟环境再安装依赖。`pip install -r requirements.txt` 安装 Frida、mitmproxy、加解密、下载和分析相关依赖。`python -m unittest discover -s tests` 运行现有测试套件。`python scripts/download_drama.py --help` 可在不连接设备的情况下检查下载器 CLI 参数。`python scripts/audit_drama_downloads.py <drama-dir> --expected-total <n>` 用于审计已下载剧集的元数据。真实抓包流程需要 ADB、已 root 的测试设备，以及与本地 `frida` 包版本兼容的 Frida Server。

## 编码风格与命名约定

Python 代码使用 4 空格缩进，函数和变量使用 `snake_case`。新增文件系统逻辑优先使用 `pathlib.Path`，CLI 行为保持显式，校验失败时给出可定位的错误信息，避免静默回退。测试文件使用 `test_*.py` 命名。生成的剧集资源遵循 `episode_001_<video_id_suffix>.mp4`、`meta_ep001_<video_id_suffix>.json` 和 `session_manifest.jsonl` 等模式。

## 测试指南

测试基于 `unittest` 和 `unittest.mock`。修改共享 helper 或脚本时，为解析、文件命名、轮次校验和 CLI 行为补充聚焦测试。涉及文件输出时优先使用临时目录，不要依赖已签入的运行产物。涉及 ADB 或 Frida 的逻辑应尽量 mock 设备调用；确实需要真机验证时，在 PR 中记录设备环境、命令和关键结果。

## 提交与 Pull Request 指南

近期提交多使用简短的祈使句或说明性摘要，例如 `Update README.md`、`Refactor SSL handling and improve match selection`。保持提交范围清晰，并说明变更原因。PR 应包含变更摘要、已运行的测试或设备检查、相关 issue/计划链接；只有在能帮助理解 Android UI 或抓包行为时才附截图和日志片段。不要提交私有抓包、密钥、完整 pcap 或大视频文件，除非它们是明确需要且已脱敏的 fixture。

## 语言

- 默认使用简体中文回复。
- 代码注释和变量名遵循项目既有规范（注释时默认使用中文为主，中文注释可接受）。