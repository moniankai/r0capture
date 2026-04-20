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
    --max-total 100          # 只下总集数 ≤ 100 的剧
    --max-dramas 10          # 本次限 10 部

# 断点续传: .batch_state.json 自动记录, 重跑 skip 已 done
# 失败策略: 默认 skip 继续, --halt-on-fatal 遇错即停
# 设备自愈: --reboot-every 5 每 5 部 reboot 手机
```

### 流程 C：只下某一集 / 集数段

```bash
# 先跑 spawn_nav.py, 然后
python scripts/v5_lean.py -n "剧名" --series-id XXX -t 60 -s 10 -e 20
```

---

## 如何获取 series_id

### 方式 1：排行榜批量采（推荐）

开独立 session 按 `HANDOFF_METADATA.md` 操作，产出 `.planning/rankings/dramas.json`：
```json
{
  "7622955207885851672": {
    "series_id": "7622955207885851672",
    "name": "开局一条蛇，无限进化",
    "total": 83,
    "first_vid": "...",
    "source_ranks": ["热播榜/1", "完结榜/5"]
  }
}
```

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
