#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
BatchAgent — 从 JSON list 批量下载多部红果短剧.

输入: [{"name": "...", "series_id": "...", "total": N}, ...]
行为:
  - 每部剧调 scripts/hongguo_agent.py
  - 记录进度到 .batch_state.json (支持中断 resume)
  - 每 N 部强制 adb reboot (绕设备累积疲劳)
  - 失败分类:
      DONE (全集)       → ok, 继续下一部
      DONE + missing>0  → partial, 记录后继续下一部
      ABORTED           → partial, 继续
      FATAL (cross_drama / subprocess 崩溃) → 可配置 halt
      TIMEOUT (单部超时)→ 当 partial 处理
  - 最终写 batch_report.json

用法:
  python scripts/hongguo_batch.py --input dramas.json
  python scripts/hongguo_batch.py --input dramas.json --resume
  python scripts/hongguo_batch.py --input dramas.json --per-drama-timeout 2400

输入示例 (dramas.json):
  [
    {"name": "我真不是大佬啊", "series_id": "7625840320642567192", "total": 88},
    {"name": "凡人仙葫", "series_id": "7617050216549583897", "total": 60}
  ]
"""
from __future__ import annotations
import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path

PY = os.environ.get("HONGGUO_PY", sys.executable)
AGENT = Path(__file__).parent / "hongguo_agent.py"
DEFAULT_VIDEOS = Path("videos")


@dataclass
class DramaTask:
    name: str
    series_id: str
    total: int = 0        # 0 = 让 Agent 动态检测


@dataclass
class DramaResult:
    name: str
    series_id: str
    state: str            # DONE | ABORTED | SKIPPED | FATAL | TIMEOUT | UNKNOWN
    downloaded: int = 0
    total: int = 0
    missing: list[int] = field(default_factory=list)
    elapsed_seconds: float = 0.0
    error: str = ""
    report_path: str = ""
    restarts: int = 0


def read_input_list(path: Path) -> list[DramaTask]:
    raw = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(raw, list):
        raise ValueError(f"input must be JSON array, got {type(raw).__name__}")
    out = []
    for i, d in enumerate(raw):
        if not isinstance(d, dict):
            raise ValueError(f"item {i}: expect object, got {type(d).__name__}")
        if not d.get('name') or not d.get('series_id'):
            raise ValueError(f"item {i} missing name/series_id: {d}")
        out.append(DramaTask(
            name=str(d['name']).strip(),
            series_id=str(d['series_id']).strip(),
            total=int(d.get('total', 0)),
        ))
    return out


def load_state(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding='utf-8'))
        except json.JSONDecodeError:
            return {}
    return {}


def save_state(path: Path, state: dict) -> None:
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2),
                    encoding='utf-8')
    try:
        os.replace(tmp, path)
    except OSError as e:
        print(f"[state] save failed: {e}")


def read_report(videos_dir: Path, drama: str) -> dict | None:
    p = videos_dir / drama / 'report.json'
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding='utf-8'))
    except json.JSONDecodeError:
        return None


def is_complete(report: dict) -> bool:
    """已 DONE 且无 missing."""
    if report.get('state') != 'DONE':
        return False
    if report.get('missing'):
        return False
    return (report.get('downloaded_count', 0) > 0 or
            len(report.get('downloaded', [])) > 0)


def adb_reboot_and_wait(wait_seconds: int = 120) -> bool:
    """adb reboot + 等设备回来."""
    print(f"[reboot] 发送 adb reboot, 等 {wait_seconds}s")
    env = {**os.environ, 'MSYS_NO_PATHCONV': '1'}
    try:
        subprocess.run(['adb', 'reboot'], capture_output=True, timeout=10, env=env)
    except (subprocess.TimeoutExpired, OSError) as e:
        print(f"[reboot] adb reboot failed: {e}")
        return False
    time.sleep(wait_seconds)
    # 等 adb 连回
    deadline = time.time() + 90
    while time.time() < deadline:
        try:
            r = subprocess.run(['adb', 'devices'], capture_output=True,
                                text=True, timeout=5, env=env)
            if r.returncode == 0:
                for line in r.stdout.splitlines():
                    parts = line.split()
                    if len(parts) >= 2 and parts[1] == 'device':
                        print(f"[reboot] 设备就绪: {parts[0]}")
                        return True
        except (subprocess.TimeoutExpired, OSError):
            pass
        time.sleep(3)
    print("[reboot] 设备回连 timeout")
    return False


def start_frida_server() -> bool:
    env = {**os.environ, 'MSYS_NO_PATHCONV': '1'}
    try:
        subprocess.run(
            ['adb', 'shell',
             "su -c 'nohup /data/local/tmp/frida-server >/dev/null 2>&1 &'"],
            capture_output=True, timeout=10, env=env)
        time.sleep(3)
        r = subprocess.run(['adb', 'shell', 'ps -A'],
                            capture_output=True, text=True, timeout=5, env=env)
        ok = 'frida-server' in r.stdout
        if ok:
            print("[frida] started")
        else:
            print("[frida] 启动失败 (ps 未见)")
        return ok
    except (subprocess.TimeoutExpired, OSError) as e:
        print(f"[frida] start failed: {e}")
        return False


def run_single_agent(task: DramaTask, videos_dir: Path,
                      per_drama_timeout: int,
                      max_restarts: int = 20) -> DramaResult:
    """调 scripts/hongguo_agent.py 下载单部剧.
    返回 DramaResult — 基于 Agent 写的 report.json.
    """
    cmd = [
        PY, '-u', str(AGENT),
        '-n', task.name,
        '--series-id', task.series_id,
        '--out', str(videos_dir),
        '--max-total-seconds', str(per_drama_timeout),
        '--max-restarts', str(max_restarts),
        '--max-consec-fail', '3',
    ]
    if task.total > 0:
        cmd += ['--total', str(task.total)]
    env = {
        **os.environ,
        'PYTHONUNBUFFERED': '1',
        'PYTHONIOENCODING': 'utf-8',
        'MSYS_NO_PATHCONV': '1',
    }
    start = time.time()
    try:
        proc = subprocess.run(
            cmd, env=env, timeout=per_drama_timeout + 180,
            capture_output=True, text=True, encoding='utf-8',
            errors='replace',
        )
        rc = proc.returncode
        tail = '\n'.join(proc.stdout.splitlines()[-5:])
    except subprocess.TimeoutExpired:
        return DramaResult(
            name=task.name, series_id=task.series_id,
            state='TIMEOUT', error='subprocess timeout',
            elapsed_seconds=time.time() - start,
        )
    elapsed = time.time() - start
    report = read_report(videos_dir, task.name)
    if report:
        downloaded = len(report.get('downloaded', []))
        return DramaResult(
            name=task.name, series_id=task.series_id,
            state=str(report.get('state', 'UNKNOWN')),
            downloaded=downloaded,
            total=int(report.get('total', task.total)),
            missing=list(report.get('missing', []) or []),
            elapsed_seconds=elapsed,
            restarts=int(report.get('restarts', 0) or 0),
            report_path=str(videos_dir / task.name / 'report.json'),
        )
    return DramaResult(
        name=task.name, series_id=task.series_id,
        state='FATAL' if rc != 0 else 'UNKNOWN',
        error=f'no report.json, rc={rc}, tail={tail[:200]}',
        elapsed_seconds=elapsed,
    )


def summarize_line(i: int, n: int, task: DramaTask, r: DramaResult) -> str:
    if r.state == 'DONE' and not r.missing:
        mark = 'OK '
    elif r.state == 'SKIPPED':
        mark = '- '
    else:
        mark = 'X '
    return (f"[{i:>3}/{n}] {mark}《{task.name}》 "
            f"{r.downloaded}/{r.total} "
            f"state={r.state} restarts={r.restarts} "
            f"elapsed={r.elapsed_seconds:.0f}s "
            f"{'missing=' + str(r.missing[:8]) if r.missing else ''}"
            f"{' err=' + r.error[:80] if r.error else ''}")


def main() -> int:
    ap = argparse.ArgumentParser(description='BatchAgent: 批量下载红果短剧')
    ap.add_argument('--input', required=True, type=Path,
                    help='输入 JSON 列表路径')
    ap.add_argument('--out', type=Path, default=DEFAULT_VIDEOS,
                    help='videos 输出根目录 (默认 videos/)')
    ap.add_argument('--state', type=Path, default=Path('.batch_state.json'),
                    help='进度状态文件 (用于 resume)')
    ap.add_argument('--report', type=Path, default=Path('batch_report.json'),
                    help='最终汇总报告路径')
    ap.add_argument('--per-drama-timeout', type=int, default=1800,
                    help='单部剧最大秒数 (默认 1800=30min)')
    ap.add_argument('--reboot-every', type=int, default=5,
                    help='每 N 部强制 adb reboot (0=不 reboot, 默认 5)')
    ap.add_argument('--fresh', action='store_true',
                    help='忽略已有 state, 从零开始 (仍 skip 磁盘已完成的剧)')
    ap.add_argument('--halt-on-fatal', action='store_true',
                    help='某部 FATAL/TIMEOUT 时停止整批 (默认继续)')
    args = ap.parse_args()

    tasks = read_input_list(args.input)
    print(f"[batch] loaded {len(tasks)} dramas from {args.input}")
    print(f"[batch] videos out → {args.out}")
    print(f"[batch] state → {args.state}")
    print(f"[batch] per_drama_timeout={args.per_drama_timeout}s "
          f"reboot_every={args.reboot_every}")

    state = {} if args.fresh else load_state(args.state)
    state.setdefault('results', {})
    state.setdefault('ts_start', time.time())
    state.setdefault('total_reboots', 0)

    consec_since_reboot = 0
    ok_count = skip_count = fail_count = 0

    for i, task in enumerate(tasks, 1):
        # 磁盘 Skip (已 DONE 且无 missing)
        existing = read_report(args.out, task.name)
        if existing and is_complete(existing):
            total = existing.get('total') or task.total
            downloaded = len(existing.get('downloaded', []))
            print(f"\n[{i:>3}/{len(tasks)}] - 《{task.name}》 "
                  f"已完成 {downloaded}/{total}, skip")
            state['results'][task.name] = {
                'name': task.name, 'series_id': task.series_id,
                'state': 'DONE', 'skipped_resume': True,
                'downloaded': downloaded, 'total': total, 'missing': [],
            }
            skip_count += 1
            save_state(args.state, state)
            continue

        # 设备自愈 reboot
        if args.reboot_every > 0 and consec_since_reboot >= args.reboot_every:
            print(f"\n[batch] 连续 {consec_since_reboot} 部, 强制 adb reboot")
            if adb_reboot_and_wait(wait_seconds=120):
                start_frida_server()
                state['total_reboots'] += 1
            consec_since_reboot = 0
            save_state(args.state, state)

        print(f"\n[{i:>3}/{len(tasks)}] 开始《{task.name}》 "
              f"series_id={task.series_id} total={task.total}")
        result = run_single_agent(task, args.out, args.per_drama_timeout)
        state['results'][task.name] = asdict(result)
        save_state(args.state, state)
        consec_since_reboot += 1

        print('  ' + summarize_line(i, len(tasks), task, result))

        if result.state == 'DONE' and not result.missing:
            ok_count += 1
        elif result.state in ('FATAL', 'TIMEOUT') and args.halt_on_fatal:
            print(f"[batch] halt on fatal: {result.state}")
            fail_count += 1
            break
        else:
            fail_count += 1

    # 最终报告
    final = {
        'input': str(args.input.resolve()),
        'total_dramas': len(tasks),
        'ok': ok_count,
        'skipped': skip_count,
        'failed': fail_count,
        'total_reboots': state.get('total_reboots', 0),
        'total_elapsed_seconds': time.time() - state.get('ts_start', time.time()),
        'ts_end': time.time(),
        'results': list(state['results'].values()),
    }
    args.report.write_text(
        json.dumps(final, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f"\n[batch] 完成: ok={ok_count} skipped={skip_count} "
          f"failed={fail_count} reboots={final['total_reboots']} "
          f"elapsed={final['total_elapsed_seconds']:.0f}s")
    print(f"[batch] report → {args.report}")
    return 0 if fail_count == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
