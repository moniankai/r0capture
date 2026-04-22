# lean-2session 下载架构（推荐）

**2026-04 起新版红果 App 的推荐下载方案**。老 `download_drama.py` / `hongguo_agent.py` 已因 App 升级失效（Frida RPC 被多 hook 阻塞）。已在 4 部剧 250+ 集端到端验证 **0 串集 0 缺集 0 重复**。

---

## 为什么要改架构

新版红果 App 的播放器路径由 `ot3.z.B0(VideoModel, long, String, SaasVideoData)` 单入口承载。但 Frida RPC 和高频 Java hook **不能共存于同一会话**：hook 每次触发都要走 Java bridge 锁，RPC 的 Promise resolve 会被排队饿死 → 15s timeout 假返回。

**方案**：拆 2 会话

| Session | 职责 | hook 数 |
|---------|------|---------|
| A `spawn_nav.py` | spawn App + Intent 跳 `ShortSeriesActivity` | **0** |
| B `v5_lean.py` | attach + B0 hook + swipe 扫描下载 | **1**（仅 `ot3.z.B0`）|

Session A 零 hook → RPC 100ms 内完成；Session B attach 时 Main thread 空闲 → hook 不阻塞。下载全程单 hook 就拿齐所需字段：`idx`（long）/ `biz_vid` / `tt_vid` / `kid` / `spadea`（派生 AES key）/ `streams`。

---

## 环境要求

| 项 | 约束 |
|----|------|
| 设备 | Android 9（实测 MI6），root |
| Frida | **16.5.9 严格一致**（17.x 不可用，Android 9 Java bridge 失效）|
| App | `com.phoenix.read`，**已登录** |
| ADB | USB 调试 + 设备已连 |
| ADBKeyboard | 仅 metadata 采集需要，下载链路不依赖 |
| Python | 3.10+ |
| 依赖 | `pip install -r requirements.txt`（frida, loguru, pycryptodome, requests, tqdm, imageio-ffmpeg, psutil）|

**启动 frida-server**（每次手机重启后）：
```bash
adb shell "su -c 'nohup /data/local/tmp/frida-server >/dev/null 2>&1 &'"
```

---

## 3 个核心脚本

| 脚本 | 一句话 |
|------|--------|
| `scripts/spawn_nav.py` | 冷启 App + Intent 跳目标剧 ShortSeriesActivity |
| `scripts/v5_lean.py` | attach + 单 B0 hook + 双向扫描下载全集 |
| `scripts/verify_drama.py` | 6 项机械校验 + 抽首/中/末 3×3 帧 |
| `scripts/hongguo_batch_lean.py` | 消费 dramas.json 批量编排 |

---

## 操作流程

### 流程 A：单部剧（3 步）

你需要手头 3 项：`剧名 + series_id + total`（怎么拿见下节）。

```bash
# 1. Session A 进剧（10 秒左右）
python scripts/spawn_nav.py --series-id 7622955207885851672 --pos 0

# 2. Session B 下全集（每集 ~10 秒）
python scripts/v5_lean.py -n "开局一条蛇，无限进化" \
    --series-id 7622955207885851672 -t 83

# 3. 质量验证（30 秒）
python scripts/verify_drama.py -n "开局一条蛇，无限进化" \
    -t 83 --series-id 7622955207885851672
```

### 流程 B：批量下载（推荐）

```bash
# 前置：准备 dramas.json (见下节"如何获取 series_id")

# 跑批量（每部剧自动 spawn_nav + v5_lean + verify）
python scripts/hongguo_batch_lean.py \
    --input .planning/rankings/dramas.json \
    --max-total 130          # 只下总集数 ≤ 100 的剧
    --max-dramas   100        # 本次限 10 部

# 断点续传: .batch_state.json 自动记录, 重跑 skip 已 done
# 失败策略: 默认 skip 继续, --halt-on-fatal 遇错即停
# 设备自愈: --reboot-every 5 每 5 部 reboot 手机

python scripts/hongguo_batch_lean.py --input .planning/rankings/dramas.json --max-total 130 --max-dramas 100
```

### 流程 C：只下某一集 / 集数段

```bash
# 先跑 spawn_nav.py, 然后
python scripts/v5_lean.py -n "剧名" --series-id XXX -t 60 -s 10 -e 20
```

---

## 如何获取 series_id

### 方式 1：排行榜批量采（推荐）

**脚本**: `scripts/rank_collect.py`（2026-04-20 实装, Compose 榜单页 `SeriesRankTabViewModel.c0(List)` hook）

#### 前置条件

| 项 | 要求 |
|---|------|
| ADB 连通 | `adb devices` 看到 `device` 状态 |
| App 在运行 | `adb shell pidof com.phoenix.read` 非空 |
| frida-server 运行 | `adb shell "su -c 'pidof frida-server'"` 非空 |
| Frida 16.5.9 | PC + 手机 frida-server 版本一致 |
| App 已登录 | 任意前台页均可, 排行榜需登录态 |
| 屏幕分辨率 | 1080x1920 (坐标按此硬编码) |

#### 运行命令

```bash
# 默认榜: 热播榜 + 漫剧榜
python scripts/rank_collect.py --serial 4d53df1f --ranks "热播榜,漫剧榜"

# 自定义榜 + 小剂量试采
python scripts/rank_collect.py --serial 4d53df1f --ranks "热播榜" \
    --per-rank-limit 20 --max-swipes 5

# 清旧产物干净重采
rm -f .planning/rankings/dramas.json .planning/rankings/snapshots.jsonl
python scripts/rank_collect.py --serial 4d53df1f --ranks "热播榜,漫剧榜" --max-swipes 15

# 后台跑 (Windows Git Bash 管道会吞 buffered 输出, 必须 -u)
python -u scripts/rank_collect.py --serial 4d53df1f ... > d:/tmp/collect.log 2>&1 &
```

**CLI 参数**:

| 参数 | 默认 | 说明 |
|------|------|------|
| `--serial` | `$ADB_SERIAL` | ADB 设备序列号 |
| `--ranks` | `热播榜,漫剧榜` | 榜单列表, 逗号分隔 |
| `--per-rank-limit` | `0` (不限) | 每榜采到 N 条就停 |
| `--max-swipes` | `40` | 单榜最大下滑次数 (安全上限) |

**首版支持的榜** (首屏可见 5 个): 预约榜 / 推荐榜 / 热播榜 / 漫剧榜 / 新剧榜
（演员榜 / 必看榜 / 收藏榜 / 热搜榜需横滑才可见, 首版未支持）


# 1. 命令行参数 (最常用)
python scripts/rank_collect.py --serial 4d53df1f --ranks "热播榜,漫剧榜"

# 2. 环境变量
export ADB_SERIAL=4d53df1f      # Linux/Mac/Git Bash
set ADB_SERIAL=4d53df1f         # Windows cmd
python scripts/rank_collect.py --ranks "热播榜,漫剧榜"

# 3. 不传 (只连了一台设备时)
python scripts/rank_collect.py --ranks "热播榜,漫剧榜"
# 脚本会自动执行 adb devices 取第一台

脚本现支持全部 9 个榜
榜名	tap x	前置横滑次数
预约榜 / 推荐榜 / 热播榜 / 漫剧榜 / 新剧榜	120 / 324 / 528 / 732 / 936	0
演员榜 / 必看榜 / 收藏榜	390 / 594 / 798	1
热搜榜	960	2

#### 脚本内部流程

```
每个榜循环执行:
  1. force_reenter: BACK 到主页 → tap 剧场 (324,1820) → tap 排行榜 (442,381)
     (确保列表回到顶部 + tab bar 在 y=516 可见)
  2. sleep 1.5s + drain 队列残留 (推荐榜进入时的 stale c0 事件)
  3. tap_rank_tab(当前榜, y=516)
  4. sleep 2.5s 等 Compose 首屏数据到达
  5. drain() 采首屏 (通常 10 条)
  6. 循环 swipe 下滚 (540,1550→540,900, 700ms) + drain, 直到:
     - 连续 3 次无新 series_id, 或
     - 达到 --max-swipes 上限, 或
     - 达到 --per-rank-limit 条目数
  7. atomic save dramas.json
```

#### 产物

```
.planning/rankings/dramas.json       主档 (series_id 为 key, 覆盖写)
.planning/rankings/snapshots.jsonl   历史快照 (append-only, 每榜 snapshot)
```

**dramas.json 示例条目**:
```json
{
  "7622955207885851672": {
    "series_id": "7622955207885851672",
    "name": "开局一条蛇，无限进化",
    "total": 83,
    "first_vid": "7622955207885851xxx",
    "cover_url": "https://p3-reading-sign.fqnovelpic.com/...",
    "recommend_text": "玄幻 / 修仙·全83集",
    "top_comment": "...",
    "popularity": 27731250,
    "source_ranks": ["热播榜/1", "漫剧榜/5"],
    "first_seen_at": "2026-04-20T17:10:01",
    "last_updated_at": "2026-04-20T17:10:04"
  }
}
```

字段合并规则: `source_ranks` UNION, 其他空值不覆盖; `first_seen_at` 首次永不改。

#### 故障恢复

| 症状 | 处理 |
|------|------|
| `frida.TransportError: timeout` | `taskkill /F /IM python.exe` + 重启 frida-server |
| ANR 对话框弹出 | `adb shell am force-stop com.phoenix.read` + 重启 App + 重启 frida-server |
| "未进入排行榜页" | 脚本已 3 次重试, 还失败则 screencap 检查是否有弹窗 / 键盘遮挡 |
| 某榜采到 0 条 | 上次采集可能滚到底 tab bar 被遮挡, 已用 `force_reenter` 每榜修复 |
| `hook ready 超时` | App 正在启动中, 等 10s 稳定后重试 |

#### 已知局限

- **漫剧榜实际只有 ~10 条**, 之后 App fallback 显示热播榜推荐, 标记为"漫剧榜/11+"
- **横滑才露出的榜暂不支持**: 演员榜 / 必看榜 / 收藏榜 / 热搜榜
- **`is_locked` / `unlocked_eps` 字段留 null**, P3 再补
- **rank_pos 用 Python 首见顺序**（服务端 `v()` 里的 rank 只是 batch 内 1-10）

#### 详细文档

- 详细字段映射 / hook 原理 / 故障排查: `.planning/rankings/P1_采集器操作指南.md`
- UI 坐标探测过程: `.planning/rankings/P0_探测结果.md`
- Bug 反推记录: `.planning/rankings/METADATA_BUG_REPORT.md`
- Schema 约定: `HANDOFF_METADATA.md`

### 方式 2：交互式单部采集

`scripts/resolve_interactive.py`：在红果 App 里随便浏览，hook 自动抓。手动在 App 点几部剧 → Ctrl-C 退出 → 输出 `dramas.json`。

### 方式 3：已知 series_id

直接传给 CLI。**注意**：必须是 **19 位纯数字的真 series_id**，不是当集的 biz_vid。
- 获取途径：从任何一次 v5_lean 的 `session_manifest.jsonl` 的 `series_id` 字段抄
- 验证方式：跑 `spawn_nav --series-id X --pos 0`，session A 成功就说明 sid 正确

---

## 输出目录结构

```
videos/<剧名>/
├── episode_001_<kid8>.mp4      # 1 集 1 文件，可直接播放
├── episode_002_<kid8>.mp4
├── ...
├── session_manifest.jsonl       # 每集 1 行: {ep, vid, biz_vid, kid, series_id, file, ts}
├── verify/                      # verify_drama 抽帧
│   ├── ep1_t5.png / ep1_t30.png / ep1_t60.png
│   ├── ep<mid>_t*.png
│   └── ep<end>_t*.png
├── verify_report.json           # 机械校验结果
└── cross_episodes_report.json   # find_crossed_episodes 输出
```

---

## 质量验证（verify_drama）

**6 项机械校验**（单部剧 30 秒完成）：

| 检测 | 方法 | 含义 |
|------|------|------|
| file_count | 统计 mp4 文件数 | 无缺集 |
| series_id_consistent | manifest 每行 sid 一致 | 无串剧 |
| ep_idx_match | manifest `ep` 字段 == 文件名集号 | B0 idx 强校验 |
| vid_unique | manifest 里 vid 无重复 | 每集独立 |
| biz_vid_unique | biz_vid 无重复 | 每集独立 |
| hash_unique | `find_crossed_episodes.py` 扫文件内容 | 无串集 |

**抽帧**：首集 / 中段 / 末集各抽 5s/30s/60s 3 帧 PNG，用于肉眼快速看画风是否一致、剧情是否递进。

---

## 性能基准

| 场景 | 实测耗时 |
|------|---------|
| 1 部 83 集《开局一条蛇》| 14 分钟 |
| 1 部 60 集《卿卿入怀川》| 10 分钟 |
| 1 部 60 集《疯美人》| 9 分钟 |
| 单集平均 | **~10 秒**（swipe + 下载 + 解密 + manifest）|
| 每部固定开销 | ~15 秒（spawn_nav + hook 加载 + swipe 定位）|

**外推**：100 部 × 60 集 ≈ **17 小时**（单线程串行，不含 reboot）。建议通宵跑 + `--reboot-every 10`。

---

## 已知约束与限制

### ❌ 暂不支持
- **漫剧（AIGC 动画短视频）**：播放路径不走 `ot3.z.B0`，本架构全失效。用 `source_ranks` 过滤含"漫剧榜"的条目（batch_lean 可以加自定义 filter）。
- **VIP / 广告解锁剧**：仅能下已解锁集数，`total` 可能 ≠ 实际可下集数。metadata 里 `is_locked=true` 的剧 batch_lean 默认 `--skip-locked` 过滤。

### ⚠️ 行为差异
- App 有 **进度恢复机制**，Intent 的 `key_click_video_pos=0` 被忽略 → v5_lean 靠 swipe 双向扫描补下。
- swipe 偶尔过冲（一次跳 2-3 集），扫描会到边界转向回补，**无需干预**。

---

## 常见问题

**Q：初始 B0 15s 未到**
看 log 里 `rejected_sids`：
- `= none` → App 可能卡 splash 或不在 ShortSeriesActivity → force-stop + 重跑 spawn_nav
- `!= none` → App 在播别的剧 → spawn_nav 的 Intent 没生效，重跑

**Q：v5_lean 进度停滞 100 轮退出**
App 卡死或 Frida session 失联：
```bash
adb shell "su -c 'pkill -9 frida-server; nohup /data/local/tmp/frida-server >/dev/null 2>&1 &'"
adb shell "am force-stop com.phoenix.read"
# 重跑 batch_lean，会自动 resume
```

**Q：frida-server 挂了 (ServerNotRunningError: closed)**
反 Frida 检测触发或多次 attach 后 server 不稳。同上重启 frida-server。

**Q：series_id 传错（dramas.json 里 sid 是 biz_vid）**
这是 metadata 采集的历史 bug（2026-04-20 已修复）。详见 `.planning/rankings/METADATA_BUG_REPORT.md`。修复后重采一次 dramas.json。

**Q：手机屏幕会自动锁屏打断下载**
```bash
adb shell "settings put system screen_off_timeout 1800000"  # 30 分钟
```

---

## 架构细节（可选阅读）

- 方案决策：见 memory `memory/project_lean_2session.md`
- 深诊过程：git commit `2a4bc25` 的 body
- 故障恢复：v5_lean 的 `try_download_current` 有运行时 sid 校验，`state.rejected_sids` 记录所有被拒的剧 id 便于诊断
- 批量调度：`hongguo_batch_lean.py` 原子 state（tmp + rename），支持 resume / reboot / timeout / halt 策略

---

## 相关工具

| 工具 | 何时用 |
|------|--------|
| `scripts/resolve_interactive.py` | 单部剧 attach 抓 series_id（交互式）|
| `scripts/find_crossed_episodes.py` | 单部剧 hash 扫串集（verify_drama 内部调用）|
| `scripts/audit_drama_downloads.py` | 下载审计报告（JSON）|
| `scripts/preprocess_video.py` | LLM 预处理：抽关键帧 + Whisper ASR |
| `scripts/decrypt_video.py` | 独立解密：已有 key + 加密 MP4 → 可播放 MP4 |

---

## 历史架构（legacy）

以下脚本基于多 hook + RPC，**新版 App 下已失效**，仅保留参考：

- `scripts/download_drama.py`（单部下载，老 README 主推）
- `scripts/hongguo_v5.py`（v5 legacy 主入口）
- `scripts/hongguo_agent.py` + `scripts/hongguo_batch.py`（老编排器）

详见 [README.md](README.md) 的"红果短剧下载器"章节。
