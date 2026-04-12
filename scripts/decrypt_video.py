"""
Decrypt a CENC-encrypted MP4 video from HongGuo app.

Handles both video (vide) and audio (soun) tracks.


  python scripts/decrypt_video.py --key <hex> --input encrypted.mp4 --output decrypted.mp4
  python scripts/decrypt_video.py --key <hex> --url <cdn_url> --output decrypted.mp4
"""

from __future__ import annotations

import os
import struct
import sys
from pathlib import Path

from Crypto.Cipher import AES
from Crypto.Util import Counter
from loguru import logger


def find_all(data: bytes, pattern: bytes, start: int = 0, end: int | None = None) -> list[int]:
    results = []
    idx = start
    if end is None:
        end = len(data)
    while idx < end:
        idx = data.find(pattern, idx)
        if idx < 0 or idx >= end:
            break
        results.append(idx)
        idx += len(pattern)
    return results


def parse_track(data: bytes, t_start: int, t_end: int) -> dict:
    """Parse stsz, stco, stsc, senc from a single trak box."""
    info: dict = {}

    idx = data.find(b"stsz", t_start)
    if t_start <= idx < t_end:
        default_sz = struct.unpack(">I", data[idx + 8 : idx + 12])[0]
        count = struct.unpack(">I", data[idx + 12 : idx + 16])[0]
        if default_sz > 0:
            info["sizes"] = [default_sz] * count
        else:
            info["sizes"] = [
                struct.unpack(">I", data[idx + 16 + i * 4 : idx + 20 + i * 4])[0]
                for i in range(count)
            ]
        info["sample_count"] = count

    idx = data.find(b"stco", t_start)
    if t_start <= idx < t_end:
        count = struct.unpack(">I", data[idx + 8 : idx + 12])[0]
        info["chunk_offsets"] = [
            struct.unpack(">I", data[idx + 12 + i * 4 : idx + 16 + i * 4])[0]
            for i in range(count)
        ]

    idx = data.find(b"stsc", t_start)
    if t_start <= idx < t_end:
        count = struct.unpack(">I", data[idx + 8 : idx + 12])[0]
        info["stsc"] = [
            (
                struct.unpack(">I", data[idx + 12 + i * 12 : idx + 16 + i * 12])[0],
                struct.unpack(">I", data[idx + 16 + i * 12 : idx + 20 + i * 12])[0],
            )
            for i in range(count)
        ]

    idx = data.find(b"senc", t_start)
    if t_start <= idx < t_end:
        count = struct.unpack(">I", data[idx + 8 : idx + 12])[0]
        info["ivs"] = [
            bytes(data[idx + 12 + i * 8 : idx + 20 + i * 8]) for i in range(count)
        ]

    return info


def build_sample_offsets(
    chunk_offsets: list[int],
    stsc: list[tuple[int, int]],
    sizes: list[int],
) -> list[int]:
    """Compute per-sample file offsets from stco + stsc + stsz."""
    offsets = []
    si = 0
    for ci in range(len(chunk_offsets)):
        chunk_num = ci + 1
        spc = 1
        for entry in stsc:
            if entry[0] <= chunk_num:
                spc = entry[1]
        offset = chunk_offsets[ci]
        for _ in range(spc):
            if si >= len(sizes):
                break
            offsets.append(offset)
            offset += sizes[si]
            si += 1
    return offsets


def decrypt_mp4(data: bytearray, key: bytes) -> int:
    """ all CENC-encrypted samples in a MP4 bytearray. Returns sample count."""
    # trak box
    traks = []
    for t_idx in find_all(data, b"trak"):
        t_start = t_idx - 4
        t_size = struct.unpack(">I", data[t_start : t_start + 4])[0]
        t_end = t_start + t_size
        hdlr = data.find(b"hdlr", t_start)
        handler = (
            data[hdlr + 12 : hdlr + 16].decode("ascii", errors="replace")
            if hdlr < t_end
            else "unknown"
        )
        traks.append((t_start, t_end, handler))

    total = 0
    for t_start, t_end, handler in traks:
        info = parse_track(data, t_start, t_end)
        sizes = info.get("sizes", [])
        chunks = info.get("chunk_offsets", [])
        stsc = info.get("stsc", [(1, 1)])
        ivs = info.get("ivs", [])

        offsets = build_sample_offsets(chunks, stsc, sizes)

        ok = 0
        for i in range(min(len(offsets), len(ivs), len(sizes))):
            off = offsets[i]
            sz = sizes[i]
            iv = ivs[i]
            if sz == 0 or off + sz > len(data):
                continue

            sample = bytes(data[off : off + sz])
            ctr = Counter.new(64, prefix=iv, initial_value=0)
            cipher = AES.new(key, AES.MODE_CTR, counter=ctr)
            data[off : off + sz] = cipher.decrypt(sample)
            ok += 1

        logger.info(f"Track [{handler}]: {ok}/{info.get('sample_count', 0)} samples decrypted")
        total += ok

    return total


def fix_metadata(data: bytearray) -> None:
    """Remove CENC encryption markers from MP4 metadata.

    1. Restore original codec (encv→hvc1/bvc2, enca→mp4a) from frma.
    2. Zero-fill and remove each sinf box so players don't see residual DRM markers.
    """
    # 回退 frma codec sinf 
    sinf_ranges: list[tuple[int, int]] = []
    pos = 0
    while True:
        idx = data.find(b"sinf", pos)
        if idx < 0:
            break
        sinf_start = idx - 4
        sinf_size = struct.unpack(">I", data[sinf_start : sinf_start + 4])[0]
        sinf_end = sinf_start + sinf_size

        frma = data.find(b"frma", idx)
        if 0 <= frma < sinf_end:
            orig_fmt = bytes(data[frma + 4 : frma + 8])
            # 处理 sinf encv/enca sample entry
            for s in range(sinf_start - 4, max(sinf_start - 2000, 0), -1):
                if bytes(data[s + 4 : s + 8]) in (b"encv", b"enca"):
                    logger.info(f"Fix: {data[s+4:s+8].decode()} -> {orig_fmt.decode()}")
                    data[s + 4 : s + 8] = orig_fmt
                    break

        sinf_ranges.append((sinf_start, sinf_end))
        pos = idx + 4

    # 处理 sinf box处理
    for start, end in reversed(sinf_ranges):
        size = end - start
        # stsd sample-entry box 
        # sinf sample-entry encv/hvc1 size 
        # entry 4 处理处理
        # 处理 size 处理 sinf 处理
        # type to 'free' (a standard MP4 skip box) — simpler and equally effective.
        data[start + 4 : start + 8] = b"free"
        data[start + 8 : end] = b"\x00" * (size - 8)
        logger.info(f"sinf at {start} ({size}B) -> free box")


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Decrypt CENC-encrypted HongGuo video")
    parser.add_argument("--key", "-k", required=True, help="AES-128 key in hex (32 chars)")
    parser.add_argument("--input", "-i", help="Encrypted MP4 file path")
    parser.add_argument("--url", "-u", help="CDN URL to download encrypted video")
    parser.add_argument("--output", "-o", required=True, help="Output decrypted MP4 path")
    args = parser.parse_args()

    key = bytes.fromhex(args.key)
    if len(key) != 16:
        logger.error(f"Key must be 16 bytes (32 hex chars), got {len(key)}")
        return

    # 处理
    if args.input:
        with open(args.input, "rb") as f:
            data = bytearray(f.read())
        logger.info(f"Loaded: {args.input} ({len(data)/1024/1024:.1f}MB)")
    elif args.url:
        import requests
        logger.info(f"Downloading: {args.url[:100]}...")
        resp = requests.get(
            args.url,
            headers={"User-Agent": "AVDML_2.1.230.181-novel_ANDROID"},
            timeout=60,
        )
        resp.raise_for_status()
        data = bytearray(resp.content)
        logger.info(f"Downloaded: {len(data)/1024/1024:.1f}MB")
    else:
        logger.error("Provide --input or --url")
        return

    # 
    total = decrypt_mp4(data, key)
    fix_metadata(data)

    # 
    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "wb") as f:
        f.write(data)

    logger.info(f"Saved: {args.output} ({len(data)/1024/1024:.1f}MB, {total} samples)")


if __name__ == "__main__":
    main()
