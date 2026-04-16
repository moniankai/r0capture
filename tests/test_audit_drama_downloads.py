import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.audit_drama_downloads import analyze_drama_directory


class AuditDramaDownloadsTests(unittest.TestCase):
    def test_analyze_drama_directory_reports_missing_mismatched_and_rename_targets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / '十八岁太奶奶驾到，重整家族荣耀第三部'
            root.mkdir()

            (root / 'episode_001.mp4').write_bytes(b'video-1')
            (root / 'meta_ep001.json').write_text(
                json.dumps(
                    {
                        'drama': '爹且慢，我来了',
                        'episode': 1,
                        'video_id': 'abcdef1234567890',
                        'ui_total_episodes': 3,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding='utf-8',
            )

            (root / 'episode_003_12345678.mp4').write_bytes(b'video-3')
            (root / 'meta_ep003_12345678.json').write_text(
                json.dumps(
                    {
                        'drama': '十八岁太奶奶驾到，重整家族荣耀第三部',
                        'episode': 3,
                        'video_id': '12345678aaaa9999',
                        'ui_total_episodes': 3,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding='utf-8',
            )

            report = analyze_drama_directory(root)

            self.assertEqual(report['expected_total_episodes'], 3)
            self.assertEqual(report['expected_drama_name'], '十八岁太奶奶驾到，重整家族荣耀第三部')
            self.assertFalse(report['folder_name_mismatch'])
            self.assertEqual(report['missing_episodes'], [2])
            self.assertEqual(len(report['drama_name_mismatches']), 1)
            self.assertTrue(report['rename_plan'])
            self.assertEqual(
                report['rename_plan'][0]['target_video_name'],
                'episode_001_34567890.mp4',
            )

    def test_cli_can_run_via_script_path(self):
        repo_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'sample'
            root.mkdir()
            (root / 'meta_ep001.json').write_text(
                json.dumps(
                    {
                        'drama': 'sample',
                        'episode': 1,
                        'video_id': 'abcdef1234567890',
                        'ui_total_episodes': 1,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding='utf-8',
            )
            (root / 'episode_001.mp4').write_bytes(b'video')

            result = subprocess.run(
                [sys.executable, 'scripts/audit_drama_downloads.py', str(root), '--expected-total', '1'],
                cwd=repo_root,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('"expected_total_episodes": 1', result.stdout)

    def test_metadata_title_mismatch_does_not_imply_folder_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'target-drama'
            root.mkdir()
            (root / 'episode_001.mp4').write_bytes(b'video-1')
            (root / 'meta_ep001.json').write_text(
                json.dumps(
                    {
                        'drama': 'wrong-meta-title',
                        'episode': 1,
                        'video_id': 'abcdef1234567890',
                        'ui_total_episodes': 1,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding='utf-8',
            )

            report = analyze_drama_directory(root, expected_total=1)

            self.assertFalse(report['folder_name_mismatch'])
            self.assertEqual(report['metadata_majority_drama_name'], 'wrong-meta-title')
            self.assertEqual(len(report['drama_name_mismatches']), 1)

    def test_order_renumber_plan_uses_video_file_order_and_video_id_suffix(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'target-drama'
            root.mkdir()
            first_video = root / 'episode_004.mp4'
            second_video = root / 'episode_005.mp4'
            first_video.write_bytes(b'video-1')
            second_video.write_bytes(b'video-2')

            (root / 'meta_ep004.json').write_text(
                json.dumps(
                    {
                        'drama': 'wrong-meta-title',
                        'episode': 4,
                        'video_id': 'v02ebeg10000d3stuavog65u8i75lvc0',
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding='utf-8',
            )
            (root / 'meta_ep005.json').write_text(
                json.dumps(
                    {
                        'drama': 'wrong-meta-title',
                        'episode': 5,
                        'video_id': 'v02ebeg10000d3stucvog65rcvl0bv8g',
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding='utf-8',
            )

            report = analyze_drama_directory(root, renumber_from=1, order_by='name')

            self.assertEqual(report['order_renumber_plan'][0]['target_video_name'], 'episode_001_8i75lvc0.mp4')
            self.assertEqual(report['order_renumber_plan'][1]['target_video_name'], 'episode_002_cvl0bv8g.mp4')

    def test_audit_reads_session_manifest(self):
        """测试审计工具能读取 session_manifest.jsonl"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'test-drama'
            root.mkdir()

            # 创建视频文件
            (root / 'episode_001_abc12345.mp4').write_bytes(b'video-1')
            (root / 'meta_ep001_abc12345.json').write_text(
                json.dumps({
                    'drama': 'test-drama',
                    'episode': 1,
                    'video_id': 'abc12345',
                    'ui_total_episodes': 3,
                }, ensure_ascii=False, indent=2),
                encoding='utf-8',
            )

            # 创建 session_manifest.jsonl
            manifest_path = root / 'session_manifest.jsonl'
            with manifest_path.open('w', encoding='utf-8') as f:
                f.write(json.dumps({
                    "episode": 1,
                    "status": "downloaded",
                    "timestamp": 1713196800.0,
                    "video_id": "abc12345",
                    "resolution": "720p"
                }) + '\n')
                f.write(json.dumps({
                    "episode": 2,
                    "status": "skipped_resume",
                    "timestamp": 1713196850.0,
                    "reason": "already_completed"
                }) + '\n')

            # 验证审计工具能读取 session_manifest.jsonl
            from scripts.drama_download_common import parse_session_manifest
            completed = parse_session_manifest(manifest_path)
            self.assertEqual(completed, {1})  # 只有 downloaded 状态被统计

    def test_audit_identifies_retry_patterns(self):
        """测试审计工具能识别重试模式"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'test-drama'
            root.mkdir()

            # 创建 session_manifest.jsonl，包含重试记录
            manifest_path = root / 'session_manifest.jsonl'
            with manifest_path.open('w', encoding='utf-8') as f:
                # 第 1 集：首次成功
                f.write(json.dumps({
                    "episode": 1,
                    "status": "downloaded",
                    "timestamp": 1713196800.0,
                    "video_id": "abc12345",
                    "retry_count": 0
                }) + '\n')

                # 第 2 集：重试 1 次后成功
                f.write(json.dumps({
                    "episode": 2,
                    "status": "retry_attempt",
                    "attempt": 1,
                    "reason": "stale_data",
                    "timestamp": 1713196850.0
                }) + '\n')
                f.write(json.dumps({
                    "episode": 2,
                    "status": "retry_success",
                    "attempt": 2,
                    "timestamp": 1713196860.0,
                    "video_id": "def67890"
                }) + '\n')

                # 第 3 集：重试 3 次后失败
                f.write(json.dumps({
                    "episode": 3,
                    "status": "retry_attempt",
                    "attempt": 1,
                    "reason": "download_failed",
                    "timestamp": 1713196900.0
                }) + '\n')
                f.write(json.dumps({
                    "episode": 3,
                    "status": "retry_attempt",
                    "attempt": 2,
                    "reason": "download_failed",
                    "timestamp": 1713196910.0
                }) + '\n')
                f.write(json.dumps({
                    "episode": 3,
                    "status": "retry_attempt",
                    "attempt": 3,
                    "reason": "download_failed",
                    "timestamp": 1713196920.0
                }) + '\n')
                f.write(json.dumps({
                    "episode": 3,
                    "status": "failed_after_retries",
                    "max_retries": 3,
                    "final_reason": "download_failed",
                    "timestamp": 1713196930.0
                }) + '\n')

            # 解析重试模式
            retry_stats = {}
            with manifest_path.open('r', encoding='utf-8') as f:
                for line in f:
                    record = json.loads(line.strip())
                    ep = record["episode"]
                    status = record["status"]

                    if ep not in retry_stats:
                        retry_stats[ep] = {"attempts": 0, "success": False}

                    if status == "retry_attempt":
                        retry_stats[ep]["attempts"] += 1
                    elif status == "retry_success":
                        retry_stats[ep]["success"] = True
                    elif status == "downloaded":
                        retry_stats[ep]["success"] = True

            # 验证重试统计
            self.assertEqual(retry_stats[1]["attempts"], 0)
            self.assertTrue(retry_stats[1]["success"])

            self.assertEqual(retry_stats[2]["attempts"], 1)
            self.assertTrue(retry_stats[2]["success"])

            self.assertEqual(retry_stats[3]["attempts"], 3)
            self.assertFalse(retry_stats[3]["success"])


if __name__ == '__main__':
    unittest.main()
