# Metadata 采集 Bug 报告

**状态**: ✅ **已修复 (2026-04-20 18:35)** — 详见文末"修复记录"

**发现时间**：2026-04-20 18:12
**发现方**：下载验证 session（non-metadata）
**验证数据**：`.planning/rankings/dramas.json`（46 部剧）
**严重性**：**HIGH**（所有 46 部 series_id 字段错，batch 下载无法使用）

---

## 现象

下载 session 用 dramas.json 里《疯美人》的 `series_id=7624374611039226905` 跑 `spawn_nav + v5_lean`：
- spawn_nav 报 "session A 成功 App 已在 ShortSeriesActivity"（Intent 跳转声称成功）
- v5_lean attach 后初始 B0 超时未到
- swipe 后 B0 到达，**`svd.getSeriesId()` 返回 `7624372698860227646`**（与 target 不符）
- v5_lean 的串剧保护拒绝所有事件 → 退出失败

## 证据

| 字段 | dramas.json 存储 | App Hook 实际值 | 来源 |
|------|-----------------|----------------|------|
| series_id | `7624374611039226905` | `7624372698860227646` | `svd.getSeriesId()` |
| ep1.biz_vid | *（未记录）* | `7624374611039226...` | `svd.getVid()` |

注意：**dramas.json 的 series_id 值 = App 里 ep1 的 biz_vid 前缀**。

验证（下载 session 用 App 真 sid 跑 v5_lean -s 1 -e 2）：
- ep1 biz_vid 实测 `7624374611039226xxx`
- ep2 biz_vid 实测 `7624374551744367xxx`
- 两集均落地成功（`videos/疯美人/episode_00{1,2}_*.mp4`）

## 根因推测

Metadata 采集 session 的 hook 代码里，大概率是以下之一：

1. **字段写错**：
   ```js
   // 误写
   send({series_id: String(svd.getVid()), ...});
   // 正确
   send({series_id: String(svd.getSeriesId()), ...});
   ```

2. **JSON key 命名混淆**：在某个转换层把 `vid` / `biz_vid` 存进了 `series_id`。

3. **SaasVideoData 首次 setter 时序**：若 hook 了 `setSeriesId` 但捕获的是 ep1 的 biz_vid 被错认为 series_id（需 source 排查）。

## 修复指引

### 代码层
排查 metadata 采集代码里所有写入 `series_id` 字段的地方，确认来源是：
- ✅ `svd.getSeriesId()` / `svd.getEpisodesId()` — 两者等价，都是真 series_id
- ❌ `svd.getVid()` — 这是**当集 biz_vid**，不同集不同值

建议 hook 里同时 emit 两个值便于对照：
```js
send({
  t: 'catalog',
  series_id: String(svd.getSeriesId() || ''),  // ← 稳定的剧 id
  biz_vid:   String(svd.getVid() || ''),        // ← 当集 biz_vid (存 first_vid)
  name:      String(svd.getSeriesName() || ''),
  total:     Number(svd.getEpisodesCount() || 0),
  // ...
});
```

### 数据层
修复代码后，**必须重采**整份 dramas.json（不能就地修，因为现有 series_id 字段全是 biz_vid，无法反推真 sid 除非再 attach 一次）。

或者离线修正工具（较复杂）：
- 用 batch_lean 的 spawn_nav **用 biz_vid 做 Intent extra `key_first_vid`**（不是 `short_series_id`）看能否进剧
- v5_lean attach 后读 B0 的 `svd.getSeriesId()` 拿真 sid
- 回填 dramas.json

## 验证方案

修复后：
1. 重采**同一部剧**（例如《疯美人》）
2. 确认输出的 `series_id` == `7624372698860227646`（= App hook 真实值）
3. 喂 batch_lean 跑 `-s 1 -e 2` 能成功下载

## 本 session 产出

- `videos/疯美人/episode_001_69e18b08.mp4` (11.6MB)
- `videos/疯美人/episode_002_69cf3842.mp4` (15.0MB)

证明 v5_lean 架构无问题，瓶颈在 metadata 数据质量。

---

## 修复记录 (2026-04-20 18:35)

### 真正根因

**不是** hook 代码逻辑错误，**不是** SaasVideoData 字段写错 —— 本次 metadata session 用的
是 Compose 榜单页的新架构 hook（见 `.planning/rankings/P1_采集器操作指南.md`），数据通路
是 `SeriesRankTabViewModel.c0(List<tc4.e>) → tc4.e._a → y34.c → j30`。

真正问题：**j30 (`com.bytedance.kmp.reading.model.j30`) 的 getter 命名混淆, 初版
反推字段映射时把 series_id 和 first_vid 完全搞反了**：

| 原代码 (错) | 真相 |
|------------|------|
| `series_id = j30.J()` | J() 返回 **ep1 biz_vid** (例: 7624374611039226905) |
| `first_vid = j30.u()` | u() 返回 **真 series_id** (例: 7624372698860227646) |

### 反推过程（保留作未来参考）

1. 从 `videos/疯美人/session_manifest.jsonl` 确认 ground truth:
   - series_id = `7624372698860227646`
   - ep1 biz_vid = `7624374611039226905`
2. 写 `scripts/rank_probe_sid.py`: 挂 c0, 按剧名过滤《疯美人》, dump 全部 40 个 getter +
   所有 120 个非空 field 值，标记包含 ground truth sid 的位置
3. 输出显示 `u()` / `x()` / field A / field p 这四处都返回 `7624372698860227646`
4. 选 `x()` 作 series_id 主来源，`u()` 作 fallback 并检查等价
5. `J()` 原以为是 sid，实际是 ep1 biz_vid，更正为 `first_vid` 来源

### 修复代码位置

- `scripts/rank_collect.py` 的 `JS_HOOK` 字段映射 (2026-04-20 commit)
- 新增 `_sid_u` 辅助字段: Python 侧对比 `x() != u()` 时打印警告, 未来 App
  重命名混淆时能第一时间发现

### 验证结果

修复后重采热播榜 + 漫剧榜（46 条 unique）：

- **字段完整性**: series_id / name / total / first_vid / cover_url 全部 100% 非空
- **series_id 合法性**: 46/46 全部是 19 位纯数字
- **first_vid ≠ series_id**: 0 条冲突（之前 bug 会导致这两个字段数值相同）
- **Ground truth 匹配**: 《疯美人》series_id = `7624372698860227646` ✓ 与本报告开头实测值一致
- **无 `_sid_u` 不一致 warning**: x() 和 u() 在所有 46 条都等价

### 辅助工具

- `scripts/rank_probe_sid.py` — 保留. 未来 App 更新混淆名后, 改 `GROUND_TRUTH` 字典
  （取一部当前已下载剧）再跑一次即可重新找回正确 getter。
- `scripts/rank_vm_methods.py` — 辅助枚举新版本类的方法签名。
