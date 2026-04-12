"""Batch download manager with queue, dedup, resume and progress tracking."""

from __future__ import annotations

import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Optional

from loguru import logger

from scripts.hls_downloader import HLSDownloader, DownloadConfig, DownloadResult, download_mp4
from scripts.pcap_parser import VideoURL


class DownloadStatus(str, Enum):
    PENDING = "pending"
    DOWNLOADING = "downloading"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class DownloadTask:
    url: str
    format: str
    output_name: str
    status: DownloadStatus = DownloadStatus.PENDING
    output_path: str = ""
    error: str = ""
    retries: int = 0
    size: int = 0
    url_hash: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    series_name: str = ""
    episode: int = 0


@dataclass
class BatchState:
    tasks: list[DownloadTask] = field(default_factory=list)
    total: int = 0
    completed: int = 0
    failed: int = 0
    skipped: int = 0
    total_size: int = 0
    start_time: float = 0.0

    def to_dict(self) -> dict:
        return {
            "total": self.total,
            "completed": self.completed,
            "failed": self.failed,
            "skipped": self.skipped,
            "total_size": self.total_size,
            "start_time": self.start_time,
            "tasks": [asdict(t) for t in self.tasks],
        }


# 处理
EPISODE_PATTERNS = [
    re.compile(r'[_/](?:ep|episode|e)(\d+)', re.IGNORECASE),
    re.compile(r'第(\d+)[集话]'),
    re.compile(r'[_/](\d{1,4})[_/.]'),
    re.compile(r'(\d+)\.(?:m3u8|ts|mp4)', re.IGNORECASE),
]


def guess_episode(url: str) -> int:
    """Try to extract episode number from URL."""
    for pattern in EPISODE_PATTERNS:
        match = pattern.search(url)
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                continue
    return 0


def guess_series_name(url: str) -> str:
    """Try to extract series/show name from URL."""
    parts = url.split("/")
    for part in parts:
        if len(part) > 3 and not part.startswith("http") and "." not in part:
            cleaned = re.sub(r'[_\-]', ' ', part).strip()
            if cleaned and not cleaned.isdigit():
                return cleaned
    return "unknown_series"


class BatchManager:
    """Manage batch downloads with queue, dedup, and state persistence."""

    def __init__(
        self,
        output_dir: str = "./videos",
        max_concurrent: int = 2,
        max_retries: int = 3,
        state_file: Optional[str] = None,
    ) -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.max_concurrent = max(1, min(max_concurrent, 10))
        self.max_retries = max_retries
        self.state_file = state_file or str(self.output_dir / ".batch_state.json")
        self.state = BatchState()
        self._seen_hashes: set[str] = set()

    def add_urls(self, video_urls: list[VideoURL]) -> int:
        """Add video URLs to the download queue, deduplicating."""
        added = 0
        for v in video_urls:
            if v.url_hash in self._seen_hashes:
                continue
            self._seen_hashes.add(v.url_hash)

            series = guess_series_name(v.url)
            episode = guess_episode(v.url)

            if episode > 0:
                name = f"{series}_ep{episode:03d}"
            else:
                name = f"{series}_{v.url_hash[:8]}"

            task = DownloadTask(
                url=v.url,
                format=v.format,
                output_name=name,
                url_hash=v.url_hash,
                headers=v.headers,
                series_name=series,
                episode=episode,
            )
            self.state.tasks.append(task)
            added += 1

        self.state.total = len(self.state.tasks)
        logger.info(f"Added {added} URLs to queue (total: {self.state.total})")
        return added

    def add_url(self, url: str, format: str = "m3u8", headers: Optional[dict[str, str]] = None) -> bool:
        """Add a single URL to the queue."""
        import hashlib
        url_hash = hashlib.md5(url.encode()).hexdigest()[:16]
        if url_hash in self._seen_hashes:
            return False

        v = VideoURL(url=url, format=format, headers=headers or {}, url_hash=url_hash)
        self.add_urls([v])
        return True

    def load_state(self) -> bool:
        """Load batch state from file for resume."""
        if not Path(self.state_file).exists():
            return False

        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            tasks = []
            for t in data.get("tasks", []):
                task = DownloadTask(
                    url=t["url"],
                    format=t["format"],
                    output_name=t["output_name"],
                    status=DownloadStatus(t.get("status", "pending")),
                    output_path=t.get("output_path", ""),
                    error=t.get("error", ""),
                    retries=t.get("retries", 0),
                    size=t.get("size", 0),
                    url_hash=t.get("url_hash", ""),
                    headers=t.get("headers", {}),
                    series_name=t.get("series_name", ""),
                    episode=t.get("episode", 0),
                )
                tasks.append(task)
                if task.url_hash:
                    self._seen_hashes.add(task.url_hash)

            self.state.tasks = tasks
            self.state.total = data.get("total", len(tasks))
            self.state.completed = data.get("completed", 0)
            self.state.failed = data.get("failed", 0)
            self.state.skipped = data.get("skipped", 0)
            self.state.total_size = data.get("total_size", 0)

            logger.info(f"Resumed state: {self.state.completed} completed, "
                        f"{self.state.failed} failed, "
                        f"{self.state.total - self.state.completed - self.state.failed} pending")
            return True
        except Exception as e:
            logger.warning(f"Failed to load state: {e}")
            return False

    def save_state(self) -> None:
        """Persist batch state to file."""
        try:
            with open(self.state_file, "w", encoding="utf-8") as f:
                json.dump(self.state.to_dict(), f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.warning(f"Failed to save state: {e}")

    def run(self) -> BatchState:
        """Execute all pending downloads."""
        self.state.start_time = time.time()
        pending = [t for t in self.state.tasks if t.status in (DownloadStatus.PENDING, DownloadStatus.FAILED)]

        if not pending:
            logger.info("No pending tasks")
            return self.state

        logger.info(f"Starting batch download: {len(pending)} tasks, "
                    f"max concurrent: {self.max_concurrent}")

        config = DownloadConfig(
            output_dir=str(self.output_dir),
            max_workers=5,
            headers={},
        )

        with ThreadPoolExecutor(max_workers=self.max_concurrent) as executor:
            futures = {}
            for task in pending:
                task.status = DownloadStatus.DOWNLOADING
                future = executor.submit(self._download_task, task, config)
                futures[future] = task

            for future in as_completed(futures):
                task = futures[future]
                try:
                    result = future.result()
                    if result.success:
                        task.status = DownloadStatus.COMPLETED
                        task.output_path = result.output_path
                        task.size = result.total_size
                        self.state.completed += 1
                        self.state.total_size += result.total_size
                        logger.info(f"[OK] {task.output_name}")
                    else:
                        task.retries += 1
                        if task.retries >= self.max_retries:
                            task.status = DownloadStatus.FAILED
                            task.error = result.error
                            self.state.failed += 1
                            logger.error(f"[FAIL] {task.output_name}: {result.error}")
                        else:
                            task.status = DownloadStatus.PENDING
                            logger.warning(f"[RETRY {task.retries}] {task.output_name}")
                except Exception as e:
                    task.status = DownloadStatus.FAILED
                    task.error = str(e)
                    self.state.failed += 1
                    logger.error(f"[ERROR] {task.output_name}: {e}")

                self.save_state()

        self.save_state()
        return self.state

    def _download_task(self, task: DownloadTask, config: DownloadConfig) -> DownloadResult:
        """Download a single task."""
        # 处理
        series_dir = self.output_dir / task.series_name
        series_dir.mkdir(parents=True, exist_ok=True)

        task_config = DownloadConfig(
            output_dir=str(series_dir),
            max_workers=config.max_workers,
            headers=task.headers,
        )

        if task.format == "mp4":
            output_path = str(series_dir / f"{task.output_name}.mp4")
            return download_mp4(task.url, output_path, task.headers)

        downloader = HLSDownloader(task_config)
        try:
            return downloader.download(task.url, task.output_name)
        finally:
            downloader.close()

    def get_report(self) -> str:
        """Generate a human-readable batch report."""
        elapsed = time.time() - self.state.start_time if self.state.start_time else 0
        size_mb = self.state.total_size / 1024 / 1024

        lines = [
            "=" * 50,
            "Batch Download Report",
            "=" * 50,
            f"Total tasks:  {self.state.total}",
            f"Completed:    {self.state.completed}",
            f"Failed:       {self.state.failed}",
            f"Skipped:      {self.state.skipped}",
            f"Total size:   {size_mb:.1f} MB",
            f"Elapsed:      {elapsed:.1f}s",
        ]

        if self.state.failed > 0:
            lines.append("\nFailed tasks:")
            for t in self.state.tasks:
                if t.status == DownloadStatus.FAILED:
                    lines.append(f"  - {t.output_name}: {t.error}")

        return "\n".join(lines)

    def export_failed(self, output_path: Optional[str] = None) -> str:
        """Export failed URLs to a text file."""
        path = output_path or str(self.output_dir / "failed_urls.txt")
        failed = [t for t in self.state.tasks if t.status == DownloadStatus.FAILED]

        with open(path, "w", encoding="utf-8") as f:
            for t in failed:
                f.write(f"{t.url}\t{t.error}\n")

        logger.info(f"Exported {len(failed)} failed URLs to: {path}")
        return path


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print(" python batch_manager.py <report.json> [output_dir]")
        print("  Reads a PCAP analysis report and downloads all found videos.")
        sys.exit(1)

    report_file = sys.argv[1]
    output_dir = sys.argv[2] if len(sys.argv) > 2 else "./videos"

    with open(report_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    urls = []
    for entry in data.get("m3u8_urls", []) + data.get("mp4_urls", []):
        urls.append(VideoURL(
            url=entry["url"],
            format=entry["format"],
            headers=entry.get("headers", {}),
            url_hash=entry.get("url_hash", ""),
        ))

    manager = BatchManager(output_dir=output_dir)
    manager.add_urls(urls)
    manager.run()
    print(manager.get_report())

    if manager.state.failed > 0:
        manager.export_failed()
