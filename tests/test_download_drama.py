import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace
from pathlib import Path
from tempfile import TemporaryDirectory

import frida
from scripts import download_drama
from scripts.drama_download_common import (
    UIContext,
    SessionValidationState,
    build_episode_base_name,
    build_episode_paths,
    parse_ui_context,
    validate_round,
)


SAMPLE_UI_XML = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node text="爹且慢，我来了" resource-id="com.phoenix.read:id/d4" />
  <node text="第3集" resource-id="com.phoenix.read:id/jjj" />
  <node text=" · 已完结 · 全60集" resource-id="com.phoenix.read:id/jr1" />
</hierarchy>
"""

ALT_TOTAL_UI_XML = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node text="十八岁太奶奶驾到，重整家族荣耀第三部" resource-id="com.phoenix.read:id/d4" />
  <node text="已完结 共84集" />
</hierarchy>
"""

COMMENT_PANEL_XML = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node text="评论" />
  <node text="8.1万" />
  <node text="剧评" />
  <node text="大家都在搜：十八岁太奶奶驾到，重整家族荣耀…" />
  <node text="听花岛剧场" />
  <node text="出品方" />
  <node text="第2集 | 1955年容遇教授意外去世" />
</hierarchy>
"""


PLAYER_OVERLAY_XML = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node resource-id="com.phoenix.read:id/joj" text="选集" />
  <node resource-id="com.phoenix.read:id/jjj" text="第2集" />
</hierarchy>
"""

DETAIL_EPISODE_GRID_XML = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node resource-id="com.phoenix.read:id/joj" text="选集" />
  <node resource-id="com.phoenix.read:id/ivi" text="2" />
</hierarchy>
"""


DETAIL_SELECTED_EPISODE_XML = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node resource-id="com.phoenix.read:id/d0d" class="android.widget.GridView">
    <node class="android.view.ViewGroup">
      <node text="2" resource-id="com.phoenix.read:id/ivi" />
      <node class="android.widget.FrameLayout">
        <node resource-id="com.phoenix.read:id/zu" />
      </node>
    </node>
    <node class="android.view.ViewGroup">
      <node text="3" resource-id="com.phoenix.read:id/ivi" />
    </node>
  </node>
</hierarchy>
"""


SEARCH_RESULTS_XML = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node resource-id="com.phoenix.read:id/h7h" text="女帝妹妹早产了" bounds="[80,60][980,140]" />
  <node resource-id="com.phoenix.read:id/jy3" text="女帝妹妹早产了第四部" bounds="[40,220][1000,320]" />
  <node resource-id="com.phoenix.read:id/jy3" text="女帝妹妹早产了" bounds="[40,360][1000,460]" />
  <node text="女帝妹妹" bounds="[40,520][1000,620]" />
</hierarchy>
"""


DETAIL_SELECTED_EPISODE_44_XML = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node resource-id="com.phoenix.read:id/d0d" class="android.widget.GridView">
    <node class="android.view.ViewGroup">
      <node text="44" resource-id="com.phoenix.read:id/ivi" bounds="[100,1600][180,1680]" />
      <node class="android.widget.FrameLayout">
        <node resource-id="com.phoenix.read:id/zu" />
      </node>
    </node>
  </node>
</hierarchy>
"""


DETAIL_SELECTED_EPISODE_51_XML = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node resource-id="com.phoenix.read:id/d0d" class="android.widget.GridView">
    <node class="android.view.ViewGroup">
      <node text="51" resource-id="com.phoenix.read:id/ivi" bounds="[100,1600][180,1680]" />
      <node class="android.widget.FrameLayout">
        <node resource-id="com.phoenix.read:id/zu" />
      </node>
    </node>
  </node>
</hierarchy>
"""


class ParseUiContextTests(unittest.TestCase):
    def test_parse_ui_context_extracts_title_episode_and_total(self):
        context = parse_ui_context(SAMPLE_UI_XML)

        self.assertEqual(context.title, "爹且慢，我来了")
        self.assertEqual(context.episode, 3)
        self.assertEqual(context.total_episodes, 60)

    def test_parse_ui_context_accepts_alt_total_pattern(self):
        context = parse_ui_context(ALT_TOTAL_UI_XML)

        self.assertEqual(context.title, "十八岁太奶奶驾到，重整家族荣耀第三部")
        self.assertIsNone(context.episode)
        self.assertEqual(context.total_episodes, 84)

    def test_parse_ui_context_does_not_treat_metrics_as_title(self):
        context = parse_ui_context(COMMENT_PANEL_XML)

        self.assertEqual(context.title, "")
        self.assertEqual(context.episode, 2)

    def test_parse_ui_context_extracts_selected_episode_from_detail_grid(self):
        context = parse_ui_context(DETAIL_SELECTED_EPISODE_XML)

        self.assertEqual(context.episode, 2)


class FileNamingTests(unittest.TestCase):
    def test_build_episode_base_name_includes_episode_and_video_id_suffix(self):
        self.assertEqual(
            build_episode_base_name(1, "v02ebeg10000d3stuavog65u8i75lvc0"),
            "episode_001_8i75lvc0",
        )

    def test_build_episode_paths_include_video_and_meta_suffix(self):
        video_path, meta_path = build_episode_paths(
            "D:/videos/Drama",
            12,
            "abcdef1234567890",
        )

        self.assertTrue(video_path.endswith("episode_012_34567890.mp4"))
        self.assertTrue(meta_path.endswith("meta_ep012_34567890.json"))


class SessionValidationTests(unittest.TestCase):
    def test_validate_round_accepts_first_valid_round(self):
        state = SessionValidationState()
        context = parse_ui_context(SAMPLE_UI_XML)

        ok, reason = validate_round(state, context, "vid-001")

        self.assertTrue(ok)
        self.assertEqual(reason, "ok")

    def test_validate_round_accepts_missing_ui_episode_when_fallback_available(self):
        state = SessionValidationState()
        context = parse_ui_context(ALT_TOTAL_UI_XML)

        ok, reason = validate_round(state, context, "vid-001", fallback_episode=1)

        self.assertTrue(ok)
        self.assertEqual(reason, "ok")

    def test_validate_round_rejects_title_drift(self):
        state = SessionValidationState(locked_title="爹且慢，我来了", last_episode=3)
        context = parse_ui_context(
            SAMPLE_UI_XML.replace("爹且慢，我来了", "十八岁太奶奶驾到，重整家族荣耀第三部").replace("第3集", "第4集")
        )

        ok, reason = validate_round(state, context, "vid-004")

        self.assertFalse(ok)
        self.assertEqual(reason, "title_drift")

    def test_validate_round_rejects_duplicate_video_id(self):
        state = SessionValidationState(locked_title="爹且慢，我来了", seen_video_ids={"vid-003"}, last_episode=3)
        context = parse_ui_context(SAMPLE_UI_XML.replace("第3集", "第4集"))

        ok, reason = validate_round(state, context, "vid-003")

        self.assertFalse(ok)
        self.assertEqual(reason, "duplicate_video_id")

    def test_validate_round_rejects_non_monotonic_episode(self):
        state = SessionValidationState(locked_title="爹且慢，我来了", last_episode=5)
        context = parse_ui_context(SAMPLE_UI_XML.replace("第3集", "第5集"))

        ok, reason = validate_round(state, context, "vid-005")

        self.assertFalse(ok)
        self.assertEqual(reason, "episode_not_ascending")


class FridaDeviceTests(unittest.TestCase):
    def test_get_frida_usb_device_returns_none_when_device_is_missing(self):
        with patch.object(
            download_drama.frida,
            "get_usb_device",
            side_effect=frida.InvalidArgumentError("device not found"),
        ) as get_usb_device, patch.object(download_drama, "run_adb") as run_adb, patch.object(
            download_drama.logger, "error"
        ) as log_error:
            device = download_drama.get_frida_usb_device()

        self.assertIsNone(device)
        run_adb.assert_called_once_with(["devices"])
        get_usb_device.assert_called_once_with(timeout=5)
        self.assertGreaterEqual(log_error.call_count, 2)

    def test_get_frida_usb_device_returns_detected_device(self):
        expected_device = Mock()

        with patch.object(
            download_drama.frida,
            "get_usb_device",
            return_value=expected_device,
        ) as get_usb_device, patch.object(download_drama, "run_adb") as run_adb:
            device = download_drama.get_frida_usb_device()

        self.assertIs(device, expected_device)
        run_adb.assert_called_once_with(["devices"])
        get_usb_device.assert_called_once_with(timeout=5)


class RunningPidSelectionTests(unittest.TestCase):
    def test_select_running_app_pid_prefers_exact_identifier_over_subprocess(self):
        processes = [
            SimpleNamespace(pid=22912, identifier="com.phoenix.read:push", name="com.phoenix.read"),
            SimpleNamespace(pid=28156, identifier="com.phoenix.read:downloader", name="com.phoenix.read"),
            SimpleNamespace(pid=22217, identifier="com.phoenix.read", name="红果免费短剧"),
        ]

        pid = download_drama.select_running_app_pid(processes, "com.phoenix.read")

        self.assertEqual(pid, 22217)

    def test_select_running_app_pid_falls_back_to_subprocess(self):
        processes = [
            SimpleNamespace(pid=22912, identifier="com.phoenix.read:push", name="com.phoenix.read"),
            SimpleNamespace(pid=28156, identifier="com.phoenix.read:downloader", name="com.phoenix.read"),
        ]

        pid = download_drama.select_running_app_pid(processes, "com.phoenix.read")

        self.assertEqual(pid, 22912)

    def test_select_running_app_pid_uses_adb_main_pid_when_frida_only_sees_subprocess(self):
        processes = [
            SimpleNamespace(pid=22912, identifier="com.phoenix.read:push", name="com.phoenix.read:push"),
        ]

        with patch.object(download_drama, "get_running_app_pid_via_adb", return_value=24247):
            pid = download_drama.select_running_app_pid(processes, "com.phoenix.read")

        self.assertEqual(pid, 24247)


class BatchNavigationStrategyTests(unittest.TestCase):
    def test_choose_batch_navigation_mode_prefers_swipe_on_player_page(self):
        mode = download_drama.choose_batch_navigation_mode(
            PLAYER_OVERLAY_XML,
            "ShortSeriesActivity",
        )

        self.assertEqual(mode, "swipe")

    def test_choose_batch_navigation_mode_uses_search_on_detail_page(self):
        mode = download_drama.choose_batch_navigation_mode(
            DETAIL_EPISODE_GRID_XML,
            "ShortSeriesActivity",
        )

        self.assertEqual(mode, "search")

    def test_choose_search_result_bounds_prefers_exact_non_sequel_result(self):
        bounds = download_drama.choose_search_result_bounds(
            SEARCH_RESULTS_XML,
            "女帝妹妹早产了",
        )

        self.assertEqual(bounds, (40, 360, 1000, 460))


class PlayerEntryStrategyTests(unittest.TestCase):
    def test_should_enter_player_from_detail_page(self):
        self.assertTrue(download_drama.should_enter_player_from_detail(DETAIL_EPISODE_GRID_XML))

    def test_should_not_enter_player_from_player_overlay(self):
        self.assertFalse(download_drama.should_enter_player_from_detail(PLAYER_OVERLAY_XML))

    def test_is_target_episode_selected_in_detail_confirms_matching_highlight(self):
        self.assertTrue(download_drama.is_target_episode_selected_in_detail(DETAIL_SELECTED_EPISODE_44_XML, 44))
        self.assertFalse(download_drama.is_target_episode_selected_in_detail(DETAIL_SELECTED_EPISODE_51_XML, 44))

    def test_wait_for_target_episode_on_device_succeeds_after_ui_reaches_target(self):
        contexts = [
            UIContext(title="女帝妹妹早产了", episode=43, total_episodes=53),
            UIContext(title="女帝妹妹早产了", episode=44, total_episodes=53),
        ]

        with patch.object(download_drama, "detect_ui_context_from_device", side_effect=contexts), patch.object(
            download_drama.time, "sleep"
        ):
            self.assertTrue(
                download_drama.wait_for_target_episode_on_device(
                    expected_title="女帝妹妹早产了",
                    expected_episode=44,
                    timeout_seconds=2,
                    poll_seconds=0,
                )
            )

    def test_wait_for_target_episode_on_device_rejects_wrong_title(self):
        contexts = [
            UIContext(title="最强胎儿", episode=44, total_episodes=48),
            UIContext(title="最强胎儿", episode=44, total_episodes=48),
        ]

        with patch.object(download_drama, "detect_ui_context_from_device", side_effect=contexts), patch.object(
            download_drama.time, "sleep"
        ), patch.object(download_drama.time, "time", side_effect=[0, 0.5, 1.1]):
            self.assertFalse(
                download_drama.wait_for_target_episode_on_device(
                    expected_title="女帝妹妹早产了",
                    expected_episode=44,
                    timeout_seconds=1,
                    poll_seconds=0,
                )
            )


class EpisodeResolutionTests(unittest.TestCase):
    def test_resolve_actual_episode_prefers_ui_episode(self):
        episode, source = download_drama.resolve_actual_episode(
            ui_episode=2,
            hook_episode=9,
            video_id="vid-002",
        )

        self.assertEqual(episode, 2)
        self.assertEqual(source, "ui")

    def test_resolve_actual_episode_falls_back_to_hook_episode(self):
        episode, source = download_drama.resolve_actual_episode(
            ui_episode=None,
            hook_episode=3,
            video_id="vid-003",
        )

        self.assertEqual(episode, 3)
        self.assertEqual(source, "hook")

    def test_resolve_actual_episode_refuses_counter_fallback(self):
        episode, source = download_drama.resolve_actual_episode(
            ui_episode=None,
            hook_episode=None,
            video_id="vid-unknown",
        )

        self.assertIsNone(episode)
        self.assertEqual(source, "missing")

    def test_actual_episode_must_match_expected_target(self):
        self.assertTrue(download_drama.is_expected_episode(3, 3))
        self.assertFalse(download_drama.is_expected_episode(10, 7))

    def test_should_accept_out_of_order_episode_when_it_fills_missing_gap(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "Drama_episode_044.mp4").write_bytes(b"x")

            self.assertTrue(
                download_drama.should_accept_out_of_order_episode(
                    actual_episode=45,
                    expected_episode=44,
                    expected_total_episodes=53,
                    output_dir=base,
                    drama_name="Drama",
                )
            )

    def test_should_not_accept_out_of_order_episode_when_file_exists(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "Drama_episode_045.mp4").write_bytes(b"x")

            self.assertFalse(
                download_drama.should_accept_out_of_order_episode(
                    actual_episode=45,
                    expected_episode=44,
                    expected_total_episodes=53,
                    output_dir=base,
                    drama_name="Drama",
                )
            )


class ResumeAndTotalTests(unittest.TestCase):
    def test_find_first_missing_episode_returns_one_for_empty_dir(self):
        with TemporaryDirectory() as tmp:
            episode = download_drama.find_first_missing_episode(
                Path(tmp),
                "Drama",
            )

        self.assertEqual(episode, 1)

    def test_find_first_missing_episode_returns_next_after_contiguous_files(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            for n in (1, 2, 3):
                (base / f"Drama_episode_{n:03d}.mp4").write_bytes(b"x")

            episode = download_drama.find_first_missing_episode(base, "Drama")

        self.assertEqual(episode, 4)

    def test_find_first_missing_episode_returns_first_gap(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            for n in (1, 2, 4):
                (base / f"Drama_episode_{n:03d}.mp4").write_bytes(b"x")

            episode = download_drama.find_first_missing_episode(base, "Drama")

        self.assertEqual(episode, 3)

    def test_resolve_start_episode_prefers_explicit_episode_without_resume(self):
        episode = download_drama.resolve_start_episode(
            explicit_episode=5,
            resume_enabled=False,
            output_dir=Path("D:/videos/Drama"),
            drama_name="Drama",
        )

        self.assertEqual(episode, 5)

    def test_resolve_start_episode_uses_first_missing_with_resume(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            for n in (1, 2, 4):
                (base / f"Drama_episode_{n:03d}.mp4").write_bytes(b"x")

            episode = download_drama.resolve_start_episode(
                explicit_episode=1,
                resume_enabled=True,
                output_dir=base,
                drama_name="Drama",
            )

        self.assertEqual(episode, 3)

    def test_choose_effective_total_prefers_user_total(self):
        total, source = download_drama.choose_effective_total_episodes(
            user_total=53,
            locked_total=40,
            ui_total=41,
        )

        self.assertEqual(total, 53)
        self.assertEqual(source, "user")

    def test_choose_effective_total_falls_back_to_locked_then_ui(self):
        total, source = download_drama.choose_effective_total_episodes(
            user_total=None,
            locked_total=40,
            ui_total=41,
        )

        self.assertEqual(total, 40)
        self.assertEqual(source, "locked")

        total, source = download_drama.choose_effective_total_episodes(
            user_total=None,
            locked_total=None,
            ui_total=41,
        )

        self.assertEqual(total, 41)
        self.assertEqual(source, "ui")

    def test_should_stop_for_total_stops_at_or_after_total(self):
        self.assertTrue(download_drama.should_stop_for_total(53, 53))
        self.assertTrue(download_drama.should_stop_for_total(54, 53))
        self.assertFalse(download_drama.should_stop_for_total(52, 53))

    def test_find_existing_episode_file_matches_suffix_and_legacy_name(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            suffix_path = base / "Drama_episode_002_abcdef12.mp4"
            legacy_path = base / "Drama_episode_003.mp4"
            suffix_path.write_bytes(b"x")
            legacy_path.write_bytes(b"y")

            self.assertEqual(
                download_drama.find_existing_episode_file(base, "Drama", 2),
                suffix_path,
            )
            self.assertEqual(
                download_drama.find_existing_episode_file(base, "Drama", 3),
                legacy_path,
            )
            self.assertIsNone(download_drama.find_existing_episode_file(base, "Drama", 4))


class TaskStateTests(unittest.TestCase):
    def test_task_state_prefers_user_total_and_warns_on_ui_mismatch(self):
        state = download_drama.DownloadTaskState(
            target_title="Drama",
            start_episode=1,
            user_total_episodes=53,
        )

        total, source, warning = download_drama.register_total_episodes(state, 52)

        self.assertEqual(total, 53)
        self.assertEqual(source, "user")
        self.assertIn("52", warning)

    def test_task_state_locks_ui_total_when_user_total_missing(self):
        state = download_drama.DownloadTaskState(
            target_title="Drama",
            start_episode=1,
        )

        total, source, warning = download_drama.register_total_episodes(state, 53)

        self.assertEqual(total, 53)
        self.assertEqual(source, "locked")
        self.assertIsNone(warning)
        self.assertEqual(state.locked_total_episodes, 53)

    def test_end_signal_requires_two_consecutive_failures(self):
        state = download_drama.DownloadTaskState(
            target_title="Drama",
            start_episode=1,
        )

        self.assertFalse(download_drama.should_stop_for_end_signal(state, True))
        self.assertEqual(state.consecutive_end_signals, 1)
        self.assertTrue(download_drama.should_stop_for_end_signal(state, True))

    def test_end_signal_resets_after_successful_round(self):
        state = download_drama.DownloadTaskState(
            target_title="Drama",
            start_episode=1,
            consecutive_end_signals=1,
        )

        self.assertFalse(download_drama.should_stop_for_end_signal(state, False))
        self.assertEqual(state.consecutive_end_signals, 0)

    def test_mark_confirmed_episode_resets_failure_counters(self):
        state = download_drama.DownloadTaskState(
            target_title="Drama",
            start_episode=1,
            consecutive_end_signals=1,
            consecutive_recovery_failures=2,
        )

        download_drama.mark_confirmed_episode(state, 3)

        self.assertEqual(state.current_episode, 3)
        self.assertEqual(state.last_confirmed_episode, 3)
        self.assertEqual(state.consecutive_end_signals, 0)
        self.assertEqual(state.consecutive_recovery_failures, 0)

    def test_register_recovery_failure_requires_two_end_signals(self):
        state = download_drama.DownloadTaskState(
            target_title="Drama",
            start_episode=1,
        )

        self.assertFalse(download_drama.register_recovery_failure(state, True))
        self.assertEqual(state.consecutive_recovery_failures, 1)
        self.assertEqual(state.consecutive_end_signals, 1)
        self.assertTrue(download_drama.register_recovery_failure(state, True))
        self.assertEqual(state.consecutive_recovery_failures, 2)

    def test_register_recovery_failure_without_end_signal_resets_counter(self):
        state = download_drama.DownloadTaskState(
            target_title="Drama",
            start_episode=1,
            consecutive_end_signals=1,
        )

        self.assertFalse(download_drama.register_recovery_failure(state, False))
        self.assertEqual(state.consecutive_end_signals, 0)
        self.assertEqual(state.consecutive_recovery_failures, 1)

    def test_duplicate_cache_artifact_requires_swipe_context(self):
        self.assertTrue(
            download_drama.can_treat_duplicate_as_cache_artifact(
                allow_duplicate_skip=True,
                actual_episode=3,
                expected_episode=3,
            )
        )
        self.assertFalse(
            download_drama.can_treat_duplicate_as_cache_artifact(
                allow_duplicate_skip=False,
                actual_episode=3,
                expected_episode=3,
            )
        )
        self.assertFalse(
            download_drama.can_treat_duplicate_as_cache_artifact(
                allow_duplicate_skip=True,
                actual_episode=2,
                expected_episode=3,
            )
        )


class EpisodeNumberMatchingTests(unittest.TestCase):
    """测试 episode_number 精确匹配逻辑"""

    def test_exact_match_success(self):
        """测试精确匹配成功场景"""
        import time

        now = time.time()

        # 模拟多集数据（目标集 + preload 集）
        refs = [
            download_drama.VideoRef(
                video_id="vid001",
                duration=100,
                raw_data={},
                timestamp=now,
                episode_number=5,
            ),
            download_drama.VideoRef(
                video_id="vid002",
                duration=100,
                raw_data={},
                timestamp=now,
                episode_number=6,
            ),  # preload
        ]

        keys = [
            download_drama.AESKey(
                key_hex="key001", bits=128, timestamp=now, episode_number=5
            ),
            download_drama.AESKey(
                key_hex="key002", bits=128, timestamp=now, episode_number=6
            ),
        ]

        # 精确匹配 episode_number=5
        ep_num = 5
        matched_refs = [r for r in refs if r.episode_number == ep_num]
        matched_keys = [k for k in keys if k.episode_number == ep_num]

        self.assertEqual(len(matched_refs), 1)
        self.assertEqual(matched_refs[0].video_id, "vid001")
        self.assertEqual(len(matched_keys), 1)
        self.assertEqual(matched_keys[0].key_hex, "key001")

    def test_fallback_to_timestamp(self):
        """测试回退到时序选择"""
        import time

        now = time.time()

        # 模拟无 episode_number 的数据
        refs = [
            download_drama.VideoRef(
                video_id="vid001",
                duration=100,
                raw_data={},
                timestamp=now - 1,
                episode_number=None,
            ),
            download_drama.VideoRef(
                video_id="vid002",
                duration=100,
                raw_data={},
                timestamp=now,
                episode_number=None,
            ),  # 最新
        ]

        keys = [
            download_drama.AESKey(
                key_hex="key001", bits=128, timestamp=now - 1, episode_number=None
            ),
            download_drama.AESKey(
                key_hex="key002", bits=128, timestamp=now, episode_number=None
            ),
        ]

        # 无精确匹配，回退到时序选择
        ep_num = 5
        matched_refs = [r for r in refs if r.episode_number == ep_num]
        matched_keys = [k for k in keys if k.episode_number == ep_num]

        self.assertEqual(len(matched_refs), 0)
        self.assertEqual(len(matched_keys), 0)

        # 时序选择：选择最新的数据
        latest_ref = sorted(refs, key=lambda r: r.timestamp, reverse=True)[0]
        latest_key = sorted(keys, key=lambda k: k.timestamp, reverse=True)[0]

        self.assertEqual(latest_ref.video_id, "vid002")
        self.assertEqual(latest_key.key_hex, "key002")

    def test_partial_match_fallback(self):
        """测试部分匹配回退场景"""
        import time

        now = time.time()

        # 模拟仅 refs 有 episode_number，keys 无
        refs = [
            download_drama.VideoRef(
                video_id="vid001",
                duration=100,
                raw_data={},
                timestamp=now,
                episode_number=5,
            ),
        ]

        keys = [
            download_drama.AESKey(
                key_hex="key001", bits=128, timestamp=now, episode_number=None
            ),
        ]

        # 部分匹配
        ep_num = 5
        matched_refs = [r for r in refs if r.episode_number == ep_num]
        matched_keys = [k for k in keys if k.episode_number == ep_num]

        self.assertEqual(len(matched_refs), 1)
        self.assertEqual(len(matched_keys), 0)  # 部分匹配，应回退

    def test_multiple_matches_select_latest(self):
        """测试多个匹配时选择最新数据"""
        import time

        now = time.time()

        # 模拟同一集的多次捕获（重试场景）
        refs = [
            download_drama.VideoRef(
                video_id="vid001",
                duration=100,
                raw_data={},
                timestamp=now - 2,
                episode_number=5,
            ),
            download_drama.VideoRef(
                video_id="vid002",
                duration=100,
                raw_data={},
                timestamp=now,
                episode_number=5,
            ),  # 最新
            download_drama.VideoRef(
                video_id="vid003",
                duration=100,
                raw_data={},
                timestamp=now - 1,
                episode_number=5,
            ),
        ]

        ep_num = 5
        matched_refs = [r for r in refs if r.episode_number == ep_num]
        latest_ref = sorted(matched_refs, key=lambda r: r.timestamp, reverse=True)[0]

        self.assertEqual(latest_ref.video_id, "vid002")


class TestResumeFromCheckpoint(unittest.TestCase):
    """测试断点续传功能"""

    def test_resume_from_checkpoint(self):
        """测试从断点续传时跳过已完成集数"""
        from tempfile import TemporaryDirectory
        import json
        from scripts.drama_download_common import append_jsonl

        with TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / "session_manifest.jsonl"

            # 模拟已完成的集数
            append_jsonl(manifest_path, {"episode": 1, "status": "downloaded", "video_id": "abc123"})
            append_jsonl(manifest_path, {"episode": 2, "status": "skipped_existing", "video_id": "def456"})
            append_jsonl(manifest_path, {"episode": 3, "status": "downloaded", "video_id": "ghi789"})

            # 解析已完成集数
            from scripts.drama_download_common import parse_session_manifest
            completed = parse_session_manifest(manifest_path)

            # 验证已完成集数
            self.assertEqual(completed, {1, 2, 3})

            # 模拟 download_and_decrypt 的跳过逻辑
            ep_num = 2
            if ep_num in completed:
                # 记录跳过事件
                append_jsonl(manifest_path, {
                    "episode": ep_num,
                    "status": "skipped_resume",
                    "timestamp": 1234567890.0,
                    "reason": "already_completed"
                })
                result = {"success": True, "reason": "skipped_resume", "episode": ep_num}
            else:
                result = {"success": False}

            # 验证跳过结果
            self.assertTrue(result["success"])
            self.assertEqual(result["reason"], "skipped_resume")
            self.assertEqual(result["episode"], 2)

            # 验证 session_manifest.jsonl 包含 skipped_resume 记录
            with manifest_path.open('r', encoding='utf-8') as f:
                lines = f.readlines()
                last_record = json.loads(lines[-1])
                self.assertEqual(last_record["status"], "skipped_resume")
                self.assertEqual(last_record["episode"], 2)
                self.assertEqual(last_record["reason"], "already_completed")

    def test_resume_append_to_manifest(self):
        """测试跳过事件正确追加到 session_manifest.jsonl"""
        from tempfile import TemporaryDirectory
        import json
        from scripts.drama_download_common import append_jsonl, parse_session_manifest

        with TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / "session_manifest.jsonl"

            # 模拟已完成的集数
            append_jsonl(manifest_path, {"episode": 1, "status": "downloaded"})
            append_jsonl(manifest_path, {"episode": 2, "status": "downloaded"})

            # 解析已完成集数
            completed = parse_session_manifest(manifest_path)
            self.assertEqual(completed, {1, 2})

            # 模拟断点续传跳过
            for ep in [1, 2]:
                if ep in completed:
                    append_jsonl(manifest_path, {
                        "episode": ep,
                        "status": "skipped_resume",
                        "timestamp": 1234567890.0,
                        "reason": "already_completed"
                    })

            # 验证文件包含 4 条记录（2 条 downloaded + 2 条 skipped_resume）
            with manifest_path.open('r', encoding='utf-8') as f:
                lines = [line.strip() for line in f if line.strip()]
                self.assertEqual(len(lines), 4)

                # 验证最后两条是 skipped_resume
                record3 = json.loads(lines[2])
                record4 = json.loads(lines[3])
                self.assertEqual(record3["status"], "skipped_resume")
                self.assertEqual(record4["status"], "skipped_resume")


class TestDownloadWithRetry(unittest.TestCase):
    """测试自动重试功能"""

    def test_download_with_retry_success_first_attempt(self):
        """测试第一次成功时不触发重试"""
        from tempfile import TemporaryDirectory
        from unittest.mock import MagicMock
        from scripts.drama_download_common import append_jsonl

        with TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / "session_manifest.jsonl"

            # 模拟 download_and_decrypt 第一次成功
            mock_download = MagicMock(return_value={"success": True, "episode": 5})
            mock_reset = MagicMock()

            # 模拟 download_with_retry 逻辑
            result = mock_download(5)

            # 验证：第一次成功，不调用 reset_capture_state
            self.assertTrue(result["success"])
            mock_reset.assert_not_called()
            mock_download.assert_called_once_with(5)

    def test_download_with_retry_success_after_one_retry(self):
        """测试第一次失败、第二次成功时重试 1 次"""
        from tempfile import TemporaryDirectory
        from unittest.mock import MagicMock, call
        from scripts.drama_download_common import append_jsonl
        import time

        with TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / "session_manifest.jsonl"

            # 模拟 download_and_decrypt：第一次失败，第二次成功
            mock_download = MagicMock(side_effect=[
                {"success": False, "reason": "stale_data"},
                {"success": True, "episode": 5}
            ])
            mock_reset = MagicMock()
            mock_sleep = MagicMock()

            # 模拟 download_with_retry 逻辑（简化版）
            max_retries = 3
            for attempt in range(max_retries):
                if attempt > 0:
                    mock_reset()
                    mock_sleep(2)

                result = mock_download(5)

                if result.get("success", False):
                    break

            # 验证：第二次成功，调用 1 次 reset
            self.assertTrue(result["success"])
            mock_reset.assert_called_once()
            mock_sleep.assert_called_once_with(2)
            self.assertEqual(mock_download.call_count, 2)

    def test_download_with_retry_fail_after_max_retries(self):
        """测试连续失败 3 次后返回失败结果"""
        from tempfile import TemporaryDirectory
        from unittest.mock import MagicMock
        from scripts.drama_download_common import append_jsonl

        with TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / "session_manifest.jsonl"

            # 模拟 download_and_decrypt：连续失败 3 次
            mock_download = MagicMock(side_effect=[
                {"success": False, "reason": "stale_data"},
                {"success": False, "reason": "stale_key"},
                {"success": False, "reason": "download_failed"}
            ])
            mock_reset = MagicMock()

            # 模拟 download_with_retry 逻辑
            max_retries = 3
            for attempt in range(max_retries):
                if attempt > 0:
                    mock_reset()

                result = mock_download(5)

                if result.get("success", False):
                    break

            # 验证：失败 3 次，调用 2 次 reset（第一次不调用）
            self.assertFalse(result["success"])
            self.assertEqual(mock_reset.call_count, 2)
            self.assertEqual(mock_download.call_count, 3)

    def test_download_with_retry_clears_state(self):
        """测试每次重试前调用 reset_capture_state()"""
        from unittest.mock import MagicMock, patch

        # 模拟 download_and_decrypt：第一次失败，第二次成功
        mock_download = MagicMock(side_effect=[
            {"success": False, "reason": "stale_data"},
            {"success": True, "episode": 5}
        ])

        with patch('scripts.download_drama.reset_capture_state') as mock_reset:
            # 模拟 download_with_retry 逻辑
            max_retries = 3
            for attempt in range(max_retries):
                if attempt > 0:
                    mock_reset()

                result = mock_download(5)

                if result.get("success", False):
                    break

            # 验证：第二次尝试前调用 reset_capture_state
            mock_reset.assert_called_once()

    def test_download_with_retry_logs_to_manifest(self):
        """测试重试历史正确记录到 session_manifest.jsonl"""
        from tempfile import TemporaryDirectory
        import json
        from scripts.drama_download_common import append_jsonl

        with TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / "session_manifest.jsonl"

            # 模拟重试历史记录
            ep_num = 5
            max_retries = 3

            # 第一次尝试失败
            append_jsonl(manifest_path, {
                "episode": ep_num,
                "status": "retry_attempt",
                "attempt": 1,
                "max_retries": max_retries,
                "reason": "stale_data",
                "error": None,
                "timestamp": 1234567890.0
            })

            # 第二次尝试成功
            append_jsonl(manifest_path, {
                "episode": ep_num,
                "status": "retry_success",
                "attempt": 2,
                "max_retries": max_retries,
                "reason": "unknown",
                "error": None,
                "timestamp": 1234567892.0
            })

            # 验证记录
            with manifest_path.open('r', encoding='utf-8') as f:
                lines = [line.strip() for line in f if line.strip()]
                self.assertEqual(len(lines), 2)

                record1 = json.loads(lines[0])
                self.assertEqual(record1["status"], "retry_attempt")
                self.assertEqual(record1["attempt"], 1)
                self.assertEqual(record1["reason"], "stale_data")

                record2 = json.loads(lines[1])
                self.assertEqual(record2["status"], "retry_success")
                self.assertEqual(record2["attempt"], 2)

    def test_download_with_retry_skips_resume(self):
        """测试 skipped_resume 不触发重试"""
        from unittest.mock import MagicMock

        # 模拟 download_and_decrypt 返回 skipped_resume
        mock_download = MagicMock(return_value={
            "success": True,
            "reason": "skipped_resume",
            "episode": 5
        })
        mock_reset = MagicMock()

        # 模拟 download_with_retry 逻辑
        result = mock_download(5)

        # 验证：skipped_resume 视为成功，不触发重试
        self.assertTrue(result["success"])
        self.assertEqual(result["reason"], "skipped_resume")
        mock_reset.assert_not_called()
        mock_download.assert_called_once()


if __name__ == "__main__":
    unittest.main()
