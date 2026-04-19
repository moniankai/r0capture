#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
remap_episodes — 根据 probe-bind (pos walk) 输出, 重建 "kid → 真实剧情集数"
映射, 重命名错位的 mp4, 重建 manifest, 并列出需要续下的 ep.

背景 (详见 v5 commit "fix(v5): CAP/BIND vid 对齐"):
  旧版 wait_cap_for_seq 缺少 vid 校验 + 有 fallback, 导致 manifest 里的 ep→vid
  (来自 BIND, 正确) 和磁盘 mp4 里的 kid (来自 CAP, 可能错位) 不对齐.
  新代码已封堵, 但历史数据仍错位. 本脚本通过 "对每 pos 跑 probe" 建立
  {kid → real_ep} 映射, 然后修 manifest + 改文件名.

输入:
  --drama  剧名 (用来定位 videos/<drama>/)
  --probe  probe jsonl (hongguo_v5 probe-bind stdout, 含 probe_ep_ok 事件)
  --dry-run  只打 plan, 不动文件

流程:
  1. 解析 probe: real_ep → {vid, kid8, title}
  2. 扫磁盘: kid8 → filepath
  3. 读老 manifest: old_ep → {vid, kid8}
  4. 建立 rename plan (冲突优先 bytes 大的)
  5. apply (.tmp swap 保证原子性) + 重建 manifest.new
  6. 报告缺失的 real_ep 列表

退出码: 0 = ok, 1 = 有错位已修, 2 = fatal.
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ProbeRec:
    ep: int
    vid: str
    kid8: str    # manifest/文件名里保留 kid 前 8 字符 (小写)
    title: str


@dataclass
class DiskMp4:
    path: Path
    old_ep: int   # 从文件名 episode_XXX_ 解析
    kid8: str     # 从文件名 _<kid8>.mp4 解析, 小写


@dataclass
class Plan:
    total_expected: int
    probe_count: int
    probe_failed_eps: list[int]
    renames: list[tuple[Path, Path]] = field(default_factory=list)  # (old, new)
    orphans: list[Path] = field(default_factory=list)               # 无映射的 mp4
    conflicts: list[tuple[int, list[Path]]] = field(default_factory=list)
    new_manifest: list[dict] = field(default_factory=list)
    missing_real_eps: list[int] = field(default_factory=list)


def parse_probe(jsonl_path: Path) -> tuple[dict[int, ProbeRec], list[int]]:
    probes: dict[int, ProbeRec] = {}
    failed: list[int] = []
    for line in jsonl_path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or not line.startswith('{'):
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        t = rec.get('type')
        # 同时支持 probe-bind (probe_ep_ok) 和 walk-only (walk_ep_ok) 两种来源.
        if t in ('probe_ep_ok', 'walk_ep_ok'):
            ep = rec.get('ep')
            kid = (rec.get('kid') or '').lower()
            if not ep or not kid:
                continue
            probes[int(ep)] = ProbeRec(
                ep=int(ep), vid=rec.get('vid') or '',
                kid8=kid[:8], title=rec.get('title') or '',
            )
        elif t in ('probe_ep_fail', 'ep_fail'):
            ep = rec.get('ep')
            if ep:
                failed.append(int(ep))
    return probes, failed


def scan_disk_mp4(drama_dir: Path) -> list[DiskMp4]:
    """扫 videos/<drama>/episode_XXX_<kid8>.mp4"""
    out: list[DiskMp4] = []
    for p in sorted(drama_dir.glob('episode_*.mp4')):
        name = p.stem  # episode_001_aabbccdd
        parts = name.split('_')
        if len(parts) < 3:
            continue
        try:
            old_ep = int(parts[1])
        except ValueError:
            continue
        kid8 = parts[2].lower()
        out.append(DiskMp4(path=p, old_ep=old_ep, kid8=kid8))
    return out


def read_manifest(drama_dir: Path) -> list[dict]:
    mfile = drama_dir / 'session_manifest.jsonl'
    if not mfile.exists():
        return []
    recs = []
    for line in mfile.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            recs.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return recs


def build_plan(
    probes: dict[int, ProbeRec],
    probe_failed: list[int],
    disk: list[DiskMp4],
    old_manifest: list[dict],
    total: int,
) -> Plan:
    # kid8 → real_ep (probe 反向索引)
    kid_to_real_ep: dict[str, int] = {p.kid8: p.ep for p in probes.values()}

    # real_ep → 候选磁盘文件
    real_ep_candidates: dict[int, list[DiskMp4]] = {}
    orphans: list[Path] = []
    for m in disk:
        re_ep = kid_to_real_ep.get(m.kid8)
        if re_ep is None:
            orphans.append(m.path)
        else:
            real_ep_candidates.setdefault(re_ep, []).append(m)

    # 冲突处理 (一集多候选) + 重命名 plan
    renames: list[tuple[Path, Path]] = []
    conflicts: list[tuple[int, list[Path]]] = []
    drama_dir = disk[0].path.parent if disk else Path('.')
    # 为新文件名保留老 manifest 里对应 ep 的 kid8 (用 probe.kid8)
    # 真实规则: 新 mp4 命名 = episode_<real_ep:03d>_<kid8>.mp4
    for re_ep, cands in real_ep_candidates.items():
        # 若多个候选, 按文件大小排序优先 (大者为真, 老的或坏的丢 orphan)
        if len(cands) > 1:
            cands.sort(key=lambda m: m.path.stat().st_size, reverse=True)
            conflicts.append((re_ep, [c.path for c in cands]))
            # 第一个作正主, 其余改名为 .orphan 后缀 + 加入 orphans 列表
            for extra in cands[1:]:
                orphans.append(extra.path)
        chosen = cands[0]
        new_name = f"episode_{re_ep:03d}_{chosen.kid8}.mp4"
        new_path = drama_dir / new_name
        if chosen.path.resolve() != new_path.resolve():
            renames.append((chosen.path, new_path))

    # 重建 manifest: 以 probes 为真值, 只留磁盘上有的 real_ep
    real_eps_with_mp4 = set(real_ep_candidates.keys())
    new_manifest = []
    for re_ep in sorted(real_eps_with_mp4):
        p = probes[re_ep]
        # 从老 manifest 里借 series_id + bytes (若能)
        series_id = ''
        for old in old_manifest:
            if old.get('series_id'):
                series_id = old['series_id']
                break
        # bytes 用当前磁盘文件大小 (重命名前后不变)
        chosen = real_ep_candidates[re_ep][0]
        try:
            bts = chosen.path.stat().st_size
        except OSError:
            bts = 0
        new_manifest.append({
            'ep': re_ep,
            'vid': p.vid,
            'kid': p.kid8 + '0' * 24,  # 占位 (manifest 原本存完整 kid, 这里仅有 8 位)
            'kid8': p.kid8,
            'title': p.title,
            'series_id': series_id,
            'bytes': bts,
            'ts': time.time(),
            'source': 'remap_episodes',
        })

    # 缺失 ep = [1..total] - real_eps_with_mp4, 且也包含 probe 失败的
    missing = [e for e in range(1, total + 1) if e not in real_eps_with_mp4]

    return Plan(
        total_expected=total,
        probe_count=len(probes),
        probe_failed_eps=sorted(probe_failed),
        renames=renames,
        orphans=orphans,
        conflicts=conflicts,
        new_manifest=new_manifest,
        missing_real_eps=missing,
    )


def print_plan(plan: Plan) -> None:
    print(f"=== REMAP PLAN ===")
    print(f"probe: ok={plan.probe_count}, failed={len(plan.probe_failed_eps)} "
          f"{plan.probe_failed_eps[:10]}{'...' if len(plan.probe_failed_eps) > 10 else ''}")
    print(f"renames: {len(plan.renames)}")
    for old, new in plan.renames[:20]:
        print(f"  {old.name} -> {new.name}")
    if len(plan.renames) > 20:
        print(f"  ... 还有 {len(plan.renames) - 20} 条")
    print(f"orphans (无映射/冲突): {len(plan.orphans)}")
    for p in plan.orphans[:20]:
        print(f"  {p.name}")
    print(f"conflicts (一集多候选): {len(plan.conflicts)}")
    for ep, paths in plan.conflicts[:10]:
        print(f"  ep{ep}: {[p.name for p in paths]}")
    print(f"missing real eps ({len(plan.missing_real_eps)}): "
          f"{plan.missing_real_eps[:20]}{'...' if len(plan.missing_real_eps) > 20 else ''}")


def apply_plan(drama_dir: Path, plan: Plan) -> dict:
    """两阶段: 1. 所有 rename 到 .remap.tmp 路径 2. 再 rename 到最终路径.
    orphan mp4 加 .orphan 后缀保留 (不删, 方便审计)."""
    stats = {'renamed': 0, 'orphaned': 0, 'manifest_rewritten': False, 'errors': []}
    # 阶段 1: 所有源文件先 rename 到 _remap_tmp_<i> (避开目标冲突)
    staged: list[tuple[Path, Path]] = []  # (tmp, final)
    for i, (old, final) in enumerate(plan.renames):
        tmp = drama_dir / f'__remap_{i:03d}.tmp'
        try:
            old.rename(tmp)
            staged.append((tmp, final))
        except OSError as e:
            stats['errors'].append(f'stage rename fail {old.name}: {e}')

    # 阶段 2: tmp → final
    for tmp, final in staged:
        try:
            # 冲突: 若 final 已存在, 给它让路先 (加 .before_remap)
            if final.exists():
                final.rename(final.with_suffix(final.suffix + '.before_remap'))
            tmp.rename(final)
            stats['renamed'] += 1
        except OSError as e:
            stats['errors'].append(f'final rename fail {tmp.name} -> {final.name}: {e}')

    # orphans
    for p in plan.orphans:
        try:
            if p.exists():
                p.rename(p.with_suffix(p.suffix + '.orphan'))
                stats['orphaned'] += 1
        except OSError as e:
            stats['errors'].append(f'orphan rename fail {p.name}: {e}')

    # 重写 manifest (原子)
    mfile = drama_dir / 'session_manifest.jsonl'
    tmpm = drama_dir / 'session_manifest.jsonl.remap_tmp'
    try:
        # 备份老 manifest
        if mfile.exists():
            mfile.rename(drama_dir / f'session_manifest.jsonl.bak.{int(time.time())}')
        with tmpm.open('w', encoding='utf-8') as f:
            for rec in plan.new_manifest:
                f.write(json.dumps(rec, ensure_ascii=False) + '\n')
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        tmpm.rename(mfile)
        stats['manifest_rewritten'] = True
    except OSError as e:
        stats['errors'].append(f'manifest rewrite fail: {e}')

    return stats


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--drama', required=True, help='剧名')
    ap.add_argument('--probe', required=True, type=Path,
                    help='probe-bind stdout jsonl')
    ap.add_argument('--out', default='videos', type=Path,
                    help='videos 根目录 (默认 videos)')
    ap.add_argument('--total', type=int, default=0,
                    help='总集数, 用于算缺失 ep; 0 = 取 probe 最大 ep')
    ap.add_argument('--dry-run', action='store_true',
                    help='只输出 plan, 不动任何文件')
    args = ap.parse_args()

    drama_dir = args.out / args.drama
    if not drama_dir.exists():
        print(f"FATAL: {drama_dir} 不存在")
        return 2

    probes, failed = parse_probe(args.probe)
    if not probes:
        print(f"FATAL: probe 没有任何 probe_ep_ok 事件")
        return 2

    disk = scan_disk_mp4(drama_dir)
    if not disk:
        print(f"FATAL: {drama_dir} 没 mp4")
        return 2

    total = args.total or max(probes.keys())
    old_manifest = read_manifest(drama_dir)

    plan = build_plan(probes, failed, disk, old_manifest, total)
    print_plan(plan)

    # 把 plan 写到 drama_dir/remap_report.json 方便审计
    report_path = drama_dir / 'remap_report.json'
    report = {
        'ts': time.time(),
        'total_expected': plan.total_expected,
        'probe_count': plan.probe_count,
        'probe_failed_eps': plan.probe_failed_eps,
        'renames': [[str(old), str(new)] for old, new in plan.renames],
        'orphans': [str(p) for p in plan.orphans],
        'conflicts': [[ep, [str(p) for p in paths]] for ep, paths in plan.conflicts],
        'missing_real_eps': plan.missing_real_eps,
        'dry_run': args.dry_run,
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                            encoding='utf-8')
    print(f"\n[report] {report_path}")

    if args.dry_run:
        print("[dry-run] 未动任何文件")
        return 0

    stats = apply_plan(drama_dir, plan)
    print(f"\n=== APPLIED ===")
    print(f"renamed: {stats['renamed']}  orphaned: {stats['orphaned']}  "
          f"manifest: {'OK' if stats['manifest_rewritten'] else 'FAIL'}")
    for e in stats['errors']:
        print(f"  ERR: {e}")

    return 0 if not stats['errors'] else 1


if __name__ == '__main__':
    sys.exit(main())
