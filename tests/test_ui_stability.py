"""测试 UI 稳定性检查逻辑"""

import sys
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

# 添加 scripts 目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import pytest
from drama_download_common import UIContext


class TestWaitForUIStable:
    """测试 wait_for_ui_stable 函数"""

    @patch("download_drama.detect_ui_context_from_device")
    def test_ui_stable_immediately(self, mock_detect):
        """测试 UI 立即稳定的情况"""
        from download_drama import wait_for_ui_stable

        # 模拟 UI 立即返回正确集数
        mock_detect.return_value = UIContext(
            title="测试剧",
            episode=5,
            total_episodes=10
        )

        result = wait_for_ui_stable(expected_ep=5, timeout=10.0)

        assert result is True, "UI 匹配时应返回 True"
        assert mock_detect.call_count >= 1, "应至少调用一次 detect"

    @patch("download_drama.detect_ui_context_from_device")
    def test_ui_stable_after_delay(self, mock_detect):
        """测试 UI 延迟后稳定的情况"""
        from download_drama import wait_for_ui_stable

        # 模拟 UI 先返回旧集数，后返回新集数
        mock_detect.side_effect = [
            UIContext("测试剧", 3, 10),  # 第 1 次：旧集数
            UIContext("测试剧", 3, 10),  # 第 2 次：仍是旧集数
            UIContext("测试剧", 5, 10),  # 第 3 次：新集数
        ]

        result = wait_for_ui_stable(expected_ep=5, timeout=10.0, poll_interval=0.1)

        assert result is True, "UI 最终匹配时应返回 True"
        assert mock_detect.call_count == 3, "应调用 3 次 detect"

    @patch("download_drama.detect_ui_context_from_device")
    def test_ui_timeout(self, mock_detect):
        """测试 UI 超时的情况"""
        from download_drama import wait_for_ui_stable

        # 模拟 UI 始终返回错误集数
        mock_detect.return_value = UIContext("测试剧", 3, 10)

        result = wait_for_ui_stable(expected_ep=5, timeout=1.0, poll_interval=0.2)

        assert result is False, "超时时应返回 False"
        assert mock_detect.call_count >= 4, "应多次尝试"

    @patch("download_drama.detect_ui_context_from_device")
    def test_ui_parse_failure(self, mock_detect):
        """测试 UI 解析失败的情况"""
        from download_drama import wait_for_ui_stable

        # 模拟 UI 解析失败（返回 None）
        mock_detect.side_effect = [
            None,  # 第 1 次：解析失败
            None,  # 第 2 次：解析失败
            UIContext("测试剧", 5, 10),  # 第 3 次：成功
        ]

        result = wait_for_ui_stable(expected_ep=5, timeout=10.0, poll_interval=0.1)

        assert result is True, "最终成功时应返回 True"
        assert mock_detect.call_count == 3, "应调用 3 次 detect"

    @patch("download_drama.detect_ui_context_from_device")
    def test_ui_always_fails(self, mock_detect):
        """测试 UI 始终解析失败的情况"""
        from download_drama import wait_for_ui_stable

        # 模拟 UI 始终解析失败
        mock_detect.return_value = None

        result = wait_for_ui_stable(expected_ep=5, timeout=1.0, poll_interval=0.2)

        assert result is False, "始终失败时应返回 False"
        assert mock_detect.call_count >= 4, "应多次尝试"

    @patch("download_drama.detect_ui_context_from_device")
    def test_poll_interval_respected(self, mock_detect):
        """测试轮询间隔是否生效"""
        from download_drama import wait_for_ui_stable

        # 模拟 UI 始终返回错误集数
        mock_detect.return_value = UIContext("测试剧", 3, 10)

        start = time.time()
        result = wait_for_ui_stable(expected_ep=5, timeout=1.0, poll_interval=0.3)
        elapsed = time.time() - start

        assert result is False, "超时时应返回 False"
        assert elapsed >= 1.0, "应至少等待 timeout 时间"
        assert elapsed < 1.5, "不应超时太多"


class TestTwoPhaseDownload:
    """测试两阶段下载模式集成"""

    @patch("download_drama.select_episode_from_ui")
    @patch("download_drama.wait_for_ui_stable")
    @patch("download_drama.get_capture_state")
    def test_ui_stable_success(self, mock_state, mock_wait, mock_select):
        """测试 UI 稳定成功的情况"""
        # 此测试需要 download_and_decrypt 函数的完整实现
        # 暂时跳过，留待集成测试
        pass

    @patch("download_drama.select_episode_from_ui")
    @patch("download_drama.wait_for_ui_stable")
    @patch("download_drama.reset_capture_state")
    def test_ui_stable_failure_clears_state(self, mock_reset, mock_wait, mock_select):
        """测试 UI 未稳定时清空 state"""
        # 此测试需要 download_and_decrypt 函数的完整实现
        # 暂时跳过，留待集成测试
        pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
