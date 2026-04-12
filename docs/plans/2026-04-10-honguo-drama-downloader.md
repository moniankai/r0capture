# HongGuo Drama Downloader Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Refactor the HongGuo downloader so it validates the on-device drama context before downloading, names each episode file with the true episode number plus `video_id`, and adds an offline audit tool for existing downloads.

**Architecture:** Keep the Frida capture path, but wrap each play/swipe cycle in a validated session round built from both UI context and captured media metadata. Add a separate audit script that scans saved metadata and filenames to detect and optionally normalize mismatches.

**Tech Stack:** Python 3, adb, Frida, requests, loguru, built-in `unittest`

---

### Task 1: Build the failing tests for UI parsing and filename generation

**Files:**
- Create: `tests/test_download_drama.py`
- Modify: `scripts/download_drama.py`

**Step 1: Write the failing test**
- Add tests for parsing title, current episode, and total episodes from a minimal UI XML snippet.
- Add tests for generating `episode_{episode:03d}_{videoid8}.mp4` and corresponding metadata filename.

**Step 2: Run test to verify it fails**
- Run: `python -m unittest tests.test_download_drama -v`
- Expected: FAIL because parsing and filename helpers do not exist yet.

**Step 3: Write minimal implementation**
- Extract pure helper functions for UI parsing, title sanitization, and deterministic filename generation.

**Step 4: Run test to verify it passes**
- Run: `python -m unittest tests.test_download_drama -v`
- Expected: PASS.

### Task 2: Build the failing tests for session validation rules

**Files:**
- Modify: `tests/test_download_drama.py`
- Modify: `scripts/download_drama.py`

**Step 1: Write the failing test**
- Add tests that reject title drift, duplicate `video_id`s, and non-monotonic episode progress.

**Step 2: Run test to verify it fails**
- Run: `python -m unittest tests.test_download_drama -v`
- Expected: FAIL because session validation behavior does not exist yet.

**Step 3: Write minimal implementation**
- Introduce session/round helper structures and validation functions.
- Update the downloader flow to lock the title on first accepted round and to stop on validation drift.

**Step 4: Run test to verify it passes**
- Run: `python -m unittest tests.test_download_drama -v`
- Expected: PASS.

### Task 3: Implement validated filenames and manifest persistence in the downloader

**Files:**
- Modify: `scripts/download_drama.py`

**Step 1: Write the failing test**
- Extend tests to verify metadata and video output names include the `video_id` suffix and the metadata payload records UI and capture context.

**Step 2: Run test to verify it fails**
- Run: `python -m unittest tests.test_download_drama -v`
- Expected: FAIL because current persistence payload is incomplete.

**Step 3: Write minimal implementation**
- Change saved video/meta filenames.
- Persist a session manifest and richer per-episode metadata.
- Use explicit UTF-8 for metadata writes.

**Step 4: Run test to verify it passes**
- Run: `python -m unittest tests.test_download_drama -v`
- Expected: PASS.

### Task 4: Build the failing tests for the offline audit tool

**Files:**
- Create: `tests/test_audit_drama_downloads.py`
- Create: `scripts/audit_drama_downloads.py`

**Step 1: Write the failing test**
- Add temp-directory tests that create sample metadata/video files and assert the audit report flags missing episodes, drama mismatches, duplicates, and rename targets.

**Step 2: Run test to verify it fails**
- Run: `python -m unittest tests.test_audit_drama_downloads -v`
- Expected: FAIL because the audit tool does not exist yet.

**Step 3: Write minimal implementation**
- Implement metadata loading, report generation, and deterministic rename target creation.

**Step 4: Run test to verify it passes**
- Run: `python -m unittest tests.test_audit_drama_downloads -v`
- Expected: PASS.

### Task 5: Run integrated verification

**Files:**
- Modify: `tasks/todo.md`

**Step 1: Run targeted test suite**
- Run: `python -m unittest tests.test_download_drama tests.test_audit_drama_downloads -v`
- Expected: PASS.

**Step 2: Run a lightweight static smoke check**
- Run: `python -m compileall scripts\download_drama.py scripts\audit_drama_downloads.py tests`
- Expected: PASS.

**Step 3: Update progress tracking**
- Mark all stages complete in `tasks/todo.md`.