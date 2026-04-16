# Hongguo Player State Machine Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Rebuild `scripts/download_drama.py` around a player-first state machine so Hongguo short-drama downloads use verified real episode numbers, user-supplied total episode counts when available, and robust stop/recovery rules without depending on title-specific hardcoding.

**Architecture:** The downloader should become a task-oriented runtime that locks a single target drama, a start episode, and an expected total episode count for the whole job. Initial search is only for first entry or last-resort recovery; the steady-state loop must stay in the player and advance by swipe whenever possible. Episode acceptance, skip, stop, and recovery decisions must all use verified runtime state: target title match, confirmed episode number, per-round `video_id`, captured URL/key presence, and user/UI total-episode signals.

**Tech Stack:** Python, Frida, ADB, `unittest`, `loguru`

---

## Scope

This plan covers these behavior changes:

1. Introduce an explicit task-level runtime state without duplicating existing validation state.
2. Determine the start episode dynamically from local files when resume mode is enabled and the target output directory is already known.
3. Treat player swipe as the main path for advancing to the next episode.
4. Demote full-drama search to the final recovery path.
5. Prefer a user-supplied total episode count from a new explicit CLI parameter and use UI-derived totals only as fallback or consistency checks.
6. Skip already downloaded episodes via a hybrid strategy that avoids turning resume into a full-device recapture pass.
7. Refuse to write files when the episode number is not confirmed.
8. Preserve the existing `duplicate_video_id` pre-cache carve-out explicitly instead of silently deleting it.

This plan does **not** include:

1. Reworking the AES-key-to-`video_id` binding problem.
2. Reworking the decryption algorithm itself.
3. Media quality heuristics beyond current selection rules.

---

## Runtime Model

Do **not** create an independent top-level state object that overlaps with `SessionValidationState` without clarifying ownership. The implementation should use one of these two approaches:

1. Extend `SessionValidationState` directly with task-level fields, or
2. Wrap `SessionValidationState` inside a task-level object and treat it as the single source of truth for title and last-episode validation.

Recommended shape:

1. `target_title`
2. `start_episode`
3. `user_total_episodes`
4. `locked_total_episodes`
5. `current_episode`
6. `last_confirmed_episode`
7. `consecutive_end_signals`
8. `consecutive_recovery_failures`
9. `first_missing_episode`
10. `validation_state`

Ownership rules:

1. `validation_state.locked_title` remains the source of truth for title lock.
2. `validation_state.last_episode` remains the source of truth for monotonic episode confirmation.
3. `locked_total_episodes` exists only at the task layer.
4. `user_total_episodes` is immutable after argument parsing.

Behavior rules:

1. `target_title` always comes from user input for `--search` flows.
2. `user_total_episodes` prefers user input when supplied.
3. `locked_total_episodes` is filled from UI only when `user_total_episodes` is absent.
4. If user input exists and UI later reports a different total, log a consistency warning but do not overwrite the user value.

---

## Start Episode

Rules for choosing the episode to download first:

1. If the user passes `--episode`, use it as the requested start episode.
2. Do not silently overload the current `--episode` default. Add an explicit `--resume` flag before enabling local pre-scan behavior.
3. Only run the first-missing-episode pre-scan when the target output directory is already known at startup.
4. If resume mode is enabled and files `1..k` exist with `k+1` missing, start from `k+1`.
5. If resume mode is enabled and files contain holes, pick the first hole.
6. If resume mode is disabled or the output directory is not yet knowable, start from the explicit `--episode` value or default `1`.

Compatibility note:

1. The existing meaning of `--episode` must remain “explicit start override.”
2. Resume behavior should be opt-in through `--resume`, not inferred from omission of `--episode`.
3. When the user does not supply `--name`, pre-scan cannot run before the first round because `output_dir` is unresolved until runtime identifies the drama title.

---

## Initial Entry

The initial search/navigation phase should happen only once per task launch unless recovery escalates all the way back to search.

Expected sequence:

1. Search by drama title.
2. Enter the target drama detail page.
3. Navigate to the chosen start episode.
4. Ensure the app transitions into the player.
5. Confirm the current round before download.

The round is valid only if all of the following are true:

1. UI or Hook confirms the title matches `target_title`.
2. UI or Hook confirms the episode number equals the target start episode.
3. A new `video_id` has been observed.
4. A playable URL for that `video_id` exists.
5. A key exists for the active round.

If episode number cannot be confirmed, reject the round and do not write files.

---

## Main Loop And Stop Logic

The main loop and stop semantics must be designed together. Do **not** implement them as separate phases because stop decisions (`break` / `continue` / `recover`) are emitted on every round.

Primary path for episode `N+1`:

1. Confirm we are currently on episode `N`.
2. Swipe up inside the player.
3. Wait for a fresh round.
4. Accept only if:
   - title still matches target title
   - episode is exactly `N+1`
   - `video_id` is new for the round
   - URL/key are present for the round
5. If the round is confirmed and a valid local file for episode `N+1` already exists, skip download and record the round as confirmed.
6. Otherwise download and decrypt the episode.

Reject and do not write files when any of the following is true:

1. title mismatch
2. episode missing
3. episode is not `N+1`
4. missing playable URL
5. missing key

Special-case handling:

1. `duplicate_video_id` must **not** be silently removed from the plan.
2. Preserve the current pre-cache carve-out explicitly:
   - if the app re-emits a previously seen `video_id` during a confirmed advance and all other signals still indicate the next episode, treat it as a cache artifact and continue with bounded tolerance
   - if the duplicate appears without supporting UI/episode confirmation, reject the round

Stop conditions should follow this priority:

1. If `user_total_episodes` is available and the current confirmed episode is `>= user_total_episodes`, stop immediately.
2. If `user_total_episodes` is absent but `locked_total_episodes` is available, stop once the current confirmed episode reaches it.
3. If neither total is available, stop only after strong end-of-series signals:
   - swipe leads to a different title
   - episode is not continuous (`!= N+1`)
   - recovery cannot bring us back to `N+1`
   - this failure pattern happens in two consecutive rounds

Do not stop just because the app surfaces recommendations from another drama once.

Additional guardrail:

1. The “two consecutive strong end signals” rule only applies after the recovery chain has explicitly failed to reach `N+1`.
2. A single foreign-title swipe must not terminate the job on its own.
3. If the player episode panel can positively show that episode `N+1` does not exist, that signal may short-circuit the second-round requirement.

---

## Recovery Order

Recovery must be ordered and bounded.

For a failed swipe transition:

1. Short wait and poll once more for delayed round data.
2. Wake player controls and re-read UI.
3. Open player episode panel and explicitly jump to `N+1`.
4. Reconfirm title, episode, `video_id`, URL, and key.
5. Only if all of the above fail, re-run the one-time search flow to relocate the drama and episode.

Search is the final fallback, not the default route.

Implementation note:

1. Recovery step 3 is new UI automation work, not a refactor of existing detail-page selection code.
2. The plan must account for this as new behavior with dedicated tests.

---

## File Semantics

File-skip behavior must not turn resume into a full-device recapture pass.

Use a hybrid strategy:

1. Fast local pre-scan determines the first missing episode before runtime starts.
2. During the active loop, already-downloaded files may be skipped only when the current round is confirmed to be episode `N`.
3. Do **not** force the runtime to swipe through every already-downloaded episode purely to re-confirm historical files.
4. Unknown-episode rounds must never create or rename `episode_XXX` files.
5. The existing “first episode already exists” shortcut must be folded into the same skip policy, not left as a special undocumented third path.

Metadata for a skipped existing file must still record:

1. confirmed episode
2. title
3. `video_id`
4. selected URL
5. capture timestamp
6. skip status

---

## Implementation Tasks

### Task 1: CLI Semantics And State Boundary

**Files:**
- Modify: `scripts/download_drama.py`
- Modify: `scripts/drama_download_common.py`
- Test: `tests/test_download_drama.py`

Decide and implement the boundary conditions first:

1. clarify whether task state wraps or extends `SessionValidationState`
2. add an explicit `--total-episodes` CLI parameter
3. add an explicit `--resume` CLI parameter
4. add user-total vs. locked-total handling

Expected tests:

1. user-supplied total is stored and not overwritten by UI total
2. `--episode` keeps its existing override semantics
3. `--resume` is required to activate first-missing-episode scanning

### Task 2: First Missing Episode Selection

**Files:**
- Modify: `scripts/download_drama.py`
- Test: `tests/test_download_drama.py`

Implement first-missing-episode scanning only for cases where the target output directory is knowable at startup.

Expected tests:

1. no files -> returns `1`
2. `1,2,3` exist -> returns `4`
3. `1,2,4` exist -> returns `3`
4. unnamed/manual flow does not attempt pre-scan before `output_dir` is resolved

### Task 2: Round Confirmation Contract

**Files:**
- Modify: `scripts/download_drama.py`
- Possibly modify: `scripts/drama_download_common.py`
- Test: `tests/test_download_drama.py`

Centralize round acceptance so every write/skip path depends on the same confirmation contract:

1. target title match
2. confirmed episode
3. new `video_id`
4. playable URL
5. key presence
6. explicit duplicate-`video_id` carve-out

### Task 4: Player-First Loop With Stop Semantics

**Files:**
- Modify: `scripts/download_drama.py`
- Test: `tests/test_download_drama.py`

Implement the main loop and stop semantics together:

1. swipe first
2. player episode-panel recovery second
3. search relocation last
4. user total beats UI total
5. two consecutive strong end signals required only after the full recovery chain fails when no total is available

### Task 5: Skip Semantics And Resume Efficiency

**Files:**
- Modify: `scripts/download_drama.py`
- Test: `tests/test_download_drama.py`

Implement the hybrid skip strategy:

1. fast local pre-scan at task start
2. round-confirmed skip during active runtime
3. no forced recapture for every already-downloaded episode
4. unify the current first-episode skip path with the same skip policy

### Task 6: Regression Verification

**Files:**
- Modify: `tests/test_download_drama.py`

Add tests for:

1. user total beats UI total
2. UI total fills in when user total is missing
3. skip existing only after confirmed episode during active rounds
4. no file write when episode is missing
5. stop when confirmed episode reaches expected total
6. no stop after a single transient foreign-title swipe
7. duplicate-`video_id` cache tolerance vs. true duplicate rejection
8. recovery escalation order: swipe -> player panel -> search
9. consecutive recovery failure counter resets after a successful round
10. first-episode skip path follows the unified skip policy

---

## Verification Commands

Run after implementation:

1. `py -m unittest tests.test_download_drama -v`
2. `py -m py_compile scripts\download_drama.py scripts\drama_download_common.py`
3. Real-device smoke:
   - search to target drama
   - confirm first missing episode selection
   - verify player swipe advances to `N+1`
   - verify player-panel recovery works when swipe stalls
   - verify stop when confirmed episode reaches the expected total
   - verify no forced full-device recapture for already downloaded leading episodes

---

## Open Risks

1. AES key binding is still a known correctness risk and should be handled in a follow-up change.
2. `uiautomator dump` instability in video playback may still require defensive retries around UI reads.
3. Some Hongguo entry flows may land on detail-page variants that need extra player-entry heuristics.
4. Frida process enumeration may fail to expose the app main process consistently; the ADB PID fallback must be preserved.
5. If the team decides against changing default `--episode` semantics, `--resume` must be introduced before implementation begins.
