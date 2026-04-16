"""缓存拉取和排序模块"""
import os
import re
from pathlib import Path
from typing import List, Dict
from loguru import logger

CACHE_PATH = "/sdcard/Android/data/com.phoenix.read/cache/short"


def run_adb(args: List[str], check: bool = True):
    """执行 ADB 命令"""
    import subprocess
    cmd = ["adb"] + args
    env = {**os.environ, "MSYS_NO_PATHCONV": "1"}
    return subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=check, env=env)


def list_remote_mdl_with_time() -> List[Dict[str, str]]:
    """列出远程 .mdl 文件及其修改时间"""
    result = run_adb(["shell", f"ls -l {CACHE_PATH}/*.mdl"], check=False)
    if result.returncode != 0:
        logger.error("未找到 .mdl 文件")
        return []

    files = []
    for line in result.stdout.strip().splitlines():
        parts = line.split()
        if len(parts) >= 8 and parts[-1].endswith(".mdl"):
            files.append({
                "name": Path(parts[-1]).name,
                "size": parts[4],
                "date": f"{parts[5]} {parts[6]}",
                "path": parts[-1],
            })
    return files


def pull_and_sort_cache(output_dir: str) -> int:
    """拉取并排序缓存文件，生成 concat 列表"""
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    remote_files = list_remote_mdl_with_time()
    if not remote_files:
        logger.warning("未找到缓存文件")
        return 0

    # 按修改时间排序
    sorted_files = sorted(remote_files, key=lambda x: x['date'])
    logger.info(f"找到 {len(sorted_files)} 个缓存文件，按时间排序")

    # 拉取文件
    for i, f in enumerate(sorted_files):
        local_name = f"{i+1:03d}_{f['name']}"
        local_path = output_path / local_name
        logger.info(f"拉取 [{i+1}/{len(sorted_files)}]: {f['name']}")
        run_adb(["pull", f["path"], str(local_path)])

    # 生成 concat 列表
    concat_file = output_path / "concat_list.txt"
    with open(concat_file, "w", encoding="utf-8") as f:
        for i in range(len(sorted_files)):
            f.write(f"file '{i+1:03d}_*.mdl'\n")

    logger.info(f"生成 concat 列表: {concat_file}")
    return len(sorted_files)
