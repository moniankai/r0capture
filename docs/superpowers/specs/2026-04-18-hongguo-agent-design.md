# 红果短剧无人值守下载 Agent 设计方案 (v4)

**日期**: 2026-04-18
**作者**: Claude (协同调试) + Codex (3 轮独立评审)
**状态**: v4 — Codex 三轮评审后补全契约层细节
**目标**: 输入剧名 → 全自动下载完整剧集 → 输出对齐验证报告, 无人值守

---

## 0. 修订记录

- **v1 (2026-04-18 初稿)**: Orchestrator wrapper + FSM + 熔断 + 均匀采样验证
- **v2 (2026-04-18)**: 吸收 Codex 一轮评审, 关闭 B2/B3/B4/B5, B1/B6 部分关闭
- **v3 (2026-04-18)**: 吸收 Codex 二轮评审, B1/B6 完全关闭 + VERIFYING 生命周期 + stale 清理 + probe-bind ANR
- **v4 (2026-04-18, 本版)**: 吸收 Codex 三轮评审, 5 个契约层细节封口
  - §3.1.1 前置检查分层 (启动前置 + 运行期健康拆分) + 新增 manifest/context 一致性检查
  - §3.5 新增 final-dir orphan 清理 (rename 成功但 manifest 未落盘的崩溃场景)
  - §3.5 全文统一 `session_manifest.jsonl` (消除二义性)
  - §4.4 stale 清理: owner token 防误杀并发 + frida 释放依赖 `am force-stop` + 新增真实判据函数 `is_frida_session_released`
  - §5.3 VERIFYING 回流计数器 (MAX_REFLOW=1, 防无限 VERIFYING↔DOWNLOADING 循环)
  - §5.2 `run_v5_probe()` 返回 None 守卫 + `confidence=verification_failed` + reason 字段

---

## 1. 背景

### 1.1 当前 v5 状态

`scripts/hongguo_v5.py` 已实现:
- 搜索 API 拦截 + series_id + total 自动发现
- `pos = target_ep - 1` 集数对齐策略 (probe_ep48.py 验证)
- BIND 队列降级复用 (失败时用 preload bind)
- `rpc_switch` Python 硬超时 (threading + join timeout=15s)
- `used_kids` / `seen_ep_vids` 去重
- manifest.jsonl 记录每集 vid/kid/ts
- 《凡人仙葫第一季》60/60 集实证对齐

### 1.2 无人值守标准下的缺口 (6 类)

| 场景 | v5 当前 | 人工成本 |
|---|---|---|
| App ANR | frida session 死,v5 退出 | force-stop + 重进 Land + 重启 v5 --start N |
| 搜索 catalog 未匹配 | `进入剧失败` 退出 | 手动拿 series_id + `--series-id` |
| frida TransportError | create_script/load 报错退出 | killall frida-server + 重启 |
| spawn 模式 RPC 在 Splash hang | rpc_switch 超时,wait_bind 全失败 | 手动进 Land 后改 `--attach` |
| 断点续传 | 必须用户指定 `-s N` | 用户记忆进度 |
| 对齐/串剧验证 | 无 | 肉眼抽检 |

**结论**: v5 业务逻辑基本成熟,但契约层(启动模式/事件流/可恢复 journal)严重不足,不支持被自动化编排。

---

## 2. 架构决策

### 2.1 为什么不做 Layer 分层大重构

仍然维持 v1 的判断:
1. v5 的 BIND/CAP/RPC 时序耦合强,硬拆层回归风险高
2. 业务逻辑已稳定,分层不增加正确性
3. 测试成本翻倍

### 2.2 v5 不是"50 行轻改",而是"~200 行契约改造"

Codex 指出 v1 对 v5 改动范围的估计不切实际。实际需要 v5 升级为**可监督的 runner**,Orchestrator 只做状态机和恢复决策,**不负责猜 v5 走到哪一步**。

### 2.3 分工

```
hongguo_agent.py (新建, ~600 行)
  ├── FSM 状态机 (phase-aware)
  ├── Watchdog 线程 (分阶段健康判据)
  ├── Recovery Strategies (ANR / frida / 重进剧)
  ├── Circuit Breakers (3 层 + 时间 + 进度停滞)
  ├── 进程树管理 (Windows CREATE_NEW_PROCESS_GROUP + taskkill /T)
  ├── Journal reader (manifest 为 committed source of truth)
  └── 启动 v5 subprocess (PYTHONUNBUFFERED=1, -u, 显式模式)
           ↓
       hongguo_v5.py (~200 行契约改造)  ← 可监督的 runner
         ├── 三种启动模式 (spawn-resolve / attach-resume / probe-bind)
         ├── 非缓冲事件流 (每条 send flush=True)
         ├── 阶段事件 + 心跳 (每 10s phase_alive)
         ├── 原子 journal (manifest append 为唯一 commit 点)
         └── 明确退出原因 (exit code + last event)
```

---

## 3. v5 侧改造 (~200 行)

### 3.1 三种启动模式 (修 B1)

**Codex 指出的 B1**: Recovery 恢复进了 Land 页,但 v5 默认 `force-stop + spawn` 会自毁。必须区分模式。

```
python hongguo_v5.py --mode spawn-resolve   -n "剧名"  [--series-id X] [--total T]
python hongguo_v5.py --mode attach-resume   --series-id X --total T [--start N --end M]
python hongguo_v5.py --mode probe-bind      --series-id X --eps "1,15,30,45,60"
```

- **spawn-resolve**: 冷启动 + 搜索拿 series_id + total (只在 RESOLVING 阶段用)
- **attach-resume**: attach 到当前运行的 App + 假设已在 ShortSeries* + 从 manifest 续 (DOWNLOADING 主路径)
- **probe-bind**: attach + 对指定 eps 各 RPC 一次, 只拿 BIND, **不下载不写 manifest** (VERIFYING 专用)

Agent 恢复路径里只用 `attach-resume`, 不会触发 force-stop 自毁。

#### 3.1.1 attach-resume 失败契约 (封 B1)

**分为两阶段**: (A) 启动前置检查 → (B) 运行期首次健康验证。两者性质不同,分开处理。

**阶段 A: 启动前置检查** (必须 3s 内完成,失败即退出不进入主循环):

| 前置条件 | 检查方式 | 失败事件 | 退出码 |
|---|---|---|---|
| App 进程存活 | `adb shell pidof com.phoenix.read` | `{"type":"precond_fail","reason":"no_app"}` | 5 |
| 前台 Activity 是 ShortSeries* | `adb shell dumpsys activity \| grep mResumedActivity` | `{"type":"precond_fail","reason":"wrong_foreground","actual":"..."}` | 5 |
| manifest 可读 | `videos/<剧名>/session_manifest.jsonl` 可打开 + 末行完整 JSON | `{"type":"precond_fail","reason":"manifest_corrupt"}` | 3 |
| 续跑上下文一致 | manifest 首行 `series_id/name` 与 CLI `--series-id -n` 一致 | `{"type":"precond_fail","reason":"context_mismatch","expected":"...","actual":"..."}` | 3 |
| frida attach 成功 | `device.attach(pid)` 无抛错 | `{"type":"precond_fail","reason":"frida_attach_err","detail":"..."}` | 2 |
| HOOK_JS 加载成功 | `script.load()` 无抛错 | `{"type":"precond_fail","reason":"script_load_err"}` | 2 |

**阶段 B: 运行期首次健康验证** (script.load() 后 8s 内):

| 信号 | 检查方式 | 失败事件 | 退出码 | 性质 |
|---|---|---|---|---|
| 首次 BIND 到达 | watchdog 收 `bind` 消息 | `{"type":"first_bind_timeout"}` | 2 | 运行期健康(非前置),疑似 App 状态异常 |

**退出码对照**:
- 0: 全 ok
- 2: 可恢复异常 (frida/transport/state 疑似 ANR) — Agent 转 RECOVERING
- 3: 不可恢复配置错 (context_mismatch / manifest_corrupt / fatal) — Agent 转 ABORTED
- 5: 前置不满足 (App 状态问题, 与 v5 无关) — Agent 转 NAVIGATING

**Agent 处理规则**:
- 退出码 5 → NAVIGATING (重新 tap 进 ShortSeries*)
- 退出码 2 + `frida_attach_err/script_load_err/first_bind_timeout` → RECOVERING
- 退出码 3 + `context_mismatch` → ABORTED (配置错误,人工介入)
- 退出码 3 + `manifest_corrupt` → ABORTED (manifest 损坏, 需人工检查)
- 不可识别 reason → 通用 RECOVERING + 计入 consec_fail

### 3.2 非缓冲事件流 (修 B3)

```python
# Python 侧
sys.stdout.reconfigure(line_buffering=True)  # 或用 -u 启动
# 每次事件都 flush
print(json.dumps({"type": "ep_ok", "ep": 48, "vid": "...", "kid": "...", "ts": ...}), flush=True)
```

Agent 侧:
```python
subprocess.Popen(
    [sys.executable, "-u", "scripts/hongguo_v5.py", ...],
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    env={**os.environ, "PYTHONUNBUFFERED": "1"},
    bufsize=1,  # 行缓冲
    text=True,
    # Windows 进程组 (见 §4.3)
    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
)
```

### 3.3 事件协议 (机读,一行一 JSON)

```
{"type":"phase","phase":"resolving","ts":...}
{"type":"phase_alive","phase":"downloading","ts":...}          # 心跳, 每 10s
{"type":"resolved","series_id":"...","total":60,"name":"..."}
{"type":"ep_start","ep":48,"seq":2,"ts":...}
{"type":"ep_ok","ep":48,"vid":"...","kid":"...","bytes":...,"ts":...}
{"type":"ep_fail","ep":50,"reason":"bind_timeout|cap_timeout|decrypt_err|rpc_timeout|cross_drama","ts":...}
{"type":"anr_suspected","detail":"...","ts":...}               # v5 自己检测到疑似 ANR
{"type":"done","ok":13,"fail":0,"last_ep":60,"ts":...}
```

### 3.4 退出码

- 0: 全 ok
- 1: partial (有 fail 但未致命)
- 2: ANR suspected (session/transport 错误 / 心跳丢失 / attach 失败)
- 3: fatal (配置错 / 无法启动 frida / series_id 无法解析)
- 4: user-abort (SIGINT/SIGTERM)
- 5: precond_fail (`attach-resume` 前置条件不满足,可恢复, 见 §3.1.1)

### 3.5 Manifest 作为 committed source of truth (封 B6)

**单集下载的严格提交顺序** (v5 必须按此顺序执行, Agent 的 committed 进度**只从 manifest 读**):

```
1. 下载 CDN → 写 videos/<剧名>/.tmp/ep_NN.part      (临时文件, 无解密)
2. AES-CTR 解密 + fix_metadata                      (内存 bytearray 操作)
3. 写 videos/<剧名>/.tmp/ep_NN.decrypted            (解密后文件)
4. fsync 解密文件 fd
5. 原子 rename → videos/<剧名>/episode_NNN_<kid8>.mp4
6. manifest append 一行 JSON (含 ep/vid/kid/bytes/ts/series_id)
7. f.write() + f.flush() + os.fsync(f.fileno())     (保证 manifest 落盘)
8. 【仅此时】stdout emit ep_ok 事件
```

**Manifest 文件名**: `videos/<剧名>/session_manifest.jsonl` (全文统一,与现 v5 一致)。

**关键规则**:
- **只有 manifest append+fsync 成功 = 该集 committed**
- `ep_ok` stdout 事件是 manifest 写完后的观测副本,不能反过来驱动 Agent 状态
- **Agent 的 `last_committed_ep` 只从 manifest 读**,不从 stdout 读
- `stdout ep_ok` 丢失不影响正确性

**三类崩溃后果 + 处理规则**:

| 崩溃时机 | 后果 | Agent 处理 |
|---|---|---|
| .tmp/ 写入时 | `.tmp/ep_NN.part` 或 `.tmp/ep_NN.decrypted` 孤儿 | 启动时扫 `.tmp/` 直接清空 (未 rename 就未 commit) |
| rename 成功, manifest 追加前 | final-dir 出现 `episode_NN_<kid>.mp4` 但 manifest 无记录 | **扫 final-dir orphan**: 枚举 mp4 文件与 manifest ep 对照, 不在 manifest 的 mp4 **删除**,因为 kid 未知无法供后续对齐验证 |
| manifest 末行半写 | manifest 最后一行 JSON 不完整 | 读取时跳过不完整的最后一行 |

**final-dir orphan 清理代码**:

```python
def cleanup_final_dir_orphans(drama_dir: Path, committed_eps: dict[int, str]):
    """committed_eps: {ep -> kid_prefix8} 来自 manifest 扫描.
    删除 final-dir 中不在 committed_eps 的 mp4 (rename 成功但 manifest 未落盘的牺牲品).
    """
    for f in drama_dir.glob("episode_*.mp4"):
        m = re.match(r"episode_(\d+)_([0-9a-f]{8})\.mp4", f.name)
        if not m: continue
        ep = int(m.group(1))
        kid8 = m.group(2)
        if committed_eps.get(ep) != kid8:
            logger.warning(f"orphan mp4 removed: {f.name} (not in manifest)")
            f.unlink()
```

**`--start auto` 流程**:

```
1. 读 manifest 的 (ep, kid) 列表 (authoritative)
2. 扫 final-dir cleanup_final_dir_orphans (上述函数)
3. 校验 manifest 列出的每个 ep 对应 mp4 存在 + 大小 > 1MB, 不存在或损坏的 ep 视为 missing
4. 返回最小缺失集作为 --start
```

**fsync 性能预算**:
- 每集 fsync 2 次 (mp4 + manifest), 约 10-30ms/次, 单集 overhead ~40-60ms
- 60 集总 overhead: ~3-4s (vs. 整剧下载 15-30 分钟)
- **可接受**,不降级

### 3.6 启动时 `--start auto`

见 §3.5 流程, 核心: **只读 manifest 作为 authoritative, final-dir orphan 扫除, 不存在或损坏的 mp4 回滚为 missing**。

---

## 4. Orchestrator Agent (新建 hongguo_agent.py, ~600 行)

### 4.1 状态机 (FSM)

**8 个状态 + 转移路径**:

```
INIT
  └─ (重启 frida + force-stop + 启 Splash) ──→ RESOLVING

RESOLVING (spawn-resolve subprocess)
  ├─ 拿到 series_id + total ─────────────→ NAVIGATING
  └─ 60s 超时 或 fatal ─────────────────→ ABORTED

NAVIGATING (Agent 接管, 无 subprocess)
  ├─ tap 全屏观看 + 确认进入 ShortSeries* → DOWNLOADING
  └─ 2 次失败 ─────────────────────────→ ABORTED

DOWNLOADING (attach-resume subprocess)
  ├─ 所有集下完 ────────────────────────→ VERIFYING
  ├─ subprocess fail / watchdog 触发 ──→ RECOVERING
  └─ 熔断 L3 / 时间 / 进度停滞 ─────────→ ABORTED

RECOVERING (Agent 接管)
  ├─ 恢复成功 ─────────────────────────→ DOWNLOADING  (回主路径, 非 NAVIGATING)
  │                                       (因为 attach-resume 自带 `--start auto`)
  └─ 恢复失败 max_restarts 次 ─────────→ ABORTED

VERIFYING (probe-bind subprocess, §5.4)
  ├─ 全部对齐 ─────────────────────────→ DONE
  ├─ 部分错位 (可重下) ────────────────→ DOWNLOADING (重下问题区间)
  └─ 重下后仍不齐 / probe ANR 2 次 ────→ ABORTED (partial 报告)

DONE (生成 report.json, 退出)
ABORTED (生成 partial 报告, 退出)
```

**关键**:
- RECOVERING → **DOWNLOADING** (不是回 NAVIGATING, 因为 RECOVERING 内部已完成 NAVIGATING 的全部步骤, 只差一个 subprocess)
- NAVIGATING 只在 "冷启后首次进剧" 被独立调用; 后续 recovery 不重复触发它, 而是 RECOVERING 内嵌完整导航 + attach
- VERIFYING → DOWNLOADING 回流 (重下问题区间) 后会 **再次** 进入 VERIFYING,这条边允许一次回流

### 4.2 Watchdog 分阶段健康判据 (修 B4)

Codex 指出 v1 的 "前台不是 ShortSeries* 就异常" 在多个阶段错误。修正:

| 阶段 | 预期前台 | 预期心跳 | 预期 v5 subprocess | 检测到异常的动作 |
|---|---|---|---|---|
| INIT | (任意) | 无 | 无 | 继续 |
| RESOLVING | 任意 App 界面 | `phase_alive` 10s | spawn-resolve | 60s 无 `resolved` 事件 → timeout fail |
| NAVIGATING | Main/ShortSeries* 过渡中 | (Agent 自己 tap) | 无 | tap 失败 / 30s 未进 ShortSeries* → 重试一次 + abort |
| DOWNLOADING | `ShortSeries*` | `phase_alive` 10s 或 `ep_*` 事件 | attach-resume 存活 | 见 §4.3 |
| RECOVERING | (任意) | (Agent 接管) | 无 | 见 §4.4 |
| VERIFYING | `ShortSeries*` | `phase_alive` 10s | probe-bind | 45s 无事件 → kill + fail |

### 4.3 DOWNLOADING 阶段的健康信号组合

三个独立信号,任一异常即疑似 ANR:
1. **App 存活**: `adb shell pidof com.phoenix.read` 返回空 → App 挂
2. **前台正确**: `adb shell dumpsys activity | grep mResumedActivity` 非 `ShortSeries*` 持续 15s+ → 被其他 Activity 抢占
3. **v5 进展**: 45s 未收到 `ep_*` 或 `phase_alive` 事件 → 挂死

**进度停滞单独检测** (升级到 P0, 修 Codex 建议 2):
- 累计 3 分钟内 `last_ok_ep` 未增长 → 进度停滞熔断触发 → 转 RECOVERING

### 4.4 Recovery 路径 (修 B1 + B2)

```python
def recover(reason: str):
    # 1. 安全杀 v5 subprocess (Windows 进程树 + PID 有效性保护)
    safe_kill_subprocess(v5_proc)  # 见下方进程杀法

    # 2. 清理 stale Python / frida session (修 Codex 建议 4)
    cleanup_stale_resources()
    # 清理不掉的硬错误 → raise, Agent 走 ABORTED, 不带脏状态续跑

    # 3. 停 App
    run_adb("shell", "am", "kill", "com.phoenix.read")
    run_adb("shell", "am", "force-stop", "com.phoenix.read")
    time.sleep(2)

    # 4. frida-server 健康检查 + 重启
    if not check_frida_server():
        run_adb("shell", "su -c 'killall -9 frida-server'")
        time.sleep(1)
        run_adb("shell", "su -c '/data/local/tmp/frida-server &'")
        wait_until(lambda: check_frida_server(), 10)

    # 5. 启动 App 到主页
    run_adb("shell", "am start -n com.phoenix.read/.pages.splash.SplashActivity")
    wait_until_fg("MainFragmentActivity", 15)

    # 6. 动态定位"全屏观看"按钮
    bounds = find_text_bounds_with_retry("全屏观看", retries=3)
    if bounds is None:
        tap(570, 1281)  # fallback 硬编码
    else:
        tap(*bounds_center(bounds))

    # 7. 确认进入 ShortSeries*
    wait_until_fg_matches("ShortSeries*", 10)

    # 8. 新 v5 subprocess: attach-resume --start auto
    start_v5(mode="attach-resume", start="auto")
    restart_count += 1
```

**Windows 进程树 + PID 保护** (防误杀自己):

```python
def safe_kill_subprocess(proc):
    # 句柄保护: 已退出直接返回
    if proc is None or proc.poll() is not None:
        return
    # PID 有效性: 必须是当前还在 ps 里的子进程 PID
    if not is_pid_alive(proc.pid):
        return
    # 杀自己保护: 绝不能是 Agent 自己的 PID
    if proc.pid == os.getpid():
        raise RuntimeError("refused to kill self")

    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            capture_output=True, timeout=5
        )
        # PID 复用保护: 等原 proc 句柄退出后再返回, 不依赖后续 PID
        try: proc.wait(timeout=3)
        except subprocess.TimeoutExpired: pass
    else:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        time.sleep(3)
        try: os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except ProcessLookupError: pass
```

**stale 资源清理** (§4.4, 修 Codex 建议 4):

设计思路: **不尝试用 frida API force-detach(不存在),而是用"死路一条"的强制方案**: 杀 App 进程 = 所有 frida session 一同死亡,这是 Android/frida 唯一可靠的 release 机制。

```python
def cleanup_stale_resources():
    """Recovery 前清除可能的脏状态.
    清理策略:
      1. 杀残留 hongguo_v5.py python 进程 (通过 owner token 识别, 不误杀并发)
      2. 通过 am force-stop + 观测 frida 侧判据, 间接保证 session 释放
      3. 清 .tmp/ 孤儿文件
    任一步清不掉 → raise stale_cleanup_failed (上层 ABORTED, 不带脏状态续跑).
    """
    # 1. 杀残留 hongguo_v5.py python 进程 (用 owner token 识别)
    #    启动每个 v5 subprocess 时通过环境变量 HONGGUO_AGENT_TOKEN 注入唯一 token,
    #    find_stale_v5_pythons 只匹配 cmdline 含 hongguo_v5.py 且 env token 不匹配当前 Agent 的进程
    my_token = os.environ["HONGGUO_AGENT_TOKEN"]
    stale_pids = find_stale_v5_pythons(exclude_token=my_token)
    for pid in stale_pids:
        if pid == os.getpid():
            continue  # 绝不杀 Agent 自己
        try: kill_pid_tree(pid)
        except ProcessLookupError: pass
    # 再查一次
    remaining = find_stale_v5_pythons(exclude_token=my_token)
    if remaining:
        raise RuntimeError(f"stale_v5_cleanup_failed: still alive {remaining}")

    # 2. 不枚举 frida session (无可靠 API), 改为等 am force-stop 完成后通过
    #    adb pidof com.phoenix.read 返回空 + 重试一次 attach 看是否 "session detached" 错误
    #    具体: am force-stop 后 1s 内 pidof 若仍有 pid → 重试 force-stop 最多 3 次
    #    3 次仍不死 → raise app_force_stop_failed
    for attempt in range(3):
        run_adb("shell", "am", "force-stop", "com.phoenix.read")
        time.sleep(1)
        if not adb_pidof("com.phoenix.read"):
            break
    else:
        raise RuntimeError("app_force_stop_failed")

    # 3. 清 .tmp/ 孤儿文件
    tmp_dir = Path(f"videos/{drama_name}/.tmp")
    if tmp_dir.exists():
        for f in tmp_dir.glob("*"):
            try: f.unlink()
            except OSError as e:
                raise RuntimeError(f"tmp_cleanup_failed: {f} {e}")
```

**frida session 释放的真实判据** (封 Codex 二轮问 4):

```python
def is_frida_session_released(expected_app_pid: int) -> bool:
    """判断 frida 是否不再 attach 在指定 App pid 上.
    方案: 检查 App 进程的 /proc/<pid>/maps 里是否含 frida-agent 内存段.
    前提: adb shell root 或可 cat 目标 maps (对 com.phoenix.read 在 Magisk 下可行).
    """
    try:
        out = run_adb_shell(
            f"su -c 'cat /proc/{expected_app_pid}/maps' 2>/dev/null | grep -c frida",
            timeout=3
        )
        return int(out.strip() or "0") == 0
    except Exception:
        # 取不到 maps → 保守假设未释放, 依赖上层重试
        return False
```

VERIFYING 生命周期里 "确认 frida 释放" 用这个判据,而不是伪接口。

**Windows 进程树杀法**:
```python
def kill_subprocess_tree(proc):
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                       capture_output=True)
    else:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        time.sleep(3)
        try: os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except ProcessLookupError: pass
```

### 4.5 UI dump fallback 链 (修 Codex 建议 5)

```python
def find_text_bounds_with_retry(text: str, retries: int = 3) -> tuple | None:
    """uiautomator dump 在 Android 9 经常失败 (could not get idle state).
    三次重试 + 兜底策略."""
    for i in range(retries):
        # 1. 标准 dump
        try:
            run_adb("shell", "uiautomator dump /sdcard/ui.xml", timeout=5)
            xml = run_adb("shell", "cat /sdcard/ui.xml", timeout=3)
            b = parse_text_bounds(xml, text)
            if b: return b
        except TimeoutError: pass

        # 2. --compressed 模式 (有时能绕过 idle 检查)
        try:
            run_adb("shell", "uiautomator dump --compressed /sdcard/ui.xml", timeout=5)
            xml = run_adb("shell", "cat /sdcard/ui.xml", timeout=3)
            b = parse_text_bounds(xml, text)
            if b: return b
        except TimeoutError: pass

        # 3. 稍等让 App idle
        time.sleep(3)

    return None  # 走 fallback: 硬编码坐标
```

### 4.6 熔断机制 (配置化, 修 Codex 建议 6)

```python
@dataclass
class CircuitBreaker:
    max_retry_per_ep: int = 3                      # L1
    max_consec_fail_before_restart: int = 4        # L2
    max_restarts: int = 5                          # L3
    max_total_seconds: int = None                  # 时间上限 (see below)
    max_stall_seconds: int = 180                   # 进度停滞 (3 分钟无 ok_ep 增长)
    # 首版带日志回传, 不硬编码 SLA:
    config_source: str = "default"                 # "default" | "cli" | "env"
```

默认值:
```python
# 启动时计算, 不硬编码:
#   Windows/Android 9 单集平均 ~30s (下载 5s + RPC/wait 5s + 解密 3s + sleep/buffer)
#   允许 ~30% ANR 重启 overhead
max_total_seconds = total_eps * 45 + 600
```

所有阈值都可通过 `--max-retry-per-ep` / `--max-restarts` / `--max-total-seconds` / `--max-stall-seconds` 从 CLI 覆盖,并记录到 `report.json.config_source`。

### 4.7 Series_id 解析策略 (修 Codex 建议 5)

**主策略** (必经其一):

1. **搜索 API 拦截** (主路径):
   - deeplink `dragon8662://search` + ADBKeyboard 输入剧名
   - 拦 `/search/tab/v` 响应 → JSON 解析 → 按 `book_name + total_eps` 匹配
   - 成功判据: catalog 中找到 `name == 剧名 && (total>0)` 的条目
   - 失败: 30s 内未匹配 → 回退到策略 2

2. **manifest 缓存** (续跑路径):
   - 若 `videos/<剧名>/session_manifest.jsonl` 存在且首行 `series_id` 字段有效
   - 直接使用,跳过搜索
   - 失败: 文件不存在或格式错误 → 回退到策略 1

**实验性兜底** (不作默认):

3. **BIND 被动锁定** (实验性, 仅在 `--enable-bind-passive` flag 显式开启时启用)
   - 成功判据: spawn 后 15s 内收到 `setSeriesName="剧名"` 事件, 且同一 SaasVideoData 实例 `total_eps > 0`
   - 超时判据: 15s 内未命中 → 标 `bind_passive_failed` 并退出码 3 (fatal)
   - 风险: App 推荐算法不同步, 可能匹配到同名但不同的剧; 目前无验证数据支持默认启用
   - 使用场景: 仅当策略 1+2 均失败, 且用户明确知道剧名唯一时降级使用

---

## 5. 对齐验证 (改进版, 修 B5 + Codex 建议 3 4)

### 5.1 采样策略

Codex 指出 "错位可能分段性,尤其在 recovery 边界附近"。修正:

```python
def pick_verification_eps(total: int,
                          uniform_n: int = 5,
                          recovery_boundaries: list[int] = []) -> list[int]:
    """
    组合策略:
      A. 均匀分布 uniform_n 个点
      B. 首集 + 末集 (边界强制)
      C. 每次 recovery 的"前一集 + 后一集" (错位最可能发生的位置)

    去重后返回升序列表.
    """
    # A: 均匀
    if total <= uniform_n:
        uniform = list(range(1, total + 1))
    else:
        step = (total - 1) / (uniform_n - 1)
        uniform = sorted({round(1 + i * step) for i in range(uniform_n)})

    # B: 首末 (大概率已在 A 里)
    boundary = {1, total}

    # C: recovery 边界
    recovery = set()
    for r in recovery_boundaries:
        if r > 1: recovery.add(r - 1)
        if r < total: recovery.add(r + 1)

    return sorted(uniform | boundary | recovery)
```

例: total=60, recovery 发生在 ep=25 和 ep=43 时:
`pick([60], 5, [25,43])` → [1, 15, 24, 26, 30, 42, 44, 45, 60]

Agent 运行中记录每次 recovery 时的 `last_ok_ep`, 传给验证函数。

### 5.2 验证对象: vid 而非 kid (修 Codex 建议 3)

`vid` 是业务级直接标识(SaasVideoData.getVid()), probe 模式取到就能直接对比,不需要走完整解码链路。速度快 3-5 倍。

```python
def verify_alignment(series_id: str, sample_eps: list[int]) -> VerifyReport:
    # 启动 v5 --mode probe-bind --eps "1,15,25,..."
    expected_vids = run_v5_probe(series_id, sample_eps)   # {ep: vid} 或 None (probe 失败)

    # None 守卫: probe 自身 ANR 或超时, 视为 verification_failed 但不阻塞已下载数据
    if expected_vids is None:
        return VerifyReport(
            sample_eps=sample_eps,
            misaligned=[],
            confidence="verification_failed",
            reason="probe_anr_or_timeout",
        )

    # 读 session_manifest.jsonl
    actual_vids = read_manifest_vids(series_id)            # {ep: vid}

    misaligned = []
    for ep in sample_eps:
        e = expected_vids.get(ep)
        a = actual_vids.get(ep)
        if e and a and e != a:
            misaligned.append({"ep": ep, "expected_vid": e, "actual_vid": a})

    return VerifyReport(
        sample_eps=sample_eps,
        misaligned=misaligned,
        confidence="high" if not misaligned else "failed",
    )
```

### 5.3 失败自愈 (带回流次数上限, 封 Codex 三轮问 4)

**回流计数器防无限循环**: VERIFYING ↔ DOWNLOADING 至多回流 1 次。

```python
@dataclass
class VerifyState:
    reflow_count: int = 0
    MAX_REFLOW: int = 1       # 至多一次回流

def handle_misaligned(report, verify_state: VerifyState):
    if not report.misaligned:
        return "DONE"

    if verify_state.reflow_count >= verify_state.MAX_REFLOW:
        # 已回流过一次, 仍不齐 → 不再尝试, ABORTED + partial 报告
        return "ABORTED_alignment_unrecoverable"

    # 边界裁剪 (ep-1 / ep+1 可能越界)
    for mis in report.misaligned:
        lo = max(1, mis['ep'] - 1)
        hi = min(total_eps, mis['ep'] + 1)
        # 与已提交文件冲突策略: 覆盖 (目标是修正错位, 当前 mp4 被判定为错)
        redownload_range(lo, hi, overwrite=True)

    verify_state.reflow_count += 1
    return "DOWNLOADING"     # 回流重下, 回到 DOWNLOADING 状态

# FSM 里:
if fsm_state == "VERIFYING":
    next_state = handle_misaligned(report, verify_state)
    # 最多 2 次 VERIFYING (原始 + 回流 1 次)
```

### 5.4 probe-bind ANR 策略 (补 Codex 建议 6)

probe-bind subprocess 自身也可能触发 ANR (RPC 堆积/频繁切集)。规则:

```python
PROBE_MAX_RETRIES = 1   # 最多重试一次
PROBE_ANR_TIMEOUT = 60  # 单次 probe 总耗时 60s 封顶

def run_v5_probe(series_id, sample_eps):
    for attempt in range(PROBE_MAX_RETRIES + 1):  # 共 2 次机会
        try:
            result = start_v5_probe_subprocess(
                series_id=series_id,
                eps=sample_eps,
                timeout=PROBE_ANR_TIMEOUT
            )
            if result.complete:
                return result.expected_vids
        except ProbeANR:
            safe_kill_subprocess(probe_proc)
            if attempt < PROBE_MAX_RETRIES:
                # 走一次 recover() 然后重试
                recover(reason="probe_anr_retry")
            else:
                # 重试仍失败 → 标 verification_failed
                # 关键: 不阻塞已下载数据的落盘和报告
                return None  # 调用方: VerifyReport.confidence="verification_failed"
```

**重要约束** (修 Codex 建议 6):

- probe 失败 **不回滚** 已下载的 mp4 / manifest
- `report.json.verification` 字段明确标记 `{"status": "verification_failed", "reason": "probe_anr"}`
- Agent 退出码 1 (partial), 不退 3 (fatal)
- 用户看到报告后可手动重跑 VERIFYING

### 5.5 VERIFYING 生命周期 (封 B1 后半 + Codex 建议 2)

**严格串行**, attach-resume 和 probe-bind 不能共存:

```python
def enter_verifying():
    # 1. 关掉 DOWNLOADING 的 attach-resume subprocess
    if v5_download_proc and v5_download_proc.poll() is None:
        signal_v5_graceful_exit(v5_download_proc)   # 发 SIGTERM
        try: v5_download_proc.wait(timeout=10)
        except TimeoutExpired: safe_kill_subprocess(v5_download_proc)
    v5_download_proc = None

    # 2. 确认 frida session 释放 (App 仍活, 在 ShortSeries*)
    #    注: frida Python API 没有 force-detach, 靠 v5 退出时的 session.detach() 清理
    #    残留 session 由 cleanup_stale_resources() 兜底
    assert_no_stale_frida_session(app_pid)

    # 3. 启动 probe-bind subprocess
    probe_proc = start_v5(mode="probe-bind", series_id=..., eps=sample_eps)

    # 4. 等 probe 完成或超时
    try:
        result = wait_for_probe_completion(probe_proc, timeout=PROBE_ANR_TIMEOUT)
    finally:
        if probe_proc.poll() is None:
            safe_kill_subprocess(probe_proc)

    # 5. VERIFYING 结束后, 不留任何 subprocess / frida session 残骸
    cleanup_stale_resources()

    return result
```

**关键约束**:
- DOWNLOADING → VERIFYING 之间必须**完整结束**前一个 subprocess,不能并行 attach
- VERIFYING 失败回 DOWNLOADING 时, 也要先关 probe-bind 再启 attach-resume
- frida session 只能存在一个 (Android 9 + frida 16.5.9 对多 session 支持不稳定)

---

## 6. 串剧防护 (不变,但明确)

- 搜索锁定 `locked_series_id` 后, v5 state 在 `ingest_bind` 里检查:
  `if bind.series_id != locked_series_id and bind.idx > 1: raise CrossDramaError`
- CrossDramaError 转 `ep_fail reason=cross_drama` 事件, Agent 收到立即 `ABORTED`
- manifest 每条写入 `series_id` 字段
- 验证阶段额外校: manifest 所有记录 series_id 必须一致

---

## 7. 实施计划 (工期 5 天, v3 重估)

### 阶段 A: v5 契约改造 (2 天, P0)

**Day 1** — 三模式 + 事件流
- [ ] 三种启动模式 (spawn-resolve / attach-resume / probe-bind)
- [ ] attach-resume 前置条件自检 (§3.1.1) + exit code 5
- [ ] 事件协议 JSON stdout + flush + 10s 心跳
- [ ] 退出码 0-5 分级 + 退出前 emit `done` 事件

**Day 2** — 提交顺序 + 测试
- [ ] 严格提交顺序 (§3.5): .tmp → fsync → rename → manifest fsync → ep_ok
- [ ] `--start auto` 扫 manifest + 文件完整性 (§3.6)
- [ ] `ingest_bind` 串剧 assert + `cross_drama` 事件
- [ ] 单元测试: 模拟 crash 后续跑从 manifest 正确 resume,孤儿 .tmp 清理
- [ ] manifest fsync 性能实测 (单集 overhead <50ms)

### 阶段 B: Agent 骨架 (1.5 天, P0)

**Day 3** — FSM + Watchdog + Recovery
- [ ] FSM 8 状态 (§4.1) + phase-aware Watchdog (§4.2/§4.3)
- [ ] Windows 进程树管理 + PID 保护 (§4.4)
- [ ] Recovery 路径 + stale session 清理 (§4.4)
- [ ] UI dump fallback 链 + tap 全屏观看动态定位 (§4.5)
- [ ] 熔断 L1/L2/L3 + 时间 + 进度停滞 (§4.6)

**Day 4 上午** — 端到端联调
- [ ] 跑 1 部剧 (60 集级), 中途 `adb force-stop` 制造 2+ 次 ANR, 验证独立恢复
- [ ] 故意让 frida-server 反复挂, 验证熔断在 max_restarts 后 ABORTED
- [ ] 验证 committed 进度只从 manifest 读, stdout 丢事件不影响续跑

### 阶段 C: 验证 + 报告 (1 天, P1)

**Day 4 下午** — 验证管线
- [ ] probe-bind 模式实现 (v5 侧) + VERIFYING 生命周期 (§5.5)
- [ ] 采样策略 (均匀 + 首末 + recovery 边界, §5.1)
- [ ] vid 对比 + 失败自愈 (§5.2, §5.3)
- [ ] probe-bind ANR 容错 (§5.4)

**Day 5 上午** — series_id 三策略 + 报告
- [ ] 搜索 API + manifest 缓存 两条主策略串联
- [ ] BIND 被动锁定标 `--enable-bind-passive` 实验 flag (§4.7)
- [ ] `report.json` 输出 (downloaded/failed/reasons/time/restarts/verification/config_source)

### 阶段 D: 稳定化 + 文档 (0.5 天, P2)

**Day 5 下午**
- [ ] 错误分类/诊断日志增强
- [ ] 连续测试 2 部不同剧, 含多次 ANR 场景
- [ ] README + 使用手册
- [ ] 熔断阈值调参 (基于 2 部剧实测数据)

**总工期: 5 天** (Codex 二轮评审指出 v2 的 3.5 天估算偏乐观, 主要欠缺了真机调试 + 验证管线的时间)

---

## 8. 风险与缓解

### 原有风险 (来自 v1 §6, 已部分重写)

### 风险 1: UI 坐标依赖 → **已缓解**
uiautomator dump 动态查找 + 失败回退链 + 硬编码兜底 (见 §4.5)。

### 风险 2: frida ANR 根本未解 → **已缓解(非消除)**
JS 同步 resolve 节流 + Agent 自愈。残余: 极端剧仍需多次重启, 熔断保证不死循环。

### 风险 3: 搜索 API 失败率 → **已缓解**
三策略回退: 搜索 → manifest → BIND 被动。

### 风险 4: 对齐验证成本 → **已缓解**
改用 vid 对比 + probe-bind 模式 (不下载),单集 ~5s, 5 集 ~30s。

### 风险 5: 熔断阈值硬编码 → **已缓解**
全部可配置 + report.json 记录 config_source。

### 风险 6 (Codex 新增): 窗口 / 升级弹窗 / 设备休眠 / USB 断连
**缓解**:
- USB 断连 → `adb devices` 每 N 秒检查, 断连触发 `RECOVERING` + 最多等待 60s 重连
- 设备休眠 → `adb shell input keyevent KEYCODE_WAKEUP` 在 NAVIGATING/RECOVERING 前调用
- 账号态变化 → BIND 长时间(> 30s)无响应, 检查登录状态页 (`text="登录"` 出现) → 熔断 `login_required`
- 升级弹窗 → Agent 启动时首次 NAVIGATING 前扫一次已知弹窗文案 (`text="立即更新"/"稍后"`) 并 tap 稍后

### 风险 7 (Codex 新增): 产品后果被低估
对齐假阳性 → 错标签喂进多模态模型 → 代价远高于下载失败。  
**缓解**:
- 双重验证: vid 采样 + 首末集强制验证
- 对齐验证通不过时, `report.json` 明确 `confidence: failed` 并拒绝将文件标为"可用于训练"
- 增加 P1 用户可选: `--strict-align` 模式,未通过即删文件不保留

---

## 9. Codex 评审 Blocker 关闭对照表

### v2 时 Codex 一轮评审结果

| # | Blocker | v2 状态 | v3 最终状态 | 关闭依据 |
|---|---|---|---|---|
| B1 | Recovery 和 v5 启动语义冲突 | partial | **closed** | 新增 `attach-resume` 模式 + 前置条件自检 + exit code 5 (§3.1, §3.1.1, §4.4) |
| B2 | Windows subprocess 进程树管理 | closed | closed | `CREATE_NEW_PROCESS_GROUP` + `taskkill /F /T` + PID 保护 (§3.2, §4.4) |
| B3 | stdout 缓冲 | closed | closed | `-u` + `PYTHONUNBUFFERED=1` + `flush=True` + `bufsize=1` (§3.2, §3.3) |
| B4 | Watchdog 非 phase-aware | closed | closed | 每 FSM 状态独立健康判据表 (§4.2, §4.3) |
| B5 | 5 集均匀采样置信度高估 | closed | closed | 均匀 + 首末 + recovery 边界 + vid 替代 kid (§5.1, §5.2) |
| B6 | stdout/manifest 双通道一致性 | partial | **closed** | 严格提交顺序 `fsync→rename→manifest fsync→ep_ok`,`last_committed_ep` 只从 manifest 读 (§3.5) |

### v3 新增封口项 (来自 Codex 二轮评审)

| 项 | v3 解决方式 | 章节 |
|---|---|---|
| attach-resume 前置条件失败契约 | 5 条自检 + exit code 5 + Agent 处理规则表 | §3.1.1 |
| 提交顺序 `ep_ok` vs manifest 时序 | 8 步严格顺序 + Agent 只读 manifest | §3.5 |
| VERIFYING 子进程生命周期 | 串行: 先关 attach-resume → 确认 frida 释放 → 启 probe-bind | §5.5 |
| probe-bind ANR 策略 | 最多 1 次重试 + 标 `verification_failed` + 不阻塞已下载落盘 | §5.4 |
| stale Python/frida session 清理 | Recovery 前枚举残留 + 清不掉 fail fast | §4.4 |
| 进程树误杀保护 | PID 有效性 + 句柄 poll + 拒绝杀自己 | §4.4 |
| fsync 性能预算 | 单集 ~40-60ms overhead, 60 集总 ~3-4s (可接受) | §3.5 |
| 状态机状态数 + 转移路径修正 | 8 状态, RECOVERING → DOWNLOADING (非 NAVIGATING) | §4.1 |
| BIND 被动锁定降级 | 实验性, 需 `--enable-bind-passive` 显式开启 | §4.7 |

### Codex 优化建议采纳情况 (累计两轮)

- ✅ 进程契约层 → 三种启动模式 (§3.1)
- ✅ 进度停滞作为 P0 → §4.3 / §4.6
- ✅ 验证用 vid 不用 kid → §5.2
- ✅ 验证覆盖 recovery 边界 → §5.1
- ✅ uiautomator dump fallback → §4.5
- ✅ 熔断阈值可配置 → §4.6
- ✅ `--start auto` 结合 manifest + 完整性 → §3.6
- ✅ attach-resume 失败契约 → §3.1.1
- ✅ VERIFYING 生命周期串行 → §5.5
- ✅ stale session 清理 → §4.4
- ✅ probe-bind ANR 策略 → §5.4
- ✅ BIND 被动锁定降级实验性 → §4.7
- ✅ 状态机修正 → §4.1
- ✅ 工期重估 5 天 → §7

---

## 10. 非目标 (不变)

- 分层 Layer 1/2/3 重构
- 并发多剧下载
- 智能广告识别/跳过
- LLM 驱动决策

---

## 11. 验收标准 (加强)

1. 单条命令 `python hongguo_agent.py -n "剧名"` 启动
2. 无人干预完成: 剧名 → resolve → 进剧 → 下载全集 → 验证 → 报告
3. 中途 `adb shell am force-stop com.phoenix.read` 至少 2 次, Agent 均能自愈
4. 对齐验证: 均匀采样 5 集 + 首末集 + recovery 边界集, vid 全匹配
5. `report.json` 字段齐全: `downloaded / failed / reasons / time / restarts / verification / config_source`
6. 连续测试 2 部不同剧均能独立完成 (一部含多次 ANR)
7. 熔断场景可复现: 故意让 frida-server 反复挂 → Agent 在 max_restarts 次后 ABORTED 且 partial 数据完整
