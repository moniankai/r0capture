"""红果短剧无人值守下载 Agent (hongguo_agent.py).

设计: docs/superpowers/specs/2026-04-18-hongguo-agent-design.md (v4)
目标: 输入剧名 → 全自动下载完整剧集 → 输出对齐验证报告, 无人值守

架构:
- v5 作为 subprocess 运行 (不 import, 故障隔离)
- FSM 状态机: INIT → RESOLVING → NAVIGATING → DOWNLOADING → VERIFYING → DONE
  (可从 DOWNLOADING/VERIFYING 转 RECOVERING, 回 DOWNLOADING)
- Watchdog 线程: 分阶段健康判据 + 进度停滞检测
- 熔断: L1 单集重试 / L2 连续失败硬重启 / L3 硬重启上限 / 时间上限
- Recovery: 杀 subprocess → 清 stale → 重启 frida → 启 App → tap 进剧 → attach-resume
- 验证: probe-bind 均匀采样 5 集 + recovery 边界集, vid 对齐

用法:
  python scripts/hongguo_agent.py -n "剧名" [--series-id X] [--total T]
                                  [--max-restarts 5] [--max-total-seconds 3600]
"""
from __future__ import annotations
import argparse
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from loguru import logger


# =============== 常量 ===============

APP_PACKAGE = "com.phoenix.read"
DEFAULT_OUT_DIR = Path("videos")
HONGGUO_V5 = Path(__file__).parent / "hongguo_v5.py"

# v5 退出码 (与 hongguo_v5.py 同步)
V5_EXIT_OK = 0
V5_EXIT_PARTIAL = 1
V5_EXIT_ANR = 2
V5_EXIT_FATAL = 3
V5_EXIT_USER_ABORT = 4
V5_EXIT_PRECOND = 5


# =============== ep_fail reason 分类 (Codex S2) ===============

# 基础设施类失败: frida/transport/App 本身挂掉导致, 应触发 app/frida recovery
INFRA_FAIL_REASONS = frozenset({
    'bind_timeout',        # BIND 未在 timeout 内到 (可能 frida 卡/App 卡)
    'cap_timeout',         # CAP 未到
    'rpc_timeout',         # RPC scheduleOnMainThread 超时
    'frida_attach_err',
    'script_load_err',
    'no_bind_after_attach',
    'first_bind_timeout',
})

# 业务/内容类失败: 不累加 consec_fail, 只消费单集预算 (L1)
BUSINESS_FAIL_REASONS = frozenset({
    'download_or_decrypt_err',   # CDN 404/5xx 或 AES-CTR 问题
    'manifest_append_err',       # 磁盘满 / 权限 (罕见, 但与 recovery 无关)
    'decrypt_err',
})

# 致命类 (直接 ABORTED)
FATAL_FAIL_REASONS = frozenset({
    'cross_drama',              # series_id 错位
    'context_mismatch',
    'manifest_corrupt',
})


def classify_fail_reason(reason: str) -> str:
    """返回 'infra' | 'business' | 'fatal' | 'unknown'."""
    if reason in INFRA_FAIL_REASONS:
        return 'infra'
    if reason in BUSINESS_FAIL_REASONS:
        return 'business'
    if reason in FATAL_FAIL_REASONS:
        return 'fatal'
    return 'unknown'


# =============== FSM 状态 (design v4 §4.1) ===============

class State(str, Enum):
    INIT = 'INIT'
    RESOLVING = 'RESOLVING'
    NAVIGATING = 'NAVIGATING'
    DOWNLOADING = 'DOWNLOADING'
    RECOVERING = 'RECOVERING'
    VERIFYING = 'VERIFYING'
    DONE = 'DONE'
    ABORTED = 'ABORTED'


# =============== 熔断机制 (design v4 §4.6) ===============

@dataclass
class CircuitBreaker:
    """3 层熔断 + 时间 + 进度停滞 (design v4 §4.6).
    所有阈值可通过 CLI 覆盖; config_source 记录来源.
    """
    max_retry_per_ep: int = 3
    max_consec_fail_before_restart: int = 4
    max_restarts: int = 5
    max_total_seconds: int = 3600
    max_stall_seconds: int = 180  # 3 分钟 last_ok_ep 未增长 → 停滞
    config_source: str = "default"

    # 运行时计数
    retry_per_ep: dict[int, int] = field(default_factory=dict)
    consec_fail: int = 0            # 只累加 infra 类失败, 触发 restart
    restart_count: int = 0
    start_time: float = field(default_factory=time.time)
    last_ok_ep: int = 0
    last_progress_ts: float = field(default_factory=time.time)
    verify_reflow_count: int = 0
    MAX_VERIFY_REFLOW: int = 1
    abandoned_eps: set[int] = field(default_factory=set)   # L1 放弃的集 (S1)
    fatal_fail_seen: bool = False   # 是否见过 fatal reason

    def elapsed(self) -> float:
        return time.time() - self.start_time

    def time_exceeded(self) -> bool:
        return self.elapsed() > self.max_total_seconds

    def stalled(self) -> bool:
        return (time.time() - self.last_progress_ts) > self.max_stall_seconds

    def note_progress(self, ep: int) -> None:
        if ep > self.last_ok_ep:
            self.last_ok_ep = ep
            self.last_progress_ts = time.time()
            self.consec_fail = 0  # 有进展即重置连续失败

    def note_fail(self, ep: int, reason: str = 'unknown') -> str:
        """按 reason 分层计数 (Codex S2).
        返回: 'abandoned' | 'infra' | 'business' | 'fatal' | 'unknown'
        """
        self.retry_per_ep[ep] = self.retry_per_ep.get(ep, 0) + 1
        cat = classify_fail_reason(reason)
        if cat == 'fatal':
            self.fatal_fail_seen = True
            return 'fatal'
        if cat == 'infra':
            self.consec_fail += 1  # 只 infra 才累加 restart trigger
        # business / unknown 不累 consec_fail, 但仍走 per-ep 预算
        if self.should_give_up_ep(ep):
            self.abandoned_eps.add(ep)
            return 'abandoned'
        return cat

    def should_give_up_ep(self, ep: int) -> bool:
        return self.retry_per_ep.get(ep, 0) >= self.max_retry_per_ep

    def should_trigger_restart(self) -> bool:
        return self.consec_fail >= self.max_consec_fail_before_restart

    def restart_exceeded(self) -> bool:
        return self.restart_count >= self.max_restarts

    def note_restart(self) -> None:
        self.restart_count += 1
        self.consec_fail = 0  # 重启后重置连续失败


# =============== ADB helpers ===============

def _adb_env() -> dict:
    return {**os.environ, "MSYS_NO_PATHCONV": "1"}


def adb_shell(cmd: str | list[str], timeout: float = 8.0) -> tuple[int, str]:
    """adb shell 执行, 返回 (returncode, stdout). timeout 视为 (-1, '')."""
    if isinstance(cmd, str):
        full = ["adb", "shell", cmd]
    else:
        full = ["adb", "shell"] + cmd
    try:
        r = subprocess.run(full, capture_output=True, text=True,
                           env=_adb_env(), timeout=timeout)
        return r.returncode, (r.stdout or '').replace('\r', '')
    except (subprocess.TimeoutExpired, OSError):
        return -1, ''


def adb_pidof(pkg: str = APP_PACKAGE) -> int | None:
    rc, out = adb_shell(f"pidof {pkg}", timeout=5)
    if rc != 0:
        return None
    pids = [int(x) for x in out.strip().split() if x.isdigit()]
    return min(pids) if pids else None


def adb_foreground() -> str:
    """返回前台 Activity. 兼容多种 Android/MIUI 格式:
    - `mResumedActivity: ActivityRecord{... pkg/.X.Y ...}` (标准 AOSP)
    - `ResumedActivity: ActivityRecord{... pkg/.X.Y ...}` (MIUI/部分 OEM)
    """
    rc, out = adb_shell("dumpsys activity activities", timeout=8)
    if rc != 0:
        return ''
    for line in out.splitlines():
        # 同时兼容 mResumedActivity 和 ResumedActivity (前者是标准, 后者 MIUI)
        if 'ResumedActivity' in line and 'ActivityRecord' in line:
            m = re.search(r'\S+/\S+', line)
            if m:
                return m.group(0).rstrip('}')
    return ''


def adb_force_stop(pkg: str = APP_PACKAGE) -> None:
    adb_shell(f"am kill {pkg}", timeout=5)
    adb_shell(f"am force-stop {pkg}", timeout=5)


def adb_start_app() -> None:
    adb_shell(f"am start -n {APP_PACKAGE}/com.dragon.read.pages.splash.SplashActivity",
              timeout=8)


def adb_tap(x: int, y: int) -> None:
    adb_shell(f"input tap {x} {y}", timeout=5)


# =============== Frida-server 健康检查 ===============

def frida_server_pid() -> int | None:
    rc, out = adb_shell("pidof frida-server", timeout=5)
    if rc != 0:
        return None
    pids = [int(x) for x in out.strip().split() if x.isdigit()]
    return pids[0] if pids else None


def restart_frida_server() -> bool:
    """kill + relaunch frida-server. 返回是否最终健康."""
    adb_shell("su -c 'killall -9 frida-server'", timeout=5)
    time.sleep(1.5)
    # 后台启动 (非阻塞)
    subprocess.Popen(["adb", "shell", "su -c '/data/local/tmp/frida-server &'"],
                     env=_adb_env(),
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # 等最多 10s
    for _ in range(20):
        time.sleep(0.5)
        if frida_server_pid():
            return True
    return False


# =============== UI dump fallback 链 (design v4 §4.5) ===============

def uiautomator_dump(local_path: Path, retries: int = 3) -> bool:
    """dump + pull ui.xml. 三重 retry + --compressed fallback.
    Android 9 "could not get idle state" 高频, 需要多策略兜底.
    """
    for i in range(retries):
        # 1. 标准 dump
        rc, _ = adb_shell("uiautomator dump /sdcard/ui.xml", timeout=6)
        if rc == 0:
            r = subprocess.run(["adb", "pull", "/sdcard/ui.xml", str(local_path)],
                               capture_output=True, env=_adb_env(), timeout=5)
            if r.returncode == 0 and local_path.exists():
                return True

        # 2. --compressed (有时绕过 idle 检查)
        rc, _ = adb_shell("uiautomator dump --compressed /sdcard/ui.xml", timeout=6)
        if rc == 0:
            r = subprocess.run(["adb", "pull", "/sdcard/ui.xml", str(local_path)],
                               capture_output=True, env=_adb_env(), timeout=5)
            if r.returncode == 0 and local_path.exists():
                return True

        time.sleep(2.5)  # 等 App idle
    return False


def find_text_bounds(xml_path: Path, text: str) -> tuple[int, int] | None:
    """找含 text 的 node bounds 中心坐标. 失败返回 None."""
    if not xml_path.exists():
        return None
    content = xml_path.read_bytes().decode('utf-8', errors='replace')
    pat = rf'text="{re.escape(text)}"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"'
    m = re.search(pat, content)
    if not m:
        return None
    x1, y1, x2, y2 = map(int, m.groups())
    return ((x1 + x2) // 2, (y1 + y2) // 2)


# =============== 进程树管理 (design v4 §4.4) ===============

def safe_kill_subprocess_tree(proc: subprocess.Popen | None,
                               timeout: float = 3.0) -> bool:
    """杀 subprocess 进程树. 带 poll + PID 保护 + 拒绝杀自己 (design v4 §4.4).
    返回 True = 进程已确认退出, False = 未能确认 (taskkill 报错 / wait 超时).
    Codex S3: 显式检查 taskkill 返回码 + fallback 到 proc.terminate()/kill().
    """
    if proc is None or proc.poll() is not None:
        return True
    pid = proc.pid
    if pid == os.getpid():
        raise RuntimeError("refused to kill self")

    if sys.platform == 'win32':
        # 优先 taskkill /F /T 级联
        r = subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode != 0:
            # 常见: PID 已退出 (128) / 访问拒绝 (5)
            # 128 视为"已退出"即可; 其他报警但仍 fallback
            stderr_preview = (r.stderr or '').strip()[:200]
            logger.warning(f"[kill] taskkill pid={pid} rc={r.returncode} "
                           f"stderr={stderr_preview!r}")
            # fallback: Popen.terminate/kill
            try: proc.terminate()
            except Exception: pass
            try: proc.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                try: proc.kill()
                except Exception: pass

        try:
            proc.wait(timeout=timeout)
            return True
        except subprocess.TimeoutExpired:
            logger.error(f"[kill] pid={pid} 超时 {timeout}s 未退出, 放弃")
            return False
    else:
        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
            time.sleep(1)
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            proc.wait(timeout=timeout)
            return True
        except subprocess.TimeoutExpired:
            return False


# =============== stale 清理 (design v4 §4.4) ===============

def cleanup_stale_resources(my_token: str, drama_dir: Path) -> None:
    """Recovery 前清除脏状态. 清不掉 raise.
    stale 检测能力不可用时也 raise (Codex M4: 不无声失败)."""
    # 1. 杀残留 hongguo_v5.py 进程 (owner token 识别, 防误杀并发)
    try:
        stale = _find_stale_v5(exclude_token=my_token)
    except StaleDetectUnavailable as e:
        logger.error(f"[cleanup] {e}")
        raise RuntimeError(f"stale_detect_unavailable: {e}")

    for pid in stale:
        if pid == os.getpid():
            continue
        _kill_pid(pid)
    remaining = _find_stale_v5(exclude_token=my_token)
    if remaining:
        raise RuntimeError(f"stale_v5_cleanup_failed: {remaining}")

    # 2. force-stop App (最多 3 次)
    for _ in range(3):
        adb_force_stop()
        time.sleep(1)
        if not adb_pidof():
            break
    else:
        raise RuntimeError("app_force_stop_failed")

    # 3. 清 .tmp/ 孤儿
    tmp_dir = drama_dir / ".tmp"
    if tmp_dir.exists():
        for f in tmp_dir.glob("*"):
            try:
                f.unlink()
            except OSError as e:
                raise RuntimeError(f"tmp_cleanup_failed: {f} {e}")


class StaleDetectUnavailable(RuntimeError):
    """stale 进程检测能力不可用 (psutil 未装 + 无可靠 fallback).
    Codex M4: 不能把"检测失败"伪装成"健康", recovery 必须明确 fail fast."""


def _find_stale_v5(exclude_token: str) -> list[int]:
    """查当前机器上所有跑着的 hongguo_v5.py 进程 (排除当前 Agent token).
    检测能力不可用时 raise StaleDetectUnavailable, 让上层看得见.
    """
    try:
        import psutil
    except ImportError:
        # Codex M4: 不无声失败. 明确告诉调用者 "检测不可用"
        raise StaleDetectUnavailable(
            "psutil not installed; stale v5 detection unavailable. "
            "Install via `pip install psutil` for reliable recovery."
        )
    stale: list[int] = []
    for p in psutil.process_iter(['pid', 'name', 'cmdline', 'environ']):
        try:
            if p.info.get('name', '').lower() not in ('python.exe', 'python', 'python3',
                                                        'pythonw.exe'):
                continue
            cmd = p.info.get('cmdline') or []
            if not any('hongguo_v5.py' in (c or '') for c in cmd):
                continue
            env = p.info.get('environ') or {}
            if env.get('HONGGUO_AGENT_TOKEN') == exclude_token:
                continue  # 当前 Agent 的合法子进程, 跳过
            stale.append(p.info['pid'])
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return stale


def _kill_pid(pid: int) -> None:
    if sys.platform == 'win32':
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                       capture_output=True, timeout=5)
    else:
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


# =============== v5 subprocess 管理 ===============

def start_v5(mode: str, ctx: 'AgentContext', **extra_args) -> subprocess.Popen:
    """启动 hongguo_v5.py subprocess (unbuffered + owner token).
    design v4 §3.2.
    """
    cmd = [
        sys.executable, "-u", str(HONGGUO_V5),
        "--mode", mode,
        "-n", ctx.drama_name,
        "--out", str(ctx.out_dir),
    ]
    if ctx.series_id:
        cmd += ["--series-id", ctx.series_id]
    if ctx.total:
        cmd += ["--total", str(ctx.total)]
    for k, v in extra_args.items():
        flag = f"--{k.replace('_', '-')}"
        cmd += [flag, str(v)]

    env = {
        **os.environ,
        "HONGGUO_AGENT_TOKEN": ctx.token,
        "PYTHONUNBUFFERED": "1",
        "PYTHONIOENCODING": "utf-8",
    }

    kwargs = dict(
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
        bufsize=1,        # 行缓冲
        text=True,
        encoding='utf-8',
        errors='replace',
    )
    if sys.platform == 'win32':
        kwargs['creationflags'] = subprocess.CREATE_NEW_PROCESS_GROUP

    logger.info(f"[v5] spawn mode={mode} {' '.join(cmd[3:])}")
    return subprocess.Popen(cmd, **kwargs)


def read_events(proc: subprocess.Popen,
                 on_event: callable,
                 stall_timeout: float = 60.0) -> int:
    """逐行读 v5 stdout, JSON 行调 on_event(dict), 非 JSON 行忽略.
    阻塞到 proc 退出. 返回 exit code.

    stall_timeout: 连续无任何 stdout 输出超时 (秒). 超时返回 -1 (Agent 视为 hang).
    """
    last_output_ts = time.time()

    def _reader():
        nonlocal last_output_ts
        try:
            for line in iter(proc.stdout.readline, ''):
                if not line:
                    break
                last_output_ts = time.time()
                line = line.rstrip()
                if not line:
                    continue
                # JSON 事件: 首字符 '{'
                if line.startswith('{'):
                    try:
                        ev = json.loads(line)
                        on_event(ev)
                        continue
                    except json.JSONDecodeError:
                        # Codex M2: JSON 截断不再静默吞掉, emit 显式控制面损坏事件
                        on_event({
                            'type': 'control_plane_corrupt',
                            'reason': 'json_parse_error',
                            'snippet': line[:120],
                        })
                        continue
                # 非 JSON 行 (v5 loguru 日志) → 转 debug
                # 不发给 on_event, 避免污染事件流
        except (OSError, ValueError):
            pass

    reader_t = threading.Thread(target=_reader, daemon=True, name='v5-stdout-reader')
    reader_t.start()

    # 主线程监控退出 + stall
    while proc.poll() is None:
        time.sleep(0.5)
        if time.time() - last_output_ts > stall_timeout:
            logger.warning(f"[v5] stdout stall {stall_timeout}s, treat as hang")
            return -1
    # 等 reader 把剩余输出读完
    reader_t.join(timeout=2.0)
    return proc.returncode


# =============== Manifest 读取 (v5 同名逻辑) ===============

def read_committed_eps(drama_dir: Path) -> dict[int, str]:
    """返回 {ep -> kid_prefix8}. 与 v5 的同名函数保持一致."""
    mfile = drama_dir / 'session_manifest.jsonl'
    if not mfile.exists():
        return {}
    result: dict[int, str] = {}
    try:
        with mfile.open('r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ep = rec.get('ep')
                kid = rec.get('kid') or ''
                if isinstance(ep, int) and ep > 0 and kid:
                    result[ep] = kid[:8]
    except OSError:
        pass
    return result


def _rewrite_manifest_excluding(drama_dir: Path, exclude_eps: set[int]) -> int:
    """重写 session_manifest.jsonl, 排除 exclude_eps 中的记录.
    用于 verify reflow 时把不齐的 ep 从 committed 去除, 让 --start auto 识别为 missing.

    **原子写**: temp file → fsync → os.replace (design v4 §3.5 承诺).
    崩溃中途不会截断原 manifest.
    """
    mfile = drama_dir / 'session_manifest.jsonl'
    if not mfile.exists():
        return 0
    kept: list[str] = []
    removed = 0
    try:
        with mfile.open('r', encoding='utf-8') as f:
            for line in f:
                s = line.rstrip('\n')
                if not s.strip():
                    continue
                try:
                    rec = json.loads(s)
                except json.JSONDecodeError:
                    continue
                if rec.get('ep') in exclude_eps:
                    removed += 1
                else:
                    kept.append(s)
    except OSError:
        return 0
    if not removed:
        return 0

    # 原子写: 写到 .tmp → fsync → atomic replace
    tmp = mfile.with_suffix('.jsonl.tmp')
    try:
        with tmp.open('w', encoding='utf-8') as f:
            for s in kept:
                f.write(s + '\n')
            f.flush()
            os.fsync(f.fileno())
        os.replace(str(tmp), str(mfile))
    except OSError as e:
        logger.error(f"[manifest] atomic rewrite 失败: {e}")
        if tmp.exists():
            try: tmp.unlink()
            except OSError: pass
        return 0
    return removed


def read_committed_vids(drama_dir: Path) -> dict[int, str]:
    """返回 {ep -> vid}. 用于 verify 对比."""
    mfile = drama_dir / 'session_manifest.jsonl'
    if not mfile.exists():
        return {}
    result: dict[int, str] = {}
    try:
        with mfile.open('r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ep = rec.get('ep')
                vid = rec.get('vid') or ''
                if isinstance(ep, int) and ep > 0 and vid:
                    result[ep] = vid
    except OSError:
        pass
    return result


# =============== NAVIGATING: 进入 ShortSeries* (design v4 §4.4 步 5-7) ===============

def navigate_to_short_series(drama_dir: Path, max_retries: int = 3) -> bool:
    """从任意状态启动 App + 进入 ShortSeries* Activity.
    步骤: force-stop → splash → wait main → tap 进剧入口.

    tap 入口策略 (按顺序尝试):
      1. 动态找 "全屏观看" 文本 (首页卡片)
      2. 动态找 "继续播放" (剧场 tab 续播入口, 需先 tap 剧场)
      3. fallback 硬编码 (570, 1281) (首页默认位置)
    """
    for attempt in range(max_retries):
        logger.info(f"[nav] attempt {attempt+1}/{max_retries}")

        # 优化: 先看 App 是否已在 Main/ShortSeries, 避免不必要的 force-stop 冷启动
        fg = adb_foreground()
        if 'ShortSeries' in fg:
            logger.info(f"[nav] already in ShortSeries: {fg}")
            return True
        if 'MainFragmentActivity' not in fg:
            adb_force_stop()
            time.sleep(2)
            adb_start_app()

            # Poll splash → main, 最多等 40s (MIUI 冷启动较慢)
            for i in range(40):
                time.sleep(1)
                fg = adb_foreground()
                if 'MainFragmentActivity' in fg or 'ShortSeries' in fg:
                    break
            else:
                logger.warning(f"[nav] splash→main timeout 40s, fg={fg}")
                continue

        # 已在 ShortSeries 直接返回 (cold start 可能直接进剧)
        if 'ShortSeries' in fg:
            logger.info(f"[nav] in ShortSeries after splash: {fg}")
            return True

        # 已在 ShortSeries 直接返回
        if 'ShortSeries' in fg:
            logger.info(f"[nav] already in ShortSeries after splash: {fg}")
            return True

        # Strategy 1: dump 找"全屏观看"
        ui_path = drama_dir / ".ui.xml"
        if uiautomator_dump(ui_path, retries=2):
            bounds = find_text_bounds(ui_path, "全屏观看")
            if bounds:
                logger.info(f"[nav] tap 全屏观看 @ {bounds}")
                adb_tap(*bounds)
                time.sleep(4)
                if 'ShortSeries' in adb_foreground():
                    return True

            # Strategy 2: tap "剧场" tab 再找"继续播放"
            c = find_text_bounds(ui_path, "剧场")
            if c:
                logger.info(f"[nav] tap 剧场 tab @ {c}")
                adb_tap(*c)
                time.sleep(3)
                if uiautomator_dump(ui_path, retries=2):
                    cp = find_text_bounds(ui_path, "继续播放")
                    if cp:
                        logger.info(f"[nav] tap 继续播放 @ {cp}")
                        adb_tap(*cp)
                        time.sleep(4)
                        if 'ShortSeries' in adb_foreground():
                            return True

        # Strategy 3: fallback 硬编码 (受 App 版本影响)
        logger.info("[nav] fallback tap (570, 1281)")
        adb_tap(570, 1281)
        time.sleep(4)
        if 'ShortSeries' in adb_foreground():
            return True

    return False


# =============== Recovery (design v4 §4.4) ===============

def recover(ctx: 'AgentContext', reason: str) -> bool:
    """全量恢复链路: kill subprocess → cleanup stale → frida-restart → App 重启 → 进剧.
    成功返回 True, Agent 进 DOWNLOADING; 失败 False, Agent ABORTED.
    """
    logger.warning(f"[recover] start reason={reason}")
    # 记录 recovery 发生时的 last_ok_ep, 给 verify 采样用
    ctx.recovery_boundaries.append(ctx.cb.last_ok_ep)
    ctx.cb.note_restart()
    if ctx.cb.restart_exceeded():
        logger.error(f"[recover] max_restarts={ctx.cb.max_restarts} exceeded")
        return False

    # 1. 杀 v5 subprocess
    if ctx.v5_proc is not None:
        safe_kill_subprocess_tree(ctx.v5_proc, timeout=3.0)
        ctx.v5_proc = None

    # 2. 清脏状态 (stale v5 进程 + App force-stop + .tmp 孤儿)
    try:
        cleanup_stale_resources(ctx.token, ctx.drama_dir)
    except RuntimeError as e:
        logger.error(f"[recover] cleanup failed: {e}")
        return False

    # 3. frida-server 健康检查 + 强制重启 (Codex S4)
    force_frida_restart = ctx.cleanup_timeout_seen
    if frida_server_pid() is None or force_frida_restart:
        if force_frida_restart:
            logger.info("[recover] cleanup_timeout seen → force frida-server restart")
            ctx.cleanup_timeout_seen = False
        else:
            logger.info("[recover] frida-server down, restart")
        if not restart_frida_server():
            logger.error("[recover] frida-server restart failed")
            return False

    # 4. 启 App + 进 ShortSeries*
    if not navigate_to_short_series(ctx.drama_dir, max_retries=2):
        logger.error("[recover] navigate failed")
        return False

    logger.success(f"[recover] OK (restart_count={ctx.cb.restart_count})")
    return True


# =============== VERIFYING: 对齐验证 (design v4 §5) ===============

def pick_verification_eps(total: int, uniform_n: int = 5,
                           recovery_boundaries: list[int] | None = None,
                           ) -> list[int]:
    """组合采样: 均匀 n 点 + 首末 + recovery 前后各一集 (design v4 §5.1).

    例: total=60, recovery at [25, 43] → [1, 15, 24, 26, 30, 42, 44, 45, 60]
    """
    recovery_boundaries = recovery_boundaries or []
    if total <= uniform_n:
        uniform = list(range(1, total + 1))
    else:
        step = (total - 1) / (uniform_n - 1)
        uniform = sorted({round(1 + i * step) for i in range(uniform_n)})
    boundary = {1, total}
    recovery: set[int] = set()
    for r in recovery_boundaries:
        if 1 < r:
            recovery.add(max(1, r - 1))
        if r < total:
            recovery.add(min(total, r + 1))
    return sorted(set(uniform) | boundary | recovery)


def run_verification(ctx: 'AgentContext') -> tuple[str, dict]:
    """跑 probe-bind + 对比 vid. 返回 (confidence, detail).
    confidence: 'high' | 'failed' | 'verification_failed' | 'skipped'
    """
    committed_vids = read_committed_vids(ctx.drama_dir)
    if not committed_vids:
        return 'skipped', {'reason': 'no_manifest'}
    total = ctx.total or max(committed_vids.keys())
    sample_eps = pick_verification_eps(
        total, uniform_n=5,
        recovery_boundaries=ctx.recovery_boundaries,
    )
    # 只采样已下载的
    sample_eps = [ep for ep in sample_eps if ep in committed_vids]
    if not sample_eps:
        return 'skipped', {'reason': 'no_sample_in_committed'}

    logger.info(f"[verify] sample_eps={sample_eps} (recovery_boundaries="
                f"{ctx.recovery_boundaries})")

    # 启 probe-bind subprocess
    eps_arg = ','.join(str(e) for e in sample_eps)
    ctx.v5_proc = start_v5('probe-bind', ctx, eps=eps_arg)
    probe_result: dict[int, str] = {}

    def on_event(ev: dict):
        nonlocal probe_result
        t = ev.get('type')
        if t == 'probe_ep_ok':
            ep = ev.get('ep')
            vid = ev.get('vid', '')
            if isinstance(ep, int) and vid:
                probe_result[ep] = vid
                logger.info(f"[verify ep{ep}] probe vid={vid[:14]}...")
        elif t == 'probe_ep_fail':
            logger.warning(f"[verify ep{ev.get('ep')}] probe fail: {ev.get('reason')}")
        elif t == 'probe_result':
            pr = ev.get('expected') or {}
            # ep key 是 string (JSON 反序列化), 转 int
            for k, v in pr.items():
                try:
                    probe_result[int(k)] = v
                except (ValueError, TypeError):
                    pass

    rc = read_events(ctx.v5_proc, on_event, stall_timeout=90.0)
    ctx.v5_proc = None

    # 控制面故障 (probe subprocess 挂 / 截断 JSON 导致事件丢失)
    if rc in (V5_EXIT_ANR, V5_EXIT_FATAL, -1):
        return 'verification_failed', {
            'reason': f'probe_rc={rc}',
            'captured': len(probe_result),
            'requested': len(sample_eps),
        }

    # 对比已采样到的
    misaligned: list[dict] = []
    for ep in sample_eps:
        expected = probe_result.get(ep)
        actual = committed_vids.get(ep)
        if expected and actual and expected != actual:
            misaligned.append({
                'ep': ep, 'expected_vid': expected, 'actual_vid': actual,
            })

    if misaligned:
        return 'failed', {
            'sample_eps': sample_eps,
            'misaligned': misaligned,
            'captured': len(probe_result),
            'requested': len(sample_eps),
        }

    # Codex M2 严判: high 要求**全采样点都 probe 到**. 部分 bind_timeout 的点
    # 无从证明对齐, 退化为 verification_failed (Agent 视为 partial, 不升 DONE-ok).
    if len(probe_result) < len(sample_eps):
        missing = [ep for ep in sample_eps if ep not in probe_result]
        return 'verification_failed', {
            'reason': 'incomplete_probe',
            'sample_eps': sample_eps,
            'missing_probe_eps': missing,
            'captured': len(probe_result),
            'requested': len(sample_eps),
        }
    return 'high', {
        'sample_eps': sample_eps,
        'match_count': len(probe_result),
        'requested': len(sample_eps),
    }


# =============== FSM 主循环 ===============

def run_fsm(ctx: 'AgentContext') -> int:
    """FSM 主循环. 返回 exit code (0 ok, 1 partial, 3 aborted)."""
    while True:
        # 时间熔断
        if ctx.cb.time_exceeded():
            logger.error(f"[fsm] total time exceeded "
                         f"{ctx.cb.max_total_seconds}s → ABORTED")
            ctx.state = State.ABORTED
            break

        if ctx.state == State.INIT:
            # 初始化: 确保 frida 健康 + App 在 ShortSeries
            logger.info("[fsm] INIT → check frida + navigate")
            if frida_server_pid() is None and not restart_frida_server():
                logger.error("[fsm] no frida-server, abort")
                ctx.state = State.ABORTED
                break
            if not navigate_to_short_series(ctx.drama_dir, max_retries=3):
                logger.error("[fsm] init navigate failed")
                ctx.state = State.ABORTED
                break
            ctx.state = State.DOWNLOADING

        elif ctx.state == State.DOWNLOADING:
            # 启动 attach-resume subprocess, 读事件直到它 exit
            if not ctx.series_id:
                # 无 series_id 时, 先走一次 legacy (搜索) 拿 series_id (TODO: proper spawn-resolve)
                logger.error("[fsm] no series_id, need explicit --series-id in v1 Agent")
                ctx.state = State.ABORTED
                break

            ctx.v5_proc = start_v5('attach-resume', ctx, start='auto')
            rc = _download_session(ctx)
            ctx.v5_proc = None

            # Codex S1/S2 fatal 直接 ABORTED
            if ctx.cb.fatal_fail_seen:
                logger.error("[fsm] fatal ep_fail seen → ABORTED")
                ctx.state = State.ABORTED
                break

            if rc == V5_EXIT_OK:
                # 全部已下 / 无失败
                ctx.state = State.VERIFYING
            elif rc == V5_EXIT_PARTIAL:
                # 检查是否所有 missing 集都已 abandoned (L1 放弃) → 不再 retry, 进 VERIFYING
                committed = read_committed_eps(ctx.drama_dir)
                if ctx.total:
                    still_missing = [ep for ep in range(1, ctx.total + 1)
                                     if ep not in committed]
                    stuck = [ep for ep in still_missing
                             if ep in ctx.cb.abandoned_eps]
                    if still_missing and set(still_missing).issubset(ctx.cb.abandoned_eps):
                        logger.warning(f"[fsm] 所有 missing 集均已 abandoned ({stuck}) → VERIFYING (partial)")
                        ctx.state = State.VERIFYING
                        continue

                # 有 fail, 根据熔断判断
                if ctx.cb.stalled():
                    logger.warning("[fsm] progress stalled → RECOVERING")
                    ctx.state = State.RECOVERING
                elif ctx.cb.should_trigger_restart():
                    logger.warning(f"[fsm] consec_fail={ctx.cb.consec_fail} triggered restart → RECOVERING")
                    ctx.state = State.RECOVERING
                else:
                    logger.info(f"[fsm] partial (consec={ctx.cb.consec_fail}, "
                                f"abandoned={len(ctx.cb.abandoned_eps)}), retry DOWNLOADING")
                    continue  # 再进 DOWNLOADING 让 v5 attach-resume --start auto 续跑
            elif rc == V5_EXIT_PRECOND:
                logger.info("[fsm] precond fail (likely App not in ShortSeries) → NAVIGATING")
                ctx.state = State.NAVIGATING
            elif rc == V5_EXIT_ANR or rc == -1:
                logger.warning(f"[fsm] v5 rc={rc} → RECOVERING")
                ctx.state = State.RECOVERING
            elif rc == V5_EXIT_FATAL:
                logger.error(f"[fsm] v5 rc={rc} (fatal) → ABORTED")
                ctx.state = State.ABORTED
                break
            else:
                logger.warning(f"[fsm] v5 rc={rc} (unknown) → RECOVERING")
                ctx.state = State.RECOVERING

        elif ctx.state == State.NAVIGATING:
            if navigate_to_short_series(ctx.drama_dir, max_retries=2):
                ctx.state = State.DOWNLOADING
            else:
                logger.warning("[fsm] NAVIGATING failed → RECOVERING")
                ctx.state = State.RECOVERING

        elif ctx.state == State.RECOVERING:
            if recover(ctx, reason='download_fail'):
                ctx.state = State.DOWNLOADING
            else:
                logger.error("[fsm] RECOVERING failed → ABORTED")
                ctx.state = State.ABORTED
                break

        elif ctx.state == State.VERIFYING:
            confidence, detail = run_verification(ctx)
            ctx.verify_result = {'confidence': confidence, **detail}
            logger.info(f"[verify] confidence={confidence} detail={detail}")

            if confidence == 'high':
                ctx.state = State.DONE
            elif confidence == 'failed':
                # 回流 DOWNLOADING 重下问题区间 (max_reflow=1)
                if ctx.cb.verify_reflow_count >= ctx.cb.MAX_VERIFY_REFLOW:
                    logger.error("[verify] reflow exceeded → ABORTED")
                    ctx.state = State.ABORTED
                    break
                ctx.cb.verify_reflow_count += 1
                # 删问题 ep 的文件, 让 --start auto 重下
                for mis in detail.get('misaligned', []):
                    ep = mis['ep']
                    # 删 manifest 中该 ep (简单实现: 只删文件, 让 orphan 清理顺带扫掉不在 manifest 的)
                    # 更精确的做法是重写 manifest, 这里先走 orphan 路径
                    kid_prefix = read_committed_eps(ctx.drama_dir).get(ep, '')
                    if kid_prefix:
                        f = ctx.drama_dir / f"episode_{ep:03d}_{kid_prefix}.mp4"
                        if f.exists():
                            f.unlink()
                            logger.info(f"[verify reflow] deleted {f.name}")
                # 重写 manifest 排除 misaligned ep
                _rewrite_manifest_excluding(ctx.drama_dir,
                                             {m['ep'] for m in detail.get('misaligned', [])})
                ctx.state = State.DOWNLOADING
            else:
                # verification_failed / skipped → 视为 partial 完成
                logger.warning(f"[verify] {confidence} → DONE (partial)")
                ctx.state = State.DONE

        elif ctx.state == State.DONE:
            logger.success("[fsm] DONE")
            break

        elif ctx.state == State.ABORTED:
            break

    return _finalize(ctx)


def _download_session(ctx: 'AgentContext') -> int:
    """启动 v5 attach-resume, 读事件流, 返回 exit code."""
    def on_event(ev: dict):
        t = ev.get('type')
        if t == 'ep_ok':
            ep = ev.get('ep', 0)
            ctx.cb.note_progress(ep)
            logger.info(f"[ep{ep}] ✓ vid={ev.get('vid', '')[:14]}...")
        elif t == 'ep_fail':
            ep = ev.get('ep', 0)
            reason = ev.get('reason', 'unknown')
            cat = ctx.cb.note_fail(ep, reason)
            logger.warning(f"[ep{ep}] ✗ reason={reason} cat={cat} "
                           f"(retry={ctx.cb.retry_per_ep[ep]}/"
                           f"{ctx.cb.max_retry_per_ep} consec={ctx.cb.consec_fail})")
            if cat == 'abandoned':
                logger.error(f"[ep{ep}] L1 预算用尽, 放弃该集")
        elif t == 'resolved':
            logger.info(f"[resolved] total={ev.get('total')} "
                        f"series_id={ev.get('series_id', '')[:20]}")
        elif t == 'done':
            logger.info(f"[done] ok={ev.get('ok')} fail={ev.get('fail')} "
                        f"last_ep={ev.get('last_ep')}")
        elif t == 'cross_drama':
            logger.error(f"[cross_drama] expected={ev.get('expected')} "
                         f"actual={ev.get('actual')}")
        elif t == 'cleanup_timeout':
            # Codex S4: v5 的 frida session 可能 leak → 强制下次 recover 走 frida-restart
            logger.warning(f"[cleanup_timeout] {ev.get('detail')} "
                           f"(frida session leak suspected)")
            ctx.cleanup_timeout_seen = True
        elif t in ('anr_suspected', 'fatal'):
            logger.warning(f"[{t}] {ev.get('detail')}")
        elif t == 'control_plane_corrupt':
            logger.error(f"[control_plane_corrupt] {ev.get('reason')}: "
                         f"{ev.get('snippet', '')[:80]}")
            ctx.control_plane_corrupt_count += 1

    return read_events(ctx.v5_proc, on_event, stall_timeout=60.0)


def _finalize(ctx: 'AgentContext') -> int:
    """清理 + 输出 report.json."""
    if ctx.v5_proc is not None:
        safe_kill_subprocess_tree(ctx.v5_proc, timeout=3.0)

    committed = read_committed_eps(ctx.drama_dir)
    total = ctx.total or 0
    missing = []
    if total > 0:
        missing = [ep for ep in range(1, total + 1) if ep not in committed]

    report = {
        'drama': ctx.drama_name,
        'series_id': ctx.series_id,
        'total': total,
        'downloaded': sorted(committed.keys()),
        'missing': missing,
        'state': ctx.state.value,
        'restarts': ctx.cb.restart_count,
        'recovery_boundaries': ctx.recovery_boundaries,
        'elapsed_seconds': round(ctx.cb.elapsed(), 2),
        'config_source': ctx.cb.config_source,
        'verification': ctx.verify_result,
    }
    report_path = ctx.drama_dir / 'report.json'
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                            encoding='utf-8')
    logger.info(f"[report] {report_path}")
    logger.info(f"[summary] {len(committed)}/{total} downloaded, "
                f"{len(missing)} missing, state={ctx.state.value}, "
                f"restarts={ctx.cb.restart_count}, elapsed={ctx.cb.elapsed():.0f}s")

    if ctx.state == State.DONE and not missing:
        return 0
    return 1


@dataclass
class AgentContext:
    drama_name: str
    series_id: str | None
    total: int | None
    out_dir: Path
    drama_dir: Path
    token: str
    cb: CircuitBreaker
    state: State = State.INIT
    v5_proc: subprocess.Popen | None = None
    recovery_boundaries: list[int] = field(default_factory=list)
    verify_result: dict | None = None
    cleanup_timeout_seen: bool = False  # Codex S4: v5 发过 cleanup_timeout
    control_plane_corrupt_count: int = 0  # Codex M2: 控制面截断次数


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('-n', '--name', required=True, help='剧名')
    ap.add_argument('--series-id', type=str, default='',
                    help='已知 series_id 跳过搜索 (强烈推荐)')
    ap.add_argument('-t', '--total', type=int, default=0, help='总集数 (可选)')
    ap.add_argument('--out', type=Path, default=DEFAULT_OUT_DIR)
    ap.add_argument('--max-retry-per-ep', type=int, default=3)
    ap.add_argument('--max-consec-fail', type=int, default=4)
    ap.add_argument('--max-restarts', type=int, default=5)
    ap.add_argument('--max-total-seconds', type=int, default=3600)
    ap.add_argument('--max-stall-seconds', type=int, default=180)
    args = ap.parse_args()

    logger.remove()
    logger.add(sys.stderr, level='INFO',
               format='<green>{time:HH:mm:ss}</green> | <cyan>{level:<7}</cyan> | {message}')

    cb = CircuitBreaker(
        max_retry_per_ep=args.max_retry_per_ep,
        max_consec_fail_before_restart=args.max_consec_fail,
        max_restarts=args.max_restarts,
        max_total_seconds=args.max_total_seconds,
        max_stall_seconds=args.max_stall_seconds,
        config_source='cli' if any([
            args.max_retry_per_ep != 3, args.max_consec_fail != 4,
            args.max_restarts != 5, args.max_total_seconds != 3600,
            args.max_stall_seconds != 180,
        ]) else 'default',
    )

    ctx = AgentContext(
        drama_name=args.name,
        series_id=args.series_id or None,
        total=args.total or None,
        out_dir=args.out,
        drama_dir=args.out / args.name,
        token=uuid.uuid4().hex,
        cb=cb,
    )
    ctx.drama_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"[agent] start 《{ctx.drama_name}》 series_id={ctx.series_id} "
                f"total={ctx.total} token={ctx.token[:8]}")
    logger.info(f"[agent] cb: max_retry_per_ep={cb.max_retry_per_ep} "
                f"max_consec_fail={cb.max_consec_fail_before_restart} "
                f"max_restarts={cb.max_restarts} "
                f"max_total={cb.max_total_seconds}s stall={cb.max_stall_seconds}s "
                f"source={cb.config_source}")

    try:
        return run_fsm(ctx)
    except KeyboardInterrupt:
        logger.warning("[agent] user interrupt")
        if ctx.v5_proc is not None:
            safe_kill_subprocess_tree(ctx.v5_proc, timeout=3.0)
        _finalize(ctx)
        return 4


if __name__ == '__main__':
    sys.exit(main())
