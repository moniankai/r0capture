import pytest
from pathlib import Path
from scripts.output_validator import validate_output

def test_validate_output(tmp_path, mocker):
    """测试输出验证功能"""
    output_dir = tmp_path / "独立集数"
    output_dir.mkdir()

    # 创建测试文件
    (output_dir / "episode_001.mp4").write_bytes(b"x" * 200000)  # 正常
    (output_dir / "episode_002.mp4").write_bytes(b"x" * 50000)   # 过小
    # episode_003.mp4 缺失

    mock_duration = mocker.patch('scripts.output_validator.get_mp4_duration')
    mock_duration.side_effect = [65.0, 5.0]  # 第2集时长过短

    issues = validate_output(str(output_dir), expected_episodes=3)

    assert len(issues) >= 2
    assert any("缺失" in issue and "3" in issue for issue in issues)
    assert any("过小" in issue and "2" in issue for issue in issues)
