"""Decode captured Cronet protobuf responses to find video keys.

Install: pip install blackboxprotobuf


  python scripts/decode_protobuf.py captured_response.bin
  python scripts/decode_protobuf.py videos/ssl_dump/all_raw.bin
"""

from __future__ import annotations

import json
import re
import struct
import sys
from pathlib import Path

from loguru import logger


def decode_varint(data: bytes, pos: int) -> tuple[int, int]:
    """Decode a protobuf varint, return (value, new_position)."""
    result = 0
    shift = 0
    while pos < len(data):
        b = data[pos]
        result |= (b & 0x7F) << shift
        pos += 1
        if (b & 0x80) == 0:
            return result, pos
        shift += 7
    raise ValueError("Truncated varint")


def decode_protobuf_fields(data: bytes, depth: int = 0) -> list[dict]:
    """Decode raw protobuf into a flat list of fields."""
    fields = []
    pos = 0

    while pos < len(data):
        try:
            tag, pos = decode_varint(data, pos)
        except ValueError:
            break

        field_number = tag >> 3
        wire_type = tag & 0x07

        if field_number == 0:
            break

        if wire_type == 0:  # varint
            try:
                value, pos = decode_varint(data, pos)
                fields.append({
                    "field": field_number,
                    "type": "varint",
                    "value": value,
                    "depth": depth,
                })
            except ValueError:
                break

        elif wire_type == 1:  # 64-bit
            if pos + 8 > len(data):
                break
            value = struct.unpack("<Q", data[pos:pos + 8])[0]
            pos += 8
            fields.append({
                "field": field_number,
                "type": "fixed64",
                "value": value,
                "depth": depth,
            })

        elif wire_type == 2:  # length-delimited
            try:
                length, pos = decode_varint(data, pos)
            except ValueError:
                break
            if length < 0 or pos + length > len(data):
                break
            payload = data[pos:pos + length]
            pos += length

            # UTF-8 回退
            try:
                text = payload.decode("utf-8")
                if all(c.isprintable() or c in "\r\n\t" for c in text):
                    fields.append({
                        "field": field_number,
                        "type": "string",
                        "value": text[:500],
                        "length": length,
                        "depth": depth,
                    })
                    continue
            except UnicodeDecodeError:
                pass

            # 处理
            if length >= 2:
                sub_fields = decode_protobuf_fields(payload, depth + 1)
                if len(sub_fields) >= 1:
                    fields.append({
                        "field": field_number,
                        "type": "message",
                        "sub_fields": sub_fields,
                        "length": length,
                        "depth": depth,
                    })
                    continue

            # 
            fields.append({
                "field": field_number,
                "type": "bytes",
                "hex": payload.hex(),
                "length": length,
                "depth": depth,
            })

        elif wire_type == 5:  # 32-bit
            if pos + 4 > len(data):
                break
            value = struct.unpack("<I", data[pos:pos + 4])[0]
            pos += 4
            fields.append({
                "field": field_number,
                "type": "fixed32",
                "value": value,
                "depth": depth,
            })

        else:
            # wire 
            break

    return fields


def search_fields_for_keys(fields: list[dict], path: str = "") -> list[dict]:
    """Search decoded protobuf fields for potential AES keys and video URLs."""
    results = []

    for f in fields:
        current = f"{path}/{f['field']}"
        ftype = f.get("type", "")

        if ftype == "bytes":
            length = f.get("length", 0)
            hex_val = f.get("hex", "")

            # AES-128 key16 处理
            if length == 16:
                payload = bytes.fromhex(hex_val)
                unique = len(set(payload))
                non_zero = sum(1 for b in payload if b != 0)
                if non_zero >= 12 and unique >= 8:
                    results.append({
                        "path": current,
                        "type": "potential_aes128_key",
                        "hex": hex_val,
                        "entropy_score": f"{unique}/16 unique, {non_zero}/16 non-zero",
                    })

            # AES-256 key32 
            elif length == 32:
                payload = bytes.fromhex(hex_val)
                unique = len(set(payload))
                non_zero = sum(1 for b in payload if b != 0)
                if non_zero >= 24 and unique >= 16:
                    results.append({
                        "path": current,
                        "type": "potential_aes256_key",
                        "hex": hex_val,
                    })

            # IVCENC 8 
            elif length == 8:
                payload = bytes.fromhex(hex_val)
                non_zero = sum(1 for b in payload if b != 0)
                if non_zero >= 4:
                    results.append({
                        "path": current,
                        "type": "potential_iv",
                        "hex": hex_val,
                    })

            # KID16 UUID
            elif length == 16:
                results.append({
                    "path": current,
                    "type": "potential_kid",
                    "hex": hex_val,
                })

        elif ftype == "string":
            value = f.get("value", "")
            lo = value.lower()
            if any(kw in lo for kw in [
                "play_url", "video_url", ".mp4", ".m3u8",
                "content_key", "decrypt", "encrypt",
                "kid", "key_id", "media_key", "video_key",
                "play_info", "video_list",
            ]):
                results.append({
                    "path": current,
                    "type": "keyword_match",
                    "value": value[:500],
                })

        elif ftype == "message":
            sub = f.get("sub_fields", [])
            results.extend(search_fields_for_keys(sub, current))

    return results


def print_fields(fields: list[dict], indent: int = 0) -> None:
    """Pretty-print decoded protobuf fields."""
    prefix = "  " * indent
    for f in fields:
        ftype = f.get("type", "?")
        fnum = f.get("field", "?")

        if ftype == "string":
            val = f["value"][:100]
            print(f"{prefix}[{fnum}] string({f['length']}): \"{val}\"")
        elif ftype == "varint":
            print(f"{prefix}[{fnum}] varint: {f['value']}")
        elif ftype == "bytes":
            hex_preview = f["hex"][:64]
            print(f"{prefix}[{fnum}] bytes({f['length']}): {hex_preview}")
        elif ftype == "message":
            print(f"{prefix}[{fnum}] message({f['length']}):")
            print_fields(f["sub_fields"], indent + 1)
        elif ftype in ("fixed32", "fixed64"):
            print(f"{prefix}[{fnum}] {ftype}: {f['value']}")


def main() -> None:
    if len(sys.argv) < 2:
        print(" python scripts/decode_protobuf.py <file.bin> [file2.bin ...]")
        print("  Accepts raw binary protobuf dumps or the all_raw.bin from SSL capture.")
        return

    all_findings: list[dict] = []

    for filepath in sys.argv[1:]:
        p = Path(filepath)
        if not p.exists():
            logger.error(f"File not found: {filepath}")
            continue

        data = p.read_bytes()
        logger.info(f"Processing {filepath} ({len(data)} bytes)")

        # 回退 protobuf 
        fields = decode_protobuf_fields(data)

        if fields:
            logger.info(f"Decoded {len(fields)} top-level fields")
            print_fields(fields)

            findings = search_fields_for_keys(fields)
            if findings:
                logger.info(f"Found {len(findings)} potential key fields:")
                for f in findings:
                    logger.info(f"  {f}")
                all_findings.extend(findings)
        else:
            logger.warning(f"Could not decode as protobuf: {filepath}")

            # 逻辑
            # 处理 SSL dump
            chunks = split_protobuf_stream(data)
            logger.info(f"Split into {len(chunks)} potential messages")

            for i, chunk in enumerate(chunks):
                if len(chunk) < 4:
                    continue
                chunk_fields = decode_protobuf_fields(chunk)
                if chunk_fields:
                    findings = search_fields_for_keys(chunk_fields)
                    if findings:
                        logger.info(f"  Chunk {i}: {len(findings)} findings")
                        for f in findings:
                            f["chunk"] = i
                            logger.info(f"    {f}")
                        all_findings.extend(findings)

    if all_findings:
        output = "protobuf_findings.json"
        with open(output, "w", encoding="utf-8") as f:
            json.dump(all_findings, f, indent=2, ensure_ascii=False, default=str)
        logger.info(f"All findings saved to {output}")
    else:
        logger.info("No key-like fields found in protobuf data")


def split_protobuf_stream(data: bytes) -> list[bytes]:
    """Split a stream of concatenated protobuf messages."""
    chunks = []
    pos = 0

    while pos < len(data):
        # 处理
        # Protobuf 处理 1 tag 0x08 0x0a
        start = pos
        found = False

        for i in range(pos, min(pos + 100, len(data))):
            if data[i] in (0x08, 0x0a, 0x10, 0x12, 0x1a, 0x22):
                if i > start and chunks:
                    # 回退
                    pass
                # 处理
                try_fields = decode_protobuf_fields(data[i:i + min(10000, len(data) - i)])
                if try_fields:
                    # 处理
                    end = find_message_end(data, i)
                    chunks.append(data[i:end])
                    pos = end
                    found = True
                    break

        if not found:
            pos += 1

    return chunks


def find_message_end(data: bytes, start: int) -> int:
    """Estimate end of a protobuf message."""
    pos = start
    while pos < len(data):
        try:
            tag, new_pos = decode_varint(data, pos)
        except ValueError:
            return pos

        field_number = tag >> 3
        wire_type = tag & 0x07

        if field_number == 0 or field_number > 1000:
            return pos

        pos = new_pos

        if wire_type == 0:
            try:
                _, pos = decode_varint(data, pos)
            except ValueError:
                return pos
        elif wire_type == 1:
            pos += 8
        elif wire_type == 2:
            try:
                length, pos = decode_varint(data, pos)
            except ValueError:
                return pos
            pos += length
        elif wire_type == 5:
            pos += 4
        else:
            return pos

        if pos > len(data):
            return len(data)

    return pos


if __name__ == "__main__":
    main()
