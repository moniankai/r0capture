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


# ============================================================================
# ADB UI 自动化函数（从 download_drama.py 迁移）
# ============================================================================

def run_adb(args: list[str]) -> None:
    """执行 ADB 命令（Windows 环境下自动设置 MSYS_NO_PATHCONV）"""
    import subprocess
    env = {**os.environ, "MSYS_NO_PATHCONV": "1"}
    subprocess.run(["adb"] + args, capture_output=True, check=False, env=env)


def tap_bounds(bounds: tuple[int, int, int, int]) -> None:
    """点击指定 bounds 的中心点"""
    x, y = bounds_center(bounds)
    run_adb(["shell", "input", "tap", str(x), str(y)])


def read_ui_xml_from_device() -> str:
    """dump 并返回手机当前 UI XML。

    两步命令均加了 timeout，避免设备渲染视频时 uiautomator dump 无限挂起。
    dump 失败（非零退出码）时先删除旧文件再返回空串，防止 cat 读到陈旧数据。
    """
    import subprocess
    env = {**os.environ, "MSYS_NO_PATHCONV": "1"}
    try:
        dump_result = subprocess.run(
            ["adb", "shell", "uiautomator", "dump", "/sdcard/_ui.xml"],
            capture_output=True, check=False, env=env, timeout=12,
        )
    except subprocess.TimeoutExpired:
        from loguru import logger
        logger.debug("[ADB] uiautomator dump 超时 (12s)")
        return ""
    if dump_result.returncode != 0:
        from loguru import logger
        logger.debug(f"[ADB] uiautomator dump 失败 (rc={dump_result.returncode})")
        return ""
    try:
        result = subprocess.run(
            ["adb", "shell", "cat", "/sdcard/_ui.xml"],
            capture_output=True, check=False, env=env, timeout=8,
        )
    except subprocess.TimeoutExpired:
        from loguru import logger
        logger.debug("[ADB] cat _ui.xml 超时 (8s)")
        return ""
    if result.returncode != 0 or not result.stdout:
        return ""
    return result.stdout.decode("utf-8", errors="replace")


def should_enter_player_from_detail(xml_text: str) -> bool:
    """选集后如果仍停在详情页，则需要补一次进入播放器动作。"""
    if not xml_text:
        return False
    return (
        'com.phoenix.read:id/ivi' in xml_text
        and 'com.phoenix.read:id/jjj' not in xml_text
    )


def is_target_episode_selected_in_detail(xml_text: str, target_episode: int) -> bool:
    """确认详情页当前高亮选中的确实是目标集。"""
    if not xml_text:
        return False
    if not should_enter_player_from_detail(xml_text):
        return False
    context = parse_ui_context(xml_text)
    return context.episode == target_episode


def tap_detail_cover_to_enter_player() -> None:
    """点击详情页封面区，触发当前已选剧集真正进入播放器。"""
    run_adb(["shell", "input", "tap", "195", "474"])


def _find_episode_button(xml_text: str, ep_num: int) -> tuple | None:
    """在选集网格中查找指定集数按钮的 bounds。

    只匹配 resource-id 为 ``ivi`` 的选集按钮，避免误命中
    分段按钮（如 "1-30"）或总集数文案等元素。
    """
    import xml.etree.ElementTree as _ET

    target = str(ep_num)
    try:
        root = _ET.fromstring(xml_text)
        for elem in root.iter():
            rid = elem.attrib.get('resource-id', '')
            text = (elem.attrib.get('text') or '').strip()
            if rid == 'com.phoenix.read:id/ivi' and text == target:
                bounds_str = elem.attrib.get('bounds', '')
                match = re.fullmatch(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]', bounds_str)
                if match:
                    return tuple(int(p) for p in match.groups())
    except _ET.ParseError:
        pass
    return None


def _select_episode_range(xml_text: str, ep_num: int) -> bool:
    """目标集不在当前可见范围时，点击正确的分段页签。

    例如目标为第 35 集时点击 "31-60"。如果点击了分段按钮则返回 True。
    """
    import xml.etree.ElementTree as _ET
    import time
    from loguru import logger

    try:
        root = _ET.fromstring(xml_text)
        for elem in root.iter():
            rid = elem.attrib.get('resource-id', '')
            text = (elem.attrib.get('text') or '').strip()
            if rid != 'com.phoenix.read:id/gi1' or not text:
                continue
            # 解析类似 "1-30"、"31-60" 的分段文本
            m = re.fullmatch(r'(\d+)-(\d+)', text)
            if not m:
                continue
            lo, hi = int(m.group(1)), int(m.group(2))
            if lo <= ep_num <= hi:
                bounds_str = elem.attrib.get('bounds', '')
                bm = re.fullmatch(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]', bounds_str)
                if bm:
                    tap_bounds(tuple(int(p) for p in bm.groups()))
                    logger.info(f"[ADB] 已切换到范围 {text}")
                    time.sleep(1.0)
                    return True
    except _ET.ParseError:
        pass
    return False


def select_episode_from_ui(ep_num: int, max_attempts: int = 8) -> bool:
    """打开选集面板并点击指定集数。

    使用 resource-id ``ivi`` 精确匹配选集网格按钮，并处理超过 30 集时的
    分段切换（例如 "31-60"）。

    上下文处理：
    - 短剧详情页：``ivi`` 选集网格已经可见，跳过 (540,960) 的唤醒点击，
      避免误点详情页内容区触发播放。
    - 播放器页：``ivi`` 不可见，先唤出控制层，再打开选集面板并查找集数。
    """
    import xml.etree.ElementTree as _ET_sel
    import time
    from loguru import logger

    def _has_ivi(xml_text: str) -> bool:
        try:
            root = _ET_sel.fromstring(xml_text)
            return any('ivi' in e.attrib.get('resource-id', '') for e in root.iter())
        except _ET_sel.ParseError:
            return False

    def _count_ivi_episode_buttons(xml_text: str) -> int:
        """统计详情页真实集数按钮数量（resource-id 包含 ivi 且 text 是 1-999 的数字）。
        用于区分真实详情页（≥5个）和搜索结果卡片内的迷你 ivi 预览（<5个）。"""
        try:
            root = _ET_sel.fromstring(xml_text)
            count = 0
            for e in root.iter():
                if 'ivi' not in e.attrib.get('resource-id', ''):
                    continue
                text = e.attrib.get('text', '').strip()
                if text.isdigit() and 1 <= int(text) <= 999:
                    count += 1
            return count
        except Exception:
            return 0

    # 先检查是否已经在详情页选集网格中，避免误触播放器区域。
    # 需要至少 5 个集数按钮（resource-id 含 ivi、text 为数字）才判定为真实详情页，
    # 以排除搜索结果页卡片内只有 2-3 个集数预览的误判。
    _initial_xml = read_ui_xml_from_device()
    picker_open = _initial_xml is not None and _count_ivi_episode_buttons(_initial_xml) >= 5
    if picker_open:
        logger.debug("[ADB] 剧情详情页集数网格已可见，跳过打开选集弹窗")

    if not picker_open:
        # 最多尝试 3 次打开选集面板：先唤醒控制层，再点击"选集"按钮
        # 优先通过 resource-id "joj" 或文本"选集"动态定位；若 dump 期间控制层已隐藏，
        # 重新唤醒后点击已知位置（joj 在控制层左侧，约 138,1836）。
        _last_known_joj = None  # 记录成功定位的 joj bounds，供后续回退使用
        for _open_try in range(3):
            # 唤醒播放器控制层
            run_adb(["shell", "input", "tap", "540", "960"])
            time.sleep(1.0)  # 给控制层足够时间出现后再 dump

            # 读取控制层 XML 并动态定位"选集"按钮
            _overlay_xml = read_ui_xml_from_device()
            _joj_bounds = None
            if _overlay_xml:
                _joj_bounds = find_element_by_resource_id(_overlay_xml, "com.phoenix.read:id/joj")
                if not _joj_bounds:
                    _joj_bounds = find_text_bounds(_overlay_xml, "选集")
            if _joj_bounds:
                _last_known_joj = _joj_bounds  # 保存成功定位结果

            if _joj_bounds:
                logger.debug(f"[ADB] 找到选集按钮 bounds={_joj_bounds}，点击")
                tap_bounds(_joj_bounds)
            else:
                # dump 期间控制层已自动隐藏；重新唤醒后立刻点击已知位置，避免再次超时
                _fallback = _last_known_joj or (96, 1808, 180, 1865)  # 实测 joj 默认位置
                _fx = (_fallback[0] + _fallback[2]) // 2
                _fy = (_fallback[1] + _fallback[3]) // 2
                logger.debug(f"[ADB] 未找到选集按钮，重新唤醒后点击已知位置 ({_fx},{_fy})")
                run_adb(["shell", "input", "tap", "540", "960"])  # 重新唤醒
                time.sleep(0.6)
                run_adb(["shell", "input", "tap", str(_fx), str(_fy)])  # 立刻点击 joj
            time.sleep(1.5)

            # 只要 XML 中出现任意 ivi 元素，就说明选集面板已经打开
            _peek_xml = read_ui_xml_from_device()
            if _peek_xml and _has_ivi(_peek_xml):
                picker_open = True
                break
            if _open_try < 2:
                logger.debug(f"[ADB] 选集面板未打开，重试 ({_open_try + 1}/3)")

    range_switched = False
    for attempt in range(max_attempts):
        xml_text = read_ui_xml_from_device()
        if not xml_text:
            time.sleep(1)
            continue

        # 首次尝试时点击范围页签，将选集面板定位到目标集所在分段。
        # 对小集号（如第1集），点击"1-30"页签可将面板重置到顶部，避免依赖
        # 可能穿透到播放器的滑动手势。仅在首次尝试（range_switched=False）时执行。
        if not range_switched:
            range_switched = True  # 无论成功与否，只尝试一次
            if _select_episode_range(xml_text, ep_num):
                logger.debug(f'[ADB] 已点击范围页签，重新读取 ivi 面板')
                time.sleep(1.0)
                _new_xml = read_ui_xml_from_device()
                if _new_xml:
                    xml_text = _new_xml

        # 通过 resource-id ivi 精确查找集数按钮
        target_bounds = _find_episode_button(xml_text, ep_num)
        if target_bounds:
            tap_bounds(target_bounds)
            logger.info(f"[ADB] 已在选集面板点击第{ep_num}集")
            time.sleep(2.5)
            _after_select_xml = read_ui_xml_from_device()
            if should_enter_player_from_detail(_after_select_xml):
                if not is_target_episode_selected_in_detail(_after_select_xml, ep_num):
                    logger.warning(f"[ADB] 点击第{ep_num}集后，高亮选中并未切到目标集，继续重试")
                    continue
                logger.info("[ADB] 选集后仍停在详情页，补点封面进入播放器")
                tap_detail_cover_to_enter_player()
                time.sleep(2.5)
            return True

        # 从 ivi 元素实际 y 范围计算滚动坐标，确保手势落在选集面板内
        _ivi_ys: list[tuple[int, int]] = []
        try:
            for _e in _ET_sel.fromstring(xml_text).iter():
                if 'ivi' not in _e.attrib.get('resource-id', ''):
                    continue
                _bm = re.fullmatch(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]', _e.attrib.get('bounds', ''))
                if _bm:
                    _ivi_ys.append((int(_bm.group(2)), int(_bm.group(4))))
        except Exception:
            pass
        if _ivi_ys:
            _sy_top = _ivi_ys[0][0]
            _sy_bot = _ivi_ys[-1][1]
            _sy_ctr = (_sy_top + _sy_bot) // 2
            _sdy = max(60, min(100, (_sy_bot - _sy_top) // 4))
            _y_a = str(max(_sy_ctr - _sdy, _sy_top + 5))
            _y_b = str(min(_sy_ctr + _sdy, _sy_bot - 5))
        else:
            _y_a, _y_b = '1580', '1700'
        # scroll_up：手指向下（y 增大），列表上滑，显示更早的集
        # scroll_down：手指向上（y 减小），列表下滑，显示更晚的集
        scroll_up   = ['shell', 'input', 'swipe', '540', _y_a, '540', _y_b, '500']
        scroll_down = ['shell', 'input', 'swipe', '540', _y_b, '540', _y_a, '500']
        if ep_num <= 15:
            run_adb(scroll_up if attempt < max_attempts // 2 else scroll_down)
        else:
            run_adb(scroll_down if attempt < max_attempts // 2 else scroll_up)
        time.sleep(0.8)

        # 滚动后确认面板仍然打开；如已消失则手势穿透到播放器，及早终止
        _chk_xml = read_ui_xml_from_device()
        if _chk_xml and not _has_ivi(_chk_xml):
            logger.warning('[ADB] 滚动后选集面板已关闭（手势穿透播放器），终止滚动')
            break

    logger.error(f"[ADB] 未能在选集面板找到第{ep_num}集")
    # 处理 XML 处理
    try:
        _debug_xml = read_ui_xml_from_device()
        if _debug_xml:
            # ivi XML 1500 处理
            import xml.etree.ElementTree as _ET
            try:
                _root = _ET.fromstring(_debug_xml)
                _ivi_texts = [
                    (e.attrib.get('resource-id',''), e.attrib.get('text',''))
                    for e in _root.iter()
                    if 'ivi' in e.attrib.get('resource-id','') or e.attrib.get('text','').strip().isdigit()
                ][:20]
                logger.debug(f"[ADB] picker XML ivi/digit elements: {_ivi_texts}")
            except Exception:
                pass
            logger.debug(f"[ADB] picker XML (first 1500): {_debug_xml[:1500]}")
    except Exception:
        pass
    return False
