"""测试 CaptureState 单例模式和时间戳过滤逻辑"""

import sys
import time
from pathlib import Path

# 添加 scripts 目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import pytest
from download_drama import (
    get_capture_state,
    reset_capture_state,
    CaptureState,
    VideoRef,
)


class TestCaptureStateSingleton:
    """测试单例模式"""

    def test_singleton_pattern(self):
        """测试 get_capture_state 返回同一实例"""
        state1 = get_capture_state()
        state2 = get_capture_state()
        assert state1 is state2, "get_capture_state 应返回同一实例"

    def test_reset_creates_new_instance(self):
        """测试 reset_capture_state 创建新实例"""
        state1 = get_capture_state()
        state1.video_refs.append(
            VideoRef("test_id", 100, {"url": "http://test"}, timestamp=time.time())
        )

        reset_capture_state()
        state2 = get_capture_state()

        assert state1 is not state2, "reset 后应返回新实例"
        assert len(state2.video_refs) == 0, "新实例应为空"

    def test_state_isolation_after_reset(self):
        """测试 reset 后状态隔离"""
        state1 = get_capture_state()
        state1.video_urls.append("http://old_url")
        state1.aes_keys.append("old_key")

        reset_capture_state()
        state2 = get_capture_state()

        assert len(state2.video_urls) == 0, "新实例不应包含旧 URL"
        assert len(state2.aes_keys) == 0, "新实例不应包含旧密钥"


class TestVideoRefTimestamp:
    """测试 VideoRef 时间戳字段"""

    def test_videoref_has_timestamp_field(self):
        """测试 VideoRef 包含 timestamp 字段"""
        ref = VideoRef("id1", 100, {"url": "http://test"}, timestamp=123.456)
        assert hasattr(ref, "timestamp"), "VideoRef 应有 timestamp 字段"
        assert ref.timestamp == 123.456, "timestamp 值应正确"

    def test_videoref_default_timestamp(self):
        """测试 VideoRef 默认 timestamp 为 0.0"""
        ref = VideoRef("id1", 100, {"url": "http://test"})
        assert ref.timestamp == 0.0, "默认 timestamp 应为 0.0"

    def test_videoref_has_context_field(self):
        """测试 VideoRef 包含 context 字段"""
        ref = VideoRef("id1", 100, {"url": "http://test"})
        assert hasattr(ref, "context"), "VideoRef 应有 context 字段"
        assert isinstance(ref.context, dict), "context 应为字典"
        assert len(ref.context) == 0, "默认 context 应为空"


class TestTimestampFiltering:
    """测试时间戳过滤逻辑"""

    def test_filter_recent_data(self):
        """测试过滤最近 5 秒内的数据"""
        now = time.time()

        refs = [
            VideoRef("id1", 100, {"url": "http://test1"}, timestamp=now - 10),  # 过期
            VideoRef("id2", 100, {"url": "http://test2"}, timestamp=now - 2),    # 新鲜
            VideoRef("id3", 100, {"url": "http://test3"}, timestamp=now - 0.5), # 新鲜
        ]

        FRESHNESS_THRESHOLD = 5.0
        recent = [r for r in refs if now - r.timestamp < FRESHNESS_THRESHOLD]

        assert len(recent) == 2, "应过滤出 2 个新鲜数据"
        assert recent[0].video_id == "id2"
        assert recent[1].video_id == "id3"

    def test_select_newest_data(self):
        """测试选择最新的数据"""
        now = time.time()

        refs = [
            VideoRef("id1", 100, {"url": "http://test1"}, timestamp=now - 3),
            VideoRef("id2", 100, {"url": "http://test2"}, timestamp=now - 1),  # 最新
            VideoRef("id3", 100, {"url": "http://test3"}, timestamp=now - 2),
        ]

        newest = max(refs, key=lambda r: r.timestamp)
        assert newest.video_id == "id2", "应选择最新的数据"

    def test_all_data_expired(self):
        """测试所有数据都过期的情况"""
        now = time.time()

        refs = [
            VideoRef("id1", 100, {"url": "http://test1"}, timestamp=now - 10),
            VideoRef("id2", 100, {"url": "http://test2"}, timestamp=now - 20),
        ]

        FRESHNESS_THRESHOLD = 5.0
        recent = [r for r in refs if now - r.timestamp < FRESHNESS_THRESHOLD]

        assert len(recent) == 0, "所有数据都应被过滤"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
