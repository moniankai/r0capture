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


if __name__ == '__main__':
    unittest.main()
