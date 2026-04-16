import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
import json

from scripts.drama_download_common import parse_session_manifest


class TestParseSessionManifest(unittest.TestCase):
    """测试 parse_session_manifest() 函数"""

    def test_parse_session_manifest_success(self):
        """测试正常解析包含多条记录的 jsonl 文件"""
        with TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / "session_manifest.jsonl"

            # 写入测试数据
            with manifest_path.open('w', encoding='utf-8') as f:
                f.write(json.dumps({"episode": 1, "video_id": "abc123", "status": "downloaded"}) + "\n")
                f.write(json.dumps({"episode": 2, "video_id": "def456", "status": "skipped_existing"}) + "\n")
                f.write(json.dumps({"episode": 3, "video_id": "ghi789", "status": "downloaded"}) + "\n")

            # 解析
            completed = parse_session_manifest(manifest_path)

            # 验证
            self.assertEqual(completed, {1, 2, 3})

    def test_parse_session_manifest_file_not_exists(self):
        """测试文件不存在时返回空集合"""
        with TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / "nonexistent.jsonl"

            completed = parse_session_manifest(manifest_path)

            self.assertEqual(completed, set())

    def test_parse_session_manifest_malformed_lines(self):
        """测试忽略格式错误的行"""
        with TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / "session_manifest.jsonl"

            # 写入测试数据（包含格式错误的行）
            with manifest_path.open('w', encoding='utf-8') as f:
                f.write(json.dumps({"episode": 1, "status": "downloaded"}) + "\n")
                f.write("this is not valid json\n")  # 格式错误
                f.write(json.dumps({"episode": 2, "status": "downloaded"}) + "\n")
                f.write("{incomplete json\n")  # 格式错误
                f.write(json.dumps({"episode": 3, "status": "downloaded"}) + "\n")

            # 解析（应忽略格式错误的行）
            completed = parse_session_manifest(manifest_path)

            # 验证
            self.assertEqual(completed, {1, 2, 3})

    def test_parse_session_manifest_filter_by_status(self):
        """测试只统计 status 为 downloaded 或 skipped_existing 的记录"""
        with TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / "session_manifest.jsonl"

            # 写入测试数据（包含不同状态）
            with manifest_path.open('w', encoding='utf-8') as f:
                f.write(json.dumps({"episode": 1, "status": "downloaded"}) + "\n")
                f.write(json.dumps({"episode": 2, "status": "skipped_existing"}) + "\n")
                f.write(json.dumps({"episode": 3, "status": "failed"}) + "\n")  # 不应统计
                f.write(json.dumps({"episode": 4, "status": "pending"}) + "\n")  # 不应统计
                f.write(json.dumps({"episode": 5, "status": "downloaded"}) + "\n")

            # 解析
            completed = parse_session_manifest(manifest_path)

            # 验证（只包含 downloaded 和 skipped_existing）
            self.assertEqual(completed, {1, 2, 5})

    def test_parse_session_manifest_missing_episode_field(self):
        """测试缺少 episode 字段的记录被忽略"""
        with TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / "session_manifest.jsonl"

            # 写入测试数据
            with manifest_path.open('w', encoding='utf-8') as f:
                f.write(json.dumps({"episode": 1, "status": "downloaded"}) + "\n")
                f.write(json.dumps({"video_id": "abc", "status": "downloaded"}) + "\n")  # 缺少 episode
                f.write(json.dumps({"episode": 2, "status": "downloaded"}) + "\n")

            # 解析
            completed = parse_session_manifest(manifest_path)

            # 验证
            self.assertEqual(completed, {1, 2})

    def test_parse_session_manifest_non_integer_episode(self):
        """测试 episode 字段非整数时被忽略"""
        with TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / "session_manifest.jsonl"

            # 写入测试数据
            with manifest_path.open('w', encoding='utf-8') as f:
                f.write(json.dumps({"episode": 1, "status": "downloaded"}) + "\n")
                f.write(json.dumps({"episode": "2", "status": "downloaded"}) + "\n")  # 字符串
                f.write(json.dumps({"episode": 3, "status": "downloaded"}) + "\n")

            # 解析
            completed = parse_session_manifest(manifest_path)

            # 验证（字符串 "2" 不应被统计）
            self.assertEqual(completed, {1, 3})


if __name__ == '__main__':
    unittest.main()
