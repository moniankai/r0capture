"""视频合并模块"""
import subprocess
from pathlib import Path
from loguru import logger


def merge_videos(cache_dir: str, output_dir: str, drama_name: str) -> str:
    """使用 ffmpeg 合并视频文件"""
    cache_path = Path(cache_dir)
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    concat_list = cache_path / "concat_list.txt"
    if not concat_list.exists():
        raise FileNotFoundError(f"concat 列表不存在: {concat_list}")

    output_file = output_path / f"{drama_name}_全集.mp4"

    logger.info(f"开始合并视频: {output_file}")

    cmd = [
        "ffmpeg",
        "-f", "concat",
        "-safe", "0",
        "-i", str(concat_list),
        "-c", "copy",
        "-y",
        str(output_file)
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        logger.error(f"ffmpeg 合并失败: {result.stderr}")
        raise RuntimeError(f"视频合并失败: {result.stderr}")

    logger.info(f"合并完成: {output_file}")
    return str(output_file)
