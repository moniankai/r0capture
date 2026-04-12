"""PCAP file parser for extracting video URLs (M3U8/TS/MP4)."""

from __future__ import annotations

import hashlib
import json
import re
import struct
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

from loguru import logger


@dataclass
class VideoURL:
    url: str
    format: str  # "m3u8", "ts", "mp4"
    headers: dict[str, str] = field(default_factory=dict)
    params: dict[str, str] = field(default_factory=dict)
    encrypted: bool = False
    encryption_method: str = ""
    key_uri: str = ""
    iv: str = ""
    url_hash: str = ""

    def __post_init__(self) -> None:
        if not self.url_hash:
            self.url_hash = hashlib.md5(self.url.encode()).hexdigest()[:16]


@dataclass
class AnalysisReport:
    pcap_file: str
    total_packets: int = 0
    http_packets: int = 0
    videos_found: int = 0
    m3u8_urls: list[VideoURL] = field(default_factory=list)
    ts_urls: list[VideoURL] = field(default_factory=list)
    mp4_urls: list[VideoURL] = field(default_factory=list)
    other_urls: list[VideoURL] = field(default_factory=list)

    @property
    def all_video_urls(self) -> list[VideoURL]:
        return self.m3u8_urls + self.ts_urls + self.mp4_urls


# URL 
M3U8_PATTERN = re.compile(r'https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*', re.IGNORECASE)
TS_PATTERN = re.compile(r'https?://[^\s"\'<>]+\.ts[^\s"\'<>]*', re.IGNORECASE)
MP4_PATTERN = re.compile(r'https?://[^\s"\'<>]+\.mp4[^\s"\'<>]*', re.IGNORECASE)
VIDEO_URL_PATTERN = re.compile(
    r'https?://[^\s"\'<>]+(?:\.m3u8|\.ts|\.mp4|/video/|/hls/|/stream/)[^\s"\'<>]*',
    re.IGNORECASE,
)

# HTTP 
AUTH_HEADER = re.compile(r'Authorization:\s*(.+)', re.IGNORECASE)
UA_HEADER = re.compile(r'User-Agent:\s*(.+)', re.IGNORECASE)
REFERER_HEADER = re.compile(r'Referer:\s*(.+)', re.IGNORECASE)

# HLS 
EXT_X_KEY = re.compile(
    r'#EXT-X-KEY:METHOD=([^,]+)(:,URI="([^"]+)")(:,IV=([^\s,]+))',
    re.IGNORECASE,
)


def read_pcap_packets(pcap_path: str) -> list[bytes]:
    """Read packets from a PCAP file (LINKTYPE_IPV4 format from r0capture)."""
    packets = []
    path = Path(pcap_path)
    if not path.exists():
        logger.error(f"PCAP file not found: {pcap_path}")
        return packets

    with open(pcap_path, "rb") as f:
        # 处理24 
        global_header = f.read(24)
        if len(global_header) < 24:
            logger.error("Invalid PCAP file: too short")
            return packets

        magic = struct.unpack("=I", global_header[:4])[0]
        if magic not in (0xA1B2C3D4, 0xD4C3B2A1):
            logger.error(f"Invalid PCAP magic number: {hex(magic)}")
            return packets

        # 回退
        big_endian = magic == 0xD4C3B2A1
        endian = ">" if big_endian else "<"

        while True:
            # packet 16 
            pkt_header = f.read(16)
            if len(pkt_header) < 16:
                break

            ts_sec, ts_usec, incl_len, orig_len = struct.unpack(
                f"{endian}IIII", pkt_header
            )

            # packet 
            pkt_data = f.read(incl_len)
            if len(pkt_data) < incl_len:
                break

            packets.append(pkt_data)

    return packets


def extract_http_data(packet: bytes) -> Optional[str]:
    """Extract HTTP payload from an IPv4+TCP packet."""
    if len(packet) < 40:
        return None

    # IPv4 
    ihl = (packet[0] & 0x0F) * 4
    protocol = packet[9]

    # TCP 6
    if protocol != 6:
        return None

    # TCP 
    tcp_start = ihl
    if tcp_start + 20 > len(packet):
        return None

    tcp_data_offset = ((packet[tcp_start + 12] >> 4) & 0x0F) * 4
    payload_start = tcp_start + tcp_data_offset

    if payload_start >= len(packet):
        return None

    payload = packet[payload_start:]
    try:
        return payload.decode("utf-8", errors="replace")
    except Exception:
        return None


def extract_headers(http_text: str) -> dict[str, str]:
    """Extract relevant HTTP headers from raw HTTP text."""
    headers: dict[str, str] = {}

    auth = AUTH_HEADER.search(http_text)
    if auth:
        headers["Authorization"] = auth.group(1).strip()

    ua = UA_HEADER.search(http_text)
    if ua:
        headers["User-Agent"] = ua.group(1).strip()

    ref = REFERER_HEADER.search(http_text)
    if ref:
        headers["Referer"] = ref.group(1).strip()

    return headers


def extract_url_params(url: str) -> dict[str, str]:
    """Extract query parameters from URL, focusing on auth-related ones."""
    params: dict[str, str] = {}
    if "?" not in url:
        return params

    query_string = url.split("?", 1)[1]
    for param in query_string.split("&"):
        if "=" in param:
            key, value = param.split("=", 1)
            key_lower = key.lower()
            if any(k in key_lower for k in ["token", "sign", "auth", "key", "expire", "t="]):
                params[key] = value

    return params


def detect_encryption(http_text: str) -> tuple[bool, str, str, str]:
    """Detect HLS  from M3U8 content."""
    match = EXT_X_KEY.search(http_text)
    if match:
        method = match.group(1) or ""
        uri = match.group(2) or ""
        iv = match.group(3) or ""
        if method.upper() != "NONE":
            return True, method, uri, iv
    return False, "", "", ""


def classify_url(url: str) -> str:
    """Classify a URL by video format."""
    url_lower = url.lower().split("?")[0]
    if url_lower.endswith(".m3u8"):
        return "m3u8"
    if url_lower.endswith(".ts"):
        return "ts"
    if url_lower.endswith(".mp4"):
        return "mp4"
    if "/hls/" in url_lower or "m3u8" in url_lower:
        return "m3u8"
    return "other"


def parse_pcap(pcap_path: str) -> AnalysisReport:
    """Parse a PCAP file and extract video URLs with metadata."""
    logger.info(f"Parsing PCAP: {pcap_path}")
    report = AnalysisReport(pcap_file=pcap_path)

    packets = read_pcap_packets(pcap_path)
    report.total_packets = len(packets)
    logger.info(f"Total packets: {report.total_packets}")

    seen_hashes: set[str] = set()
    current_headers: dict[str, str] = {}

    for packet in packets:
        http_text = extract_http_data(packet)
        if http_text is None:
            continue

        report.http_packets += 1

        # 处理 headers
        headers = extract_headers(http_text)
        if headers:
            current_headers.update(headers)

        # M3U8 处理
        encrypted, enc_method, key_uri, iv = detect_encryption(http_text)

        # 处理 URL
        for pattern in [M3U8_PATTERN, TS_PATTERN, MP4_PATTERN, VIDEO_URL_PATTERN]:
            for match in pattern.finditer(http_text):
                url = match.group(0).rstrip("\\r\\n\"' ")
                fmt = classify_url(url)
                url_hash = hashlib.md5(url.encode()).hexdigest()[:16]

                if url_hash in seen_hashes:
                    continue
                seen_hashes.add(url_hash)

                video_url = VideoURL(
                    url=url,
                    format=fmt,
                    headers=dict(current_headers),
                    params=extract_url_params(url),
                    encrypted=encrypted,
                    encryption_method=enc_method,
                    key_uri=key_uri,
                    iv=iv,
                    url_hash=url_hash,
                )

                if fmt == "m3u8":
                    report.m3u8_urls.append(video_url)
                elif fmt == "ts":
                    report.ts_urls.append(video_url)
                elif fmt == "mp4":
                    report.mp4_urls.append(video_url)
                else:
                    report.other_urls.append(video_url)

    report.videos_found = len(report.all_video_urls)
    logger.info(
        f"Found: {len(report.m3u8_urls)} M3U8, "
        f"{len(report.ts_urls)} TS, "
        f"{len(report.mp4_urls)} MP4"
    )
    return report


def save_report(report: AnalysisReport, output_path: str) -> None:
    """ analysis report to JSON file."""
    data = {
        "pcap_file": report.pcap_file,
        "total_packets": report.total_packets,
        "http_packets": report.http_packets,
        "videos_found": report.videos_found,
        "m3u8_urls": [asdict(u) for u in report.m3u8_urls],
        "ts_urls": [asdict(u) for u in report.ts_urls],
        "mp4_urls": [asdict(u) for u in report.mp4_urls],
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    logger.info(f"Report saved to: {output_path}")


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print(" python pcap_parser.py <pcap_file> [output.json]")
        sys.exit(1)

    pcap_file = sys.argv[1]
    output_file = sys.argv[2] if len(sys.argv) > 2 else pcap_file.replace(".pcap", "_report.json")

    report = parse_pcap(pcap_file)
    save_report(report, output_file)

    # 
    print(f"\n{'='*50}")
    print(f"PCAP Analysis Report")
    print(f"{'='*50}")
    print(f"Total packets: {report.total_packets}")
    print(f"HTTP packets:  {report.http_packets}")
    print(f"Videos found:  {report.videos_found}")
    print(f"  M3U8: {len(report.m3u8_urls)}")
    print(f"  TS:   {len(report.ts_urls)}")
    print(f"  MP4:  {len(report.mp4_urls)}")

    for v in report.m3u8_urls[:10]:
        print(f"\n  [{v.format}] {v.url[:100]}...")
        if v.encrypted:
            print(f"    Encrypted: {v.encryption_method}")
