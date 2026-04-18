"""Unit tests for hongguo_v5 Agent 契约层函数 (design doc v4).

覆盖:
- append_manifest + read_committed_eps (提交顺序 + 半行容错)
- cleanup_final_dir_orphans (rename→manifest 崩溃恢复)
- cleanup_tmp_dir (.tmp 孤儿清理)
- resolve_start_ep (--start auto 的 manifest 扫 + 缺口定位)
- emit 事件格式
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from scripts.hongguo_v5 import (
    append_manifest,
    cleanup_final_dir_orphans,
    cleanup_tmp_dir,
    read_committed_eps,
    resolve_start_ep,
)


class TestManifestRoundtrip:
    """append_manifest 和 read_committed_eps 的闭环一致性."""

    def test_empty_dir_returns_empty_dict(self, tmp_path: Path):
        assert read_committed_eps(tmp_path) == {}

    def test_append_then_read_returns_same(self, tmp_path: Path):
        assert append_manifest(tmp_path, {
            'ep': 1, 'vid': 'v1', 'kid': 'abcdef1234567890', 'ts': 1.0,
        })
        assert read_committed_eps(tmp_path) == {1: 'abcdef12'}

    def test_multiple_appends(self, tmp_path: Path):
        for ep, kid in [(1, 'aaaa0000'), (2, 'bbbb1111'), (3, 'cccc2222')]:
            append_manifest(tmp_path, {'ep': ep, 'kid': kid + '0' * 24, 'vid': f'v{ep}'})
        result = read_committed_eps(tmp_path)
        assert result == {1: 'aaaa0000', 2: 'bbbb1111', 3: 'cccc2222'}

    def test_half_written_last_line_is_skipped(self, tmp_path: Path):
        """v5 崩溃留下末行半写 JSON → read_committed_eps 跳过."""
        append_manifest(tmp_path, {'ep': 1, 'kid': 'aaaa0000' + '0' * 24, 'vid': 'v1'})
        # 再手动追加半行
        mfile = tmp_path / 'session_manifest.jsonl'
        with mfile.open('a', encoding='utf-8') as f:
            f.write('{"ep": 2, "kid": "bbbb')  # 残缺
        # 应只读到 ep=1
        assert read_committed_eps(tmp_path) == {1: 'aaaa0000'}

    def test_missing_ep_or_kid_is_skipped(self, tmp_path: Path):
        mfile = tmp_path / 'session_manifest.jsonl'
        # 缺 kid
        with mfile.open('w', encoding='utf-8') as f:
            f.write(json.dumps({'ep': 1, 'vid': 'v1'}) + '\n')
            f.write(json.dumps({'ep': 2, 'vid': 'v2', 'kid': 'valid000' + '0' * 24}) + '\n')
        assert read_committed_eps(tmp_path) == {2: 'valid000'}


class TestFinalDirOrphanCleanup:
    """rename 成功但 manifest 未落盘的 orphan mp4 清理."""

    def test_removes_mp4_not_in_manifest(self, tmp_path: Path):
        # 模拟: ep1 在 manifest, ep2 只有 mp4 (orphan)
        (tmp_path / 'episode_001_aaaa0000.mp4').write_bytes(b'mp4-ep1')
        (tmp_path / 'episode_002_bbbb1111.mp4').write_bytes(b'mp4-ep2-orphan')
        committed = {1: 'aaaa0000'}  # 只有 ep1

        n = cleanup_final_dir_orphans(tmp_path, committed)

        assert n == 1
        assert (tmp_path / 'episode_001_aaaa0000.mp4').exists()
        assert not (tmp_path / 'episode_002_bbbb1111.mp4').exists()

    def test_removes_mp4_with_wrong_kid(self, tmp_path: Path):
        """同一 ep 对应两个 kid: manifest 说 A, 磁盘是 B (重下过但 orphan 残留)."""
        (tmp_path / 'episode_001_bbbb1111.mp4').write_bytes(b'old-kid')
        committed = {1: 'aaaa0000'}  # manifest 说 ep1 kid 是 aaaa

        n = cleanup_final_dir_orphans(tmp_path, committed)

        assert n == 1
        assert not (tmp_path / 'episode_001_bbbb1111.mp4').exists()

    def test_empty_dir(self, tmp_path: Path):
        assert cleanup_final_dir_orphans(tmp_path, {}) == 0

    def test_case_insensitive_kid_match(self, tmp_path: Path):
        """kid 大小写混用时 manifest 小写 vs 文件名可能大写,都应视为匹配."""
        (tmp_path / 'episode_001_AAAA0000.mp4').write_bytes(b'test')
        committed = {1: 'aaaa0000'}
        n = cleanup_final_dir_orphans(tmp_path, committed)
        assert n == 0
        assert (tmp_path / 'episode_001_AAAA0000.mp4').exists()


class TestTmpDirCleanup:
    def test_removes_all_tmp_files(self, tmp_path: Path):
        tmp_dir = tmp_path / '.tmp'
        tmp_dir.mkdir()
        (tmp_dir / 'ep_001.decrypted').write_bytes(b'crash_remnant')
        (tmp_dir / 'ep_002.part').write_bytes(b'another')
        n = cleanup_tmp_dir(tmp_path)
        assert n == 2
        assert list(tmp_dir.glob('*')) == []

    def test_no_tmp_dir_is_ok(self, tmp_path: Path):
        assert cleanup_tmp_dir(tmp_path) == 0


class TestResolveStartEp:
    """--start auto 定位最小缺失集."""

    def _setup(self, tmp_path: Path, committed_eps: list[int], total: int):
        """create manifest + mp4 files for given committed eps."""
        for ep in committed_eps:
            kid = f'{ep:08x}' + '0' * 24
            append_manifest(tmp_path, {'ep': ep, 'kid': kid, 'vid': f'v{ep}'})
            # 创建 > 1MB 的 mp4 文件通过测试校验
            mp4 = tmp_path / f'episode_{ep:03d}_{kid[:8]}.mp4'
            mp4.write_bytes(b'x' * (2 * 1024 * 1024))  # 2MB

    def test_int_start_is_passed_through(self, tmp_path: Path):
        assert resolve_start_ep(tmp_path, total=60, cli_start='5') == 5

    def test_auto_on_empty_returns_1(self, tmp_path: Path):
        assert resolve_start_ep(tmp_path, total=60, cli_start='auto') == 1

    def test_auto_finds_first_gap(self, tmp_path: Path):
        self._setup(tmp_path, [1, 2, 3, 5], total=10)
        # committed [1,2,3,5] (注意 4 缺失) → start=4
        assert resolve_start_ep(tmp_path, total=10, cli_start='auto') == 4

    def test_auto_returns_next_after_all_committed(self, tmp_path: Path):
        self._setup(tmp_path, [1, 2, 3], total=3)
        # 全部下完 → total+1
        assert resolve_start_ep(tmp_path, total=3, cli_start='auto') == 4

    def test_auto_skips_small_mp4_files(self, tmp_path: Path):
        """mp4 < 1MB 视为坏档, 那集回滚为 missing."""
        append_manifest(tmp_path, {'ep': 1, 'kid': 'aaaaaaaa' + '0' * 24, 'vid': 'v1'})
        (tmp_path / 'episode_001_aaaaaaaa.mp4').write_bytes(b'x' * 100)  # 100B, 坏档
        assert resolve_start_ep(tmp_path, total=10, cli_start='auto') == 1

    def test_auto_invalid_cli_start_falls_back_to_1(self, tmp_path: Path):
        assert resolve_start_ep(tmp_path, total=10, cli_start='garbage') == 1

    def test_auto_handles_orphan_mp4_with_agent_token(self, tmp_path: Path, monkeypatch):
        """Agent 编排时 (HONGGUO_AGENT_TOKEN 设置), orphan cleanup 应清掉无主 mp4."""
        monkeypatch.setenv('HONGGUO_AGENT_TOKEN', 'test-token')
        append_manifest(tmp_path, {'ep': 1, 'kid': 'aaaaaaaa' + '0' * 24, 'vid': 'v1'})
        (tmp_path / 'episode_001_aaaaaaaa.mp4').write_bytes(b'x' * (2 << 20))
        (tmp_path / 'episode_002_bbbbbbbb.mp4').write_bytes(b'x' * (2 << 20))  # orphan

        start = resolve_start_ep(tmp_path, total=10, cli_start='auto')
        assert start == 2  # ep2 被清, 从 ep2 重下
        assert not (tmp_path / 'episode_002_bbbbbbbb.mp4').exists()

    def test_auto_skips_orphan_cleanup_without_token(self, tmp_path: Path, monkeypatch):
        """Codex S5: 无 HONGGUO_AGENT_TOKEN 时不跑 orphan cleanup (防并发 writer 误删)."""
        monkeypatch.delenv('HONGGUO_AGENT_TOKEN', raising=False)
        append_manifest(tmp_path, {'ep': 1, 'kid': 'aaaaaaaa' + '0' * 24, 'vid': 'v1'})
        (tmp_path / 'episode_001_aaaaaaaa.mp4').write_bytes(b'x' * (2 << 20))
        (tmp_path / 'episode_002_bbbbbbbb.mp4').write_bytes(b'x' * (2 << 20))  # orphan

        start = resolve_start_ep(tmp_path, total=10, cli_start='auto')
        # ep2 仍视为 missing (manifest 不认), 但文件保留
        assert start == 2
        assert (tmp_path / 'episode_002_bbbbbbbb.mp4').exists()  # orphan 保留


class TestEmitEventFormat:
    """stdout 事件是 single-line JSON."""

    def test_emit_writes_json_line(self, capsys):
        from scripts.hongguo_v5 import emit
        emit('ep_ok', ep=48, vid='7620', kid='69c')
        captured = capsys.readouterr()
        lines = [l for l in captured.out.splitlines() if l.strip()]
        assert len(lines) == 1
        rec = json.loads(lines[0])
        assert rec['type'] == 'ep_ok'
        assert rec['ep'] == 48
        assert rec['vid'] == '7620'
        assert rec['kid'] == '69c'
        assert 'ts' in rec
