# 排行榜 Metadata 采集任务 Handoff

**目标**：自动化遍历红果短剧 App 所有排行榜（热播/新剧/完结/分类榜等），
每榜采前 N 部剧的结构化元数据，输出可作为 `hongguo_batch_lean.py` 的直接输入。

**范围约束**：
- 只采元数据，不下载不播放不解密
- 不改动已稳定的下载链路（v5_lean/spawn_nav/hongguo_v5/verify_drama）
- 产出与 batch 下载的输入接口严格兼容

---

## 输出 Schema（分层存储）

```
.planning/rankings/
├─ dramas.json       — 主档 (series_id 为主键, 覆盖写, dedup 后)
└─ snapshots.jsonl   — 历史快照 (append-only, 每次采集一行一条 rank 记录)
```

### dramas.json 结构

整个文件是一个以 `series_id` 为 key 的 dict：

```json
{
  "7622955207885851672": {
    "series_id": "7622955207885851672",
    "name": "开局一条蛇，无限进化",
    "total": 83,
    "first_vid": "v02ebeg10000d75383vog65nn8lufucg",
    "cover_url": "https://p6-novel.byteimg.com/novel-pic/....image",
    "recommend_text": "玄幻 / 修仙",
    "is_locked": false,
    "unlocked_eps": null,
    "first_seen_at": "2026-04-20T15:40:00",
    "last_updated_at": "2026-04-20T15:40:00",
    "source_ranks": ["热播榜/1", "完结榜/5"]
  },
  "...": {...}
}
```

**字段说明**：

| 字段 | 类型 | 必填 | 来源 | 说明 |
|------|------|------|------|------|
| series_id | string | ✓ | `svd.getSeriesId()` | 主键 |
| name | string | ✓ | `svd.getSeriesName()` | 剧名 |
| total | int | ✓ | `svd.getEpisodesCount()` (long) | 总集数 |
| first_vid | string | optional | `svd.getVid()` 的首集 / 或 `B0` 的 VideoRef.mVideoId | 首集 vid，Intent 进 ep1 用 |
| cover_url | string | optional | `svd.getCover()` | 封面 |
| recommend_text | string | optional | `svd.getRecommendText()` 或 `getRecommendReasonList()` | 分类/tag |
| is_locked | bool | ✓ | 启发式判断（见下方） | 是否付费/广告解锁 |
| unlocked_eps | int\|null | optional | 已解锁集数（若 is_locked） | total 可能 ≠ 可下载集数 |
| first_seen_at | ISO8601 | ✓ | 首次采集时间 | 覆盖写时保留 |
| last_updated_at | ISO8601 | ✓ | 本次采集时间 | 每次覆盖写更新 |
| source_ranks | list[string] | ✓ | 所有在榜记录 "榜名/位置" | UNION 保留历史 |

### snapshots.jsonl 每行

```json
{"ts":"2026-04-20T15:40:00","rank_type":"热播榜","rank_pos":1,"series_id":"7622955207885851672","session_id":"sess_20260420_1540"}
```

Append-only，不 dedup，保留历史（追踪霸榜变化）。

---

## batch 下载输入转换

```python
import json
dramas = json.load(open('.planning/rankings/dramas.json'))
batch_input = [
    {"name": d["name"], "series_id": d["series_id"], "total": d["total"]}
    for d in dramas.values()
    if not d.get("is_locked") and d["total"] <= 150  # 按策略过滤
]
```

---

## 已有资产（继承 / 必读）

### 已验证的技术栈

| 组件 | 用途 | 状态 |
|------|------|------|
| Frida 16.5.9 + Android 9 MI6 | Hook 运行时 | 稳定 |
| `ot3.z.B0(VideoModel, long, String, SaasVideoData)` | 单 hook 拿剧数据 | 稳定 |
| `SaasVideoData` 新路径 | `com.dragon.read.component.shortvideo.data.saas.video.SaasVideoData` | 确认 |
| `dragon8662://` deeplink 分发 | 路由到各 Activity | 已用 search |
| ADBKeyboard 中文输入 | 搜索用（本任务不一定需要） | 已启用 |
| `scripts/resolve_interactive.py` | 半成品：已 hook setSeriesName/Id/EpisodesCount + atomic save | **最佳起点** |
| `scripts/spawn_nav.py` | Intent startActivity 模板 | 可参考 |
| `scripts/v5_lean.py` | B0 hook 模板 | 可参考 |

### 关键文件路径

```
scripts/resolve_interactive.py   — 最接近目标, 扩展它即可
scripts/spawn_nav.py             — spawn + Intent 纯 hook-free 示例
scripts/v5_lean.py               — 单 hook + 扫描循环模板
CLAUDE.md                        — 项目规范
memory/project_lean_2session.md  — 下载架构总结
memory/feedback_*.md             — 各种坑（必读 feedback_miui_deeplink_hijack, feedback_uiautomator_player, feedback_agent_single_input）
```

### `SaasVideoData` 可用 getter（2026-04 版已探测）

| 方法 | 返回 | 用途 |
|------|------|------|
| `getSeriesId()` | String | series_id 主键 |
| `getEpisodesId()` | String | 同 series_id（两者等价） |
| `getSeriesName()` | String | 剧名（setter 里也能 hook 到） |
| `getVid()` | String | biz_vid，当集业务 id |
| `getVidIndex()` | long | 当前集 idx（1-based 观察到）|
| `getEpisodesCount()` | long | 总集数 |
| `getCover()` / `getEpisodeCover()` | String | 封面 URL |
| `getIndexInList()` | int | 在榜单里的位置（0-based）|
| `getRecommendText()` / `getRecommendReasonList()` | String | 分类标签 |
| `getDuration()` | long | 当集时长（秒）|
| `getDiggCount()` / `getCommentCount()` / getPlayCnt() | long | 热度指标 |

所有 getter 都在 `resolve_interactive.py` 的 hook 体里能调。

---

## 任务分解（建议顺序）

### P0 — 探索排行榜入口

1. **查 deeplink 路由表**
   ```bash
   adb shell "dumpsys package com.phoenix.read" | grep -B 3 -A 10 "dragon8662"
   ```
   找有没有 `dragon8662://rank` / `dragon8662://hot` / `dragon8662://category` 之类 host

2. **静态 UI dump MainFragmentActivity**
   - 启动 App 后 `uiautomator dump` 看底部 tab 或顶部入口（"排行/榜单/热门"等）
   - 获取固定坐标（MI6 竖屏 1080x1920）

3. **探测榜单页**
   - tap 进排行榜 tab → `uiautomator dump` → 发现所有榜类别（热播/新剧/完结/分类）
   - 记录每个榜类别的 tap 坐标或 tab 索引

### P1 — 单榜单遍历原型

以"热播榜"为例：

1. attach App + 挂 hook：
   - `ot3.z.B0` 监听 idx + svd 全字段
   - `SaasVideoData.setSeriesName/Id/EpisodesCount/setCover` setter
2. Python driver 循环：
   - `adb input swipe` 向下滚动榜单（量力而行，每次半屏）
   - Hook 捕获进入 ViewHolder 的每条 SaasVideoData
   - 重复直到连续 K 次 swipe 没抓到新 series_id（到底）
3. atomic save `dramas.json`（参考 resolve_interactive 的 tmp + rename）

### P2 — 多榜单编排

1. 配置 `rankings.yaml` 定义要采的榜单（name + 入口 tap 坐标 or deeplink）
2. 主脚本循环每个榜单：
   - nav 到该榜单
   - 执行 P1 遍历
   - 记录本榜 50 部 snapshot 到 `snapshots.jsonl`
3. 合并去重 → `dramas.json`

### P3 — 边界处理

1. **VIP/广告锁剧识别**
   - `svd.getPlayStatus()` 值含义？
   - 或者 UI 上有 "VIP" / "免费" 角标，dump xml 判断
   - 落 `is_locked` 字段
2. **unlocked_eps 探测**
   - 进剧详情页看可解锁集数（可能需要 tap 进剧页）
3. **重复 series_id 在多榜**
   - 合并 source_ranks 而非覆盖

### P4 — 验收

目标：
- [ ] 5 个榜单 × 每榜 50 部 = ≥ 200 unique series_id
- [ ] 字段完整性：name/series_id/total 必填字段 100% 覆盖
- [ ] 产出能直接喂 hongguo_batch_lean.py 跑 5 部（抽样）

### ⚠️ 已知 bug（已修复 2026-04-20）

**2026-04-20 18:12** 下载 session 发现 dramas.json 里 `series_id` 字段**存成了 ep1 的 biz_vid**。
详见 `.planning/rankings/METADATA_BUG_REPORT.md`。

**修复 (2026-04-20 18:35)**: 在 `rank_collect.py` 的 JS_HOOK 里更正 j30 字段映射：
- 错: `series_id = j30.J()` (实际返回 ep1 biz_vid)
- 对: `series_id = j30.x()`（主）/ `j30.u()`（fallback，等价）
- 错: `first_vid = j30.u()` (实际返回 series_id)
- 对: `first_vid = j30.J()` (真正的 ep1 biz_vid)

Ground truth 验证: 《疯美人》series_id = `7624372698860227646` 与下载 session
实测值完全匹配。重采后 46 条 unique 字段完整性 100%, 无 first_vid == series_id 冲突。

---

## 技术要点 & 陷阱

### ✓ 正确做法

- **单 Frida session**：全程 attach 一个 hook script，只挂 B0 + SaasVideoData setter。如 v5_lean 经验，高频 hook 会堵 RPC。
- **atomic save**：每采 N 条就 tmp + rename 落盘，防进程中断丢数据。`resolve_interactive.py` 已实现。
- **ViewHolder 复用识别**：同一 SaasVideoData 实例可能被 setSeriesName/setSeriesId 多次调用。以 `series_id` 为 dedup key，名字、total 以最后一次为准。
- **B0 只在播放时触发**，排行榜只看列表不播放 → **不能依赖 B0**，必须 hook setSeriesName/setSeriesId/setEpisodesCount 这三个 setter。
- **swipe 温和**（参考 v5_lean: 540,1200→540,700 / 700ms），避免一滑飞十条。

### ✗ 避免踩的坑

- 不要在排行榜页 tap 剧卡片（会进入播放页，破坏纯列表浏览）
- 不要依赖 uiautomator dump 太多次，Frida 挂着时 dump 可能卡（见 `feedback_uiautomator_player.md`）。必要时固定坐标。
- 不要挂 `SsHttpCall.execute` 抓搜索/推荐 API body — v5 踩过这个坑会堵 Java bridge。
- 不要挂 `TTVideoEngine.setVideoModel` overload — 同上，高频阻塞。
- Frida 被阻塞时 RPC Promise 不 resolve → 不要在 hook 里做 scheduleOnMainThread 的重活
- MIUI 劫持 `dragon8662://` 要带 `-p com.phoenix.read`（见 `feedback_miui_deeplink_hijack.md`）

---

## 禁止

- 不修改 `scripts/v5_lean.py` / `scripts/spawn_nav.py` / `scripts/hongguo_v5.py` / `scripts/verify_drama.py`
- 不引入新的高频 hook
- 不把采集产出写到 `videos/` 目录（那是下载的家），只写 `.planning/rankings/`

---

## 新 Session 快速启动提示词

```
我想在红果短剧 App (com.phoenix.read) 上自动采集排行榜 metadata.
详见 HANDOFF_METADATA.md.

先做 P0: 探索排行榜 deeplink 入口和 tab 坐标.

环境已就绪:
- Android 9 MI6 (adb: 4d53df1f)
- Frida 16.5.9 + frida-server 运行
- ADBKeyboard 已是默认 IME
- App 已登录

手机已连接. 不要让我手动操作 App 除非必要.
```

---

## 验证案例（参考）

当前项目已下的第 1 部剧，其元数据（完美 ground truth）：

```json
{
  "series_id": "7622955207885851672",
  "name": "开局一条蛇，无限进化",
  "total": 83,
  "first_vid": "v02ebeg10000d75383vog65nn8lufucg"
}
```

采集工具应能从热播榜/完结榜等抓到这条（如果 App 此刻这部剧还在榜）。
