"""HLS (M3U8/TS) video downloader with AES-128 decryption support."""

from __future__ import annotations

import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin

import m3u8
import requests
from loguru import logger
from tqdm import tqdm

try:
    from Crypto.Cipher import AES
except ImportError:
    AES = None  # type: ignore[assignment,misc]


@dataclass
class DownloadConfig:
    output_dir: str = "./videos"
    max_workers: int = 5
    max_retries: int = 3
    timeout: int = 30
    headers: dict[str, str] = field(default_factory=dict)
    rate_limit_workers: int = 2


@dataclass
class SegmentInfo:
    index: int
    url: str
    duration: float
    key: Optional[bytes] = None
    iv: Optional[bytes] = None
    downloaded: bool = False
    local_path: str = ""


@dataclass
class DownloadResult:
    success: bool
    output_path: str = ""
    total_segments: int = 0
    downloaded_segments: int = 0
    failed_segments: int = 0
    total_size: int = 0
    elapsed_time: float = 0.0
    error: str = ""


class HLSDownloader:
    """Download HLS streams by parsing M3U8 playlists."""

    def __init__(self, config: Optional[DownloadConfig] = None) -> None:
        self.config = config or DownloadConfig()
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Linux; Android 10) AppleWebKit/537.36",
            **self.config.headers,
        })

    def download(self, m3u8_url: str, output_name: str = "video") -> DownloadResult:
        """Download a complete HLS stream from M3U8 URL."""
        start_time = time.time()
        logger.info(f"Starting HLS download: {m3u8_url}")

        try:
            playlist = self._parse_playlist(m3u8_url)
        except Exception as e:
            return DownloadResult(success=False, error=f"Failed to parse M3U8: {e}")

        if playlist.is_variant:
            # 处理处理
            best = max(playlist.playlists, key=lambda p: p.stream_info.bandwidth or 0)
            actual_url = urljoin(m3u8_url, best.uri)
            logger.info(f"Selected quality: {best.stream_info.bandwidth} bps")
            try:
                playlist = self._parse_playlist(actual_url)
            except Exception as e:
                return DownloadResult(success=False, error=f"Failed to parse media playlist: {e}")

        segments = self._build_segment_list(playlist, m3u8_url)
        if not segments:
            return DownloadResult(success=False, error="No segments found in playlist")

        # 处理
        output_dir = Path(self.config.output_dir)
        ts_dir = output_dir / f".{output_name}_segments"
        ts_dir.mkdir(parents=True, exist_ok=True)

        # 
        result = self._download_segments(segments, ts_dir)

        if result.failed_segments > 0 and result.downloaded_segments == 0:
            return DownloadResult(
                success=False,
                total_segments=result.total_segments,
                failed_segments=result.failed_segments,
                error="All segments failed to download",
            )

        # 
        output_path = str(output_dir / f"{output_name}.mp4")
        merge_ok = self._merge_segments(segments, ts_dir, output_path)

        elapsed = time.time() - start_time
        result.elapsed_time = elapsed
        result.output_path = output_path if merge_ok else ""
        result.success = merge_ok

        if merge_ok:
            # temp segments
            self._cleanup(ts_dir)
            logger.info(f"Download complete: {output_path} ({elapsed:.1f}s)")

        return result

    def _parse_playlist(self, url: str) -> m3u8.M3U8:
        """Fetch and parse an M3U8 playlist."""
        resp = self.session.get(url, timeout=self.config.timeout)
        resp.raise_for_status()
        return m3u8.loads(resp.text, uri=url)

    def _build_segment_list(
        self, playlist: m3u8.M3U8, base_url: str
    ) -> list[SegmentInfo]:
        """Build segment list with encryption info."""
        segments: list[SegmentInfo] = []
        current_key: Optional[bytes] = None
        current_iv: Optional[bytes] = None

        for i, seg in enumerate(playlist.segments):
            # key
            if seg.key and seg.key.method and seg.key.method.upper() != "NONE":
                if seg.key.method.upper() == "AES-128":
                    key_url = urljoin(base_url, seg.key.uri)
                    current_key = self._download_key(key_url)
                    if seg.key.iv:
                        iv_hex = seg.key.iv.replace("0x", "").replace("0X", "")
                        current_iv = bytes.fromhex(iv_hex.zfill(32))
                    else:
                        current_iv = i.to_bytes(16, byteorder="big")
                else:
                    logger.warning(f"Unsupported encryption: {seg.key.method}")

            seg_url = urljoin(base_url, seg.uri)
            segments.append(SegmentInfo(
                index=i,
                url=seg_url,
                duration=seg.duration or 0.0,
                key=current_key,
                iv=current_iv,
            ))

        logger.info(f"Found {len(segments)} segments, encrypted: {current_key is not None}")
        return segments

    def _download_key(self, key_url: str) -> bytes:
        """Download AES encryption key."""
        logger.debug(f"Downloading encryption key: {key_url}")
        resp = self.session.get(key_url, timeout=self.config.timeout)
        resp.raise_for_status()
        return resp.content

    def _download_segments(
        self, segments: list[SegmentInfo], ts_dir: Path
    ) -> DownloadResult:
        """Download all segments with multi-threading and progress bar."""
        result = DownloadResult(
            success=True,
            total_segments=len(segments),
        )

        # 逻辑
        for seg in segments:
            seg.local_path = str(ts_dir / f"seg_{seg.index:05d}.ts")
            if Path(seg.local_path).exists() and Path(seg.local_path).stat().st_size > 0:
                seg.downloaded = True
                result.downloaded_segments += 1

        pending = [s for s in segments if not s.downloaded]
        if not pending:
            logger.info("All segments already downloaded (resume)")
            result.downloaded_segments = len(segments)
            return result

        logger.info(f"Downloading {len(pending)} segments ({result.downloaded_segments} already done)")

        workers = self.config.max_workers
        with tqdm(total=len(segments), initial=result.downloaded_segments, desc="Downloading", unit="seg") as pbar:
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {
                    executor.submit(self._download_one_segment, seg): seg
                    for seg in pending
                }

                for future in as_completed(futures):
                    seg = futures[future]
                    try:
                        size = future.result()
                        seg.downloaded = True
                        result.downloaded_segments += 1
                        result.total_size += size
                    except Exception as e:
                        result.failed_segments += 1
                        logger.warning(f"Segment {seg.index} failed: {e}")
                    pbar.update(1)

        return result

    def _download_one_segment(self, seg: SegmentInfo) -> int:
        """Download a single segment with retry and optional decryption."""
        last_error: Optional[Exception] = None

        for attempt in range(self.config.max_retries):
            try:
                resp = self.session.get(seg.url, timeout=self.config.timeout)
                if resp.status_code == 429:
                    wait_time = 2 ** (attempt + 1)
                    logger.warning(f"Rate limited, waiting {wait_time}s...")
                    time.sleep(wait_time)
                    continue
                resp.raise_for_status()
                data = resp.content

                # if needed
                if seg.key and AES is not None:
                    iv = seg.iv or seg.index.to_bytes(16, byteorder="big")
                    cipher = AES.new(seg.key, AES.MODE_CBC, iv)
                    data = cipher.decrypt(data)
                    # PKCS7 padding
                    if data:
                        pad_len = data[-1]
                        if 0 < pad_len <= 16:
                            data = data[:-pad_len]

                with open(seg.local_path, "wb") as f:
                    f.write(data)

                return len(data)

            except Exception as e:
                last_error = e
                if attempt < self.config.max_retries - 1:
                    time.sleep(2 ** attempt)

        raise last_error or Exception("Download failed")

    def _merge_segments(
        self, segments: list[SegmentInfo], ts_dir: Path, output_path: str
    ) -> bool:
        """Merge TS segments into a single MP4 file using ffmpeg."""
        downloaded = [s for s in segments if s.downloaded]
        if not downloaded:
            logger.error("No segments to merge")
            return False

        # ffmpeg concat 
        concat_file = ts_dir / "concat.txt"
        with open(concat_file, "w", encoding="utf-8") as f:
            for seg in sorted(downloaded, key=lambda s: s.index):
                # 处理 ffmpeg
                path = seg.local_path.replace("\\", "/")
                f.write(f"file '{path}'\n")

        try:
            cmd = [
                "ffmpeg", "-y",
                "-f", "concat",
                "-safe", "0",
                "-i", str(concat_file),
                "-c", "copy",
                "-movflags", "+faststart",
                output_path,
            ]
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=300,
            )
            if result.returncode != 0:
                logger.error(f"ffmpeg merge failed: {result.stderr[:500]}")
                return False

            logger.info(f"Merged to: {output_path}")
            return True

        except FileNotFoundError:
            logger.error("ffmpeg not found. Please install ffmpeg.")
            return False
        except subprocess.TimeoutExpired:
            logger.error("ffmpeg merge timed out")
            return False

    def _cleanup(self, ts_dir: Path) -> None:
        """Remove temporary segment files."""
        import shutil
        try:
            shutil.rmtree(ts_dir)
        except Exception as e:
            logger.warning(f"Cleanup failed: {e}")

    def close(self) -> None:
        self.session.close()


def download_mp4(url: str, output_path: str, headers: Optional[dict[str, str]] = None) -> DownloadResult:
    """Download a direct MP4 file with progress bar."""
    start_time = time.time()
    session = requests.Session()
    if headers:
        session.headers.update(headers)

    try:
        resp = session.get(url, stream=True, timeout=30)
        resp.raise_for_status()
        total = int(resp.headers.get("content-length", 0))

        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

        downloaded = 0
        with open(output_path, "wb") as f:
            with tqdm(total=total, desc="Downloading", unit="B", unit_scale=True) as pbar:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)
                    downloaded += len(chunk)
                    pbar.update(len(chunk))

        elapsed = time.time() - start_time
        return DownloadResult(
            success=True,
            output_path=output_path,
            total_size=downloaded,
            elapsed_time=elapsed,
        )
    except Exception as e:
        return DownloadResult(success=False, error=str(e))
    finally:
        session.close()


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print(" python hls_downloader.py <m3u8_url> [output_name]")
        sys.exit(1)

    url = sys.argv[1]
    name = sys.argv[2] if len(sys.argv) > 2 else "video"

    config = DownloadConfig(output_dir="./videos")
    downloader = HLSDownloader(config)
    try:
        result = downloader.download(url, name)
        if result.success:
            print(f"\nDownload complete: {result.output_path}")
            print(f"Segments: {result.downloaded_segments}/{result.total_segments}")
            print(f"Size: {result.total_size / 1024 / 1024:.1f} MB")
            print(f"Time: {result.elapsed_time:.1f}s")
        else:
            print(f"\nDownload failed: {result.error}")
    finally:
        downloader.close()
