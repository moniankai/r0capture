"""hongguo_batch_lean.py — lean 架构批量下载编排器.

消费 dramas.json (or flat list) → 串行调度 spawn_nav + v5_lean + verify_drama.

与老 hongguo_batch.py 的区别:
  - 不走 hongguo_agent (Agent 兼容老架构的 RPC+BIND+CAP)
  - 调用链改为 lean 三段: spawn_nav → v5_lean → verify_drama
  - 只接受已知 series_id (input 必须含), 不做运行时 resolve
  - 串剧由 v5_lean 运行时 sid 校验 + verify_drama 事后检测双重兜底

用法:
    # 从 metadata 采集产出的 dramas.json 跑
    python scripts/hongguo_batch_lean.py --input .planning/rankings/dramas.json

    # 从扁平 list 跑 (老格式兼容)
    python scripts/hongguo_batch_lean.py --input list.json

    # POC 只跑 2 部
    python scripts/hongguo_batch_lean.py --input dramas.json --max-dramas 2

输入格式自动识别:
  - dramas.json (metadata session 产出): {"series_id": {"name": ..., "total": ..., ...}}
  - flat list (老格式): [{"name": ..., "series_id": ..., "total": ...}, ...]
"""
from __future__ import annotations
import sys, os, json, time, argparse, subprocess, tempfile
from pathlib import Path
from dataclasses import dataclass, asdict
from datetime import datetime

PROJECT_ROOT = Path(__file__).parent.parent
APP_PACKAGE = 'com.phoenix.read'

# 子脚本路径
SPAWN_NAV = PROJECT_ROOT / 'scripts' / 'spawn_nav.py'
V5_LEAN = PROJECT_ROOT / 'scripts' / 'v5_lean.py'
VERIFY = PROJECT_ROOT / 'scripts' / 'verify_drama.py'

# 默认超时
DEFAULT_SPAWN_TIMEOUT = 60
DEFAULT_DRAMA_TIMEOUT = 1800  # 30 分钟单剧上限
DEFAULT_VERIFY_TIMEOUT = 300


@dataclass
class DramaTask:
    name: str
    series_id: str
    total: int
    is_locked: bool = False
    source_ranks: list[str] | None = None


# ============ 输入解析 ============

def read_input_list(path: Path) -> list[DramaTask]:
    """自动识别 dramas.json (dict by sid) 或 flat list, 返回标准化 DramaTask 列表."""
    data = json.loads(path.read_text(encoding='utf-8'))
    tasks: list[DramaTask] = []

    if isinstance(data, dict):
        # dramas.json schema: key 是 series_id
        for sid, d in data.items():
            if not isinstance(d, dict):
                continue
            name = d.get('name') or ''
            total = d.get('total') or 0
            if not name or total <= 0:
                continue
            tasks.append(DramaTask(
                name=name,
                series_id=d.get('series_id') or sid,
                total=int(total),
                is_locked=bool(d.get('is_locked', False)),
                source_ranks=d.get('source_ranks'),
            ))
    elif isinstance(data, list):
        # flat list: [{name, series_id, total}]
        for d in data:
            if not isinstance(d, dict):
                continue
            name = d.get('name') or ''
            sid = d.get('series_id') or ''
            total = d.get('total') or 0
            if not name or not sid or total <= 0:
                continue
            tasks.append(DramaTask(
                name=name,
                series_id=sid,
                total=int(total),
                is_locked=bool(d.get('is_locked', False)),
            ))
    else:
        raise ValueError(f'无法识别 input 格式: {type(data).__name__}')

    return tasks


def filter_tasks(tasks: list[DramaTask],
                 skip_locked: bool = True,
                 max_total: int | None = None,
                 max_dramas: int | None = None) -> list[DramaTask]:
    """按策略过滤 tasks."""
    out = []
    for t in tasks:
        if skip_locked and t.is_locked:
            continue
        if max_total is not None and t.total > max_total:
            continue
        out.append(t)
        if max_dramas is not None and len(out) >= max_dramas:
            break
    return out


# ============ State I/O ============

def load_state(path: Path) -> dict:
    if not path.exists():
        return {
            'session_id': f'batch_{datetime.now():%Y%m%d_%H%M%S}',
            'started_at': datetime.now().isoformat(timespec='seconds'),
            'dramas': {},
        }
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return {
            'session_id': f'batch_{datetime.now():%Y%m%d_%H%M%S}',
            'started_at': datetime.now().isoformat(timespec='seconds'),
            'dramas': {},
        }


def save_state(path: Path, state: dict) -> None:
    """原子写: tmp + os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=str(path.parent), prefix='.batch_state.', suffix='.tmp',
    )
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, str(path))
    except Exception:
        try: os.unlink(tmp_path)
        except OSError: pass
        raise


def should_skip(state: dict, task: DramaTask) -> bool:
    """state 里已 done 则跳过. 'failed' 的不跳 (允许重试)."""
    entry = state.get('dramas', {}).get(task.series_id)
    if not entry:
        return False
    return entry.get('status') == 'done'


def is_complete(out_root: Path, task: DramaTask) -> bool:
    """检查 videos/<name>/ 已完整下 total 集 (mp4 数 + manifest 行数都达标)."""
    drama_dir = out_root / task.name
    if not drama_dir.is_dir():
        return False
    mp4_count = sum(1 for _ in drama_dir.glob('episode_*.mp4'))
    if mp4_count < task.total:
        return False
    manifest = drama_dir / 'session_manifest.jsonl'
    if not manifest.exists():
        return False
    manifest_eps: set[int] = set()
    try:
        with manifest.open('r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    if rec.get('ep'):
                        manifest_eps.add(int(rec['ep']))
                except (json.JSONDecodeError, ValueError):
                    continue
    except OSError:
        return False
    return len(manifest_eps) >= task.total


def mark_state(state: dict, task: DramaTask, status: str, **extra) -> None:
    entry = state.setdefault('dramas', {}).setdefault(task.series_id, {
        'name': task.name,
        'series_id': task.series_id,
        'total': task.total,
        'attempts': 0,
    })
    entry['status'] = status
    entry['updated_at'] = datetime.now().isoformat(timespec='seconds')
    entry['attempts'] = int(entry.get('attempts', 0)) + (1 if status == 'running' else 0)
    for k, v in extra.items():
        entry[k] = v


# ============ 子脚本调度 ============

def run_subprocess(argv: list[str], timeout: float) -> tuple[int, str]:
    """跑子脚本, 返回 (returncode, tail stderr)."""
    env = {**os.environ, 'MSYS_NO_PATHCONV': '1', 'PYTHONIOENCODING': 'utf-8'}
    try:
        r = subprocess.run(
            [sys.executable] + argv,
            capture_output=True, text=True, env=env,
            timeout=timeout, encoding='utf-8', errors='replace',
        )
    except subprocess.TimeoutExpired as e:
        return -1, f'timeout after {timeout}s'
    except Exception as e:
        return -1, f'exception: {e}'
    tail = (r.stderr or r.stdout or '').splitlines()[-5:]
    return r.returncode, ' | '.join(tail)[:500]


def reboot_device(wait: float = 60.0) -> bool:
    env = {**os.environ, 'MSYS_NO_PATHCONV': '1'}
    try:
        subprocess.run(['adb', 'reboot'], capture_output=True, env=env, timeout=10)
    except Exception:
        return False
    time.sleep(wait)
    try:
        r = subprocess.run(
            ['adb', 'wait-for-device'], capture_output=True, env=env, timeout=180,
        )
        return r.returncode == 0
    except Exception:
        return False


# ============ 单剧流水线 ============

def run_one_drama(task: DramaTask, out_root: Path,
                  timeouts: dict, skip_verify: bool = False) -> dict:
    """返回 {status, verdict?, error?} dict."""
    # force-stop App 清状态
    env = {**os.environ, 'MSYS_NO_PATHCONV': '1'}
    try:
        subprocess.run(['adb', 'shell', 'am', 'force-stop', APP_PACKAGE],
                       capture_output=True, env=env, timeout=10)
        time.sleep(2)
    except Exception:
        pass

    # 1. spawn_nav
    rc, err = run_subprocess(
        [str(SPAWN_NAV), '--series-id', task.series_id, '--pos', '0'],
        timeout=timeouts['spawn'],
    )
    if rc != 0:
        return {'status': 'failed', 'stage': 'spawn_nav', 'rc': rc, 'error': err}

    # 2. v5_lean
    rc, err = run_subprocess(
        [str(V5_LEAN),
         '-n', task.name, '--series-id', task.series_id,
         '-t', str(task.total), '-s', '1', '-e', str(task.total)],
        timeout=timeouts['drama'],
    )
    if rc != 0:
        return {'status': 'failed', 'stage': 'v5_lean', 'rc': rc, 'error': err}

    # 3. verify_drama
    verdict = 'SKIPPED'
    if not skip_verify:
        rc, err = run_subprocess(
            [str(VERIFY),
             '-n', task.name, '-t', str(task.total),
             '--series-id', task.series_id],
            timeout=timeouts['verify'],
        )
        # verify returncode: 0=PASS_MECHANICAL, 1=FAIL (目前只这两类)
        verdict = 'PASS_MECHANICAL' if rc == 0 else 'FAIL'

    return {'status': 'done', 'verdict': verdict}


# ============ 主循环 ============

def summarize_line(task: DramaTask, result: dict) -> str:
    verdict = result.get('verdict', '')
    stage = result.get('stage', '')
    extra = f' verdict={verdict}' if verdict else (f' stage={stage}' if stage else '')
    return f'[{result.get("status","?"):>6}] {task.name} ({task.total}集){extra}'


def read_report(report_path: Path) -> dict | None:
    if not report_path.exists():
        return None
    try:
        return json.loads(report_path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--input', required=True, type=Path,
                    help='dramas.json (dict by sid) 或 flat list')
    ap.add_argument('--state', type=Path, default=Path('.batch_state.json'))
    ap.add_argument('--out', type=Path, default=Path('videos'))
    ap.add_argument('--max-dramas', type=int, default=0,
                    help='限制跑的剧数 (0=不限)')
    ap.add_argument('--skip-locked', action='store_true', default=True,
                    help='跳过 is_locked=True 的剧')
    ap.add_argument('--max-total', type=int, default=0,
                    help='只下总集数 <= 此值的剧 (0=不限)')
    ap.add_argument('--reboot-every', type=int, default=0,
                    help='每 N 部剧 reboot 设备清状态 (0=不 reboot)')
    ap.add_argument('--per-drama-timeout', type=int, default=DEFAULT_DRAMA_TIMEOUT)
    ap.add_argument('--halt-on-fatal', action='store_true',
                    help='单剧失败即停 (默认 skip 继续)')
    ap.add_argument('--skip-verify', action='store_true',
                    help='跳过 verify_drama')
    args = ap.parse_args()

    if not args.input.exists():
        print(f'ERR: input {args.input} 不存在', file=sys.stderr)
        return 2

    # 解析 + 过滤
    tasks = read_input_list(args.input)
    tasks = filter_tasks(
        tasks,
        skip_locked=args.skip_locked,
        max_total=args.max_total or None,
        max_dramas=args.max_dramas or None,
    )
    print(f'=== batch_lean: 待跑 {len(tasks)} 部剧 ===')

    # load state
    state = load_state(args.state)
    state['input_file'] = str(args.input)
    save_state(args.state, state)

    timeouts = {
        'spawn': DEFAULT_SPAWN_TIMEOUT,
        'drama': args.per_drama_timeout,
        'verify': DEFAULT_VERIFY_TIMEOUT,
    }

    ok_count = fail_count = skip_count = 0
    for i, task in enumerate(tasks, 1):
        print(f'\n--- [{i}/{len(tasks)}] {task.name} (total={task.total}) ---')

        if should_skip(state, task):
            print(f'  state 标记 done, skip')
            skip_count += 1
            continue

        if is_complete(args.out, task):
            print(f'  已完整下载, 标记 done')
            mark_state(state, task, 'done', verdict='PRE_EXISTING')
            save_state(args.state, state)
            skip_count += 1
            continue

        mark_state(state, task, 'running')
        save_state(args.state, state)

        result = run_one_drama(task, args.out, timeouts,
                                skip_verify=args.skip_verify)
        mark_state(state, task, result['status'],
                   verdict=result.get('verdict'),
                   stage=result.get('stage'),
                   last_error=result.get('error'))
        save_state(args.state, state)

        print(f'  {summarize_line(task, result)}')
        if result['status'] == 'done':
            ok_count += 1
        else:
            fail_count += 1
            if args.halt_on_fatal:
                print('  --halt-on-fatal 生效, 停止批量')
                break

        # 每 N 部 reboot
        if args.reboot_every > 0 and i < len(tasks) and i % args.reboot_every == 0:
            print(f'  reboot 设备 (每 {args.reboot_every} 部)')
            reboot_device()

    print(f'\n=== 批量完成 ok={ok_count} fail={fail_count} skip={skip_count} ===')
    return 0 if fail_count == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
