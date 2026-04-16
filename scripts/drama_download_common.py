from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

INVALID_PATH_CHARS = '<>:"/\\|?*'
SKIP_TITLE_TEXTS = {
    '全屏观看',
    '选集',
    '展开',
    '收起',
    '倍速',
    '立即领取',
    '确定',
    '取消',
    '关注',
    '分享',
    '评论',
    '点赞',
    '更多',
    '下一集',
    '上一集',
    '热评',
    '评论',
    '剧评',
    '出品方',
    '听花岛剧场',
    '回复',
    '作者声明：内容由AI生成',
}
KNOWN_TITLE_RESOURCE_IDS = {
    'com.phoenix.read:id/d4',
}
KNOWN_EPISODE_RESOURCE_IDS = {
    'com.phoenix.read:id/jjj',
}
KNOWN_TOTAL_RESOURCE_IDS = {
    'com.phoenix.read:id/jr1',
}


@dataclass
class UIContext:
    title: str = ''
    episode: Optional[int] = None
    total_episodes: Optional[int] = None
    raw_texts: list[str] = field(default_factory=list)


@dataclass
class SessionValidationState:
    locked_title: str = ''
    seen_video_ids: set[str] = field(default_factory=set)
    last_episode: int = 0


def sanitize_drama_name(name: str) -> str:
    cleaned = ''.join('_' if ch in INVALID_PATH_CHARS else ch for ch in (name or '').strip())
    cleaned = cleaned.rstrip('. ').strip()
    return cleaned or 'unknown_drama'


def _extract_nodes(xml_text: str) -> list[dict[str, str]]:
    root = ET.fromstring(xml_text)
    nodes: list[dict[str, str]] = []
    for elem in root.iter():
        text = (elem.attrib.get('text') or '').strip()
        if not text:
            continue
        nodes.append(
            {
                'text': text,
                'resource_id': elem.attrib.get('resource-id', ''),
                'class_name': elem.attrib.get('class', ''),
            }
        )
    return nodes


def find_text_bounds(xml_text: str, target_text: str) -> Optional[tuple[int, int, int, int]]:
    root = ET.fromstring(xml_text)
    for elem in root.iter():
        if (elem.attrib.get('text') or '').strip() != target_text:
            continue
        bounds = elem.attrib.get('bounds', '')
        match = re.fullmatch(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]', bounds)
        if not match:
            continue
        return tuple(int(part) for part in match.groups())
    return None


def find_text_contains_bounds(
    xml_text: str, substring: str
) -> Optional[tuple[int, int, int, int]]:
    """Return bounds of the first element whose text contains *substring*."""
    root = ET.fromstring(xml_text)
    for elem in root.iter():
        text = (elem.attrib.get('text') or '').strip()
        if substring not in text:
            continue
        bounds = elem.attrib.get('bounds', '')
        match = re.fullmatch(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]', bounds)
        if not match:
            continue
        return tuple(int(part) for part in match.groups())
    return None


def find_content_desc_bounds(
    xml_text: str, target: str
) -> Optional[tuple[int, int, int, int]]:
    """Return bounds of the first element whose content-desc equals *target*."""
    root = ET.fromstring(xml_text)
    for elem in root.iter():
        if (elem.attrib.get('content-desc') or '').strip() == target:
            bounds = elem.attrib.get('bounds', '')
            match = re.fullmatch(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]', bounds)
            if not match:
                continue
            return tuple(int(part) for part in match.groups())
    return None


def find_element_by_class(
    xml_text: str, class_name: str
) -> Optional[tuple[int, int, int, int]]:
    """Return bounds of the first element with the given Android widget class."""
    root = ET.fromstring(xml_text)
    for elem in root.iter():
        if elem.attrib.get('class', '') != class_name:
            continue
        bounds = elem.attrib.get('bounds', '')
        match = re.fullmatch(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]', bounds)
        if not match:
            continue
        return tuple(int(part) for part in match.groups())
    return None


def find_element_by_resource_id(
    xml_text: str, resource_id: str
) -> Optional[tuple[int, int, int, int]]:
    """Return bounds of the first element with the given resource-id."""
    root = ET.fromstring(xml_text)
    for elem in root.iter():
        if elem.attrib.get('resource-id', '') != resource_id:
            continue
        bounds = elem.attrib.get('bounds', '')
        match = re.fullmatch(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]', bounds)
        if not match:
            continue
        return tuple(int(part) for part in match.groups())
    return None


def bounds_center(bounds: tuple[int, int, int, int]) -> tuple[int, int]:
    left, top, right, bottom = bounds
    return (left + right) // 2, (top + bottom) // 2


def _parse_episode_value(text: str) -> Optional[int]:
    match = re.search(r'第\s*(\d+)\s*[集话]', text)
    if match:
        return int(match.group(1))
    return None


def _parse_total_value(text: str) -> Optional[int]:
    match = re.search(r'(?:全|共)\s*(\d+)\s*[集话]', text)
    if match:
        return int(match.group(1))
    return None


def _parse_selected_episode_from_grid(xml_text: str) -> Optional[int]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None

    parent_map = {child: parent for parent in root.iter() for child in parent}
    for elem in root.iter():
        if elem.attrib.get('resource-id', '') != 'com.phoenix.read:id/ivi':
            continue
        text = (elem.attrib.get('text') or '').strip()
        if not text.isdigit():
            continue
        parent = parent_map.get(elem)
        if parent is None:
            continue
        for child in parent.iter():
            if child.attrib.get('resource-id', '') == 'com.phoenix.read:id/zu':
                return int(text)
    return None


def _looks_like_title(text: str) -> bool:
    if not text or text in SKIP_TITLE_TEXTS:
        return False
    if len(text) < 2 or len(text) > 40:
        return False
    if re.fullmatch(r'[\d.]+万?', text):
        return False
    if re.fullmatch(r'[\d.]+[万次点赞收藏热度推荐分]+', text):
        return False
    if text.startswith('大家都在搜'):
        return False
    if _parse_episode_value(text) is not None or _parse_total_value(text) is not None:
        return False
    if '已完结' in text or '更新' in text:
        return False
    if text.startswith('·'):
        return False
    return bool(re.search(r'[\u4e00-\u9fff]', text))


def parse_ui_context(xml_text: str) -> UIContext:
    nodes = _extract_nodes(xml_text)
    texts = [node['text'] for node in nodes]

    title = ''
    episode = None
    total_episodes = None

    for node in nodes:
        text = node['text']
        resource_id = node['resource_id']
        if resource_id in KNOWN_TITLE_RESOURCE_IDS and _looks_like_title(text):
            title = text
            break
    is_episode_comment_panel = (
        ('评论' in texts or '剧评' in texts)
        and any(re.search(r'第\s*\d+\s*[集话]\s*\|', text) for text in texts)
    )
    if not title and not is_episode_comment_panel:
        for node in nodes:
            if _looks_like_title(node['text']):
                title = node['text']
                break

    for node in nodes:
        text = node['text']
        resource_id = node['resource_id']
        if resource_id in KNOWN_EPISODE_RESOURCE_IDS:
            episode = _parse_episode_value(text)
            if episode is not None:
                break
    if episode is None:
        for node in nodes:
            episode = _parse_episode_value(node['text'])
            if episode is not None:
                break
    if episode is None:
        episode = _parse_selected_episode_from_grid(xml_text)

    for node in nodes:
        text = node['text']
        resource_id = node['resource_id']
        if resource_id in KNOWN_TOTAL_RESOURCE_IDS:
            total_episodes = _parse_total_value(text)
            if total_episodes is not None:
                break
    if total_episodes is None:
        for node in nodes:
            total_episodes = _parse_total_value(node['text'])
            if total_episodes is not None:
                break

    return UIContext(
        title=title,
        episode=episode,
        total_episodes=total_episodes,
        raw_texts=texts,
    )


def video_id_suffix(video_id: str, length: int = 8) -> str:
    if not video_id:
        return 'unknown'
    return video_id[-length:] if len(video_id) > length else video_id


def build_episode_base_name(episode: int, video_id: str) -> str:
    suffix = video_id_suffix(video_id)
    return f'episode_{episode:03d}_{suffix}'


def build_episode_paths(
    output_dir: str, episode: int, video_id: str, drama_name: str = ''
) -> tuple[str, str]:
    folder_name = os.path.basename(output_dir) if not drama_name else drama_name
    suffix = video_id_suffix(video_id)
    video_path = os.path.join(output_dir, f'{folder_name}_episode_{episode:03d}_{suffix}.mp4')
    meta_path = os.path.join(output_dir, f'meta_ep{episode:03d}_{suffix}.json')
    return video_path, meta_path


_CHINESE_NUMERAL_CHARS = frozenset('零一二三四五六七八九十百千万')


def _title_core(title: str) -> str:
    """去掉标题开头的阿拉伯数字或中文数字，便于模糊比较。

    这用于处理同一剧名在搜索页显示为 "18岁太奶奶"、播放器显示为
    "十八岁太奶奶" 的情况。去掉两种数字前缀后会得到相同核心标题
    "岁太奶奶..."，避免跨集校验时误报标题漂移。
    """
    s = re.sub(r'^\d+', '', title)  # 去掉开头的阿拉伯数字
    while s and s[0] in _CHINESE_NUMERAL_CHARS:  # 去掉开头的中文数字
        s = s[1:]
    return s.strip()


def validate_round(
    state: SessionValidationState,
    ui_context: UIContext,
    video_id: str,
    expected_title: str = '',
    fallback_episode: Optional[int] = None,
) -> tuple[bool, str]:
    actual_title = sanitize_drama_name(ui_context.title or expected_title)
    forced_title = sanitize_drama_name(expected_title) if expected_title else ''
    resolved_episode = ui_context.episode if ui_context.episode is not None else fallback_episode

    if not ui_context.title and not expected_title:
        return False, 'missing_title'
    if resolved_episode is None:
        return False, 'missing_episode'
    if forced_title and actual_title != forced_title:
        return False, 'title_mismatch'
    # 比较标题核心部分，以容忍阿拉伯数字和中文数字前缀差异。
    # 例如搜索页记录为 "18岁..."，播放器展示为 "十八岁..."。
    if state.locked_title and _title_core(actual_title) != _title_core(state.locked_title):
        return False, 'title_drift'
    if video_id and video_id in state.seen_video_ids:
        return False, 'duplicate_video_id'
    if state.last_episode and resolved_episode <= state.last_episode:
        return False, 'episode_not_ascending'
    return True, 'ok'


def apply_valid_round(
    state: SessionValidationState,
    ui_context: UIContext,
    video_id: str,
    expected_title: str = '',
    fallback_episode: Optional[int] = None,
) -> tuple[bool, str]:
    ok, reason = validate_round(
        state,
        ui_context,
        video_id,
        expected_title=expected_title,
        fallback_episode=fallback_episode,
    )
    if not ok:
        return ok, reason

    resolved_title = sanitize_drama_name(ui_context.title or expected_title)
    resolved_episode = ui_context.episode if ui_context.episode is not None else fallback_episode
    if not state.locked_title:
        state.locked_title = resolved_title
    if video_id:
        state.seen_video_ids.add(video_id)
    if resolved_episode is not None:
        state.last_episode = resolved_episode
    return True, 'ok'


def append_jsonl(path: str | Path, payload: dict) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open('a', encoding='utf-8', newline='\n') as fh:
        import json

        fh.write(json.dumps(payload, ensure_ascii=False))
        fh.write('\n')


def parse_session_manifest(manifest_path: str | Path) -> set[int]:
    """解析 session_manifest.jsonl，返回已完成的集数集合。

    Args:
        manifest_path: session_manifest.jsonl 文件路径

    Returns:
        已完成的集数集合（episode 字段值）
    """
    import json
    import logging

    logger = logging.getLogger(__name__)
    target = Path(manifest_path)

    if not target.exists():
        return set()

    completed = set()
    with target.open('r', encoding='utf-8') as fh:
        for line_num, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
                status = record.get('status', '')
                if status in ('downloaded', 'skipped_existing'):
                    episode = record.get('episode')
                    if isinstance(episode, int):
                        completed.add(episode)
            except json.JSONDecodeError as e:
                logger.warning(f"跳过 session_manifest.jsonl 第 {line_num} 行（格式错误）: {e}")
                continue

    return completed
