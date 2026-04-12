# HongGuo Drama Downloader Design

**Goal:** Make `scripts/download_drama.py` reliably download the currently playing HongGuo short drama into a correctly named folder, name each episode file with both episode number and `video_id`, and produce enough audit metadata to catch cross-drama and ordering mistakes early.

## Problem Statement

The current downloader trusts CLI episode numbering and a weak assumption that the first captured `video_ref` and AES key correspond to the currently playing episode. In practice this breaks when the app lands on a different drama, preloads neighboring videos, or the batch swipe flow loses alignment. The result is mislabeled folders, missing early episodes, and filenames that cannot be safely deduplicated after the fact.

## Design Summary

Adopt a two-layer design:

1. **Validated capture session in `download_drama.py`**
   - Read the device UI as the source of truth for current drama title, current episode number, and total episode count.
   - Capture Frida events into an explicit round/session structure instead of only storing flat arrays.
   - Gate each download on validation rules: title must match the target session title, the UI episode must be known, and the captured `video_id` must not already exist in the session manifest.
   - Persist a session manifest containing UI context, chosen `video_id`, key, quality, and output filenames.

2. **Offline audit tool**
   - Scan a drama directory and all `meta_ep*.json` files.
   - Report missing episodes, duplicate `video_id`s, mismatched drama names, filename drift, and suspicious ordering.
   - Optionally propose or apply safe filename normalization to `episode_{episode:03d}_{videoid8}.mp4`.

## Data Model

### UI Context

A parsed UI snapshot should include:
- `title`
- `episode`
- `total_episodes`
- `raw_texts`

### Capture Round

Each swipe/play cycle should produce a structured record:
- `round_index`
- `ui_title`
- `ui_episode`
- `ui_total_episodes`
- `captured_video_refs`
- `captured_video_urls`
- `captured_aes_keys`
- `selected_video_id`
- `selected_quality`
- `selected_codec`
- `output_video_path`
- `output_meta_path`
- `timestamp`

### File Naming

Folder name:
- Use the runtime-detected drama title after sanitization.

Video name:
- `episode_{episode:03d}_{video_id[:8]}.mp4`

Meta name:
- `meta_ep{episode:03d}_{video_id[:8]}.json`

This makes same-number collisions across dramas or retries auditable and prevents silent overwrites.

## Validation Rules

Before accepting a round for download:
- The UI title must be present.
- The session title is locked on the first accepted round.
- Later rounds must keep the same title, otherwise stop and report title drift.
- The UI episode number must be parseable.
- The chosen `video_id` must not have been downloaded earlier in the same session manifest.
- In batch mode the UI episode should advance monotonically; if not, stop and report drift.

## Recommended Selection Strategy

Recommended approach:
- Keep using the current `video_ref` + `video_info` capture path, but store refs and URLs per round.
- Use the first `video_ref` from the current validated round as the active candidate.
- Keep the best-quality selection, but bind it to the chosen round `video_id`.
- Record all captured alternatives in the manifest for post-run debugging.

Why this approach:
- It is the smallest change from the existing hook logic.
- It keeps the active playback selection explicit instead of hidden in flat mutable arrays.
- It produces auditable artifacts when future misalignment happens.

## Audit Tool Scope

The offline audit command should:
- Read metadata with explicit UTF-8.
- Build an episode map and a `video_id` map.
- Emit a JSON report and a human-readable summary.
- Flag:
  - missing episode numbers
  - duplicate `video_id`s
  - drama name mismatches
  - filename pattern mismatches
  - metadata file missing for a video file
  - video file missing for a metadata file

Optional fix mode:
- Rename files only when metadata is present and the target name is deterministic.
- Never overwrite an existing file.
- Emit a dry-run preview first.

## Testing Strategy

Use built-in Python `unittest`.

Critical test coverage:
- UI XML parsing extracts title, current episode, and total count.
- Filename builder includes episode and `video_id` suffix.
- Session validation rejects title drift and duplicate `video_id`s.
- Audit tool reports missing episodes and drama mismatches.
- Audit tool generates deterministic rename targets.

## Files To Modify

Primary implementation:
- `scripts/download_drama.py`

New helper or audit code:
- `scripts/audit_drama_downloads.py`

Tests:
- `tests/test_download_drama.py`
- `tests/test_audit_drama_downloads.py`

Project tracking:
- `tasks/todo.md`

## Risks

- UI XML text and resource IDs may vary across app versions.
- Some rounds may still capture prefetch traffic; the session manifest is intended to make these cases observable instead of silent.
- Historical folders already polluted by wrong downloads cannot be auto-corrected without trusted metadata.

## Non-Goals

- Fully remote-control the phone UI with a new automation framework in this change.
- Retrospectively infer the correct drama for already wrong folders without metadata evidence.