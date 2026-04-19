#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
find_crossed_episodes — 通过抽帧哈希对比, 找出 videos/<drama>/ 里**内容相同但
ep 编号不同**的 mp4 (即串集候选).

背景: 旧 hongguo_v5 的 wait_cap_for_seq fallback 会拿到前一集 / nav 阶段残留的
cap, 导致 manifest 说是 ep80 但 mp4 内容是 ep1. 用户肉眼只能看到 ep1/ep80 相同,
其他未知. 本脚本对所有 mp4 抽第 1.5 秒帧 downscale 到 32x18 灰度算 md5,
同 hash >= 2 个 ep 就是串集.

输出:
  videos/<drama>/cross_episodes_report.json
      - crossed_groups: [{hash, eps}, ...]
      - ep2hash: 所有 ep 的 hash
      - crossed_eps: 串集 ep 列表 (除保留外都要删除重下)

策略: 每组同 hash 的 eps 里, **保留 ep 号最小的** (大概率是对齐正确的 - App 的
播放列表 pos=0 首次 nav 直接进入, nav 阶段 BIND/CAP 最可靠). 其余的是串集,
要重下. 缺失的 ep 也会被 Agent --start auto 自动补.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

try:
    import imageio_ffmpeg
    FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
except ImportError:
    FFMPEG = 'ffmpeg'


EP_RE = re.compile(r'episode_(\d{3})_([0-9a-fA-F]{8})\.mp4$')


def parse_name(mp4_name: str) -> tuple[int, str] | None:
    m = EP_RE.match(mp4_name)
    if not m:
        return None
    return int(m.group(1)), m.group(2).lower()


def fingerprint(mp4: Path, at_sec: float = 1.5, w: int = 32, h: int = 18,
                timeout: float = 20.0) -> str | None:
    """抽 mp4 在 `at_sec` 秒的一帧, downscale 到 wxh 灰度, 返回 md5 hex."""
    cmd = [FFMPEG, '-ss', str(at_sec), '-i', str(mp4),
           '-frames:v', '1',
           '-vf', f'scale={w}:{h},format=gray',
           '-f', 'rawvideo',
           '-loglevel', 'error',
           '-']
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None
    expected_bytes = w * h
    if r.returncode != 0 or len(r.stdout) < expected_bytes:
        return None
    return hashlib.md5(r.stdout[:expected_bytes]).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--drama', required=True, help='剧名')
    ap.add_argument('--out', default='videos', type=Path,
                    help='videos 根目录')
    ap.add_argument('--at', type=float, default=1.5,
                    help='抽帧时间点 (秒). 默认 1.5s, 避开片头黑屏.')
    ap.add_argument('--second-pass-at', type=float, default=5.0,
                    help='若首次抽帧出现大量同 hash (可能整剧共享片头), 用该秒点重抽')
    args = ap.parse_args()

    drama_dir = args.out / args.drama
    mp4s = sorted(drama_dir.glob('episode_*.mp4'))
    if not mp4s:
        print(f"FATAL: {drama_dir} 没 mp4")
        return 1

    print(f"扫描 {len(mp4s)} 个 mp4 在 t={args.at}s 抽帧...")

    def scan(at: float) -> tuple[dict[int, str], dict[str, list[int]]]:
        ep2hash: dict[int, str] = {}
        hash2eps: dict[str, list[int]] = defaultdict(list)
        for i, mp4 in enumerate(mp4s, 1):
            parsed = parse_name(mp4.name)
            if parsed is None:
                continue
            ep, _kid8 = parsed
            h = fingerprint(mp4, at)
            if h is None:
                print(f"  ep{ep:3d}: 抽帧失败 ({mp4.name})")
                continue
            ep2hash[ep] = h
            hash2eps[h].append(ep)
            if i % 10 == 0:
                print(f"  已扫 {i}/{len(mp4s)}")
        return ep2hash, hash2eps

    ep2hash, hash2eps = scan(args.at)

    # 二次抽帧: 若 >= 40% mp4 共享同一 hash (剧的共同片头), 换时间点重抽
    largest_group = max((len(v) for v in hash2eps.values()), default=0)
    if largest_group > len(mp4s) * 0.4:
        print(f"\n警告: 最大同 hash 组 {largest_group}/{len(mp4s)} 超 40%, "
              f"可能是共享片头. 用 t={args.second_pass_at}s 重抽...")
        ep2hash, hash2eps = scan(args.second_pass_at)

    # 找串集组
    crossed_groups = {h: sorted(eps) for h, eps in hash2eps.items() if len(eps) > 1}

    print(f"\n=== 串集候选 (同 hash >= 2 个 ep) ===")
    if not crossed_groups:
        print("  (无)")
    else:
        for h, eps in sorted(crossed_groups.items(), key=lambda kv: kv[1]):
            keep = eps[0]  # ep 号最小保留
            rest = eps[1:]
            print(f"  hash={h[:8]}... eps={eps}  → 保留 ep{keep:03d}, 重下 {rest}")

    # crossed_eps: 要重下的 (除每组最小 ep 外的)
    crossed_eps: list[int] = []
    kept_eps: list[int] = []
    for h, eps in crossed_groups.items():
        kept_eps.append(eps[0])
        crossed_eps.extend(eps[1:])
    crossed_eps = sorted(set(crossed_eps))

    print(f"\n=== 总结 ===")
    print(f"总 mp4: {len(mp4s)}")
    print(f"唯一内容: {len(hash2eps)}")
    print(f"串集组数: {len(crossed_groups)}")
    print(f"要重下的 ep: {len(crossed_eps)} {crossed_eps[:20]}"
          f"{'...' if len(crossed_eps) > 20 else ''}")
    print(f"保留的 ep (每组 ep 号最小者): {sorted(kept_eps)[:20]}"
          f"{'...' if len(kept_eps) > 20 else ''}")

    report = {
        'ts': time.time(),
        'drama': args.drama,
        'at_sec': args.at,
        'total_mp4': len(mp4s),
        'unique_content': len(hash2eps),
        'crossed_groups': [
            {'hash': h[:16], 'eps': eps, 'keep_ep': eps[0],
             'redownload_eps': eps[1:]}
            for h, eps in sorted(crossed_groups.items(), key=lambda kv: kv[1])
        ],
        'crossed_eps': crossed_eps,
        'kept_eps': sorted(kept_eps),
        'ep2hash': {str(ep): h[:16] for ep, h in sorted(ep2hash.items())},
    }
    rfile = drama_dir / 'cross_episodes_report.json'
    rfile.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                     encoding='utf-8')
    print(f"\n[report] {rfile}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
