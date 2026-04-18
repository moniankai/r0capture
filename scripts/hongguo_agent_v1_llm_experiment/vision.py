"""vision.py: Claude Sonnet 4.6 VLM 截图校验。

唯一入口: verify_screen_impl(expected_episode) -> dict

流程:
  1. svc.screenshot() 拿屏幕 PNG
  2. base64 读图
  3. 调 anthropic.messages.create() 发结构化 prompt
  4. 解析 VLM JSON 回复
  5. 返回 {ok, drama_match, observed_drama, observed_episode, confidence, evidence_path}

约束:
- 要求 VLM 返回 JSON(提示词强制格式)
- 容错: 解析失败时退回"raw 字符串 + ok=false"
- 不抛异常给 Agent,失败都走 return
"""
from __future__ import annotations

import base64
import json
import os
import re
from pathlib import Path
from typing import Any

from io import BytesIO

from loguru import logger
from PIL import Image

from .llm import FallbackAnthropic, build_default_client
from .service import HongguoService


_VISION_MODEL = "claude-sonnet-4-6"
_MAX_TOKENS = 400

# 下采样 + 压缩阈值: 1080p PNG ~2-3MB,压缩后 ~150-300KB JPEG
_MAX_SIDE = 1280        # 宽/高两边中较长边的上限(像素)
_JPEG_QUALITY = 80      # 0-95,80 够读清 OSD 小字

_client: FallbackAnthropic | None = None


def _get_client() -> FallbackAnthropic:
    global _client
    if _client is None:
        _client = build_default_client()
    return _client


def _build_prompt(drama: str, expected_episode: int) -> str:
    return (
        f"这是红果短剧 App 的一张屏幕截图。目标剧名是《{drama}》,Agent 期望当前播放第 {expected_episode} 集。\n\n"
        "请观察截图并回答:\n"
        "1. 页面顶部/中央显示的剧名是什么?是否是《" + drama + "》?\n"
        "2. 播放器左上角或顶部是否显示类似'第 N 集'的集数?N 是多少?\n"
        "3. 如果剧名对不上或集数对不上,请明确指出观察到的值。\n\n"
        "**只返回严格 JSON,不要 markdown 包裹,不要解释文字**。格式:\n"
        "{\n"
        '  "observed_drama": "观察到的剧名(如不清楚写 null)",\n'
        '  "drama_match": true/false,\n'
        '  "observed_episode": 观察到的集数整数(如不清楚写 null),\n'
        f'  "episode_match": true/false (是否等于期望 {expected_episode}),\n'
        '  "confidence": 0.0-1.0 的置信度,\n'
        '  "notes": "简短说明观察到的关键证据,例如 \'左上角见 第 3 集\'"\n'
        "}"
    )


def _compress_for_vlm(png_path: str) -> tuple[bytes, str]:
    """读 PNG,等比缩到 _MAX_SIDE 以内,转 JPEG。返回 (jpeg_bytes, 'image/jpeg')。"""
    img = Image.open(png_path)
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    w, h = img.size
    m = max(w, h)
    if m > _MAX_SIDE:
        scale = _MAX_SIDE / m
        img = img.resize((int(w * scale), int(h * scale)), Image.Resampling.LANCZOS)
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=_JPEG_QUALITY, optimize=True)
    return buf.getvalue(), "image/jpeg"


_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def _parse_vlm_reply(text: str) -> dict[str, Any] | None:
    """VLM 回复尽量解析成 JSON。失败返回 None。"""
    text = (text or "").strip()
    if not text:
        return None
    # 直接 JSON
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # 剥 markdown code fence
    if text.startswith("```"):
        inner = re.sub(r"^```[a-zA-Z]*\n?", "", text).rstrip("` \n")
        try:
            return json.loads(inner)
        except json.JSONDecodeError:
            pass
    # 正则提大括号段
    m = _JSON_RE.search(text)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    return None


def _build_compare_prompt(drama: str, expected_episode: int) -> str:
    return (
        f"这里有两张图。目标: 短剧《{drama}》的第 {expected_episode} 集。\n\n"
        "图 A 是我下载的 mp4 文件中抽出的一帧。\n"
        "图 B 是手机 App 当前屏幕截图,期望当前在播放第 " + str(expected_episode) + " 集。\n\n"
        "请判断: 这两张图是否来自同一集的内容(同一场景或同一集的不同时间点)?\n"
        "判断依据优先级: 画面场景/人物/服装/道具 > 剧名/OSD 文字。\n"
        "注意: 同一主角在不同集可能穿同样衣服,但场景、情节、配角会不同。\n\n"
        "**只返回严格 JSON,不要 markdown 包裹。格式**:\n"
        "{\n"
        '  "same_episode": true/false,\n'
        '  "confidence": 0.0-1.0,\n'
        '  "reason": "简短观察依据,必须提及两图的共同/差异点"\n'
        "}"
    )


def compare_two_images_impl(frame_a_path: str, frame_b_path: str,
                             expected_episode: int) -> dict:
    """对比两张图是否是同一集内容。两张图都会压缩/转 JPEG。"""
    svc = HongguoService.get()
    if not svc.drama:
        return {"ok": False, "reason": "service 未 start_session"}

    try:
        a_bytes, a_mt = _compress_for_vlm(frame_a_path)
        b_bytes, b_mt = _compress_for_vlm(frame_b_path)
    except Exception as e:
        return {"ok": False, "reason": f"读图失败: {e}"}

    a_b64 = base64.standard_b64encode(a_bytes).decode("ascii")
    b_b64 = base64.standard_b64encode(b_bytes).decode("ascii")
    prompt = _build_compare_prompt(svc.drama, expected_episode)

    try:
        client = _get_client()
        resp = client.messages.create(
            model=_VISION_MODEL,
            max_tokens=_MAX_TOKENS,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": "图 A(下载 mp4 首帧):"},
                    {"type": "image",
                     "source": {"type": "base64", "media_type": a_mt, "data": a_b64}},
                    {"type": "text", "text": "图 B(App 当前屏幕):"},
                    {"type": "image",
                     "source": {"type": "base64", "media_type": b_mt, "data": b_b64}},
                    {"type": "text", "text": prompt},
                ],
            }],
        )
    except Exception as e:
        return {"ok": False, "reason": f"VLM API 失败: {e}",
                "frame_a": frame_a_path, "frame_b": frame_b_path}

    raw = "".join(
        block.text for block in resp.content
        if getattr(block, "type", "") == "text"
    ).strip()
    parsed = _parse_vlm_reply(raw)

    svc.log_trace("compare_two_images", {
        "expected_episode": expected_episode,
        "frame_a": os.path.basename(frame_a_path),
        "frame_b": os.path.basename(frame_b_path),
        "raw_preview": raw[:200],
        "parsed": parsed,
    })

    if parsed is None:
        return {"ok": False, "reason": "VLM 回复无法解析",
                "raw": raw[:500],
                "frame_a": frame_a_path, "frame_b": frame_b_path}

    return {
        "ok": True,
        "same_episode": bool(parsed.get("same_episode")),
        "confidence": parsed.get("confidence"),
        "reason": parsed.get("reason", "")[:300] if isinstance(parsed.get("reason"), str) else "",
        "frame_a": frame_a_path,
        "frame_b": frame_b_path,
    }


def verify_screen_impl(expected_episode: int) -> dict:
    """校验当前屏幕是否是目标剧的 expected_episode 集。"""
    svc = HongguoService.get()
    if not svc.drama:
        return {"ok": False, "reason": "service 未 start_session,无 drama 上下文"}

    shot = svc.screenshot(label=f"verify_ep{expected_episode}")
    if not shot.get("ok"):
        return {"ok": False, "reason": f"截图失败: {shot.get('reason')}"}
    shot_path = shot["path"]

    try:
        jpeg_bytes, media_type = _compress_for_vlm(shot_path)
        img_b64 = base64.standard_b64encode(jpeg_bytes).decode("ascii")
        logger.debug(
            f"[vision] 截图压缩: {os.path.getsize(shot_path) // 1024}KB PNG "
            f"→ {len(jpeg_bytes) // 1024}KB JPEG"
        )
    except (OSError, Exception) as e:
        return {"ok": False, "reason": f"读取/压缩截图失败: {e}"}

    prompt = _build_prompt(svc.drama, expected_episode)

    try:
        client = _get_client()
    except Exception as e:
        return {"ok": False, "reason": f"anthropic 客户端初始化失败: {e}"}

    try:
        resp = client.messages.create(
            model=_VISION_MODEL,
            max_tokens=_MAX_TOKENS,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": img_b64,
                        },
                    },
                    {"type": "text", "text": prompt},
                ],
            }],
        )
    except Exception as e:
        return {"ok": False, "reason": f"VLM API 调用失败: {e}",
                "evidence_path": shot_path}

    raw = "".join(
        block.text for block in resp.content
        if getattr(block, "type", "") == "text"
    ).strip()

    parsed = _parse_vlm_reply(raw)
    svc.log_trace("verify_screen", {
        "expected_episode": expected_episode,
        "screenshot": os.path.basename(shot_path),
        "raw_preview": raw[:200],
        "parsed": parsed,
    })

    if parsed is None:
        return {
            "ok": False,
            "reason": "VLM 回复无法解析为 JSON",
            "raw": raw[:500],
            "evidence_path": shot_path,
        }

    return {
        "ok": True,
        "drama_match": bool(parsed.get("drama_match")),
        "observed_drama": parsed.get("observed_drama"),
        "observed_episode": parsed.get("observed_episode"),
        "episode_match": bool(parsed.get("episode_match")),
        "confidence": parsed.get("confidence"),
        "notes": parsed.get("notes", "")[:200] if isinstance(parsed.get("notes"), str) else "",
        "evidence_path": shot_path,
    }
