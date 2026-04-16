#!/usr/bin/env python
"""红果短剧缓存提取与切分主入口脚本"""
import argparse
import json
import sys
from pathlib import Path
from loguru import logger

# 添加项目根目录到 sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.cache_puller import pull_and_sort_cache
from scripts.video_merger import merge_videos
from scripts.ocr_detector import detect_episode_boundaries
from scripts.split_planner import generate_split_plan
from scripts.video_splitter import split_episodes
from scripts.output_validator import validate_output, get_mp4_duration


def main():
    parser = argparse.ArgumentParser(description="红果短剧缓存提取与切分工具")
    parser.add_argument("--drama-name", required=True, help="短剧名称")
    parser.add_argument("--output", default="./videos", help="输出根目录")
    parser.add_argument("--expected-episodes", type=int, default=60, help="预期集数")
    parser.add_argument("--step", choices=["pull", "merge", "ocr", "split", "validate"], help="只执行指定步骤")
    parser.add_argument("--sample-interval", type=int, default=30, help="OCR 采样间隔（秒）")

    args = parser.parse_args()

    # 设置输出目录
    base_dir = Path(args.output) / args.drama_name
    base_dir.mkdir(parents=True, exist_ok=True)

    cache_dir = base_dir / "cache"
    full_dir = base_dir / "全集"
    episodes_dir = base_dir / "独立集数"

    full_video_path = full_dir / f"{args.drama_name}_全集.mp4"
    boundaries_file = base_dir / "ocr_boundaries.json"
    plan_file = base_dir / "split_plan.json"

    # 配置日志落盘
    log_file = base_dir / "extraction.log"
    logger.add(
        log_file,
        rotation="10 MB",
        retention="7 days",
        encoding="utf-8",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {message}",
        level="INFO"
    )
    logger.info(f"开始处理短剧: {args.drama_name}")
    logger.info(f"输出目录: {base_dir}")
    logger.info(f"日志文件: {log_file}")

    # 步骤 1: 拉取缓存
    if not args.step or args.step == "pull":
        logger.info("[1/5] 拉取缓存文件...")
        file_count = pull_and_sort_cache(str(cache_dir))
        logger.info(f"✓ 拉取完成: {file_count} 个文件")
        if args.step:
            return

    # 步骤 2: 合并全集
    if not args.step or args.step == "merge":
        logger.info("[2/5] 合并全集视频...")
        merge_videos(str(cache_dir), str(full_dir), args.drama_name)
        duration = get_mp4_duration(str(full_video_path))
        size_mb = full_video_path.stat().st_size / 1024 / 1024
        logger.info(f"✓ 全集视频: {duration/60:.1f} 分钟 | {size_mb:.1f}MB")
        if args.step:
            return

    # 步骤 3: OCR 识别
    if not args.step or args.step == "ocr":
        logger.info("[3/5] OCR 识别集数边界...")
        boundaries = detect_episode_boundaries(str(full_video_path), args.sample_interval)

        with open(boundaries_file, "w", encoding="utf-8") as f:
            json.dump(boundaries, f, indent=2, ensure_ascii=False)

        logger.info(f"✓ 检测到 {len(boundaries)} 个集数边界")
        logger.info(f"✓ 置信度: {'高' if len(boundaries) > args.expected_episodes * 0.9 else '中'}")
        if args.step:
            return

    # 步骤 4: 生成切分计划
    if not args.step or args.step == "split":
        logger.info("[4/5] 生成切分计划...")

        with open(boundaries_file, "r", encoding="utf-8") as f:
            boundaries = json.load(f)

        duration = get_mp4_duration(str(full_video_path))
        split_plan = generate_split_plan(boundaries, duration, args.expected_episodes)

        with open(plan_file, "w", encoding="utf-8") as f:
            json.dump(split_plan, f, indent=2, ensure_ascii=False)

        estimated_count = sum(1 for p in split_plan if p['confidence'] == 'estimated')
        if estimated_count > 0:
            logger.warning(f"⚠ 缺失集数: {estimated_count} 个 (使用插值估算)")

        logger.info("✓ 保存切分计划: split_plan.json")

        # 执行切分
        logger.info("[5/5] 切分独立集数...")
        success = split_episodes(str(full_video_path), split_plan, str(episodes_dir))
        logger.info(f"✓ 切分完成: {success}/{args.expected_episodes} 集")

        if args.step:
            return

    # 步骤 5: 验证输出
    if not args.step or args.step == "validate":
        logger.info("验证输出...")
        issues = validate_output(str(episodes_dir), args.expected_episodes)

        if issues:
            logger.warning(f"⚠️  需要人工检查:")
            for issue in issues[:5]:
                logger.warning(f"  - {issue}")

        # 生成报告
        report_file = base_dir / "REPORT.md"
        with open(report_file, "w", encoding="utf-8") as f:
            f.write(f"# {args.drama_name} - 处理报告\n\n")
            f.write(f"## 输出目录\n")
            f.write(f"- 全集: {full_video_path}\n")
            f.write(f"- 独立集数: {episodes_dir}\n\n")
            if issues:
                f.write(f"## 问题列表\n")
                for issue in issues:
                    f.write(f"- {issue}\n")

        logger.info(f"✅ 处理完成！详细报告: {report_file}")


if __name__ == "__main__":
    main()
