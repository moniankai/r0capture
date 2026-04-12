import unittest
from unittest.mock import Mock, patch

import frida
from scripts import download_drama
from scripts.drama_download_common import (
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


if __name__ == "__main__":
    unittest.main()
