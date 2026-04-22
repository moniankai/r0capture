"""rank_collect 导航辅助逻辑测试."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SCRIPTS = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import rank_collect as rc  # noqa: E402


class TestRankCollectNav(unittest.TestCase):
    def test_theater_xml_detects_rank_entry(self) -> None:
        xml = '<node text="排行榜" bounds="[379,353][505,410]" />'

        self.assertTrue(rc._theater_rank_entry_visible(xml))

    def test_dismiss_theater_overlay_taps_close_when_markers_absent(self) -> None:
        xml = '<hierarchy><node text="" bounds="[0,0][1080,1920]" /></hierarchy>'

        with patch.object(rc, "dump_ui_xml", return_value=xml), \
                patch.object(rc, "current_focus",
                             return_value=f"{rc.APP_PACKAGE}/{rc.MAIN_ACTIVITY}"), \
                patch.object(rc, "adb_tap") as tap, \
                patch.object(rc.time, "sleep"):
            dismissed = rc.dismiss_theater_overlay_if_blocked("serial")

        self.assertTrue(dismissed)
        tap.assert_called_once_with("serial", *rc.COORD_REWARD_POPUP_CLOSE)

    def test_dismiss_theater_overlay_does_not_tap_when_theater_ready(self) -> None:
        xml = '<hierarchy><node text="排行榜" /></hierarchy>'

        with patch.object(rc, "dump_ui_xml", return_value=xml), \
                patch.object(rc, "adb_tap") as tap:
            dismissed = rc.dismiss_theater_overlay_if_blocked("serial")

        self.assertFalse(dismissed)
        tap.assert_not_called()

    def test_dump_ui_xml_handles_missing_stdout(self) -> None:
        dump_ok = SimpleNamespace(returncode=0, stdout="dumped")
        cat_decode_failed = SimpleNamespace(returncode=0, stdout=None)

        with patch.object(rc, "_adb", side_effect=[dump_ok, cat_decode_failed]):
            self.assertEqual(rc.dump_ui_xml("serial"), "")

    def test_default_per_rank_limit_is_fifty(self) -> None:
        self.assertEqual(rc.DEFAULT_PER_RANK_LIMIT, 50)

    def test_default_max_swipes_covers_long_rank(self) -> None:
        self.assertGreaterEqual(rc.DEFAULT_MAX_SWIPES, 120)

    def test_collect_continue_ignores_no_new_before_target(self) -> None:
        self.assertTrue(rc._should_continue_collecting(
            no_new_rounds=3,
            swipes_done=3,
            max_swipes=50,
            collected=16,
            per_rank_limit=50,
            stop_requested=False,
        ))

    def test_collect_continue_ignores_no_new_when_unlimited(self) -> None:
        self.assertTrue(rc._should_continue_collecting(
            no_new_rounds=3,
            swipes_done=3,
            max_swipes=120,
            collected=16,
            per_rank_limit=0,
            stop_requested=False,
        ))

    def test_collect_continue_stops_unlimited_after_full_rank_no_new(self) -> None:
        self.assertFalse(rc._should_continue_collecting(
            no_new_rounds=3,
            swipes_done=48,
            max_swipes=120,
            collected=100,
            per_rank_limit=0,
            stop_requested=False,
        ))

    def test_collect_continue_stops_unlimited_at_max_swipes(self) -> None:
        self.assertFalse(rc._should_continue_collecting(
            no_new_rounds=30,
            swipes_done=120,
            max_swipes=120,
            collected=99,
            per_rank_limit=0,
            stop_requested=False,
        ))

    def test_collect_stop_reason_reports_unmet_target_at_max_swipes(self) -> None:
        reason = rc._collect_stop_reason(
            no_new_rounds=12,
            swipes_done=50,
            max_swipes=50,
            collected=35,
            per_rank_limit=50,
        )

        self.assertEqual(reason, "max_swipes_before_limit")

    def test_collect_stop_reason_ignores_no_new_when_unlimited(self) -> None:
        reason = rc._collect_stop_reason(
            no_new_rounds=30,
            swipes_done=120,
            max_swipes=120,
            collected=99,
            per_rank_limit=0,
        )

        self.assertEqual(reason, "max_swipes")

    def test_collect_stop_reason_reports_full_rank_no_new(self) -> None:
        reason = rc._collect_stop_reason(
            no_new_rounds=3,
            swipes_done=48,
            max_swipes=120,
            collected=100,
            per_rank_limit=0,
        )

        self.assertEqual(reason, "full_rank_no_new")

    def test_accept_rank_item_stops_at_limit(self) -> None:
        self.assertTrue(rc._can_accept_rank_item(collected=49, per_rank_limit=50))
        self.assertFalse(rc._can_accept_rank_item(collected=50, per_rank_limit=50))
        self.assertTrue(rc._can_accept_rank_item(collected=100, per_rank_limit=0))


if __name__ == "__main__":
    unittest.main()
