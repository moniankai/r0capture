# Architecture

**Analysis Date:** 2026-04-15

## Pattern Overview

**Overall:** 多模式管线架构（Multi-Mode Pipeline Architecture）

**Key Characteristics:**
- 基于 Frida 动态插桩的双层 Hook 体系（Java + Native）
- 三阶段视频处理管线：捕获 → 下载 → 解密
- 四种独立运行模式，共享核心解密和下载组件
- 事件驱动的 Hook 消息处理机制
- 状态机驱动的会话校验与集数追踪

## Layers

**Frida Hook 层（注入层）:**
- Purpose: 在目标 App 进程内拦截关键函数调用，提取视频 URL 和 AES 密钥
- Location: `frida_hooks/`
- Contains: JavaScript Hook 脚本（Java 层 + Native 层）
- Depends on: Frida 16.5.9 运行时、目标 App 的 TTVideoEngine 和 libttffmpeg.so
- Used by: `scripts/download_drama.py`、`scripts/capture_key.py`、`honguo_capture.py`

**捕获协调层（编排层）:**
- Purpose: 管理 Frida 会话生命周期、处理 Hook 消息、协调 UI 自动化
- Location: `scripts/download_drama.py`、`honguo_capture.py`、`scripts/capture_and_download.py`
- Contains: Frida 会话管理、消息回调处理、ADB UI 自动化、会话状态校验
- Depends on: Frida Python 绑定、ADB 命令行工具、Hook 层
- Used by: 用户命令行调用

**下载管线层:**
- Purpose: 从 CDN 下载加密视频、管理批量任务队列、处理断点续传
- Location: `scripts/batch_manager.py`、`scripts/hls_downloader.py`
- Contains: 多线程下载器、HLS/M3U8 解析、任务去重、进度追踪
- Depends on: requests、m3u8、tqdm
- Used by: 捕获协调层、离线模式

**解密处理层:**
- Purpose: 解析 MP4 CENC 结构、执行 AES-CTR-128 解密、修复元数据
- Location: `scripts/decrypt_video.py`
- Contains: MP4 box 解析器（stsz/stco/stsc/senc）、AES-CTR 解密器、元数据修复
- Depends on: pycryptodome
- Used by: 下载管线层、独立命令行工具

**UI 上下文层（辅助层）:**
- Purpose: 解析 App UI XML、提取剧名/集数/总集数、执行 ADB 自动化操作
- Location: `scripts/drama_download_common.py`
- Contains: XML 解析器、剧名清理、集数提取、会话校验状态机
- Depends on: xml.etree.ElementTree、ADB shell uiautomator
- Used by: 捕获协调层

**环境检测层:**
- Purpose: 检测设备信息、安装 Frida Server、验证环境配置
- Location: `scripts/check_environment.py`
- Contains: ADB 设备检测、Android 版本识别、Frida Server 下载与部署
- Depends on: ADB、urllib
- Used by: setup 命令、主工作流启动前

**审计与预处理层（后处理层）:**
- Purpose: 离线分析下载结果、提取关键帧、生成字幕、检测缺集
- Location: `scripts/audit_drama_downloads.py`、`scripts/preprocess_video.py`
- Contains: 元数据分析、缺集检测、重命名规划、FFmpeg 关键帧提取、Whisper ASR
- Depends on: imageio-ffmpeg、faster-whisper（可选）
- Used by: 用户离线审计和 LLM 预处理需求

## Data Flow

**主工作流（短剧下载）:**

1. **启动与注入** — `download_drama.py` 通过 Frida USB 连接设备，spawn 或 attach 目标 App，注入组合 Hook 脚本（`COMBINED_HOOK`）
2. **Hook 就绪** — Java Hook 监听 `TTVideoEngine.setVideoModel`，Native Hook 监听 `dlopen` 等待 `libttffmpeg.so` 加载后挂载 `av_aes_init`
3. **用户播放** — 用户在手机上播放短剧（手动模式）或脚本通过 ADB 自动搜索并打开剧集（搜索模式）
4. **捕获阶段** — Hook 拦截 `setVideoModel` 提取 CDN URL、分辨率、video_id；拦截 `av_aes_init` 提取 16 字节 AES 密钥；消息通过 `script.on('message', on_message)` 回调传递到 Python
5. **UI 上下文解析** — 通过 `adb shell uiautomator dump` 获取 UI XML，解析剧名、当前集数、总集数
6. **会话校验** — `SessionValidationState` 校验剧名一致性、video_id 去重、集数单调递增
7. **下载阶段** — 使用 `requests.get(stream=True)` 下载 CENC 加密的 MP4，显示 tqdm 进度条
8. **解密阶段** — `decrypt_mp4()` 解析 MP4 box 结构，提取 stsz/stco/stsc/senc，使用 AES-CTR-128 逐 sample 解密视频轨和音频轨
9. **元数据修复** — `fix_metadata()` 将 `encv` 改为 `hvc1`、`enca` 改为 `mp4a`，移除 `sinf` 保护信息
10. **输出与日志** — 保存解密后的 MP4 到 `videos/<剧名>/episode_XXX_<vid>.mp4`，记录元数据到 `meta_epXXX_<vid>.json`，追加会话日志到 `session_manifest.jsonl`
11. **批量循环** — 如果指定 `-b N`，脚本通过 ADB 上滑屏幕切换到下一集，重复步骤 3-10

**缓存模式（pull_cache）:**

1. 通过 `adb shell ls` 列出 `/sdcard/Android/data/com.phoenix.read/cache/short/*.mdl`
2. 使用 `adb pull` 拉取 .mdl 文件到本地
3. 直接重命名为 .mp4（.mdl 文件实际是标准 MP4 ftypisom，无需解密）

**离线模式（pcap_parser）:**

1. 读取 r0capture 生成的 PCAP 文件（LINKTYPE_IPV4 格式）
2. 解析 TCP 流，提取 HTTP 请求和响应
3. 使用正则表达式匹配 M3U8/TS/MP4 URL
4. 传递给 `batch_manager.py` 批量下载

**State Management:**
- `CaptureState` — 存储当前捕获轮次的 video_urls、aes_keys、video_refs、video_models
- `SessionValidationState` — 跨集校验：locked_title、seen_video_ids、last_episode
- `DownloadTaskState` — 批量下载任务状态：target_title、start_episode、consecutive_end_signals

## Key Abstractions

**CaptureState:**
- Purpose: 封装单次捕获轮次的所有 Hook 数据
- Examples: `scripts/download_drama.py` 第 201-273 行
- Pattern: 线程安全的状态容器，提供 `best_video()` 方法根据分辨率和编码选择最佳 URL

**SessionValidationState:**
- Purpose: 防止剧名漂移、video_id 重复、集数倒退
- Examples: `scripts/drama_download_common.py` 第 55-59 行
- Pattern: 不可变校验规则 + 可变状态累积

**UIContext:**
- Purpose: 表示从 UI XML 解析出的剧名、集数、总集数
- Examples: `scripts/drama_download_common.py` 第 46-52 行
- Pattern: 数据类（dataclass），纯数据容器

**DownloadTask:**
- Purpose: 表示单个下载任务的完整状态（URL、格式、输出路径、状态、重试次数）
- Examples: `scripts/batch_manager.py` 第 30-43 行
- Pattern: 数据类 + 枚举状态（PENDING/DOWNLOADING/COMPLETED/FAILED/SKIPPED）

**VideoURL:**
- Purpose: 表示从 PCAP 或 Hook 提取的视频 URL 及其元数据
- Examples: `scripts/pcap_parser.py` 第 17-31 行
- Pattern: 数据类 + 自动计算 url_hash

## Entry Points

**scripts/download_drama.py:**
- Location: `scripts/download_drama.py`
- Triggers: 用户命令行调用 `python scripts/download_drama.py [options]`
- Responsibilities: 主工作流入口，编排 Frida Hook、UI 自动化、下载、解密全流程

**honguo_capture.py:**
- Location: `honguo_capture.py`
- Triggers: 用户命令行调用 `python honguo_capture.py {cache|live|offline|hook|setup}`
- Responsibilities: 多模式编排器，根据子命令分发到不同模块

**r0capture.py:**
- Location: `r0capture.py`
- Triggers: 用户命令行调用 `python r0capture.py -U -f <package> -p <pcap>`
- Responsibilities: 通用 SSL 抓包工具，Hook SSL_read/SSL_write，输出 PCAP 文件

**scripts/decrypt_video.py:**
- Location: `scripts/decrypt_video.py`
- Triggers: 用户命令行调用 `python scripts/decrypt_video.py --key <hex> --input <mp4> --output <mp4>`
- Responsibilities: 独立解密工具，可离线解密已下载的 CENC MP4

**scripts/audit_drama_downloads.py:**
- Location: `scripts/audit_drama_downloads.py`
- Triggers: 用户命令行调用 `python scripts/audit_drama_downloads.py <dir> --expected-total <N>`
- Responsibilities: 离线审计下载结果，检测缺集、重复、重命名需求

## Error Handling

**Strategy:** 分层错误处理 + 用户友好提示

**Patterns:**
- **Frida 连接失败** — 捕获 `frida.InvalidArgumentError`、`frida.TimedOutError`，提示用户检查 ADB 连接和 frida-server 状态
- **Hook 超时** — 设置 `HOOK_TIMEOUT`（默认 60 秒），超时后提示用户手动操作或检查 App 状态
- **会话校验失败** — `validate_round()` 返回 `(False, reason)`，reason 包括 `title_drift`、`duplicate_video_id`、`episode_not_ascending`，记录日志并跳过当前集
- **下载失败** — `batch_manager.py` 支持重试（max_retries=3），失败后标记为 FAILED 并记录 error 字段
- **解密失败** — 捕获 `struct.error`、`ValueError`，记录详细错误信息（缺少 senc、密钥长度不匹配等）
- **ADB 命令失败** — 所有 ADB 调用通过 `run_adb()` 包装，设置 `MSYS_NO_PATHCONV=1` 环境变量（Windows 兼容），捕获 `subprocess.TimeoutExpired`

## Cross-Cutting Concerns

**Logging:** 使用 loguru 统一日志，支持彩色输出和结构化日志

**Validation:** 
- UI 上下文校验：`parse_ui_context()` 解析 XML，`validate_round()` 跨集校验
- 文件名清理：`sanitize_drama_name()` 移除非法路径字符
- 集数提取：多种正则模式（`第N集`、`ep\d+`、`\d{1,4}`）

**Authentication:** 无需用户认证，直接通过 Frida 注入目标 App 进程

**Platform Compatibility:**
- Windows 路径处理：ADB 命令需设置 `MSYS_NO_PATHCONV=1`
- Frida 版本锁定：Android 9 必须使用 16.5.9（17.x Java bridge 不可用）
- 中文处理：ADBKeyboard 广播输入、UTF-8 编码、CJK 字符正则匹配

---

*Architecture analysis: 2026-04-15*
