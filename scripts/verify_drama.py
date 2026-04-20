"""verify_drama.py — 单部剧下载质量验证.

判定策略:
  A. 机械强校验 (FAIL 直接判定串剧/串集)
     1. 文件数 ≥ expected_total (默认读 manifest 推断)
     2. manifest 每行 series_id 相等 (且 == expected_series_id 若给)
     3. manifest 每行 ep == 文件名集号 (B0 idx 校验)
     4. hash 唯一 (调 find_crossed_episodes 脚本)
     5. vid 无重复

  B. 抽帧 (首/中/末) — 人眼/LLM 检查
     - ep1 / ep(total//2) / ep_total 各抽 5s/30s/60s 3 帧
     - 存到 videos/<drama>/verify/epN_tT.png

  C. 报告 verify_report.json
     verdict:
       PASS          — 所有机械校验通过
       FLAG_VISUAL   — 机械通过但建议看帧 (首次验证剧时)
       FAIL          — 机械失败, 需要重下

用法:
    python scripts/verify_drama.py -n "开局一条蛇，无限进化"
    python scripts/verify_drama.py -n "剧名" -t 83 --series-id 7622955207885851672
    python scripts/verify_drama.py -n "剧名" --skip-hash  # 跳过 find_crossed (慢)
"""
import sys, os, json, re, argparse, subprocess
from pathlib import Path
from collections import Counter

DEFAULT_OUT_DIR = Path('videos')
FFMPEG = Path(__file__).parent.parent
# 用 imageio-ffmpeg 打包的 ffmpeg (跨平台, CLAUDE.md 提到过)
try:
    import imageio_ffmpeg
    FFMPEG_EXE = imageio_ffmpeg.get_ffmpeg_exe()
except ImportError:
    FFMPEG_EXE = 'ffmpeg'


EP_FILENAME_RE = re.compile(r'episode_(\d{3})_[0-9a-f]{8}\.mp4$')


def read_manifest(mfile: Path) -> list[dict]:
    """读 session_manifest.jsonl, 跳过末行半写."""
    if not mfile.exists():
        return []
    rows = []
    with mfile.open('r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def list_ep_files(drama_dir: Path) -> dict[int, Path]:
    """扫描 episode_NNN_*.mp4 → {ep: Path}."""
    result = {}
    for f in drama_dir.glob('episode_*.mp4'):
        m = EP_FILENAME_RE.match(f.name)
        if m:
            result[int(m.group(1))] = f
    return result


def mechanical_checks(drama_dir: Path, manifest: list[dict],
                      ep_files: dict[int, Path],
                      expected_total: int | None,
                      expected_sid: str | None) -> dict:
    """执行机械强校验. 返回 {check_name: {'pass': bool, 'detail': str}}."""
    checks = {}

    # 1. 文件数 >= expected_total
    actual_total = len(ep_files)
    if expected_total is not None:
        checks['file_count'] = {
            'pass': actual_total == expected_total,
            'detail': f'actual={actual_total} expected={expected_total}',
            'actual': actual_total, 'expected': expected_total,
        }
    else:
        checks['file_count'] = {
            'pass': True,
            'detail': f'actual={actual_total} (no expected)',
            'actual': actual_total,
        }

    # 2. series_id 一致性
    sids = Counter(r.get('series_id', '') for r in manifest if r.get('series_id'))
    unique_sids = list(sids.keys())
    if len(unique_sids) == 0:
        checks['series_id_consistent'] = {
            'pass': False, 'detail': 'manifest 无 series_id 字段',
        }
    elif len(unique_sids) > 1:
        checks['series_id_consistent'] = {
            'pass': False,
            'detail': f'多 series_id (串剧!): {dict(sids)}',
            'all_sids': dict(sids),
        }
    else:
        detail = f'all={unique_sids[0]}'
        if expected_sid and unique_sids[0] != expected_sid:
            checks['series_id_consistent'] = {
                'pass': False,
                'detail': f'sid 不匹配目标: actual={unique_sids[0]} expected={expected_sid}',
                'actual_sid': unique_sids[0], 'expected_sid': expected_sid,
            }
        else:
            checks['series_id_consistent'] = {'pass': True, 'detail': detail}

    # 3. manifest ep 字段 == 文件名集号 (B0 idx 强校验)
    mismatches = []
    for r in manifest:
        ep_manifest = r.get('ep')
        fname = r.get('file', '')
        m = EP_FILENAME_RE.match(fname)
        if not m:
            continue
        ep_filename = int(m.group(1))
        if ep_manifest != ep_filename:
            mismatches.append(f'manifest_ep={ep_manifest} file={fname}')
    checks['ep_idx_match'] = {
        'pass': len(mismatches) == 0,
        'detail': f'{len(mismatches)} 条 manifest.ep 与文件名不符'
                  + (f': {mismatches[:5]}' if mismatches else ''),
        'mismatches': mismatches,
    }

    # 4. vid 唯一 (manifest 内)
    vids = [r.get('vid', '') for r in manifest if r.get('vid')]
    vid_dupes = {v: c for v, c in Counter(vids).items() if c > 1}
    checks['vid_unique'] = {
        'pass': len(vid_dupes) == 0,
        'detail': f'{len(vid_dupes)} vid 重复' + (f': {vid_dupes}' if vid_dupes else ''),
    }

    # 5. biz_vid 唯一
    bids = [r.get('biz_vid', '') for r in manifest if r.get('biz_vid')]
    bid_dupes = {v: c for v, c in Counter(bids).items() if c > 1}
    checks['biz_vid_unique'] = {
        'pass': len(bid_dupes) == 0,
        'detail': f'{len(bid_dupes)} biz_vid 重复'
                  + (f': {bid_dupes}' if bid_dupes else ''),
    }

    return checks


def run_find_crossed(drama: str, drama_dir: Path) -> dict:
    """调 find_crossed_episodes.py, 返回解析结果."""
    script = Path(__file__).parent / 'find_crossed_episodes.py'
    try:
        r = subprocess.run(
            [sys.executable, str(script), '--drama', drama, '--out', 'videos'],
            capture_output=True, text=True, timeout=300,
        )
    except subprocess.TimeoutExpired:
        return {'pass': False, 'detail': 'find_crossed 超时 300s'}
    # find_crossed 写 cross_episodes_report.json 到 drama_dir
    report_file = drama_dir / 'cross_episodes_report.json'
    if not report_file.exists():
        return {'pass': False,
                'detail': 'cross_episodes_report.json 未生成',
                'stderr': (r.stderr or '')[:500]}
    try:
        report = json.loads(report_file.read_text(encoding='utf-8'))
    except Exception as e:
        return {'pass': False, 'detail': f'report 解析失败: {e}'}
    # find_crossed 报告结构: 嫌疑 = 同 hash 多 ep
    suspects = report.get('suspects', [])
    return {
        'pass': len(suspects) == 0,
        'detail': f'{len(suspects)} 个 hash 冲突组'
                  + (f': {suspects[:3]}' if suspects else ''),
        'suspect_count': len(suspects),
    }


def sample_frames(drama_dir: Path, ep_files: dict[int, Path],
                  total: int) -> dict:
    """抽首/中/末 3 集 × 5s/30s/60s 帧, 存 verify/."""
    if total < 1:
        return {'sampled': [], 'error': 'total < 1'}
    verify_dir = drama_dir / 'verify'
    verify_dir.mkdir(exist_ok=True)
    targets = [1, max(1, total // 2), total]
    # 去重保持顺序
    seen = set()
    targets = [e for e in targets if not (e in seen or seen.add(e))]

    results = []
    for ep in targets:
        if ep not in ep_files:
            results.append({'ep': ep, 'error': 'file missing'})
            continue
        src = ep_files[ep]
        frames = []
        for t in (5, 30, 60):
            out = verify_dir / f'ep{ep}_t{t}.png'
            try:
                r = subprocess.run(
                    [FFMPEG_EXE, '-y', '-ss', str(t), '-i', str(src),
                     '-vframes', '1', '-vf', 'scale=640:-1', str(out)],
                    capture_output=True, timeout=30,
                )
                if r.returncode == 0 and out.exists():
                    frames.append({'t': t, 'png': str(out)})
                else:
                    frames.append({'t': t, 'error': 'ffmpeg failed'})
            except subprocess.TimeoutExpired:
                frames.append({'t': t, 'error': 'timeout'})
        results.append({'ep': ep, 'src': src.name, 'frames': frames})
    return {'sampled': results, 'dir': str(verify_dir)}


def verdict_of(checks: dict, crossed: dict | None) -> str:
    hard_checks = ['file_count', 'series_id_consistent', 'ep_idx_match',
                   'vid_unique', 'biz_vid_unique']
    if any(not checks.get(k, {}).get('pass', False) for k in hard_checks):
        return 'FAIL'
    if crossed and not crossed.get('pass', True):
        return 'FAIL'
    return 'PASS_MECHANICAL'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('-n', '--name', required=True, help='剧名')
    ap.add_argument('-t', '--total', type=int, default=0,
                    help='期望总集数 (0=不校验, 从 manifest 推断)')
    ap.add_argument('--series-id', type=str, default='',
                    help='期望 series_id (强校验)')
    ap.add_argument('--out', type=Path, default=DEFAULT_OUT_DIR)
    ap.add_argument('--skip-hash', action='store_true',
                    help='跳过 find_crossed_episodes (hash 扫描慢)')
    ap.add_argument('--skip-frames', action='store_true',
                    help='跳过抽帧')
    args = ap.parse_args()

    drama_dir = args.out / args.name
    if not drama_dir.is_dir():
        print(f'ERR: {drama_dir} 不存在', file=sys.stderr)
        return 2

    print(f'=== verify 《{args.name}》 ===')
    print(f'  dir: {drama_dir}')

    manifest = read_manifest(drama_dir / 'session_manifest.jsonl')
    ep_files = list_ep_files(drama_dir)
    print(f'  manifest 行数: {len(manifest)}  mp4 文件: {len(ep_files)}')

    # 机械校验
    expected_total = args.total if args.total > 0 else None
    if expected_total is None and manifest:
        expected_total = max(r.get('ep', 0) for r in manifest)
        print(f'  从 manifest 推断 expected_total={expected_total}')

    checks = mechanical_checks(
        drama_dir, manifest, ep_files, expected_total,
        args.series_id or None,
    )
    for k, v in checks.items():
        flag = '[OK]' if v.get('pass') else '[FAIL]'
        print(f'  {flag} {k}: {v["detail"]}')

    # hash 扫描
    crossed = None
    if not args.skip_hash and len(ep_files) > 0:
        print('  运行 find_crossed_episodes (hash 扫描)...')
        crossed = run_find_crossed(args.name, drama_dir)
        flag = '[OK]' if crossed.get('pass') else '[FAIL]'
        print(f'  {flag} hash_unique: {crossed.get("detail")}')

    # 抽帧
    sampling = None
    if not args.skip_frames and expected_total and expected_total > 0:
        print('  抽样帧 (首/中/末)...')
        sampling = sample_frames(drama_dir, ep_files, expected_total)
        for s in sampling.get('sampled', []):
            ep = s.get('ep')
            frames = s.get('frames', [])
            ok = sum(1 for f in frames if 'png' in f)
            print(f'    ep{ep}: {ok}/{len(frames)} frames 抽取成功')

    verdict = verdict_of(checks, crossed)
    print(f'=== VERDICT: {verdict} ===')
    if verdict == 'PASS_MECHANICAL' and sampling:
        print('建议: 打开 verify/*.png 人眼看首/中/末 3 集, 确认剧风一致 + 剧情递进')

    report = {
        'drama': args.name,
        'dir': str(drama_dir),
        'verdict': verdict,
        'expected_total': expected_total,
        'manifest_rows': len(manifest),
        'mp4_files': len(ep_files),
        'mechanical': checks,
        'crossed': crossed,
        'sampling': sampling,
    }
    report_path = drama_dir / 'verify_report.json'
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8'
    )
    print(f'  report: {report_path}')
    return 0 if verdict == 'PASS_MECHANICAL' else 1


if __name__ == '__main__':
    sys.exit(main())
