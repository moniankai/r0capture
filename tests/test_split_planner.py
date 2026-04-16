import pytest
from scripts.split_planner import generate_split_plan

def test_generate_split_plan():
    """测试生成切分计划"""
    boundaries = [
        {"episode": 1, "start_time": 0},
        {"episode": 2, "start_time": 65},
        {"episode": 4, "start_time": 195},  # 缺失第 3 集
    ]
    total_duration = 300.0
    expected_episodes = 4

    plan = generate_split_plan(boundaries, total_duration, expected_episodes)

    assert len(plan) == 4
    assert plan[0]['episode'] == 1
    assert plan[0]['start'] == 0
    assert plan[0]['end'] == 65
    assert plan[0]['confidence'] == 'detected'

    assert plan[2]['episode'] == 3
    assert plan[2]['confidence'] == 'estimated'  # 插值估算

    assert plan[3]['end'] == 300.0  # 最后一集到结尾
