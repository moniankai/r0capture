"""OCR 集数边界识别模块"""
import re
from typing import List, Dict
import cv2
import easyocr
from loguru import logger


def detect_episode_boundaries(video_path: str, sample_interval: int = 30) -> List[Dict]:
    """
    使用 OCR 识别视频中的集数边界

    Args:
        video_path: 视频文件路径
        sample_interval: 采样间隔（秒）

    Returns:
        边界列表 [{"episode": 1, "start_time": 0, "confidence": 0.95}, ...]
    """
    logger.info(f"开始 OCR 识别: {video_path}")
    logger.info("加载 EasyOCR 模型...")

    reader = easyocr.Reader(['ch_sim', 'en'], gpu=True)
    cap = cv2.VideoCapture(video_path)

    if not cap.isOpened():
        raise RuntimeError(f"无法打开视频: {video_path}")

    frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT)
    fps = cap.get(cv2.CAP_PROP_FPS)
    duration = frame_count / fps

    logger.info(f"视频时长: {duration:.1f} 秒, 采样间隔: {sample_interval} 秒")

    boundaries = []
    current_episode = 0

    for timestamp in range(0, int(duration), sample_interval):
        cap.set(cv2.CAP_PROP_POS_MSEC, timestamp * 1000)
        ret, frame = cap.read()

        if not ret:
            continue

        # OCR 识别
        results = reader.readtext(frame)

        for (bbox, text, confidence) in results:
            # 匹配集数模式
            match = re.search(r'第\s*(\d+)\s*集|EP\s*(\d+)|(\d+)\s*/\s*\d+', text)

            if match and confidence > 0.7:
                episode_num = int(match.group(1) or match.group(2) or match.group(3))

                # 检测到新集数
                if episode_num > current_episode:
                    boundaries.append({
                        'episode': episode_num,
                        'start_time': timestamp,
                        'confidence': confidence,
                        'text': text
                    })
                    current_episode = episode_num
                    logger.info(f"检测到第 {episode_num} 集 @ {timestamp}s (置信度: {confidence:.2f})")

    cap.release()
    logger.info(f"OCR 完成，检测到 {len(boundaries)} 个集数边界")

    return boundaries
