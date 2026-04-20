# download_hongguo.py — 红果短剧全集精准下载器

## 目标

给定红果短剧的剧名，全自动下载全集视频，输出可播放的 MP4 文件。下载的视频将作为多模态大模型的输入素材，用于学习和拆解短剧剧本，因此对集数准确性和完整性有严格要求。

## 核心要求

1. **集数精准**：下载的第 N 集必须对应 App 中的第 N 集，不允许错位
2. **全集完整**：给定 60 集就必须下载 60 集，缺集需明确报告
3. **一键全自动**：给定剧名即可完成全集下载，无需人工干预
4. **断点续传**：支持中断后从断点继续，不重复下载已完成的集数

## 使用方式

```bash
# 最简用法
python scripts/download_hongguo.py -n "西游，错把玉帝当亲爹"

# 指定起始集
python scripts/download_hongguo.py -n "西游，错把玉帝当亲爹" -e 5

# 自定义输出目录
python scripts/download_hongguo.py -n "西游，错把玉帝当亲爹" --output videos
```

## 架构

### 三层分离

```
Orchestrator（编排层）
  download_hongguo.py::main()
  职责：规划 → 逐集调度 → 进度跟踪 → 报告

Episode Pipeline（单集管线）
  download_hongguo.py::download_episode()
  职责：围栏 → 选集 → 等待捕获 → 下载 → 解密 → 校验

Infrastructure（基础设施层，复用已有模块）
  drama_download_common.py / decrypt_video.py / COMBINED_HOOK
```

### 与 download_drama.py 的关系

- 全新脚本，不修改 download_drama.py
- 复用底层工具函数（ADB、UI 解析、CENC 解密）
- Hook 脚本：从 download_drama.py 的 COMBINED_HOOK（第 147-332 行）提取为独立常量复用，不使用 frida_hooks/ 目录下的独立脚本
- 重建上层控制流，解决旧脚本的 stale_data 和选集不准问题

## 核心机制：围栏式捕获（Fence-based Capture）

旧脚本失败的根因是搜索预览、首页推荐、预加载等会触发大量 Hook 回调，污染 CaptureState 中的 URL/Key 数据。围栏机制通过时间戳过滤彻底解决此问题。

### 每集下载流程

```
fence_ts = time.time()                     1. 设置时间围栏
select_episode_from_ui(N)                  2. UI 选集面板切到第 N 集
ref, key = wait_capture(state, fence_ts)   3. 只接受围栏之后的 Hook 数据
ui_ep = read_ui_episode()                  4. 从 UI 读取当前集数
assert ui_ep == N                          5. 校验集数一致
download_and_decrypt(ref.best_url, key)    6. 下载 + CENC 解密
verify_playable(output)                    7. ffprobe 验证可播放
```

**关键**：`fence_ts` 在 `select_episode_from_ui()` 调用之前设置。选集操作触发的 Hook 数据（即当前集的数据）自然通过围栏。

### 为什么围栏解决了旧脚本的问题

| 旧问题 | 围栏如何解决 |
|-------|------------|
| 搜索预览触发 Hook → CaptureState 被污染 | 预览数据的 timestamp < fence_ts → 丢弃 |
| 预加载相邻集数 → 选错集数据 | 取围栏之后第一个到达的 ref+key（即当前集） |
| FRESHNESS_THRESHOLD=5s 过严 → stale_data | 无固定阈值，只要 > fence_ts 就是新鲜的 |
| Hook episode_number 提取失败 → 匹配回退 | 不依赖 Hook 的 episode_number，UI 集数是唯一真相 |

## 入口策略

### 优先：离线缓存入口

导航路径（每步的定位方式）：

1. **Spawn App 后等待首页加载** → 检测底部 Tab "首页" 文本确认加载完成
2. **点击"我的" Tab**（右下角）→ `find_text_bounds(xml, "我的")` 定位并点击
3. **点击设置图标**（右上角三横）→ `find_text_bounds(xml, "设置")` 或 `find_content_desc_bounds(xml, "设置")` 定位；fallback：点击坐标 (1020, 144)
4. **点击"离线缓存"** → `find_text_bounds(xml, "离线缓存")` 定位并点击；如果不可见则向上滚动后重试
5. **找到目标剧名** → `find_text_contains_bounds(xml, drama_name)` 模糊匹配剧名并点击
6. **确认进入播放器** → 检测 UI 中 `parse_ui_context()` 返回有效 episode

每步之间等待 1.5-2 秒 UI 稳定，每步最多重试 3 次。

**离线缓存不可用的判断条件**：步骤 5 中找不到目标剧名（重试 2 次后仍未找到）→ 切换到搜索入口。

### 回退：搜索入口

当离线缓存中找不到目标剧时，使用搜索方式进入。复用 download_drama.py 中 `search_drama_in_app()` 的核心逻辑（deeplink → 输入 → 搜索 → 点击结果）。

## Hook 消息协议

新脚本复用 download_drama.py 中的 `COMBINED_HOOK`（第 147-332 行）。以下是该 Hook **实际发送的消息格式**（基于源码逐行核对）：

### Hook 端发送的消息（实际格式）

```javascript
// 消息类型 1: video_model — setVideoModel 触发，包含剧名等元数据
// 注意：video_id 和 duration 在 data 对象内部，不是顶层字段
{t: "video_model", data: {mVideoId: "v02ebeg...", mVideoDuration: "125", ...}, episode_number: 5}

// 消息类型 2: video_ref — 从 model.vodVideoRef 提取，包含 video_id
// 注意：video_id 在 data.mVideoId 中
{t: "video_ref", data: {mVideoId: "v02ebeg...", ...}, episode_number: 5}

// 消息类型 3: video_info — mVideoList 中每个画质的信息（每集触发多条）
// 注意：没有顶层 video_id，需要通过上下文关联到当前 video_ref
// URL 在 data.mMainUrl，画质在 data.mResolution，kid 在 data.mKid
{t: "video_info", idx: 0, data: {mMainUrl: "https://...", mResolution: "1080p", mKid: "69b57b70..."}}

// 消息类型 4: AES_KEY — Native 层 av_aes_init 触发
// 字段名是 "key"（不是 "key_hex"），episode_number 来自全局缓存 lastEpisodeNumber
{t: "AES_KEY", key: "c2b271db...", bits: 128, dec: 0, episode_number: 5}
```

### Python 端 on_message 处理

```python
def on_message(msg, data):
    if msg["type"] != "send":
        return
    p = msg["payload"]
    ts = time.time()  # ← timestamp 由 Python 端生成，不是 Hook 发送的

    if p.get("t") == "video_ref":
        vid = p.get("data", {}).get("mVideoId", "")
        with state.lock:
            state.current_video_id = vid  # 记录当前 video_id，供 video_info 关联
            try:
                dur = int(p.get("data", {}).get("mVideoDuration", 0))
            except (ValueError, TypeError):
                dur = 0
            state.refs.append(VideoRef(
                video_id=vid,
                duration=dur,
                timestamp=ts,
            ))

    elif p.get("t") == "video_info":
        d = p.get("data", {})
        url = d.get("mMainUrl", "")
        if url:
            with state.lock:
                state.urls.append(VideoURL(
                    video_id=state.current_video_id,  # 关联到最近的 video_ref
                    url=url,
                    quality=d.get("mResolution", ""),
                    kid=d.get("mKid", ""),
                    timestamp=ts,
                ))

    elif p.get("t") == "AES_KEY":
        with state.lock:
            state.keys.append(AESKey(
                key_hex=p["key"],
                bits=p.get("bits", 128),
                timestamp=ts,
            ))
```

**关键设计点**：
- timestamp 由 Python 端 `time.time()` 生成，不是 Hook 端发送的
- `video_info` 消息不携带 video_id，通过 `state.current_video_id` 关联到最近的 `video_ref`
- 字段从 `p["data"]` 子对象中提取（如 `mVideoId`、`mMainUrl`、`mResolution`、`mKid`），不是顶层字段

## HookState 设计

```python
@dataclass
class VideoRef:
    video_id: str
    duration: int
    timestamp: float          # Python 端 on_message 中生成

@dataclass
class VideoURL:
    video_id: str
    url: str
    quality: str              # "360p", "480p", "720p", "1080p"
    kid: str
    timestamp: float

@dataclass
class AESKey:
    key_hex: str
    bits: int
    timestamp: float

class HookState:
    lock: threading.Lock      # on_message 和 wait_capture 跨线程访问
    current_video_id: str     # video_info 关联用，由 video_ref 消息更新
    refs: list[VideoRef]
    urls: list[VideoURL]
    keys: list[AESKey]

    def get_after_fence(self, fence_ts: float) -> tuple[VideoRef | None, str | None, AESKey | None]:
        """返回围栏之后的第一个 ref、对应的最高画质 URL、第一个 key"""
        with self.lock:
            ref = next((r for r in self.refs if r.timestamp > fence_ts), None)
            key = next((k for k in self.keys if k.timestamp > fence_ts), None)
            best_url = None
            if ref:
                # 找到该 video_id 对应的最高画质 URL
                matching = [u for u in self.urls
                            if u.video_id == ref.video_id and u.timestamp > fence_ts]
                if matching:
                    # 按画质排序：1080p > 720p > 540p > 480p > 360p
                    quality_order = {"1080p": 5, "720p": 4, "540p": 3, "480p": 2, "360p": 1}
                    matching.sort(key=lambda u: quality_order.get(u.quality, 0), reverse=True)
                    best_url = matching[0].url
            return ref, best_url, key
```

关键简化（相比 download_drama.py 的 CaptureState）：
- 不维护 captured_episodes 字典
- 不尝试关联 episode_number（UI 是唯一集数来源）
- 不维护 SessionValidationState
- 增加 threading.Lock 保证线程安全
- 分离 VideoRef 和 VideoURL（对应 Hook 的两种消息类型）

## 数据流

```
COMBINED_HOOK (on_message 回调)
    ├─ video_model  → （日志记录，不入 state）
    ├─ video_ref    → state.refs.append(VideoRef(...)) + state.current_video_id = vid
    ├─ video_info   → state.urls.append(VideoURL(video_id=state.current_video_id, ...))
    ├─ AES_KEY      → state.keys.append(AESKey(key=p["key"], ...))
    ▼
wait_capture(state, fence_ts, timeout=30s)
    │  轮询: 每 0.5s 调用 state.get_after_fence(fence_ts)
    │  完成条件: ref + 对应 URL + key 三者都到齐
    │  超时: 30s 内未收齐 → 返回 None
    ▼
download_and_decrypt(url, key_hex, output_path)
    │  data = bytearray(requests.get(url).content)
    │  decrypt_mp4(data, bytes.fromhex(key_hex))   # 就地解密
    │  fix_metadata(data)                           # 就地修复 encv→hvc1
    │  with open(output_path, 'wb') as f: f.write(data)
    ▼
verify_playable(output_path)
    │  ffprobe: 有 video stream + audio stream，duration > 0
    │  文件 > 100KB
    ▼
episode_NNN_<vid8>.mp4 ✓
```

## 组件清单

### 复用已有模块

| 来源 | 函数 | 用途 |
|------|------|------|
| `drama_download_common.py` | `run_adb()` | ADB 命令执行 |
| `drama_download_common.py` | `read_ui_xml_from_device()` | Dump UI XML |
| `drama_download_common.py` | `tap_bounds()`, `bounds_center()` | 点击操作 |
| `drama_download_common.py` | `find_text_bounds()`, `find_text_contains_bounds()` | 文本查找 |
| `drama_download_common.py` | `find_element_by_resource_id()`, `find_content_desc_bounds()` | 元素定位 |
| `drama_download_common.py` | `parse_ui_context()` | 提取剧名/集数/总集数 |
| `drama_download_common.py` | `select_episode_from_ui()` | 选集面板操作（直接复用） |
| `drama_download_common.py` | `append_jsonl()` | 写 session_manifest.jsonl |
| `decrypt_video.py` | `decrypt_mp4(data, key)`, `fix_metadata(data)` | CENC 就地解密 |
| `download_drama.py` | `COMBINED_HOOK`（第 147-332 行） | 提取为常量复用 |

### 新建函数

| 函数 | 职责 |
|------|------|
| `setup_frida(package: str) -> tuple[Session, Script]` | Spawn App + 加载 COMBINED_HOOK，返回 session 和 script |
| `on_message(msg, data)` | 解析 video_ref / video_info / AES_KEY 三种消息，追加到 HookState |
| `navigate_to_offline_cache() -> bool` | ADB 导航到离线缓存页（5 步，每步有定位策略和 fallback） |
| `enter_drama_from_cache(drama_name: str) -> bool` | 在缓存列表中模糊匹配剧名并点击进入播放器 |
| `read_ui_episode() -> int | None` | `read_ui_xml_from_device()` + `parse_ui_context()` 的薄封装，带 tap 唤醒重试 |
| `wait_capture(state, fence_ts, timeout=30) -> tuple | None` | 轮询 HookState，等待围栏后 ref+url+key 三者到齐 |
| `download_and_decrypt(url, key_hex, output) -> bool` | 下载 + 就地解密 + 写文件 |
| `verify_playable(path) -> bool` | ffprobe 验证：有 video+audio stream，duration > 0，文件 > 100KB |
| `download_episode(ep_num, state, output_dir, drama_name) -> dict` | 围栏式单集管线 |
| `build_plan(output_dir, total_eps, start_ep) -> list[dict]` | 生成下载计划，glob 已有文件跳过已完成 |
| `recover_frida(state, drama_name) -> tuple[Session, Script]` | Frida 断连后完整恢复（Spawn + 重新导航 + 进入剧 + 清空 state） |
| `main()` | CLI 入口 + 编排循环 + 最终报告 |

## 编排层 main() 流程

```
1. 解析 CLI 参数（-n 剧名, -e 起始集, --output 输出目录）
2. setup_frida() → Spawn App + Hook
3. navigate_to_offline_cache() → 进入离线缓存页
4. enter_drama_from_cache(drama_name) → 点击剧名进入播放器
   - 失败则 fallback 到搜索入口
5. read_ui_episode() → 确认进入播放器，从 parse_ui_context 获取总集数
6. build_plan() → 生成 [ep1: pending, ep2: done, ep3: pending, ...]
7. for ep in plan:
     if ep.status == "done": skip
     result = download_episode(ep.num)
     if result.success:
         ep.status = "done"
         append_jsonl(manifest_path, result)
     else:
         ep.status = "failed"
         ep.reason = result.reason
8. 输出报告：成功 N 集 / 失败 M 集（列出具体集数）
```

## 错误处理

| 场景 | 处理 |
|------|------|
| 单集 wait_capture 超时 | 重设围栏 + 重新 select_episode_from_ui()，最多 3 次 |
| UI dump 失败（播放中不稳定） | tap 屏幕中心唤醒控制层 → 重试 dump，最多 3 次 |
| UI 集数 != 预期集数 | 重新 select_episode_from_ui()，最多 2 次 |
| 下载/解密失败 | 重试 3 次后标记 failed，继续下一集 |
| verify_playable 失败 | 删除文件，标记 failed |
| Frida session 断开 | recover_frida()：重新 Spawn App + 导航到离线缓存 + 进入剧 + 清空 HookState → 从当前集继续 |
| 所有集数完成 | 输出 session_manifest.jsonl + 终端报告 |

## 输出结构

```
videos/<剧名>/
├── episode_001_<vid8>.mp4      # 解密后可播放（vid8 = video_id 末 8 位）
├── episode_002_<vid8>.mp4
├── ...
├── episode_060_<vid8>.mp4
├── session_manifest.jsonl      # 每集一行 JSON（复用已有格式）
└── download.log                # loguru 日志
```

文件命名复用 `drama_download_common.py` 中的 `episode_NNN_<vid8>` 约定。断点续传通过 glob `episode_NNN_*.mp4` 检测已完成的集数。

## 文件规模估算

`download_hongguo.py` 预计 600-800 行：
- HookState + 数据类：~60 行
- setup_frida + on_message：~80 行
- 离线缓存导航（5 步 ADB 操作）：~150 行
- download_episode（围栏管线）：~100 行
- wait_capture + verify：~50 行
- download_and_decrypt：~40 行
- recover_frida + 错误处理：~50 行
- main + CLI + build_plan + 报告：~100 行

## 风险与缓解

| 风险 | 缓解 |
|------|------|
| App 版本更新导致 resource-id 变化 | 每步导航优先用文本匹配（"我的"、"设置"、"离线缓存"），resource-id 作为 fallback |
| 播放器页 uiautomator dump 失败 | tap 唤醒控制层后重试；最坏情况下靠截屏确认状态 |
| 离线缓存中没有目标剧 | 自动 fallback 到搜索入口 |
| 单集下载失败 3 次 | 标记 failed 继续下一集，最终报告中列出失败集数供人工处理 |
