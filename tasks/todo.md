## Stage 1: Design And Plan
**Goal**: Record the approved B + C design and implementation plan.
**Success Criteria**: Design doc and implementation plan exist under `docs/plans/`.
**Tests**: N/A
**Status**: Complete

## Stage 2: Downloader Validation Refactor
**Goal**: Make `download_drama.py` use validated UI context, stable naming, and session manifests.
**Success Criteria**: Downloader helpers are test-covered and filenames include `video_id`.
**Tests**: `python -m unittest tests.test_download_drama -v`
**Status**: Complete

## Stage 3: Offline Audit Tool
**Goal**: Add an audit script that reports coverage, mismatches, duplicates, and rename targets.
**Success Criteria**: Audit script passes its dedicated tests and produces deterministic reports.
**Tests**: `python -m unittest tests.test_audit_drama_downloads -v`
**Status**: Complete

## Stage 4: Verification
**Goal**: Run the targeted automated checks for both features.
**Success Criteria**: Unit tests and compile checks pass.
**Tests**: `python -m unittest tests.test_download_drama tests.test_audit_drama_downloads -v`; `python -m compileall scripts\download_drama.py scripts\audit_drama_downloads.py tests`
**Status**: Complete

## Stage 5: Chinese Comment Pass
**Goal**: Translate project-owned code comments and explanatory docstrings to Chinese by default.
**Success Criteria**: `scripts/` and `frida_hooks/` comments are Chinese unless they are legal headers, external references, type-ignore pragmas, URLs, or string/protocol examples.
**Tests**: targeted comment scan; Python compile checks for modified Python files.
**Status**: Complete
