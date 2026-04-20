"""hongguo_batch_lean 单元测试 (纯逻辑, 不依赖真机/subprocess)."""
from __future__ import annotations
import json, sys, os, tempfile
from pathlib import Path

SCRIPTS = Path(__file__).parent.parent / 'scripts'
sys.path.insert(0, str(SCRIPTS))

import hongguo_batch_lean as hbl  # noqa: E402


# ============ read_input_list ============

def test_read_input_dramas_dict_format(tmp_path: Path):
    """dramas.json 格式: {series_id: {name, total, ...}}."""
    data = {
        "7622955207885851672": {
            "series_id": "7622955207885851672",
            "name": "开局一条蛇，无限进化",
            "total": 83,
            "is_locked": False,
        },
        "7600000000000000001": {
            "series_id": "7600000000000000001",
            "name": "付费剧",
            "total": 60,
            "is_locked": True,
        },
    }
    p = tmp_path / 'dramas.json'
    p.write_text(json.dumps(data), encoding='utf-8')
    tasks = hbl.read_input_list(p)
    assert len(tasks) == 2
    names = {t.name for t in tasks}
    assert '开局一条蛇，无限进化' in names
    assert '付费剧' in names


def test_read_input_flat_list_format(tmp_path: Path):
    """flat list: [{name, series_id, total}]."""
    data = [
        {"name": "A", "series_id": "1", "total": 10},
        {"name": "B", "series_id": "2", "total": 20, "is_locked": True},
    ]
    p = tmp_path / 'list.json'
    p.write_text(json.dumps(data), encoding='utf-8')
    tasks = hbl.read_input_list(p)
    assert len(tasks) == 2
    assert tasks[0].name == 'A'
    assert tasks[1].is_locked is True


def test_read_input_drops_invalid_entries(tmp_path: Path):
    """缺字段的 entry 要被跳过."""
    data = [
        {"name": "ok", "series_id": "1", "total": 5},
        {"name": "no_sid", "total": 5},          # 缺 series_id
        {"series_id": "3", "total": 5},           # 缺 name
        {"name": "zero_total", "series_id": "4", "total": 0},
        "string_not_dict",
    ]
    p = tmp_path / 'bad.json'
    p.write_text(json.dumps(data), encoding='utf-8')
    tasks = hbl.read_input_list(p)
    assert len(tasks) == 1
    assert tasks[0].name == 'ok'


# ============ filter_tasks ============

def test_filter_skip_locked():
    tasks = [
        hbl.DramaTask(name='A', series_id='1', total=10, is_locked=False),
        hbl.DramaTask(name='B', series_id='2', total=10, is_locked=True),
    ]
    out = hbl.filter_tasks(tasks, skip_locked=True)
    assert len(out) == 1 and out[0].name == 'A'
    out2 = hbl.filter_tasks(tasks, skip_locked=False)
    assert len(out2) == 2


def test_filter_max_total():
    tasks = [
        hbl.DramaTask(name='A', series_id='1', total=10),
        hbl.DramaTask(name='B', series_id='2', total=100),
        hbl.DramaTask(name='C', series_id='3', total=50),
    ]
    out = hbl.filter_tasks(tasks, skip_locked=False, max_total=50)
    assert {t.name for t in out} == {'A', 'C'}


def test_filter_max_dramas():
    tasks = [hbl.DramaTask(name=f'D{i}', series_id=str(i), total=10)
             for i in range(5)]
    out = hbl.filter_tasks(tasks, skip_locked=False, max_dramas=2)
    assert len(out) == 2
    assert [t.name for t in out] == ['D0', 'D1']


# ============ State I/O ============

def test_load_state_missing_file_returns_fresh(tmp_path: Path):
    s = hbl.load_state(tmp_path / 'no_such.json')
    assert 'session_id' in s
    assert s['dramas'] == {}


def test_load_state_reads_existing(tmp_path: Path):
    p = tmp_path / 'state.json'
    payload = {'session_id': 'x', 'dramas': {'1': {'status': 'done'}}}
    p.write_text(json.dumps(payload), encoding='utf-8')
    s = hbl.load_state(p)
    assert s['session_id'] == 'x'
    assert s['dramas']['1']['status'] == 'done'


def test_load_state_corrupt_returns_fresh(tmp_path: Path):
    p = tmp_path / 'bad.json'
    p.write_text('{not valid json', encoding='utf-8')
    s = hbl.load_state(p)
    assert s['dramas'] == {}


def test_save_state_atomic_roundtrip(tmp_path: Path):
    p = tmp_path / 'state.json'
    state = {
        'session_id': 'test_session',
        'started_at': '2026-04-20T00:00:00',
        'dramas': {
            '1': {'status': 'done', 'verdict': 'PASS'},
        },
    }
    hbl.save_state(p, state)
    assert p.exists()
    loaded = json.loads(p.read_text(encoding='utf-8'))
    assert loaded == state


def test_save_state_no_tmp_leftover(tmp_path: Path):
    """原子写完成后不应残留 .tmp 文件."""
    p = tmp_path / 'state.json'
    hbl.save_state(p, {'session_id': 'x', 'dramas': {}})
    tmp_files = list(tmp_path.glob('.batch_state.*.tmp'))
    assert tmp_files == []


# ============ should_skip ============

def test_should_skip_no_entry():
    t = hbl.DramaTask(name='A', series_id='1', total=10)
    assert hbl.should_skip({'dramas': {}}, t) is False


def test_should_skip_done_true():
    t = hbl.DramaTask(name='A', series_id='1', total=10)
    state = {'dramas': {'1': {'status': 'done'}}}
    assert hbl.should_skip(state, t) is True


def test_should_skip_failed_false():
    """failed 不跳, 允许重试."""
    t = hbl.DramaTask(name='A', series_id='1', total=10)
    state = {'dramas': {'1': {'status': 'failed'}}}
    assert hbl.should_skip(state, t) is False


def test_should_skip_running_false():
    t = hbl.DramaTask(name='A', series_id='1', total=10)
    state = {'dramas': {'1': {'status': 'running'}}}
    assert hbl.should_skip(state, t) is False


# ============ is_complete ============

def test_is_complete_no_dir(tmp_path: Path):
    t = hbl.DramaTask(name='A', series_id='1', total=3)
    assert hbl.is_complete(tmp_path, t) is False


def test_is_complete_all_mp4s_and_manifest(tmp_path: Path):
    t = hbl.DramaTask(name='test', series_id='1', total=3)
    d = tmp_path / 'test'
    d.mkdir()
    for ep in [1, 2, 3]:
        (d / f'episode_{ep:03d}_abcd1234.mp4').write_bytes(b'fake')
    manifest = d / 'session_manifest.jsonl'
    manifest.write_text(
        '\n'.join(json.dumps({'ep': ep}) for ep in [1, 2, 3]),
        encoding='utf-8',
    )
    assert hbl.is_complete(tmp_path, t) is True


def test_is_complete_missing_mp4(tmp_path: Path):
    t = hbl.DramaTask(name='test', series_id='1', total=3)
    d = tmp_path / 'test'
    d.mkdir()
    (d / 'episode_001_abcd1234.mp4').write_bytes(b'fake')
    # 只有 1 个 mp4, 需要 3
    (d / 'session_manifest.jsonl').write_text(
        json.dumps({'ep': 1}), encoding='utf-8',
    )
    assert hbl.is_complete(tmp_path, t) is False


def test_is_complete_manifest_short(tmp_path: Path):
    """mp4 够但 manifest 不完整也视为未完成."""
    t = hbl.DramaTask(name='test', series_id='1', total=3)
    d = tmp_path / 'test'
    d.mkdir()
    for ep in [1, 2, 3]:
        (d / f'episode_{ep:03d}_abcd1234.mp4').write_bytes(b'fake')
    # manifest 只记录 2 集
    (d / 'session_manifest.jsonl').write_text(
        '\n'.join(json.dumps({'ep': ep}) for ep in [1, 2]),
        encoding='utf-8',
    )
    assert hbl.is_complete(tmp_path, t) is False


def test_is_complete_manifest_half_written_line(tmp_path: Path):
    """末行半写要被跳过."""
    t = hbl.DramaTask(name='test', series_id='1', total=2)
    d = tmp_path / 'test'
    d.mkdir()
    for ep in [1, 2]:
        (d / f'episode_{ep:03d}_abcd1234.mp4').write_bytes(b'fake')
    (d / 'session_manifest.jsonl').write_text(
        json.dumps({'ep': 1}) + '\n' + json.dumps({'ep': 2}) + '\n{half-wri',
        encoding='utf-8',
    )
    assert hbl.is_complete(tmp_path, t) is True


# ============ mark_state ============

def test_mark_state_creates_entry():
    state = {'dramas': {}}
    t = hbl.DramaTask(name='A', series_id='1', total=10)
    hbl.mark_state(state, t, 'done', verdict='PASS')
    entry = state['dramas']['1']
    assert entry['status'] == 'done'
    assert entry['verdict'] == 'PASS'
    assert entry['name'] == 'A'


def test_mark_state_running_increments_attempts():
    state = {'dramas': {}}
    t = hbl.DramaTask(name='A', series_id='1', total=10)
    hbl.mark_state(state, t, 'running')
    assert state['dramas']['1']['attempts'] == 1
    hbl.mark_state(state, t, 'running')
    assert state['dramas']['1']['attempts'] == 2
    # done 不增加
    hbl.mark_state(state, t, 'done')
    assert state['dramas']['1']['attempts'] == 2
    assert state['dramas']['1']['status'] == 'done'


# ============ summarize_line ============

def test_summarize_line_done():
    t = hbl.DramaTask(name='A剧', series_id='1', total=83)
    line = hbl.summarize_line(t, {'status': 'done', 'verdict': 'PASS_MECHANICAL'})
    assert 'done' in line and 'A剧' in line and 'PASS_MECHANICAL' in line


def test_summarize_line_failed_with_stage():
    t = hbl.DramaTask(name='B剧', series_id='2', total=50)
    line = hbl.summarize_line(t, {'status': 'failed', 'stage': 'v5_lean'})
    assert 'failed' in line and 'v5_lean' in line


# ============ read_report ============

def test_read_report_missing(tmp_path: Path):
    assert hbl.read_report(tmp_path / 'no.json') is None


def test_read_report_ok(tmp_path: Path):
    p = tmp_path / 'r.json'
    p.write_text(json.dumps({'verdict': 'PASS'}), encoding='utf-8')
    assert hbl.read_report(p) == {'verdict': 'PASS'}


def test_read_report_corrupt(tmp_path: Path):
    p = tmp_path / 'r.json'
    p.write_text('{bad', encoding='utf-8')
    assert hbl.read_report(p) is None
