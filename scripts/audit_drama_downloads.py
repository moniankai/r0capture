from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.drama_download_common import build_episode_base_name, sanitize_drama_name, video_id_suffix


def load_metadata(path: str | Path) -> dict[str, Any]:
    with Path(path).open('r', encoding='utf-8') as fh:
        return json.load(fh)


def analyze_drama_directory(
    path: str | Path,
    expected_total: int | None = None,
    expected_title: str = '',
    renumber_from: int | None = None,
    order_by: str = 'mtime',
) -> dict[str, Any]:
    root = Path(path)
    meta_files = sorted(root.glob('meta_ep*.json'))
    video_files = sorted(root.glob('episode_*.mp4'))
    video_names = {p.name for p in video_files}

    entries: list[dict[str, Any]] = []
    drama_names: list[str] = []
    total_candidates: list[int] = []
    duplicates: dict[str, list[dict[str, Any]]] = defaultdict(list)
    rename_plan: list[dict[str, Any]] = []
    missing_video_for_meta: list[str] = []

    for meta_path in meta_files:
        data = load_metadata(meta_path)
        episode = int(data.get('episode') or 0)
        video_id = str(data.get('video_id') or '')
        drama = str(data.get('drama') or '')
        if drama:
            drama_names.append(drama)
        total_value = data.get('ui_total_episodes') or data.get('total_episodes')
        if total_value:
            total_candidates.append(int(total_value))

        expected_video_name = f"{build_episode_base_name(episode, video_id)}.mp4"
        expected_meta_name = f"meta_ep{episode:03d}_{video_id_suffix(video_id)}.json"
        current_video_name = meta_path.name.replace('meta_ep', 'episode_').replace('.json', '.mp4')
        if current_video_name not in video_names and expected_video_name not in video_names:
            missing_video_for_meta.append(str(meta_path))

        if meta_path.name != expected_meta_name or current_video_name != expected_video_name:
            rename_plan.append(
                {
                    'meta_path': str(meta_path),
                    'target_meta_name': expected_meta_name,
                    'target_video_name': expected_video_name,
                }
            )

        entry = {
            'meta_path': str(meta_path),
            'episode': episode,
            'video_id': video_id,
            'drama': drama,
            'expected_video_name': expected_video_name,
            'expected_meta_name': expected_meta_name,
        }
        entries.append(entry)
        if video_id:
            duplicates[video_id].append(entry)

    expected_total_episodes = expected_total
    if expected_total_episodes is None and total_candidates:
        expected_total_episodes = Counter(total_candidates).most_common(1)[0][0]

    episodes_present = sorted({entry['episode'] for entry in entries if entry['episode']})
    missing_episodes: list[int] = []
    if expected_total_episodes:
        for number in range(1, expected_total_episodes + 1):
            if number not in episodes_present:
                missing_episodes.append(number)

    expected_drama_name = expected_title or root.name
    metadata_majority_drama_name = ''
    if drama_names:
        metadata_majority_drama_name = Counter(drama_names).most_common(1)[0][0]

    drama_name_mismatches = [
        entry
        for entry in entries
        if entry['drama']
        and sanitize_drama_name(entry['drama']) != sanitize_drama_name(expected_drama_name)
    ]
    folder_name_mismatch = sanitize_drama_name(root.name) != sanitize_drama_name(expected_drama_name)
    duplicate_video_ids = {
        video_id: group for video_id, group in duplicates.items() if len(group) > 1
    }

    meta_names = {p.name for p in meta_files}
    missing_meta_for_video = []
    for video_path in video_files:
        suffix = video_path.stem.removeprefix('episode_')
        guessed_meta_name = f'meta_ep{suffix}.json'
        if guessed_meta_name not in meta_names:
            missing_meta_for_video.append(str(video_path))

    metadata_by_stem_suffix = {
        Path(entry['meta_path']).stem.removeprefix('meta_ep'): entry for entry in entries
    }
    if order_by == 'name':
        ordered_videos = sorted(video_files, key=lambda item: item.name)
    elif order_by == 'mtime':
        ordered_videos = sorted(video_files, key=lambda item: (item.stat().st_mtime, item.name))
    else:
        raise ValueError("order_by must be 'mtime' or 'name'")

    order_renumber_plan: list[dict[str, Any]] = []
    if renumber_from is not None:
        for offset, video_path in enumerate(ordered_videos):
            new_episode = renumber_from + offset
            suffix = video_path.stem.removeprefix('episode_')
            entry = metadata_by_stem_suffix.get(suffix)
            video_id = entry['video_id'] if entry else ''
            target_video_name = f'{build_episode_base_name(new_episode, video_id)}.mp4'
            target_meta_name = f'meta_ep{new_episode:03d}_{video_id_suffix(video_id)}.json'
            order_renumber_plan.append(
                {
                    'source_video_path': str(video_path),
                    'source_meta_path': entry['meta_path'] if entry else '',
                    'source_episode': entry['episode'] if entry else None,
                    'target_episode': new_episode,
                    'video_id': video_id,
                    'target_video_name': target_video_name,
                    'target_meta_name': target_meta_name,
                }
            )

    return {
        'directory': str(root),
        'folder_name': root.name,
        'canonical_drama_name': expected_drama_name,
        'metadata_majority_drama_name': metadata_majority_drama_name,
        'expected_drama_name': expected_drama_name,
        'folder_name_mismatch': folder_name_mismatch,
        'expected_total_episodes': expected_total_episodes,
        'episodes_present': episodes_present,
        'missing_episodes': missing_episodes,
        'drama_name_mismatches': drama_name_mismatches,
        'duplicate_video_ids': duplicate_video_ids,
        'rename_plan': rename_plan,
        'order_by': order_by,
        'order_renumber_plan': order_renumber_plan,
        'missing_video_for_meta': missing_video_for_meta,
        'missing_meta_for_video': missing_meta_for_video,
    }


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description='Audit downloaded HongGuo drama episodes')
    parser.add_argument('path', help='Drama directory to audit')
    parser.add_argument('--expected-total', type=int, default=None, help='Expected total episode count')
    parser.add_argument('--expected-title', default='', help='Expected drama title; defaults to the folder name')
    parser.add_argument('--renumber-from', type=int, default=None, help='Generate a dry-run renumber plan starting at this episode number')
    parser.add_argument('--order-by', choices=['mtime', 'name'], default='mtime', help='Video ordering source for --renumber-from')
    parser.add_argument('--output', default='', help='Optional JSON report output path')
    args = parser.parse_args()

    report = analyze_drama_directory(
        args.path,
        expected_total=args.expected_total,
        expected_title=args.expected_title,
        renumber_from=args.renumber_from,
        order_by=args.order_by,
    )
    output = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(output, encoding='utf-8')
    print(output)


if __name__ == '__main__':
    main()
