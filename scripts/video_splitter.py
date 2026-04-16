"""视频切分执行模块"""
import subprocess
import shutil
from pathlib import Path
from typing import List, Dict
from tqdm import tqdm
from loguru import logger


def find_ffmpeg() -> str:
    """查找 ffmpeg 可执行文件路径"""
    # 首先尝试从 PATH 中查找
    ffmpeg_path = shutil.which("ffmpeg")
    if ffmpeg_path:
        return ffmpeg_path

    # 如果 PATH 中没有，尝试 WinGet 安装位置
    import os
    winget_base = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages"
    if winget_base.exists():
        for ffmpeg_exe in winget_base.rglob("ffmpeg.exe"):
            if "bin" in str(ffmpeg_exe):
                return str(ffmpeg_exe)

    # 最后尝试常见安装位置
    common_paths = [
        r"C:\ffmpeg\bin\ffmpeg.exe",
        r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
    ]
    for path in common_paths:
        if Path(path).exists():
            return path

    return "ffmpeg"  # 回退到默认值


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

    # 查找 ffmpeg
    ffmpeg_cmd = find_ffmpeg()

    success_count = 0

    for item in tqdm(split_plan, desc="切分集数"):
        episode_num = item['episode']
        start = item['start']
        end = item['end']

        output_file = output_path / f"episode_{episode_num:03d}.mp4"

        cmd = [
            ffmpeg_cmd,
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
