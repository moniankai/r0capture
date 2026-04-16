# download_drama Batch Debug Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Stabilize `python scripts/download_drama.py -n "<剧名>" --search -b` for continuous full-series downloads and prove the fix with local tests plus live-device verification.

**Architecture:** Treat this as a debugging task first, not a feature sprint. Reproduce the failure on the connected rooted device, capture evidence at the UI navigation, Frida capture, and batch state-transition boundaries, then add targeted regression tests and minimal fixes around stale capture state, batch navigation, and retry/validation behavior.

**Tech Stack:** Python, ADB, Frida, `unittest`, Loguru

---

### Task 1: Reproduce And Gather Evidence

**Files:**
- Modify: `scripts/download_drama.py`
- Reference: `README.md`

**Step 1: Add temporary or persistent debug logs only at batch/search boundaries**

Log at:
- entering `search_drama_in_app`
- after UI title/episode detection
- before and after `wait_for_capture`
- before `download_and_decrypt`
- when batch mode retries or exits

**Step 2: Run the failing flow on the device**

Run: `python scripts/download_drama.py -n "十八岁太奶奶驾到，重整家族荣耀" --search -b`
Expected: reproduce the intermittent failure and collect exact failure point.

**Step 3: Compare live evidence against the intended state machine**

Confirm which layer fails first:
- app navigation did not land on target episode
- Frida delivered stale data
- validation rejected valid data
- batch loop treated stale/duplicate data as success or fatal failure

### Task 2: Write Regression Tests For The Confirmed Failure

**Files:**
- Modify: `tests/test_download_drama.py`
- Modify: `scripts/download_drama.py`

**Step 1: Isolate the failing behavior into a helper or existing function seam**

Target the smallest unit that can express the bug without requiring a real device.

**Step 2: Write the failing test**

Test one concrete scenario from the reproduction evidence, for example:
- stale episode capture after batch navigation
- duplicate `video_id` recovery path
- title mismatch retry loop that should continue instead of aborting
- navigation retry behavior when first search attempt fails

**Step 3: Run the targeted test and confirm it fails**

Run: `py -m unittest tests.test_download_drama -v`
Expected: at least one new test fails before the fix.

### Task 3: Implement Minimal Root-Cause Fix

**Files:**
- Modify: `scripts/download_drama.py`
- Possibly modify: `scripts/drama_download_common.py`

**Step 1: Fix only the confirmed root cause**

Possible areas, depending on evidence:
- stale state clearing before/after navigation
- stricter freshness checks for captured round/video id
- bounded retry around navigation plus capture wait
- better duplicate or title-drift recovery in batch mode

**Step 2: Preserve existing behavior outside the broken path**

Do not refactor unrelated downloader logic while debugging.

**Step 3: Re-run the targeted test**

Run: `py -m unittest tests.test_download_drama -v`
Expected: new regression test passes.

### Task 4: Verify End To End

**Files:**
- Modify: `tasks/todo.md`

**Step 1: Run automated verification**

Run:
- `py -m unittest tests.test_download_drama tests.test_audit_drama_downloads -v`
- `py -m compileall scripts\download_drama.py scripts\drama_download_common.py tests`

Expected: all targeted checks pass.

**Step 2: Re-run the live-device flow**

Run: `python scripts/download_drama.py -n "十八岁太奶奶驾到，重整家族荣耀" --search -b`
Expected: batch mode advances through multiple episodes without the reproduced failure.

**Step 3: Summarize evidence and residual risks**

Record:
- exact root cause
- code fix
- verification commands and observed result
- any remaining instability that still needs longer soak testing
