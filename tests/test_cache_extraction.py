"""端到端集成测试 - 缓存提取工具"""
import pytest
from pathlib import Path
from scripts.extract_drama_from_cache import main


def test_end_to_end_extraction(mocker, tmp_path, monkeypatch):
    """端到端集成测试"""
    # 创建必要的目录结构
    drama_dir = tmp_path / "测试短剧"
    drama_dir.mkdir()

    # Mock 命令行参数
    test_args = [
        "extract_drama_from_cache.py",
        "--drama-name", "测试短剧",
        "--output", str(tmp_path),
        "--expected-episodes", "3"
    ]
    monkeypatch.setattr("sys.argv", test_args)

    # Mock 各个模块（需要 Mock 导入到 extract_drama_from_cache 中的函数）
    mock_pull = mocker.patch('scripts.extract_drama_from_cache.pull_and_sort_cache')
    mock_pull.return_value = 45

    mock_merge = mocker.patch('scripts.extract_drama_from_cache.merge_videos')

    mock_ocr = mocker.patch('scripts.extract_drama_from_cache.detect_episode_boundaries')
    mock_ocr.return_value = [
        {"episode": 1, "start_time": 0, "confidence": 0.95},
        {"episode": 2, "start_time": 65, "confidence": 0.92},
        {"episode": 3, "start_time": 130, "confidence": 0.88},
    ]

    mock_plan = mocker.patch('scripts.extract_drama_from_cache.generate_split_plan')
    mock_plan.return_value = [
        {"episode": 1, "start": 0, "end": 65, "confidence": "detected"},
        {"episode": 2, "start": 65, "end": 130, "confidence": "detected"},
        {"episode": 3, "start": 130, "end": 195, "confidence": "detected"},
    ]

    mock_split = mocker.patch('scripts.extract_drama_from_cache.split_episodes')
    mock_split.return_value = 3

    mock_duration = mocker.patch('scripts.extract_drama_from_cache.get_mp4_duration')
    mock_duration.return_value = 195.0

    mock_validate = mocker.patch('scripts.extract_drama_from_cache.validate_output')
    mock_validate.return_value = []

    # Mock Path.stat() 用于文件大小检查
    mock_stat = mocker.MagicMock()
    mock_stat.st_size = 10 * 1024 * 1024  # 10MB
    mocker.patch('pathlib.Path.stat', return_value=mock_stat)

    # 执行主流程
    main()

    # 验证调用
    assert mock_pull.called
    assert mock_merge.called
    assert mock_ocr.called
    assert mock_plan.called
    assert mock_split.called
    assert mock_validate.called
