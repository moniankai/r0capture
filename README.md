# r0capture

安卓 SSL 通杀抓包脚本，扩展支持红果免费短剧的 CENC 加密视频一键下载。

---

## 目录

- [红果短剧下载器](#红果短剧下载器)
  - [环境准备](#环境准备)
  - [操作流程](#操作流程)
  - [使用示例](#使用示例)
  - [参数说明](#参数说明)
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
| Frida Server | 版本需与 PC 端 frida 包匹配，推荐 frida 16.5.9（兼容 Android 9+） |
| 红果 App | com.phoenix.read，版本无要求 |

**启动 Frida Server（每次重启手机后执行）：**

```bash
adb shell su -c "/data/local/tmp/frida-server &"
```

#### 3. ADBKeyboard（仅 --search 模式需要）

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

### 操作流程

#### 模式 A：手动模式（无需 ADBKeyboard）

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
```

```bash
# 基础用法（自动识别剧名）
python scripts/download_drama.py

# 指定输出文件夹名（不在 App 内搜索）
python scripts/download_drama.py -n "爹且慢，我来了"
```

#### 模式 B：搜索模式（需要 ADBKeyboard）

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
```

```bash
# 搜索并下载第 1 集
python scripts/download_drama.py -n "爹且慢，我来了" --search

# 搜索并从第 5 集开始
python scripts/download_drama.py -n "爹且慢，我来了" --search -e 5
```

#### 模式 C：批量连续下载

批量模式在每集下载完成后，自动通过 ADB 在 App 内切换到下一集：

```bash
# 连续下载 10 集（从第 1 集开始）
python scripts/download_drama.py -n "剧名" --search -b 10

# 连续下载全集（不限数量）
python scripts/download_drama.py -n "十八岁太奶奶驾到，重整家族荣耀" --search -b

# 从第 3 集开始连续下载 5 集（手动模式也支持批量）
python scripts/download_drama.py -e 3 -b 5
```

---

### 使用示例

```bash
# 示例 1：手动模式，自动识别剧名，默认 1080p
python scripts/download_drama.py

# 示例 2：搜索并下载单集
python scripts/download_drama.py -n "爹且慢，我来了" --search -e 3

# 示例 3：搜索并批量下载全集
python scripts/download_drama.py -n "爹且慢，我来了" --search -b

# 
# 示例 3：搜索并批量下载5集
python scripts/download_drama.py -n "凡人仙葫第一季" --search -b 5

# 示例 4：挂载到已运行的 App（不重启 App）
python scripts/download_drama.py --attach-running -n "剧名"

# 示例 5：下载后同步生成 LLM 素材包（关键帧 + ASR 字幕）
python scripts/download_drama.py -n "剧名" --search --preprocess
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

---

### 断点续传与会话清单

#### 自动断点续传

下载器支持自动断点续传。如果批量下载中途中断（脚本崩溃、手动 Ctrl+C、网络断开等），重新运行相同命令即可从断点继续：

```bash
# 首次运行：下载到第 20 集时中断
python scripts/download_drama.py -n "剧名" --search -b 80

# 重新运行：自动跳过前 20 集，从第 21 集继续
python scripts/download_drama.py -n "剧名" --search -b 80
```

断点续传基于 `session_manifest.jsonl` 文件，该文件记录每次下载的历史。

#### 自动重试机制

当单集下载失败时（网络超时、解密失败等），脚本自动重试最多 3 次：

- 每次重试前清空 Hook 数据状态，确保数据刷新
- 重试间隔 2 秒，避免触发 App 限流
- 重试历史记录到 `session_manifest.jsonl`

#### session_manifest.jsonl 格式

每次下载会在输出目录生成 `session_manifest.jsonl` 文件（每行一个 JSON 对象）：

```jsonl
{"episode": 1, "status": "downloaded", "video_id": "abc12345", "resolution": "720p", "video_path": "episode_001_abc12345.mp4", "retry_count": 0, "timestamp": 1713196800.0}
{"episode": 2, "status": "retry_attempt", "attempt": 1, "reason": "download_failed", "timestamp": 1713196850.0}
{"episode": 2, "status": "retry_success", "video_id": "def67890", "resolution": "720p", "video_path": "episode_002_def67890.mp4", "retry_count": 0, "timestamp": 1713196860.0}
{"episode": 3, "status": "skipped_resume", "reason": "already_completed", "timestamp": 1713196900.0}
```

**字段说明**：

- `episode`: 集数
- `status`: 状态（downloaded | skipped_existing | skipped_resume | retry_attempt | retry_success | failed_after_retries）
- `video_id`: 视频 ID（8 位后缀）
- `resolution`: 分辨率
- `video_path`: 解密后视频路径（相对路径）
- `retry_count`: 重试次数（0 表示首次成功）
- `timestamp`: Unix 时间戳

#### 离线审计

使用 `audit_drama_downloads.py` 审计下载质量：

```bash
python scripts/audit_drama_downloads.py videos/剧名 --expected-total 80
```

审计工具会：

- 识别缺失的集数
- 检测重复文件
- 分析 `session_manifest.jsonl` 中的重试模式
- 生成重命名建议

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
