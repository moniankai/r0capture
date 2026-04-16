"""测试调试日志功能"""
import os
import tempfile
from pathlib import Path

from scripts.drama_download_common import (
    UIContext,
    append_debug_log,
    log_episode_details,
)


def test_append_debug_log():
    """测试追加调试日志"""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_path = Path(tmpdir) / "test.log"

        append_debug_log(log_path, "测试消息 1", level='INFO')
        append_debug_log(log_path, "测试消息 2", level='WARNING')
        append_debug_log(log_path, "测试消息 3", level='ERROR')

        content = log_path.read_text(encoding='utf-8')

        assert "测试消息 1" in content
        assert "测试消息 2" in content
        assert "测试消息 3" in content
        assert "[INFO   ]" in content
        assert "[WARNING]" in content
        assert "[ERROR  ]" in content


def test_log_episode_details_minimal():
    """测试记录最小集数详情"""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_path = Path(tmpdir) / "test.log"

        log_episode_details(
            log_path,
            episode=1,
            status='downloading',
        )

        content = log_path.read_text(encoding='utf-8')

        assert "第 1 集下载详情" in content
        assert "状态: downloading" in content


def test_log_episode_details_full():
    """测试记录完整集数详情"""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_path = Path(tmpdir) / "test.log"

        ui_context = UIContext(
            title="测试短剧",
            episode=5,
            total_episodes=60,
        )

        log_episode_details(
            log_path,
            episode=5,
            video_id="abc123def456",
            cdn_url="https://cdn.example.com/video.mp4",
            aes_key_hex="0123456789abcdef0123456789abcdef",
            resolution="720p",
            file_size=10485760,  # 10 MB
            status='success',
            ui_context=ui_context,
            extra_info={
                'codec': 'hvc1',
                'decrypted_samples': 1234,
            }
        )

        content = log_path.read_text(encoding='utf-8')

        # 检查基本信息
        assert "第 5 集下载详情" in content
        assert "剧名: 测试短剧" in content
        assert "当前集数: 5" in content
        assert "总集数: 60" in content
        assert "状态: success" in content

        # 检查视频信息
        assert "Video ID: abc123def456" in content
        assert "Video ID (后8位): 23def456" in content  # video_id_suffix 取最后 8 位
        assert "CDN URL: https://cdn.example.com/video.mp4" in content
        assert "AES 密钥 (hex): 0123456789abcdef0123456789abcdef" in content
        assert "分辨率: 720p" in content
        assert "文件大小: 10.00 MB" in content

        # 检查额外信息
        assert "额外信息:" in content
        assert "codec: hvc1" in content
        assert "decrypted_samples: 1234" in content


def test_log_episode_details_with_error():
    """测试记录失败集数详情"""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_path = Path(tmpdir) / "test.log"

        log_episode_details(
            log_path,
            episode=10,
            video_id="test123",
            status='failed',
            error='CDN download timeout',
        )

        content = log_path.read_text(encoding='utf-8')

        assert "第 10 集下载详情" in content
        assert "状态: failed" in content
        assert "错误信息: CDN download timeout" in content
        assert "Video ID: test123" in content


def test_log_multiple_episodes():
    """测试记录多个集数"""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_path = Path(tmpdir) / "test.log"

        for ep in range(1, 4):
            log_episode_details(
                log_path,
                episode=ep,
                video_id=f"vid{ep:03d}",
                status='success',
            )

        content = log_path.read_text(encoding='utf-8')

        assert "第 1 集下载详情" in content
        assert "第 2 集下载详情" in content
        assert "第 3 集下载详情" in content
        assert "Video ID: vid001" in content
        assert "Video ID: vid002" in content
        assert "Video ID: vid003" in content

        # 检查分隔符
        assert content.count("=" * 80) >= 6  # 每集 2 个分隔符
