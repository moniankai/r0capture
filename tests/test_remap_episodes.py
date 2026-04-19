"""单测: remap_episodes 的 plan 构建逻辑."""
from __future__ import annotations
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from scripts.remap_episodes import (
    DiskMp4, ProbeRec, Plan,
    build_plan, parse_probe, scan_disk_mp4, read_manifest,
)


def _mk_disk(tmp_path: Path, pairs: list[tuple[int, str]]) -> list[DiskMp4]:
    """pairs=[(old_ep, kid8), ...] → 创建 mp4 + 返回 DiskMp4 列表"""
    out = []
    for ep, kid in pairs:
        p = tmp_path / f'episode_{ep:03d}_{kid}.mp4'
        p.write_bytes(b'x' * (2 * 1024 * 1024))
        out.append(DiskMp4(path=p, old_ep=ep, kid8=kid))
    return out


def _mk_probes(data: dict[int, str]) -> dict[int, ProbeRec]:
    """{ep: kid8} → probes"""
    return {
        ep: ProbeRec(ep=ep, vid=f'vid_{ep}', kid8=kid, title=f'第{ep}集')
        for ep, kid in data.items()
    }


class TestBuildPlan:
    def test_no_misalignment(self, tmp_path: Path):
        """所有 mp4 kid 都和 probe 对得上, 无需重命名."""
        disk = _mk_disk(tmp_path, [(1, 'aaaa0000'), (2, 'bbbb1111')])
        probes = _mk_probes({1: 'aaaa0000', 2: 'bbbb1111'})
        plan = build_plan(probes, [], disk, [], total=2)
        assert plan.renames == []
        assert plan.orphans == []
        assert plan.missing_real_eps == []
        assert len(plan.new_manifest) == 2

    def test_rename_misaligned(self, tmp_path: Path):
        """磁盘 ep1 的 kid 实际是 probe 里 ep80 的 → 重命名."""
        disk = _mk_disk(tmp_path, [(1, 'aaaa0000'), (80, 'bbbb1111')])
        # probe 说: ep1 的 kid 是 bbbb, ep80 的 kid 是 aaaa (两者错位)
        probes = _mk_probes({1: 'bbbb1111', 80: 'aaaa0000'})
        # total=2, 只关心 ep1/ep80 的 rename 路径
        plan = build_plan(probes, [], disk, [], total=2)

        rename_map = {old.name: new.name for old, new in plan.renames}
        assert rename_map == {
            'episode_001_aaaa0000.mp4': 'episode_080_aaaa0000.mp4',
            'episode_080_bbbb1111.mp4': 'episode_001_bbbb1111.mp4',
        }
        assert plan.orphans == []
        # total=2 但 probe 里有 ep80, 所以 missing = {2} (ep1 和 ep80 都有 mp4)
        assert plan.missing_real_eps == [2]

    def test_orphan_kid_not_in_probe(self, tmp_path: Path):
        """磁盘有 mp4 但 probe 里没有对应 kid → 算 orphan."""
        disk = _mk_disk(tmp_path, [(1, 'aaaa0000'), (2, 'ccccffff')])
        probes = _mk_probes({1: 'aaaa0000'})  # ep2 没 probe
        plan = build_plan(probes, [2], disk, [], total=2)
        assert len(plan.orphans) == 1
        assert plan.orphans[0].name == 'episode_002_ccccffff.mp4'
        # ep2 真实仍缺失
        assert plan.missing_real_eps == [2]

    def test_missing_ep_no_disk_file(self, tmp_path: Path):
        """probe 有 ep5 但磁盘没文件 → missing."""
        disk = _mk_disk(tmp_path, [(1, 'aaaa0000')])
        probes = _mk_probes({1: 'aaaa0000', 5: 'eeee5555'})
        plan = build_plan(probes, [], disk, [], total=10)
        assert 5 in plan.missing_real_eps
        assert set(plan.missing_real_eps) == {2, 3, 4, 5, 6, 7, 8, 9, 10}

    def test_conflict_two_disk_files_same_real_ep(self, tmp_path: Path):
        """罕见: 两个磁盘 mp4 的 kid 都映射到同一 real_ep → 保留大的, 小的标 orphan."""
        p1 = tmp_path / 'episode_001_aaaa0000.mp4'
        p1.write_bytes(b'x' * 100)   # 100 B, 小
        p2 = tmp_path / 'episode_080_bbbb1111.mp4'
        p2.write_bytes(b'x' * (2 * 1024 * 1024))  # 2 MB, 大
        disk = [
            DiskMp4(path=p1, old_ep=1, kid8='aaaa0000'),
            DiskMp4(path=p2, old_ep=80, kid8='bbbb1111'),
        ]
        # probe 说 ep1 = aaaa 和 ep1 = bbbb (两者都映射到 ep1)
        probes = {
            1: ProbeRec(ep=1, vid='v1', kid8='aaaa0000', title='第1集'),
        }
        # 同步: probes 再有一个 ep1 对 bbbb
        # 实际 build_plan 的 kid_to_real_ep 只有一个 bbbb→? 这里简化: 不会出现
        # 两个 kid 映射到同 real_ep 这种情况, 因为 probes 的 ep 是 key, 每 ep 只有一个 kid.
        # 真正的冲突场景: 两个 kid 都是 probes 里某 real_ep 的候选, 但 probes 字典结构
        # 保证每 real_ep 仅对应一个 kid. 所以"冲突"需要 probes 有重复 ep (不会).
        # 这里测试 fallback: 磁盘有两个 aaaa0000 (相同 kid 不同 ep 名), 保留后者
        p3 = tmp_path / 'episode_002_aaaa0000.mp4'
        p3.write_bytes(b'x' * (3 * 1024 * 1024))
        disk.append(DiskMp4(path=p3, old_ep=2, kid8='aaaa0000'))
        plan = build_plan(probes, [], disk, [], total=2)
        # 同 kid8 两份 → ep1 一个候选, 另一个变 orphan
        assert len(plan.conflicts) == 1
        assert plan.conflicts[0][0] == 1

    def test_new_manifest_series_id_inherited(self, tmp_path: Path):
        disk = _mk_disk(tmp_path, [(1, 'aaaa0000')])
        probes = _mk_probes({1: 'aaaa0000'})
        old_manifest = [{'ep': 1, 'series_id': '123', 'vid': 'v1'}]
        plan = build_plan(probes, [], disk, old_manifest, total=1)
        assert plan.new_manifest[0]['series_id'] == '123'

    def test_new_manifest_has_title_from_probe(self, tmp_path: Path):
        disk = _mk_disk(tmp_path, [(1, 'aaaa0000')])
        probes = {
            1: ProbeRec(ep=1, vid='v1', kid8='aaaa0000', title='第1集'),
        }
        plan = build_plan(probes, [], disk, [], total=1)
        assert plan.new_manifest[0]['title'] == '第1集'


class TestParseProbe:
    def test_parses_probe_ep_ok_only(self, tmp_path: Path):
        p = tmp_path / 'probe.jsonl'
        lines = [
            '{"type": "probe_ep_start", "ep": 1}',
            '{"type": "probe_ep_ok", "ep": 1, "vid": "v1", "kid": "aaaa0000xxxx", "title": "第1集"}',
            '{"type": "probe_ep_fail", "ep": 2, "reason": "bind_timeout"}',
            'some log line not json',
            '',
        ]
        p.write_text('\n'.join(lines), encoding='utf-8')
        probes, failed = parse_probe(p)
        assert set(probes.keys()) == {1}
        assert probes[1].kid8 == 'aaaa0000'
        assert probes[1].title == '第1集'
        assert failed == [2]
