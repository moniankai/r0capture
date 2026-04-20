# r0capture

安卓 SSL 通杀抓包脚本，扩展支持红果免费短剧的 CENC 加密视频一键下载。

> **⭐ 新版红果 App 请用 [README-LEAN.md](README-LEAN.md) 的 lean-2session 架构**
> （已在 4 部剧 250+ 集验证 0 串集 0 缺集）
> 以下 `download_drama.py` / `hongguo_agent.py` 为旧架构（legacy），在 2026-04 起的 App 版本上已失效。

---

## 目录

- **[lean-2session 架构（推荐，新版 App 唯一可用）](README-LEAN.md)**
- [红果短剧下载器 (legacy)](#红果短剧下载器)
  - [环境准备](#环境准备)
  - [配置文件](#配置文件)
  - [操作流程](#操作流程)
  - [使用示例](#使用示例)
  - [参数说明](#参数说明)
  - [断点续传与自动重试](#断点续传与自动重试)
  - [常见问题](#常见问题)
- [视频解密工具](#视频解密工具)
- [LLM 预处理工具](#llm-预处理工具)
- [r0capture 通用抓包](#r0capture-通用抓包)

---

## 红果短剧下载器

`scripts/download_drama.py` — 红果免费短剧一键下载工具。

**工作原理：**

1. 启动 App 并注入 Frida 双 Hook（Java 层 TTVideoEngine URL + Native 层 av_aes_init 密钥）
2. 自动捕获视频 CDN 地址与 AES-128 解密密钥
3. 下载 CENC 加密 MP4，在本地完成 AES-CTR-128 解密
4. 输出可直接播放的 MP4 文件（H.264 / H.265 编码）

**核心特性：**

- **双层 Hook**：Java 层捕获 URL，Native 层捕获密钥
- **自动断点续传**：中断后重新运行自动跳过已完成集数
- **智能重试**：单集失败自动重试最多 3 次
- **会话持久化**：`session_manifest.jsonl` 记录所有下载历史
- **多 App 支持**：通过 `config.yaml` 配置支持不同短剧平台

### 环境准备

#### 1. Python 依赖

```bash
pip install -r requirements.txt
pip install imageio-ffmpeg   # 可选，用于 --preprocess
pip install faster-whisper   # 可选，用于 --preprocess
```

#### 2. Android 设备

| 要求 | 说明 |
| ---- | ---- |
| Root 权限 | Magisk / KernelSU |
| USB 调试 | 设置 → 开发者选项 → USB 调试 |
| Frida Server | **必须使用 16.5.9**（兼容 Android 9+，17.x 在 Android 9 上 Java bridge 不可用） |
| 红果 App | com.phoenix.read，版本无要求 |

**启动 Frida Server（每次重启手机后执行）：**

```bash
adb shell su -c "/data/local/tmp/frida-server &"
```

**验证 Frida Server 运行状态：**

```bash
# 检查进程
adb shell ps | grep frida-server

# 测试连接
frida-ps -U
```

#### 3. Frida 版本约束（重要）

**必须使用 frida 16.5.9**，PC 端 pip 包与设备端 frida-server 版本必须完全匹配：

```bash
# 安装指定版本
pip install frida==16.5.9 frida-tools==12.5.0

# 验证版本
python -c "import frida; print(frida.__version__)"
```

**为什么不能用 17.x？**
- Frida 17.x 在 Android 9 上 Java bridge 不可用，无法 Hook Java 层方法
- 16.5.9 是最后一个完全兼容 Android 9 的稳定版本

#### 4. ADBKeyboard（仅 --search 模式需要）

用于通过 ADB 向手机输入中文剧名，一次性安装：

```bash
# 自动安装（需 root）
MSYS_NO_PATHCONV=1 adb push ADBKeyboard.apk /data/local/tmp/ADBKeyboard.apk
MSYS_NO_PATHCONV=1 adb shell su -c "pm install /data/local/tmp/ADBKeyboard.apk"
MSYS_NO_PATHCONV=1 adb shell ime enable com.android.adbkeyboard/.AdbIME
MSYS_NO_PATHCONV=1 adb shell ime set com.android.adbkeyboard/.AdbIME
```

> APK 下载：[github.com/senzhk/ADBKeyBoard/releases](https://github.com/senzhk/ADBKeyBoard/releases)

---

### 配置文件

项目根目录的 `config.yaml` 用于配置目标 App 和下载行为。

**默认配置（红果短剧）：**

```yaml
# 当前使用的 App（可选值：honguo, kuaishou, douyin）
app: honguo

# App 特定配置
apps:
  honguo:
    package: com.phoenix.read
    hook_script: frida_hooks/ttengine_all.js

# 下载配置
download:
  quality: 720p              # 默认画质（360p/480p/540p/720p/1080p/origin）
  max_retries: 3             # 单集最大重试次数
  output_dir: videos         # 输出根目录

# Frida 配置
frida:
  version: 16.5.9            # 必须与设备端 frida-server 匹配
  timeout: 10                # 连接超时（秒）
```

**配置说明：**

- `app`: 当前使用的 App 标识符（未来支持快手、抖音等）
- `apps.honguo.package`: Android 包名
- `apps.honguo.hook_script`: Frida Hook 脚本路径
- `download.quality`: 命令行 `-q` 参数的默认值
- `download.max_retries`: 单集下载失败后的最大重试次数
- `frida.version`: 用于版本校验，确保 PC 端与设备端版本匹配

**多 App 支持（未来扩展）：**

```yaml
app: kuaishou  # 切换到快手短剧

apps:
  kuaishou:
    package: com.kuaishou.nebula
    hook_script: frida_hooks/kuaishou_hook.js
```

---

### 操作流程

#### 模式 A：手动模式（无需 ADBKeyboard）

适用场景：已知剧名，手动在 App 内打开目标短剧。

```
PC                              手机
─────────────────────────────────────────────────────
1. 运行脚本 (--name 可选)
   ↓
2. 脚本启动 App + 注入 Frida Hook
   ↓
3. 等待 skip-initial 秒（跳过启动推荐视频）
   ↓
                                4. 手动打开目标短剧，点击播放
                                   ↓
5. Hook 捕获 CDN URL + AES Key
   ↓
6. 自动下载 + 解密 → 保存 MP4
   ↓
7. 批量模式：自动在 App 内切换到下一集
   ↓
8. 重复步骤 5-7 直到完成所有集数
```

```bash
# 基础用法（自动识别剧名）
python scripts/download_drama.py

# 指定输出文件夹名（不在 App 内搜索）
python scripts/download_drama.py -n "爹且慢，我来了"

# 批量下载 10 集（手动打开第 1 集后自动切换）
python scripts/download_drama.py -n "剧名" -b 10
```

#### 模式 B：搜索模式（需要 ADBKeyboard）

适用场景：完全自动化，从搜索到下载全程无需手动操作。

```
PC                              手机
─────────────────────────────────────────────────────
1. 运行脚本 -n "剧名" --search
   ↓
2. 脚本启动 App + 注入 Frida Hook
   ↓
3. 等待 App 启动稳定
   ↓
4. 自动点击"搜索"标签
   ↓
5. ADBKeyboard 广播输入剧名
   ↓
6. 自动点击搜索结果中的目标剧
   ↓
7. 自动跳转到指定集数（-e N）
   ↓
8. 视频开始播放，Hook 捕获数据
   ↓
9. 自动下载 + 解密 → 保存 MP4
   ↓
10. 批量模式：自动切换到下一集
   ↓
11. 重复步骤 8-10 直到完成所有集数
```

```bash
# 搜索并下载第 1 集
python scripts/download_drama.py -n "爹且慢，我来了" --search

# 搜索并从第 5 集开始
python scripts/download_drama.py -n "爹且慢，我来了" --search -e 5

# 搜索并批量下载全集
python scripts/download_drama.py -n "爹且慢，我来了" --search -b
```

#### 模式 C：挂载模式（不重启 App）

适用场景：App 已在运行，避免重启导致的登录状态丢失。

```bash
# 挂载到已运行的 App
python scripts/download_drama.py --attach-running -n "剧名"

# 挂载 + 批量下载
python scripts/download_drama.py --attach-running -n "剧名" -b 10
```

**注意事项：**
- 挂载模式不会重启 App，需手动确保 App 已启动
- 适用于需要保持登录状态或避免触发 App 启动检测的场景

---

### 使用示例

```bash
# 示例 1：手动模式，自动识别剧名，默认 1080p
python scripts/download_drama.py

# 示例 2：搜索并下载单集
python scripts/download_drama.py -n "爹且慢，我来了" --search -e 3

# 示例 3：搜索并批量下载全集
python scripts/download_drama.py -n "爹且慢，我来了" --search -b

# 示例 4：搜索并批量下载 5 集
python scripts/download_drama.py -n "凡人仙葫第一季" --search -b 5

# 示例 5：挂载到已运行的 App（不重启 App）
python scripts/download_drama.py --attach-running -n "剧名"

# 示例 6：下载后同步生成 LLM 素材包（关键帧 + ASR 字幕）
python scripts/download_drama.py -n "剧名" --search --preprocess

# 示例 7：指定画质为 720p
python scripts/download_drama.py -n "剧名" --search -q 720p

# 示例 8：从文件读取剧名（避免命令行编码问题）
echo "爹且慢，我来了" > drama_name.txt
python scripts/download_drama.py --name-file drama_name.txt --search
```

输出目录结构：

```
videos/
└── 爹且慢，我来了/
    ├── episode_001_<vid>.mp4      # 解密后可播放
    ├── meta_ep001_<vid>.json      # 元数据（分辨率、Key、URL 等）
    ├── episode_002_<vid>.mp4
    ├── meta_ep002_<vid>.json
    ├── session_manifest.jsonl     # 本次会话下载记录
    └── llm_ready/                 # --preprocess 时生成
        └── episode_001_<vid>/
            ├── keyframes/         # 关键帧截图
            ├── transcript.srt     # ASR 字幕
            ├── transcript.txt     # 纯文本转录
            └── manifest.json
```

---

### 参数说明

| 参数 | 简写 | 默认 | 说明 |
| ---- | ---- | ---- | ---- |
| `--name` | `-n` | 自动识别 | 输出文件夹名；配合 `--search` 时用于搜索 |
| `--name-file` | | | 从 UTF-8 文件读取剧名（解决命令行编码问题） |
| `--episode` | `-e` | 1 | 起始集数 |
| `--batch` | `-b` | 不开启 | 连续下载 N 集；省略 N 表示不限集数 |
| `--search` | | false | 自动在 App 内搜索并导航（需 ADBKeyboard） |
| `--quality` | `-q` | 1080p | 画质：360p / 480p / 540p / 720p / 1080p |
| `--output` | `-o` | ./videos | 输出根目录 |
| `--timeout` | `-t` | 180 | 捕获等待超时（秒） |
| `--skip-initial` | | 15 | 跳过启动推荐视频的等待时间（秒） |
| `--attach-running` | | false | 挂载到已运行的 App，不重启 |
| `--preprocess` | | false | 额外生成 LLM 素材包（关键帧 + ASR） |
| `--whisper-model` | | large-v3 | Whisper 模型：tiny / base / small / medium / large-v3 |
| `--resume` | | false | 根据已落盘文件自动从第一个缺失集数继续 |
| `--total-episodes` | | 0 | 用户提供的总集数；优先于 UI 动态总集数 |

---

### 断点续传与自动重试

#### 自动断点续传

下载器支持自动断点续传。如果批量下载中途中断（脚本崩溃、手动 Ctrl+C、网络断开等），重新运行相同命令即可从断点继续：

```bash
# 首次运行：下载到第 20 集时中断
python scripts/download_drama.py -n "剧名" --search -b 80

# 重新运行：自动跳过前 20 集，从第 21 集继续
python scripts/download_drama.py -n "剧名" --search -b 80
```

**工作原理：**

1. 每次下载成功后，记录到 `session_manifest.jsonl`
2. 重新运行时，脚本读取 manifest 文件，识别已完成的集数
3. 自动跳过已完成集数，从第一个缺失集数继续

**手动指定断点：**

```bash
# 使用 --resume 参数自动检测断点
python scripts/download_drama.py -n "剧名" --search --resume

# 或手动指定起始集数
python scripts/download_drama.py -n "剧名" --search -e 21 -b 60
```

#### 自动重试机制

当单集下载失败时（网络超时、解密失败、Hook 数据不完整等），脚本自动重试最多 3 次：

**重试策略：**

- 每次重试前清空 Hook 数据状态，确保数据刷新
- 重试间隔 2 秒，避免触发 App 限流
- 重试历史记录到 `session_manifest.jsonl`
- 3 次重试全部失败后，跳过该集继续下一集（批量模式）

**重试日志示例：**

```
[INFO] 第 5 集下载失败: 捕获超时
[INFO] 开始重试 (1/3)...
[INFO] 清空 Hook 状态，重新捕获数据
[INFO] 第 5 集下载成功（重试 1 次后成功）
```

**配置重试次数：**

在 `config.yaml` 中修改：

```yaml
download:
  max_retries: 5  # 增加到 5 次重试
```

#### session_manifest.jsonl 格式

每次下载会在输出目录生成 `session_manifest.jsonl` 文件（每行一个 JSON 对象）：

```jsonl
{"episode": 1, "status": "downloaded", "video_id": "abc12345", "resolution": "720p", "video_path": "episode_001_abc12345.mp4", "retry_count": 0, "timestamp": 1713196800.0}
{"episode": 2, "status": "retry_attempt", "attempt": 1, "reason": "download_failed", "timestamp": 1713196850.0}
{"episode": 2, "status": "retry_success", "video_id": "def67890", "resolution": "720p", "video_path": "episode_002_def67890.mp4", "retry_count": 1, "timestamp": 1713196860.0}
{"episode": 3, "status": "skipped_resume", "reason": "already_completed", "timestamp": 1713196900.0}
{"episode": 4, "status": "failed_after_retries", "retry_count": 3, "last_error": "捕获超时", "timestamp": 1713196950.0}
```

**字段说明：**

- `episode`: 集数
- `status`: 状态
  - `downloaded`: 首次下载成功
  - `retry_success`: 重试后成功
  - `retry_attempt`: 重试尝试记录
  - `skipped_existing`: 文件已存在，跳过
  - `skipped_resume`: 断点续传跳过
  - `failed_after_retries`: 重试 3 次后仍失败
- `video_id`: 视频 ID（8 位后缀）
- `resolution`: 分辨率
- `video_path`: 解密后视频路径（相对路径）
- `retry_count`: 成功前的重试次数（0 表示首次成功）
- `timestamp`: Unix 时间戳

#### 离线审计

使用 `audit_drama_downloads.py` 审计下载质量：

```bash
# 基础审计
python scripts/audit_drama_downloads.py videos/剧名

# 指定预期总集数
python scripts/audit_drama_downloads.py videos/剧名 --expected-total 80

# 生成重命名建议
python scripts/audit_drama_downloads.py videos/剧名 --suggest-rename
```

审计工具会：

- 识别缺失的集数
- 检测重复文件（相同 video_id）
- 分析 `session_manifest.jsonl` 中的重试模式
- 生成重命名建议（统一文件名格式）
- 统计下载成功率和平均重试次数

---

### 常见问题

#### 1. Frida 连接失败

**错误信息：**
```
Failed to spawn: unable to find process with name 'com.phoenix.read'
```

**解决方法：**

```bash
# 检查 Frida Server 是否运行
adb shell ps | grep frida-server

# 如果没有运行，启动 Frida Server
adb shell su -c "/data/local/tmp/frida-server &"

# 检查版本是否匹配
python -c "import frida; print(frida.__version__)"
adb shell /data/local/tmp/frida-server --version
```

#### 2. Java bridge 不可用（Android 9）

**错误信息：**
```
Error: Java API not available
```

**原因：** Frida 17.x 在 Android 9 上 Java bridge 不可用。

**解决方法：**

```bash
# 降级到 16.5.9
pip uninstall frida frida-tools
pip install frida==16.5.9 frida-tools==12.5.0

# 下载对应的 frida-server 16.5.9
# https://github.com/frida/frida/releases/tag/16.5.9
```

#### 3. ADBKeyboard 输入失败

**错误信息：**
```
[ERROR] ADBKeyboard 输入失败
```

**解决方法：**

```bash
# 检查 ADBKeyboard 是否安装
adb shell pm list packages | grep adbkeyboard

# 检查输入法是否启用
adb shell ime list -s

# 重新设置为默认输入法
adb shell ime set com.android.adbkeyboard/.AdbIME
```

#### 4. 捕获超时

**错误信息：**
```
[ERROR] 等待 180 秒后仍未捕获到数据
```

**可能原因：**

- App 未播放视频（手动模式需手动点击播放）
- Hook 脚本未正确注入
- 视频已缓存，未触发网络请求

**解决方法：**

```bash
# 增加超时时间
python scripts/download_drama.py -n "剧名" --search -t 300

# 检查 Hook 日志
# 脚本运行时会输出 Hook 捕获的数据

# 清除 App 缓存后重试
adb shell pm clear com.phoenix.read
```

#### 5. 解密失败

**错误信息：**
```
[ERROR] 解密失败: 无法找到 senc box
```

**可能原因：**

- 视频格式不是 CENC 加密
- MP4 文件损坏
- 密钥不匹配

**解决方法：**

```bash
# 检查 meta 文件中的密钥
cat videos/剧名/meta_ep001_*.json

# 手动解密测试
python scripts/decrypt_video.py --key <密钥> --input <加密文件> --output test.mp4

# 重新下载该集
python scripts/download_drama.py -n "剧名" --search -e 1
```

#### 6. Windows 路径问题

**错误信息：**
```
adb: error: cannot create file/directory: No such file or directory
```

**原因：** Git Bash/MSYS2 自动转换路径。

**解决方法：**

```bash
# 在命令前加 MSYS_NO_PATHCONV=1
MSYS_NO_PATHCONV=1 adb push file.apk /data/local/tmp/
```

#### 7. 剧名识别错误

**错误信息：**
```
[WARNING] 剧名漂移: 期望 "剧名A"，实际 "剧名B"
```

**原因：** UI 解析识别到错误的剧名（如推荐视频）。

**解决方法：**

```bash
# 使用 --name 参数强制指定剧名
python scripts/download_drama.py -n "正确的剧名" --search

# 或从文件读取（避免编码问题）
echo "正确的剧名" > name.txt
python scripts/download_drama.py --name-file name.txt --search
```

#### 8. 批量下载中断

**场景：** 下载到第 20 集时脚本崩溃或手动中断。

**解决方法：**

```bash
# 重新运行相同命令，自动断点续传
python scripts/download_drama.py -n "剧名" --search -b 80

# 或使用 --resume 参数
python scripts/download_drama.py -n "剧名" --search --resume
```

---

## 视频解密工具

`scripts/decrypt_video.py` — 独立解密工具，支持本地文件和直接从 CDN URL 下载并解密。

```bash
# 解密本地文件
python scripts/decrypt_video.py --key <32位hex密钥> --input encrypted.mp4 --output decrypted.mp4

# 从 CDN URL 直接下载并解密
python scripts/decrypt_video.py --key <32位hex密钥> --url <cdn_url> --output decrypted.mp4
```

解密逻辑：

- 解析 MP4 中的 `stsz` / `stco` / `stsc` / `senc` box，提取每个 sample 的 IV 与偏移
- 使用 AES-128-CTR 解密每个 sample
- 将 `encv` / `enca` 还原为原始编码格式，`sinf` box 转为 `free` box

---

## LLM 预处理工具

`scripts/preprocess_video.py` — 将下载好的 MP4 预处理为 LLM 分析素材包。

```bash
# 处理整个剧集目录
python scripts/preprocess_video.py videos/爹且慢，我来了

# 处理单集
python scripts/preprocess_video.py videos/爹且慢，我来了/episode_001.mp4

# 使用较小模型加快转录速度
python scripts/preprocess_video.py videos/爹且慢，我来了 --model small

# 只处理指定集数
python scripts/preprocess_video.py videos/爹且慢，我来了 --episodes 1 2 3
```

参数：

| 参数 | 默认 | 说明 |
| ---- | ---- | ---- |
| `--model` | large-v3 | Whisper 模型大小 |
| `--scene-threshold` | 0.20 | 场景切换检测阈值（越小帧越多） |
| `--interval` | 2.0 | 固定间隔抽帧秒数 |
| `--output` | `<输入目录>/llm_ready` | 输出目录 |

---

## r0capture 通用抓包

原始 r0capture 功能保持完整，适用于任意 Android App 的 SSL/TLS 流量抓取。

支持范围：

- 安卓 7 ~ 16
- 协议：HTTP/HTTPS、WebSocket、FTP、XMPP、IMAP、SMTP、Protobuf 及其 SSL 版本
- 框架：HttpUrlConnection、OkHttp 1/3/4、Retrofit、Volley 等
- 无视证书校验与绑定，无视整体壳/二代壳/VMP 加固

```bash
# Spawn 模式
python r0capture.py -U -f com.coolapk.market -v

# Attach 模式，保存 pcap 文件
python r0capture.py -U 酷安 -v -p output.pcap
```

> Frida 版本对应关系：frida 17 / Android 16，frida 16.5.x / Android 14，frida 15.x / Android 12

---

## 致谢

- 原始项目：[r0ysue/r0capture](https://github.com/r0ysue/r0capture)
- 基础参考：[frida_ssl_logger](https://github.com/BigFaceCat2017/frida_ssl_logger)
- ADB 中文输入：[ADBKeyBoard](https://github.com/senzhk/ADBKeyBoard)
