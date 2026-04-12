"""mitmproxy addon: capture video-related API responses from HongGuo app."""

import json
import re
import os
from mitmproxy import http

VIDEO_KW = ["play_url", "video_url", "video_list", "video_download", "play_info",
            ".m3u8", ".mp4", "content_key", "media_url", "drama", "episode",
            "video_id", "video_resource", "decrypt", "kid", "key_id"]

OUTPUT = os.path.join(os.path.dirname(__file__), "..", "videos", "mitm_captured.json")
captured: list[dict] = []


class VideoCapture:
    def response(self, flow: http.HTTPFlow) -> None:
        url = flow.request.pretty_url
        ct = flow.response.headers.get("content-type", "")

        body = ""
        try:
            body = flow.response.get_text() or ""
        except Exception:
            pass

        lo_url = url.lower()
        lo_body = body.lower()

        hit = any(kw in lo_url or kw in lo_body for kw in VIDEO_KW)

        if hit and body:
            entry = {
                "url": url,
                "method": flow.request.method,
                "status": flow.response.status_code,
                "content_type": ct,
                "body_preview": body[:8000],
            }
            captured.append(entry)

            # 
            os.makedirs(os.path.dirname(OUTPUT), exist_ok=True)
            with open(OUTPUT, "w", encoding="utf-8") as f:
                json.dump(captured, f, indent=2, ensure_ascii=False)

            print(f"\n>>> VIDEO HIT [{flow.response.status_code}] {url[:200]}")

            # 处理 URL
            urls = re.findall(r'https?://[^\s"\\]+(?:\.mp4|\.m3u8)[^\s"\\]*', body)
            for u in urls[:5]:
                print(f"    URL: {u[:250]}")

            # key
            keys = re.findall(r'"(?:key|content_key|decrypt_key|kid|key_id)"\s*:\s*"([^"]+)"', body)
            for k in keys[:5]:
                print(f"    KEY: {k}")

        elif "phoenix" in lo_url or "fqnovel" in lo_url or "snssdk" in lo_url:
            # App 处理
            print(f"  [{flow.response.status_code}] {url[:150]}")


addons = [VideoCapture()]
