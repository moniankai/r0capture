"""Unit tests for Codex Must-Fix fixes (M1 / M2 / M3 / M4).

M1: v5 _download_main 裸 return 必须显式 exit code, main() 兜底 fatal
M2: confidence=high 要求全采样点 probe 到 + 截断 JSON 不再静默
M3: _rewrite_manifest_excluding 原子写 (temp+rename)
M4: psutil 缺失时 _find_stale_v5 raise StaleDetectUnavailable
"""
from __future__ import annotations
import io
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from scripts.hongguo_agent import (
    StaleDetectUnavailable,
    _find_stale_v5,
    _rewrite_manifest_excluding,
    read_events,
)


# ---- test helper (在测试里直接用 append; agent 侧没公开 helper) ----
def _write_manifest(drama_dir: Path, records: list[dict]) -> None:
    drama_dir.mkdir(parents=True, exist_ok=True)
    with (drama_dir / 'session_manifest.jsonl').open('w', encoding='utf-8') as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')


class TestM3AtomicManifestRewrite:
    def test_rewrite_uses_temp_rename(self, tmp_path: Path):
        _write_manifest(tmp_path, [
            {'ep': 1, 'vid': 'v1', 'kid': 'a' * 32},
            {'ep': 2, 'vid': 'v2', 'kid': 'b' * 32},
            {'ep': 3, 'vid': 'v3', 'kid': 'c' * 32},
        ])
        n = _rewrite_manifest_excluding(tmp_path, {2})
        assert n == 1
        # 检查 .tmp 不存在 (已 rename)
        assert not (tmp_path / 'session_manifest.jsonl.tmp').exists()
        # 剩余内容正确
        lines = (tmp_path / 'session_manifest.jsonl').read_text(encoding='utf-8').strip().split('\n')
        assert len(lines) == 2
        eps = [json.loads(l)['ep'] for l in lines]
        assert eps == [1, 3]

    def test_rewrite_crash_does_not_truncate_original(self, tmp_path: Path, monkeypatch):
        """模拟 atomic replace 失败 → 原文件不变."""
        _write_manifest(tmp_path, [
            {'ep': 1, 'kid': 'a' * 32, 'vid': 'v1'},
            {'ep': 2, 'kid': 'b' * 32, 'vid': 'v2'},
        ])
        import os as real_os
        orig_replace = real_os.replace
        def fail_replace(*a, **kw):
            raise OSError("simulated disk full")
        monkeypatch.setattr('os.replace', fail_replace)
        n = _rewrite_manifest_excluding(tmp_path, {2})
        assert n == 0
        # 原 manifest 未受损
        lines = (tmp_path / 'session_manifest.jsonl').read_text(encoding='utf-8').strip().split('\n')
        assert len(lines) == 2
        eps = [json.loads(l)['ep'] for l in lines]
        assert eps == [1, 2]

    def test_empty_exclude_is_noop(self, tmp_path: Path):
        _write_manifest(tmp_path, [{'ep': 1, 'kid': 'a' * 32, 'vid': 'v1'}])
        assert _rewrite_manifest_excluding(tmp_path, set()) == 0


class TestM4StaleDetectFailsLoud:
    def test_raises_when_psutil_missing(self, monkeypatch):
        """psutil ImportError 时必须 raise, 不能静默返回 []."""
        real_import = __builtins__['__import__'] if isinstance(__builtins__, dict) else __builtins__.__import__
        def fake_import(name, *a, **kw):
            if name == 'psutil':
                raise ImportError("mocked: no psutil")
            return real_import(name, *a, **kw)
        monkeypatch.setattr('builtins.__import__', fake_import)
        with pytest.raises(StaleDetectUnavailable):
            _find_stale_v5(exclude_token='nope')

    def test_works_with_real_psutil(self):
        """真实 psutil 下应返回 list (不 raise)."""
        result = _find_stale_v5(exclude_token='any-token')
        assert isinstance(result, list)


class TestM2ReadEventsJsonCorruption:
    """Codex M2: 控制面截断 JSON 不再静默, emit control_plane_corrupt."""

    def test_truncated_json_emits_corrupt(self):
        class FakeProc:
            def __init__(self):
                # 第一行完整, 第二行 '{' 开头但截断, 第三行完整
                self.stdout = io.StringIO(
                    '{"type":"ep_ok","ep":1}\n'
                    '{"type":"ep_ok","ep":2,"vid":"abc\n'  # 截断
                    '{"type":"ep_ok","ep":3}\n'
                )
                self._done = False

            def poll(self):
                return 0 if self._done else None

            def readline_done(self):
                self._done = True

            @property
            def returncode(self):
                return 0

        events = []
        proc = FakeProc()

        # 让 read_events 知道 proc 即将结束
        import threading
        def finish():
            import time
            time.sleep(0.3)
            proc._done = True
        threading.Thread(target=finish, daemon=True).start()

        read_events(proc, events.append, stall_timeout=5.0)

        types = [e['type'] for e in events]
        assert 'ep_ok' in types
        assert 'control_plane_corrupt' in types
        corrupt = next(e for e in events if e['type'] == 'control_plane_corrupt')
        assert 'json_parse_error' in corrupt['reason']


class TestM2ConfidenceStrict:
    """Codex M2: high 要求全采样点都 probe 到."""

    def test_incomplete_probe_is_verification_failed(self, tmp_path: Path):
        """通过构造 AgentContext + mock run_v5_probe 来测试 run_verification.
        但 run_verification 会 spawn subprocess, 不适合 unit test — 改为直接测
        confidence 判定逻辑的边界 (missing probe → verification_failed).
        """
        from scripts.hongguo_agent import AgentContext, CircuitBreaker, run_verification

        # 准备已下载 manifest (ep1-3)
        drama_dir = tmp_path / 'test_drama'
        _write_manifest(drama_dir, [
            {'ep': 1, 'vid': 'actual_v1', 'kid': 'a' * 32},
            {'ep': 2, 'vid': 'actual_v2', 'kid': 'b' * 32},
            {'ep': 3, 'vid': 'actual_v3', 'kid': 'c' * 32},
        ])

        ctx = AgentContext(
            drama_name='test',
            series_id='sid1',
            total=3,
            out_dir=tmp_path,
            drama_dir=drama_dir,
            token='tok',
            cb=CircuitBreaker(),
        )

        # Mock start_v5 + read_events 模拟只 probe 到 2 个
        mock_probe_result = {1: 'actual_v1', 2: 'actual_v2'}  # ep3 没收到

        with patch('scripts.hongguo_agent.start_v5') as mock_start, \
             patch('scripts.hongguo_agent.read_events') as mock_read:

            mock_proc = type('P', (), {'poll': lambda s: 0})()
            mock_start.return_value = mock_proc

            def fake_read(proc, on_event, stall_timeout):
                for ep, vid in mock_probe_result.items():
                    on_event({'type': 'probe_ep_ok', 'ep': ep, 'vid': vid})
                on_event({'type': 'probe_ep_fail', 'ep': 3, 'reason': 'bind_timeout'})
                return 0  # V5_EXIT_OK
            mock_read.side_effect = fake_read

            confidence, detail = run_verification(ctx)

        # 部分 probe 必须 verification_failed, 不得 high
        assert confidence == 'verification_failed', \
            f"expected verification_failed, got {confidence} (detail={detail})"
        assert 'missing_probe_eps' in detail
        assert 3 in detail['missing_probe_eps']

    def test_full_probe_with_match_is_high(self, tmp_path: Path):
        from scripts.hongguo_agent import AgentContext, CircuitBreaker, run_verification

        drama_dir = tmp_path / 'test_drama'
        _write_manifest(drama_dir, [
            {'ep': 1, 'vid': 'v1', 'kid': 'a' * 32},
            {'ep': 3, 'vid': 'v3', 'kid': 'c' * 32},
        ])
        ctx = AgentContext(
            drama_name='test', series_id='sid1', total=3,
            out_dir=tmp_path, drama_dir=drama_dir, token='tok',
            cb=CircuitBreaker(),
        )

        with patch('scripts.hongguo_agent.start_v5') as mock_start, \
             patch('scripts.hongguo_agent.read_events') as mock_read:
            mock_proc = type('P', (), {'poll': lambda s: 0})()
            mock_start.return_value = mock_proc

            def fake_read(proc, on_event, stall_timeout):
                # Sample_eps for total=3 uniform_n=5: [1, 2, 3]
                # committed 只有 ep1 和 ep3, sample 过滤后 [1, 3]
                on_event({'type': 'probe_ep_ok', 'ep': 1, 'vid': 'v1'})
                on_event({'type': 'probe_ep_ok', 'ep': 3, 'vid': 'v3'})
                return 0
            mock_read.side_effect = fake_read

            confidence, detail = run_verification(ctx)

        assert confidence == 'high', f"got {confidence} {detail}"
        assert detail['match_count'] == detail['requested']

    def test_mismatch_is_failed_not_high(self, tmp_path: Path):
        from scripts.hongguo_agent import AgentContext, CircuitBreaker, run_verification

        drama_dir = tmp_path / 'test_drama'
        _write_manifest(drama_dir, [
            {'ep': 1, 'vid': 'actual_v1', 'kid': 'a' * 32},
            {'ep': 3, 'vid': 'actual_v3', 'kid': 'c' * 32},
        ])
        ctx = AgentContext(
            drama_name='test', series_id='sid1', total=3,
            out_dir=tmp_path, drama_dir=drama_dir, token='tok',
            cb=CircuitBreaker(),
        )

        with patch('scripts.hongguo_agent.start_v5') as mock_start, \
             patch('scripts.hongguo_agent.read_events') as mock_read:
            mock_proc = type('P', (), {'poll': lambda s: 0})()
            mock_start.return_value = mock_proc

            def fake_read(proc, on_event, stall_timeout):
                on_event({'type': 'probe_ep_ok', 'ep': 1, 'vid': 'DIFFERENT_v1'})
                on_event({'type': 'probe_ep_ok', 'ep': 3, 'vid': 'actual_v3'})
                return 0
            mock_read.side_effect = fake_read

            confidence, detail = run_verification(ctx)

        assert confidence == 'failed'
        assert len(detail['misaligned']) == 1
        assert detail['misaligned'][0]['ep'] == 1


class TestS1S2CircuitBreakerLayered:
    """Codex S1+S2: L1 per-ep retry 接入 + ep_fail 按 reason 分层."""

    def test_infra_fail_accumulates_consec(self):
        from scripts.hongguo_agent import CircuitBreaker
        cb = CircuitBreaker(max_retry_per_ep=5)
        for _ in range(3):
            cb.note_fail(ep=1, reason='bind_timeout')
        assert cb.consec_fail == 3
        assert 1 not in cb.abandoned_eps

    def test_business_fail_does_not_accumulate_consec(self):
        from scripts.hongguo_agent import CircuitBreaker
        cb = CircuitBreaker(max_retry_per_ep=5)
        for _ in range(3):
            cb.note_fail(ep=1, reason='download_or_decrypt_err')
        assert cb.consec_fail == 0  # business 不触发 restart
        assert cb.retry_per_ep[1] == 3

    def test_l1_triggers_abandoned_after_max_retry(self):
        from scripts.hongguo_agent import CircuitBreaker
        cb = CircuitBreaker(max_retry_per_ep=3)
        for i in range(3):
            cat = cb.note_fail(ep=5, reason='bind_timeout')
            if i < 2:
                assert cat == 'infra'
        assert cat == 'abandoned'
        assert 5 in cb.abandoned_eps

    def test_fatal_reason_sets_fatal_flag(self):
        from scripts.hongguo_agent import CircuitBreaker
        cb = CircuitBreaker()
        cat = cb.note_fail(ep=1, reason='cross_drama')
        assert cat == 'fatal'
        assert cb.fatal_fail_seen is True

    def test_progress_resets_consec_fail(self):
        from scripts.hongguo_agent import CircuitBreaker
        cb = CircuitBreaker()
        cb.note_fail(ep=1, reason='bind_timeout')
        cb.note_fail(ep=2, reason='bind_timeout')
        assert cb.consec_fail == 2
        cb.note_progress(ep=3)
        assert cb.consec_fail == 0

    def test_unknown_reason_does_not_accumulate_consec(self):
        from scripts.hongguo_agent import CircuitBreaker
        cb = CircuitBreaker()
        for _ in range(3):
            cb.note_fail(ep=1, reason='some_new_reason')
        assert cb.consec_fail == 0  # unknown 走业务层预算


class TestS4CleanupTimeoutEvent:
    """Codex S4: safe_unload_session 超时发 cleanup_timeout 事件 (由 v5 侧发)."""

    def test_cleanup_timeout_emitted_on_v5_side(self, capsys):
        """直接调 v5 的 safe_unload_session 用卡住的 mock script 验证."""
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent / 'scripts'))
        from hongguo_v5 import safe_unload_session

        class StuckScript:
            def unload(self):
                import time
                time.sleep(10)  # 故意卡

        class FakeSession:
            def detach(self): pass

        safe_unload_session(StuckScript(), FakeSession(), timeout=0.3)
        out = capsys.readouterr().out
        # 应该有 cleanup_timeout 事件
        found = any(
            '"type":"cleanup_timeout"' in line or '"type": "cleanup_timeout"' in line
            for line in out.splitlines()
        )
        assert found, f"expected cleanup_timeout event in stdout\ngot: {out[:500]}"


class TestM1V5ExitCodesExplicit:
    """Codex M1: v5 __main__ 不能把非 int 返回值兜底成 0."""

    def test_explicit_exit_codes_are_passed_through(self, tmp_path: Path, monkeypatch):
        """运行 v5 --mode attach-resume (无 adb), 期待 exit 5 (no_app) 而非 0."""
        import subprocess
        v5 = Path(__file__).parent.parent / 'scripts' / 'hongguo_v5.py'
        env = {**__import__('os').environ, "MSYS_NO_PATHCONV": "1"}
        r = subprocess.run(
            [sys.executable, str(v5), '--mode', 'attach-resume',
             '-n', 'nonexistent', '--series-id', 'x', '--out', str(tmp_path)],
            capture_output=True, text=True, timeout=30, env=env,
        )
        # 没有 App 运行, precheck no_app → exit 5
        assert r.returncode == 5, \
            f"expected 5 (EXIT_PRECOND_FAIL), got {r.returncode}\nstdout: {r.stdout[:400]}\nstderr: {r.stderr[:400]}"
