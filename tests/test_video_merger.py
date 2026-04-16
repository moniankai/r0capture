import pytest
from pathlib import Path
from scripts.video_merger import merge_videos

def test_merge_videos(mocker, tmp_path):
    """测试合并视频功能"""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()

    concat_list = cache_dir / "concat_list.txt"
    concat_list.write_text("file '001_test.mdl'\nfile '002_test.mdl'\n")

    output_dir = tmp_path / "全集"
    drama_name = "测试短剧"

    mock_run = mocker.patch('subprocess.run')
    mock_run.return_value.returncode = 0

    result = merge_videos(str(cache_dir), str(output_dir), drama_name)

    assert result == str(output_dir / f"{drama_name}_全集.mp4")
    assert mock_run.called
    args = mock_run.call_args[0][0]
    assert "ffmpeg" in args
    assert "-f" in args and "concat" in args
