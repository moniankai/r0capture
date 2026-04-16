"""视频切分执行模块"""
import subprocess
from pathlib import Path
from typing import List, Dict
from tqdm import tqdm
from loguru import logger


def split_episodes(full_video: str, split_plan: List[Dict], output_dir: str) -> int:
    """
    根据切分计划切分视频

    Args:
        full_video: 全集视频路径
        split_plan: 切分计划
        output_dir: 输出目录

    Returns:
        成功切分的集数
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    logger.info(f"开始切分 {len(split_plan)} 集视频")

    success_count = 0

    for item in tqdm(split_plan, desc="切分集数"):
        episode_num = item['episode']
        start = item['start']
        end = item['end']

        output_file = output_path / f"episode_{episode_num:03d}.mp4"

        cmd = [
            'ffmpeg',
            '-i', full_video,
            '-ss', str(start),
            '-to', str(end),
            '-c', 'copy',
            '-avoid_negative_ts', '1',
            '-y',
            str(output_file)
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            logger.error(f"切分第 {episode_num} 集失败: {result.stderr}")
        else:
            success_count += 1

    logger.info(f"切分完成: {success_count}/{len(split_plan)} 集")
    return success_count
