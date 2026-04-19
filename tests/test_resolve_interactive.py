"""单测: resolve_interactive 的非 frida 工具函数."""
from __future__ import annotations
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from scripts.resolve_interactive import read_existing, save


class TestReadExisting:
    def test_missing_file(self, tmp_path: Path):
        assert read_existing(tmp_path / 'nope.json') == []

    def test_valid_list(self, tmp_path: Path):
        p = tmp_path / 'in.json'
        p.write_text(
            json.dumps([{'series_id': '1', 'name': 'A', 'total': 10}]),
            encoding='utf-8')
        assert read_existing(p) == [{'series_id': '1', 'name': 'A', 'total': 10}]

    def test_corrupt(self, tmp_path: Path):
        p = tmp_path / 'in.json'
        p.write_text('not json', encoding='utf-8')
        assert read_existing(p) == []

    def test_object_not_list(self, tmp_path: Path):
        p = tmp_path / 'in.json'
        p.write_text('{"foo":"bar"}', encoding='utf-8')
        assert read_existing(p) == []


class TestSave:
    def test_roundtrip(self, tmp_path: Path):
        p = tmp_path / 'out.json'
        dramas = [
            {'series_id': '1', 'name': '剧 A', 'total': 10},
            {'series_id': '2', 'name': '剧 B', 'total': 20},
        ]
        save(p, dramas)
        loaded = json.loads(p.read_text(encoding='utf-8'))
        assert loaded == dramas

    def test_atomic_replace(self, tmp_path: Path):
        p = tmp_path / 'out.json'
        save(p, [{'series_id': '1', 'name': 'v1'}])
        save(p, [{'series_id': '1', 'name': 'v2'}])
        loaded = json.loads(p.read_text(encoding='utf-8'))
        assert loaded == [{'series_id': '1', 'name': 'v2'}]
        assert not (tmp_path / 'out.json.tmp').exists()

    def test_chinese_preserved(self, tmp_path: Path):
        p = tmp_path / 'out.json'
        dramas = [{'series_id': '1', 'name': '凡人仙葫第三季', 'total': 60}]
        save(p, dramas)
        # 读原始字节确认中文按 UTF-8 写, 不 escape
        raw = p.read_bytes()
        assert '凡人仙葫第三季'.encode('utf-8') in raw
        loaded = json.loads(p.read_text(encoding='utf-8'))
        assert loaded[0]['name'] == '凡人仙葫第三季'
