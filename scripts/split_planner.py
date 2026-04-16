"""切分计划生成模块"""
from typing import List, Dict
from loguru import logger


def generate_split_plan(boundaries: List[Dict], total_duration: float, expected_episodes: int = 60) -> List[Dict]:
    """
    根据 OCR 边界生成完整的切分计划

    Args:
        boundaries: OCR 检测的边界列表
        total_duration: 视频总时长（秒）
        expected_episodes: 预期集数

    Returns:
        切分计划 [{"episode": 1, "start": 0, "end": 65, "confidence": "detected"}, ...]
    """
    split_plan = []

    for i in range(expected_episodes):
        episode_num = i + 1

        # 查找该集的起始时间
        start_time = next((b['start_time'] for b in boundaries if b['episode'] == episode_num), None)

        # 查找下一集的起始时间（作为结束时间）
        if i < expected_episodes - 1:
            end_time = next((b['start_time'] for b in boundaries if b['episode'] == episode_num + 1), None)
        else:
            end_time = total_duration

        # 处理缺失边界
        if start_time is None:
            # 使用插值估算
            prev_boundary = next((b for b in boundaries if b['episode'] < episode_num), None)
            next_boundary = next((b for b in boundaries if b['episode'] > episode_num), None)

            if prev_boundary and next_boundary:
                start_time = (prev_boundary['start_time'] + next_boundary['start_time']) / 2
            elif prev_boundary:
                start_time = prev_boundary['start_time'] + 60  # 假设每集 60 秒
            else:
                start_time = 0

            confidence = 'estimated'
            logger.warning(f"第 {episode_num} 集边界缺失，使用插值估算: {start_time:.1f}s")
        else:
            confidence = 'detected'

        split_plan.append({
            'episode': episode_num,
            'start': start_time,
            'end': end_time if end_time else total_duration,
            'duration': (end_time - start_time) if end_time else None,
            'confidence': confidence
        })

    return split_plan
