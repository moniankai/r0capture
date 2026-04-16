"""输出验证模块"""
import os
import struct
from pathlib import Path
from typing import List
from loguru import logger


def get_mp4_duration(filepath: str) -> float:
    """提取 MP4 文件时长"""
    try:
        with open(filepath, "rb") as f:
            while True:
                header = f.read(8)
                if len(header) < 8:
                    break
                box_size = struct.unpack(">I", header[:4])[0]
                box_type = header[4:8]
                if box_size == 0:
                    break
                if box_type == b"moov":
                    moov_data = f.read(min(box_size - 8, 200))
                    idx = moov_data.find(b"mvhd")
                    if idx >= 0:
                        mvhd = moov_data[idx:]
                        version = mvhd[4]
                        if version == 0:
                            timescale = struct.unpack(">I", mvhd[16:20])[0]
                            dur = struct.unpack(">I", mvhd[20:24])[0]
                        else:
                            timescale = struct.unpack(">I", mvhd[24:28])[0]
                            dur = struct.unpack(">Q", mvhd[28:36])[0]
                        if timescale > 0:
                            return dur / timescale
                    break
                else:
                    f.seek(box_size - 8, 1)
    except Exception:
        pass
    return 0.0


def validate_output(output_dir: str, expected_episodes: int = 60) -> List[str]:
    """
    验证输出文件的完整性和质量

    Args:
        output_dir: 输出目录
        expected_episodes: 预期集数

    Returns:
        问题列表
    """
    issues = []

    logger.info(f"验证输出: {output_dir}")

    for i in range(1, expected_episodes + 1):
        filepath = Path(output_dir) / f"episode_{i:03d}.mp4"

        # 检查文件是否存在
        if not filepath.exists():
            issues.append(f"缺失: 第 {i} 集")
            continue

        # 检查文件大小
        size = os.path.getsize(filepath)
        if size < 100_000:
            issues.append(f"异常: 第 {i} 集文件过小 ({size/1024:.1f}KB)")

        # 检查时长
        duration = get_mp4_duration(str(filepath))
        if duration < 10:
            issues.append(f"异常: 第 {i} 集时长过短 ({duration:.1f}秒)")
        elif duration > 300:
            issues.append(f"警告: 第 {i} 集时长过长 ({duration:.1f}秒)")

    if issues:
        logger.warning(f"发现 {len(issues)} 个问题")
    else:
        logger.info("验证通过，无问题")

    return issues
