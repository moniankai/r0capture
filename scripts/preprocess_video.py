"""
Preprocess drama videos into LLM-ready packages.

For each episode MP4, generates:
  - transcript.srt / transcript.txt  (ASR via faster-whisper)
  - keyframes/*.jpg                  (scene-change + interval-based extraction)
  - manifest.json                    (metadata for downstream pipeline)


  python scripts/preprocess_video.py videos/爹且慢，我来了
  python scripts/preprocess_video.py videos/爹且慢，我来了/episode_001.mp4
  python scripts/preprocess_video.py videos/爹且慢，我来了 --model large-v3
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from loguru import logger


def get_ffmpeg() -> str:
    """Get ffmpeg executable path."""
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        return "ffmpeg"


def get_video_duration(ffmpeg: str, video_path: str) -> float:
    """Get video duration in seconds via ffprobe/ffmpeg."""
    # ffmpeg 
    result = subprocess.run(
        [ffmpeg, "-i", video_path],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    # ffmpeg 处理 stderr
    for line in result.stderr.split("\n"):
        if "Duration:" in line:
            # Duration: 00:01:25.08, ...
            parts = line.split("Duration:")[1].split(",")[0].strip()
            h, m, s = parts.split(":")
            return int(h) * 3600 + int(m) * 60 + float(s)
    return 0.0


def extract_keyframes(
    ffmpeg: str,
    video_path: str,
    output_dir: str,
    scene_threshold: float = 0.20,
    interval_sec: float = 2.0,
) -> list[dict]:
    """回退 using scene detection + fixed interval .

    
      1. Scene-change detection (threshold=0.20, catches most cuts)
      2. Fixed interval (every 2s) to fill gaps where no scene change occurs
      3.  frames that are too close together (<0.5s)

    Returns list of {path, timestamp, source} dicts.
    """
    os.makedirs(output_dir, exist_ok=True)

    duration = get_video_duration(ffmpeg, video_path)
    if duration <= 0:
        logger.warning("无法获取视频时长")
        duration = 300  # 

    # --- 处理 ---
    scene_dir = os.path.join(output_dir, "_scene")
    os.makedirs(scene_dir, exist_ok=True)

    scene_log = os.path.join(output_dir, "_scene_log.txt")
    cmd_scene = [
        ffmpeg, "-i", video_path,
        "-vf", f"select='gt(scene,{scene_threshold})',showinfo",
        "-vsync", "vfr",
        "-q:v", "2",
        os.path.join(scene_dir, "scene_%04d.jpg"),
        "-y",
    ]
    result = subprocess.run(
        cmd_scene, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )

    # showinfo 处理
    scene_frames = []
    for line in result.stderr.split("\n"):
        if "pts_time:" in line:
            try:
                pts = float(line.split("pts_time:")[1].split()[0])
                scene_frames.append(pts)
            except (ValueError, IndexError):
                pass

    # --- 处理 ---
    interval_dir = os.path.join(output_dir, "_interval")
    os.makedirs(interval_dir, exist_ok=True)

    cmd_interval = [
        ffmpeg, "-i", video_path,
        "-vf", f"fps=1/{interval_sec}",
        "-q:v", "2",
        os.path.join(interval_dir, "int_%04d.jpg"),
        "-y",
    ]
    subprocess.run(
        cmd_interval, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )

    interval_frames = []
    for f in sorted(Path(interval_dir).glob("int_*.jpg")):
        # 逻辑
        num = int(f.stem.split("_")[1]) - 1  # 0 
        ts = num * interval_sec
        interval_frames.append(ts)

    # --- 回退 ---
    all_ts = set()
    ts_source: dict[float, str] = {}

    for ts in scene_frames:
        all_ts.add(round(ts, 2))
        ts_source[round(ts, 2)] = "scene"

    for ts in interval_frames:
        rounded = round(ts, 2)
        # 逻辑
        if not any(abs(rounded - existing) < 0.5 for existing in all_ts):
            all_ts.add(rounded)
            ts_source[rounded] = "interval"

    sorted_ts = sorted(all_ts)

    # --- 处理回退 ---
    frames = []
    for i, ts in enumerate(sorted_ts):
        fname = f"{i + 1:03d}_{_format_ts(ts)}.jpg"
        out_path = os.path.join(output_dir, fname)

        cmd = [
            ffmpeg, "-ss", str(ts), "-i", video_path,
            "-frames:v", "1", "-q:v", "2",
            out_path, "-y",
        ]
        subprocess.run(cmd, capture_output=True, check=False)

        if os.path.exists(out_path):
            frames.append({
                "path": fname,
                "timestamp": ts,
                "time_str": _format_ts(ts),
                "source": ts_source.get(round(ts, 2), "unknown"),
            })

    # 处理
    import shutil
    shutil.rmtree(scene_dir, ignore_errors=True)
    shutil.rmtree(interval_dir, ignore_errors=True)
    log_file = os.path.join(output_dir, "_scene_log.txt")
    if os.path.exists(log_file):
        os.remove(log_file)

    logger.info(
        f"  关键帧: {len(frames)} 张 "
        f"(场景切换: {len(scene_frames)}, 间隔补充: {len(frames) - len([f for f in frames if f['source'] == 'scene'])})"
    )
    return frames


def _format_ts(seconds: float) -> str:
    """Format seconds as 00m05s."""
    m = int(seconds // 60)
    s = int(seconds % 60)
    return f"{m:02d}m{s:02d}s"


def transcribe_audio(
    ffmpeg: str,
    video_path: str,
    output_dir: str,
    model_size: str = "large-v3",
    language: str = "zh",
) -> dict:
    """Extract audio and transcribe with faster-whisper.

    Returns dict with transcript info.
    """
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        logger.error("faster-whisper 未安装: pip install faster-whisper")
        return {"error": "faster-whisper not installed"}

    # 回退 WAV
    wav_path = os.path.join(output_dir, "_audio.wav")
    cmd = [
        ffmpeg, "-i", video_path,
        "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
        wav_path, "-y",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if not os.path.exists(wav_path):
        logger.error(f"音频提取失败: {result.stderr[:200]}")
        return {"error": "audio extraction failed"}

    # 
    logger.info(f"  ASR 转录中 (model={model_size})...")
    t0 = time.time()

    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    segments, info = model.transcribe(
        wav_path,
        language=language,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 300},
    )

    # 
    srt_lines = []
    txt_lines = []
    seg_list = []

    for i, seg in enumerate(segments, 1):
        start = seg.start
        end = seg.end
        text = seg.text.strip()
        if not text:
            continue

        seg_list.append({
            "id": i,
            "start": round(start, 2),
            "end": round(end, 2),
            "text": text,
        })

        # SRT 
        srt_lines.append(str(i))
        srt_lines.append(f"{_srt_time(start)} --> {_srt_time(end)}")
        srt_lines.append(text)
        srt_lines.append("")

        txt_lines.append(text)

    elapsed = time.time() - t0

    # SRT
    srt_path = os.path.join(output_dir, "transcript.srt")
    with open(srt_path, "w", encoding="utf-8") as f:
        f.write("\n".join(srt_lines))

    # 回退
    txt_path = os.path.join(output_dir, "transcript.txt")
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write("\n".join(txt_lines))

    # 
    os.remove(wav_path)

    logger.info(f"  ASR 完成: {len(seg_list)} 段, {elapsed:.1f}s, 语言={info.language}")

    return {
        "segments": len(seg_list),
        "language": info.language,
        "language_probability": round(info.language_probability, 3),
        "duration": round(info.duration, 2),
        "elapsed": round(elapsed, 1),
    }


def _srt_time(seconds: float) -> str:
    """Format seconds as SRT timestamp: HH:MM:SS,mmm."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def process_episode(
    video_path: str,
    output_dir: str,
    model_size: str = "large-v3",
    scene_threshold: float = 0.20,
    interval_sec: float = 2.0,
) -> dict:
    """Process a single episode video into LLM-ready package."""
    ffmpeg = get_ffmpeg()

    video_name = Path(video_path).stem
    ep_dir = os.path.join(output_dir, video_name)
    keyframes_dir = os.path.join(ep_dir, "keyframes")
    os.makedirs(keyframes_dir, exist_ok=True)

    duration = get_video_duration(ffmpeg, video_path)
    file_size = os.path.getsize(video_path)

    logger.info(f"\n{'=' * 50}")
    logger.info(f"  处理: {video_name}")
    logger.info(f"  时长: {duration:.1f}s  大小: {file_size / 1024 / 1024:.1f}MB")
    logger.info(f"{'=' * 50}")

    # 1. 回退
    logger.info("[1/2] 提取关键帧...")
    frames = extract_keyframes(
        ffmpeg, video_path, keyframes_dir,
        scene_threshold=scene_threshold,
        interval_sec=interval_sec,
    )

    # 2. ASR 
    logger.info("[2/2] ASR 语音转录...")
    asr_info = transcribe_audio(ffmpeg, video_path, ep_dir, model_size=model_size)

    # 3. 逻辑处理
    # Just reference the original path — no need to duplicate
    manifest = {
        "episode": video_name,
        "source_video": os.path.abspath(video_path),
        "duration": round(duration, 2),
        "file_size": file_size,
        "keyframes": {
            "count": len(frames),
            "scene_threshold": scene_threshold,
            "interval_sec": interval_sec,
            "frames": frames,
        },
        "transcript": {
            "srt": "transcript.srt",
            "txt": "transcript.txt",
            **asr_info,
        },
        "processed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }

    manifest_path = os.path.join(ep_dir, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    logger.info(f"  输出: {ep_dir}")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(
        description="将短剧视频预处理为 LLM-ready 素材包（关键帧 + ASR 字幕）"
    )
    parser.add_argument("input", help="视频文件或包含 episode_*.mp4 的目录")
    parser.add_argument("--output", "-o", default="",
                        help="输出目录（默认在输入目录下创建 llm_ready/）")
    parser.add_argument("--model", "-m", default="large-v3",
                        help="Whisper 模型 (tiny/base/small/medium/large-v3)")
    parser.add_argument("--scene-threshold", type=float, default=0.20,
                        help="场景切换检测阈值 (0-1, 越低帧越多, 默认0.20)")
    parser.add_argument("--interval", type=float, default=2.0,
                        help="固定间隔抽帧秒数 (默认2.0)")
    parser.add_argument("--episodes", "-e", nargs="*",
                        help="只处理指定集数 (如: 1 2 3)")
    args = parser.parse_args()

    input_path = Path(args.input)

    # 处理
    if input_path.is_file() and input_path.suffix.lower() == ".mp4":
        videos = [str(input_path)]
        base_dir = str(input_path.parent)
    elif input_path.is_dir():
        videos = sorted(str(f) for f in input_path.glob("episode_*.mp4"))
        base_dir = str(input_path)
    else:
        logger.error(f"输入不存在或不支持: {input_path}")
        return

    if not videos:
        logger.error(f"未找到 episode_*.mp4 文件: {input_path}")
        return

    # 处理
    if args.episodes:
        ep_nums = {int(e) for e in args.episodes}
        videos = [v for v in videos if any(f"_{n:03d}" in v for n in ep_nums)]

    output_dir = args.output or os.path.join(base_dir, "llm_ready")

    logger.info(f"输入: {base_dir}")
    logger.info(f"输出: {output_dir}")
    logger.info(f"集数: {len(videos)}")
    logger.info(f"模型: {args.model}")
    logger.info(f"场景阈值: {args.scene_threshold}  间隔: {args.interval}s")

    results = []
    t0 = time.time()

    for i, video in enumerate(videos, 1):
        logger.info(f"\n[{i}/{len(videos)}] {Path(video).name}")
        try:
            manifest = process_episode(
                video, output_dir,
                model_size=args.model,
                scene_threshold=args.scene_threshold,
                interval_sec=args.interval,
            )
            results.append(manifest)
        except Exception as e:
            logger.error(f"处理失败: {e}")
            import traceback
            traceback.print_exc()

    elapsed = time.time() - t0

    # 
    total_frames = sum(r["keyframes"]["count"] for r in results)
    total_segs = sum(r["transcript"].get("segments", 0) for r in results)

    logger.info(f"\n{'=' * 50}")
    logger.info(f"  全部完成!")
    logger.info(f"  处理: {len(results)}/{len(videos)} 集")
    logger.info(f"  关键帧: {total_frames} 张")
    logger.info(f"  ASR 段落: {total_segs} 段")
    logger.info(f"  耗时: {elapsed:.0f}s")
    logger.info(f"  输出: {output_dir}")
    logger.info(f"{'=' * 50}")


if __name__ == "__main__":
    main()
