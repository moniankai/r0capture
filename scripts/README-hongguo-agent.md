# 红果短剧无人值守下载 Agent — 使用手册

**目标**: 输入剧名 + series_id → Agent 全自动下载完整剧集 + 对齐验证 + 产出报告, 无需人工干预。

**架构**: 分层设计 (详见 [`docs/superpowers/specs/2026-04-18-hongguo-agent-design.md`](../docs/superpowers/specs/2026-04-18-hongguo-agent-design.md))
- `hongguo_batch.py` — BatchAgent (多剧串行调度 + 设备自愈 + resume)
- `hongguo_agent.py` — Orchestrator (FSM + Watchdog + Recovery + 熔断)
- `hongguo_v5.py` — 可监督的 runner (4 种启动模式 + 事件流 + 原子提交 + ot3.z.B0 强绑定 Hook)

## 批量模式 (阶段 2a)

```bash
# 准备输入 JSON (scripts/dramas.example.json 为模板)
python scripts/hongguo_batch.py --input dramas.json

# Resume (默认会跳过已 DONE 的剧, 无需额外参数)
python scripts/hongguo_batch.py --input dramas.json

# 可选参数
#   --per-drama-timeout 2400     单部剧上限 秒 (默认 1800)
#   --reboot-every 5             每 N 部强制 adb reboot (默认 5, 0 = 关)
#   --halt-on-fatal              某部 FATAL/TIMEOUT 时停止整批 (默认继续)
#   --fresh                      忽略 .batch_state.json 重新跑 (仍跳磁盘已完成)
```

输入 JSON 格式:
```json
[
  {"name": "我真不是大佬啊", "series_id": "7625840320642567192", "total": 88},
  {"name": "凡人仙葫", "series_id": "7617050216549583897", "total": 60}
]
```

输出:
- `videos/<每部剧>/` — 每部剧的 mp4 + manifest + report
- `.batch_state.json` — 进度持久化 (中断后 resume)
- `batch_report.json` — 最终汇总 (ok/skipped/failed/total_reboots/elapsed)

---

## 依赖

```
pip install -r requirements.txt
pip install psutil   # Agent 的 stale 检测必需
```

- Python 3.10+
- `frida==16.5.9` (Android 9 兼容)
- 安卓手机 + root (magisk) + frida-server 在 `/data/local/tmp/`
- ADBKeyboard app 已装到手机（当前只在 legacy 搜索路径用）

---

## 快速使用

### 1. 首次下载一部剧（需要提前拿到 series_id）

```bash
# 步骤 a: 用 legacy v5 跑第一次 (搜索 API 会被 hook 自动识别剧目)
python scripts/hongguo_v5.py -n "凡人仙葫第一季" --total 60

# 或步骤 b: 已知 series_id 直接跑
python scripts/hongguo_v5.py -n "凡人仙葫第一季" --series-id 7617050216549583897 --total 60
```

### 2. 无人值守 Agent 模式（推荐, 断点续传 + 自愈 + 验证）

```bash
python scripts/hongguo_agent.py \
    -n "凡人仙葫第一季" \
    --series-id 7617050216549583897 \
    --total 60
```

**可选参数**：

```
--max-retry-per-ep N       # L1 单集重试上限 (默认 3)
--max-consec-fail N        # L2 连续 infra 类失败触发 recovery (默认 4)
--max-restarts N           # L3 最多 recovery 次数 (默认 5)
--max-total-seconds N      # 总时间上限 (默认 3600)
--max-stall-seconds N      # 进度停滞超时 (默认 180)
--out PATH                 # 视频输出目录 (默认 ./videos)
```

---

## Agent 行为

### FSM 状态

```
INIT → RESOLVING (TODO) → NAVIGATING → DOWNLOADING
             ↕                  ↕
         VERIFYING ←──── (DONE/ABORTED)
         + RECOVERING (故障转入)
```

### 自愈能力

| 场景 | Agent 行为 |
|---|---|
| App ANR | 检测 v5 `rc=2` → kill subprocess → force-stop → 重启 frida-server → tap 进剧 → 续跑 |
| frida session leak | v5 emit `cleanup_timeout` → 下次 recover 强制 frida-restart |
| App 不在 ShortSeries* | v5 precond_fail `rc=5` → Agent 转 NAVIGATING → 三策略 tap 进剧 |
| 单集持续失败 | L1 超预算后标 `abandoned_eps`, 放过该集 |
| 基础设施类累积失败 | L2 触发, 走 Recovery |
| Recovery 累次超限 | L3 触发, 输出 partial 报告 |
| 跑得太久 | 时间/停滞上限触发 ABORTED |

### 三策略 Navigate

1. UI dump 找 "全屏观看" → tap
2. UI dump 找 "剧场" tab + "继续播放" → tap
3. 硬编码坐标 (570, 1281) fallback

### 对齐验证 (VERIFYING)

- 采样策略：均匀 5 点 + 首末 + recovery 边界 (±1 集)
- 启动 `probe-bind` 子进程（只发 RPC 抓 BIND, 不下载）
- 对比 probe 的 expected vid 与 manifest 的 actual vid
- `confidence`:
  - `high` — 全采样点 probe 到 + 无 mismatch
  - `failed` — 有 mismatch（触发回流重下问题区间, max_reflow=1）
  - `verification_failed` — 有采样点 bind_timeout（无法证明对齐, 但已下载数据保留）

---

## 输出文件

```
videos/<剧名>/
├── episode_001_<kid8>.mp4           # 解密后可直接播放
├── episode_002_<kid8>.mp4
├── ...
├── session_manifest.jsonl           # 每集 (ep/vid/kid/bytes/series_id) 提交记录
├── report.json                      # 本次 Agent 运行报告
└── .tmp/                            # 临时解密文件 (启动时清理)
```

### `report.json` 字段

```json
{
  "drama": "剧名",
  "series_id": "...",
  "total": 60,
  "downloaded": [1, 2, ..., 60],     // 已 committed 的集
  "missing": [],                      // 最终仍缺失的集
  "state": "DONE",                   // DONE / ABORTED
  "restarts": 1,                     // Recovery 次数
  "recovery_boundaries": [59],       // 每次 recovery 时的 last_ok_ep
  "elapsed_seconds": 186.67,
  "config_source": "cli",            // 熔断阈值来源 (default / cli)
  "verification": {
    "confidence": "high | failed | verification_failed | skipped",
    "sample_eps": [1, 16, 30, 45, 58, 60],
    ...
  }
}
```

---

## v5 直接使用

当你需要精细控制时，可以单独跑 v5：

### 模式
```bash
# legacy: 原有的全流程 (spawn + 搜索/--series-id + 下载)
python scripts/hongguo_v5.py -n "剧名" --series-id X --total T

# attach-resume: attach 到 App 已在 ShortSeries* 的状态, 按 manifest 续下
python scripts/hongguo_v5.py --mode attach-resume -n "剧名" --series-id X --total T --attach -s auto

# probe-bind: 只抓 BIND 不下载, 用于验证对齐
python scripts/hongguo_v5.py --mode probe-bind -n "剧名" --series-id X --eps "1,15,30,45,60" --attach
```

### 退出码

- 0 全 ok
- 1 partial (有 fail 但未致命)
- 2 ANR suspected (Agent 转 RECOVERING)
- 3 fatal (配置/manifest/context 错)
- 4 SIGINT/SIGTERM
- 5 precond_fail (attach-resume 前置不满足, Agent 转 NAVIGATING)

### 事件流 (stdout JSON)

Agent 依赖 v5 stdout 的机读事件 (一行一 JSON)。关键事件：

```json
{"type":"resolved","series_id":"...","total":60,"name":"..."}
{"type":"ep_start","ep":48,"seq":2}
{"type":"ep_ok","ep":48,"vid":"...","kid":"...","bytes":19300000}
{"type":"ep_fail","ep":50,"reason":"bind_timeout|..."}
{"type":"cleanup_timeout",...}       // v5 侧 frida unload 卡了
{"type":"cross_drama",...}           // 串剧检测
{"type":"phase_alive","phase":"..."} // 10s 心跳
{"type":"done","ok":13,"fail":0}
```

---

## 故障排查

### Agent `init navigate failed`
- App 启动后 40s 内没进 MainFragmentActivity
- 常见原因：MIUI 开屏广告卡住 / 权限弹窗 / USB 设置对话框
- 排查：`adb shell dumpsys activity activities | grep ResumedActivity`
- 手动：`adb shell input keyevent KEYCODE_BACK` 关对话框后重试

### `frida.TransportError: timeout`
- frida-server 可能挂了或陷入坏状态
- Agent 会自动尝试重启; 手动: `adb shell "su -c 'killall -9 frida-server && /data/local/tmp/frida-server &'"`

### `confidence=verification_failed + reason=incomplete_probe`
- 某些集 probe-bind 时 `bind_timeout`（比如末集 ViewPager no-op 问题）
- 已下载数据**正确且完整**，但 Agent 无法用 probe 独立证明对齐
- 可选：手工用 `--mode probe-bind` 单独跑确认, 或跑下一次 Agent（probe 成功率不稳定）

### 单元测试
```bash
pytest tests/test_hongguo_v5_contracts.py tests/test_hongguo_agent_fixes.py -v
# 37 tests covering manifest atomicity / orphan cleanup / CircuitBreaker layering / ...
```
