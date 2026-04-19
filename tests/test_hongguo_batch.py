"""单测: BatchAgent 数据处理 (不涉及 adb/subprocess)."""
from __future__ import annotations
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from scripts.hongguo_batch import (
    DramaResult, DramaTask,
    is_complete, load_state, read_input_list, read_report,
    save_state, summarize_line,
)


class TestReadInputList:
    def test_parse_valid(self, tmp_path: Path):
        p = tmp_path / 'in.json'
        p.write_text(json.dumps([
            {'name': 'A', 'series_id': '1', 'total': 10},
            {'name': 'B', 'series_id': '2', 'total': 20},
        ], ensure_ascii=False), encoding='utf-8')
        tasks = read_input_list(p)
        assert len(tasks) == 2
        assert tasks[0].name == 'A' and tasks[0].series_id == '1' and tasks[0].total == 10
        assert tasks[1].name == 'B' and tasks[1].total == 20

    def test_default_total(self, tmp_path: Path):
        p = tmp_path / 'in.json'
        p.write_text('[{"name":"A","series_id":"1"}]', encoding='utf-8')
        tasks = read_input_list(p)
        assert tasks[0].total == 0

    def test_reject_non_array(self, tmp_path: Path):
        p = tmp_path / 'in.json'
        p.write_text('{"name":"A"}', encoding='utf-8')
        with pytest.raises(ValueError, match='must be JSON array'):
            read_input_list(p)

    def test_reject_missing_name(self, tmp_path: Path):
        p = tmp_path / 'in.json'
        p.write_text('[{"series_id":"1"}]', encoding='utf-8')
        with pytest.raises(ValueError, match='missing name'):
            read_input_list(p)

    def test_reject_missing_series_id(self, tmp_path: Path):
        p = tmp_path / 'in.json'
        p.write_text('[{"name":"A"}]', encoding='utf-8')
        with pytest.raises(ValueError, match='missing'):
            read_input_list(p)


class TestIsComplete:
    def test_done_no_missing(self):
        assert is_complete({'state': 'DONE', 'missing': [],
                            'downloaded': [1, 2, 3]})

    def test_done_with_missing(self):
        assert not is_complete({'state': 'DONE', 'missing': [5]})

    def test_aborted(self):
        assert not is_complete({'state': 'ABORTED', 'missing': []})

    def test_empty_downloaded(self):
        assert not is_complete({'state': 'DONE', 'missing': [],
                                'downloaded': []})

    def test_downloaded_count_field(self):
        """新 report 可能用 downloaded_count 字段."""
        assert is_complete({'state': 'DONE', 'missing': [],
                            'downloaded_count': 5})


class TestStatePersist:
    def test_load_missing(self, tmp_path: Path):
        assert load_state(tmp_path / 'nope.json') == {}

    def test_save_and_load_roundtrip(self, tmp_path: Path):
        p = tmp_path / 'state.json'
        save_state(p, {'foo': 'bar', 'results': {'A': {'state': 'DONE'}}})
        loaded = load_state(p)
        assert loaded['foo'] == 'bar'
        assert loaded['results']['A']['state'] == 'DONE'

    def test_save_is_atomic(self, tmp_path: Path):
        p = tmp_path / 'state.json'
        save_state(p, {'x': 1})
        save_state(p, {'x': 2})
        assert load_state(p)['x'] == 2
        # .tmp 文件应该不存在 (os.replace 已原子替换)
        assert not (tmp_path / 'state.json.tmp').exists()


class TestReadReport:
    def test_missing_dir(self, tmp_path: Path):
        assert read_report(tmp_path, 'NoExist') is None

    def test_valid(self, tmp_path: Path):
        d = tmp_path / 'Drama A'
        d.mkdir()
        (d / 'report.json').write_text(
            json.dumps({'state': 'DONE', 'downloaded': [1, 2, 3]}),
            encoding='utf-8',
        )
        r = read_report(tmp_path, 'Drama A')
        assert r is not None
        assert r['state'] == 'DONE'

    def test_corrupt_json(self, tmp_path: Path):
        d = tmp_path / 'Drama B'
        d.mkdir()
        (d / 'report.json').write_text('not json', encoding='utf-8')
        assert read_report(tmp_path, 'Drama B') is None


class TestSummarizeLine:
    def test_ok(self):
        t = DramaTask(name='A', series_id='1', total=10)
        r = DramaResult(name='A', series_id='1', state='DONE',
                         downloaded=10, total=10, missing=[],
                         elapsed_seconds=120.0)
        s = summarize_line(1, 5, t, r)
        assert 'OK' in s and 'A' in s and '10/10' in s and 'state=DONE' in s

    def test_partial_with_missing(self):
        t = DramaTask(name='B', series_id='2', total=20)
        r = DramaResult(name='B', series_id='2', state='DONE',
                         downloaded=18, total=20, missing=[5, 19],
                         elapsed_seconds=300.0)
        s = summarize_line(2, 5, t, r)
        assert 'X' in s and '18/20' in s and 'missing=[5, 19]' in s

    def test_failed_with_error(self):
        t = DramaTask(name='C', series_id='3')
        r = DramaResult(name='C', series_id='3', state='FATAL',
                         error='test error', elapsed_seconds=5.0)
        s = summarize_line(3, 5, t, r)
        assert 'X' in s and 'state=FATAL' in s and 'err=test error' in s
