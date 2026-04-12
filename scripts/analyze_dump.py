""" SSL dump for video URLs and decryption keys."""

import json
import re
import sys


def main():
    with open("videos/ssl_dump/ssl_text_dump.json", "r", encoding="utf-8") as f:
        packets = json.load(f)

    print(f"Total text packets: {len(packets)}")

    video_urls: list[str] = []
    keys: list[dict] = []
    play_info: list[dict] = []

    mp4_re = re.compile(r'https?://[^\s"<>]+\.mp4[^\s"<>]*')
    m3u8_re = re.compile(r'https?://[^\s"<>]+\.m3u8[^\s"<>]*')
    key_re = re.compile(r'"(?:content_key|decrypt_key|media_key|encrypt_key|video_key)"\s*:\s*"([^"]+)"')
    kid_re = re.compile(r'"kid"\s*:\s*"([^"]+)"')

    for pkt in packets:
        text = pkt.get("text", "")
        lo = text.lower()

        for u in mp4_re.findall(text):
            if u not in video_urls:
                video_urls.append(u)
        for u in m3u8_re.findall(text):
            if u not in video_urls:
                video_urls.append(u)

        for m in key_re.finditer(text):
            keys.append({"key": m.group(1), "pkt": pkt["n"]})
        for m in kid_re.finditer(text):
            keys.append({"kid": m.group(1), "pkt": pkt["n"]})

        for kw in ["play_url", "play_info", "video_url", "video_list", "video_download_url"]:
            idx = lo.find(kw)
            if idx >= 0:
                snippet = text[max(0, idx - 30) : idx + 600]
                play_info.append({"keyword": kw, "pkt": pkt["n"], "snippet": snippet[:700]})

    print(f"\nVideo URLs: {len(video_urls)}")
    for u in video_urls[:15]:
        print(f"  {u[:300]}")

    print(f"\nKeys: {len(keys)}")
    for k in keys[:10]:
        print(f"  {k}")

    print(f"\nPlay info: {len(play_info)}")
    for p in play_info[:10]:
        print(f"\n  [{p['keyword']}] pkt#{p['pkt']}:")
        print(f"  {p['snippet'][:500]}")


if __name__ == "__main__":
    main()
