"""视频合并模块"""
import subprocess
import shutil
from pathlib import Path
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

    # 查找 ffmpeg
    ffmpeg_cmd = find_ffmpeg()
    logger.info(f"使用 ffmpeg: {ffmpeg_cmd}")

    cmd = [
        ffmpeg_cmd,
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
