import pytest
from pathlib import Path
from scripts.video_splitter import split_episodes

def test_split_episodes(mocker, tmp_path):
    """测试切分视频功能"""
    split_plan = [
        {"episode": 1, "start": 0, "end": 65, "confidence": "detected"},
        {"episode": 2, "start": 65, "end": 130, "confidence": "detected"},
    ]

    full_video = "test_full.mp4"
    output_dir = str(tmp_path / "独立集数")

    mock_run = mocker.patch('subprocess.run')
    mock_run.return_value.returncode = 0

    result = split_episodes(full_video, split_plan, output_dir)

    assert result == 2
    assert mock_run.call_count == 2
