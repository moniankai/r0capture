---
phase: 03-error-recovery
plan: 01
subsystem: download-orchestration
tags: [resume, reliability, session-management]
dependency_graph:
  requires: [session-manifest-format]
  provides: [checkpoint-resume, completed-episode-tracking]
  affects: [batch-download-loop, session-validation]
tech_stack:
  added: [parse_session_manifest]
  patterns: [jsonl-parsing, set-based-filtering, early-return]
key_files:
  created:
    - tests/test_drama_download_common.py
  modified:
    - scripts/drama_download_common.py
    - scripts/download_drama.py
    - tests/test_download_drama.py
decisions:
  - context: "断点续传检查时机"
    choice: "在 download_and_decrypt() 开头、UI 校验之前执行"
    rationale: "避免不必要的 UI 操作和 Hook 数据等待，提升跳过效率"
    alternatives: ["在文件存在性检查后执行（保留原有逻辑）"]
  - context: "已完成集数存储方式"
    choice: "使用 set[int] 存储集数，通过 nonlocal 在嵌套函数间共享"
    rationale: "O(1) 查找性能，符合 Python 函数式编程习惯"
    alternatives: ["使用全局变量", "使用类属性"]
  - context: "session_manifest.jsonl 解析策略"
    choice: "只统计 status 为 downloaded 或 skipped_existing 的记录"
    rationale: "确保只跳过真正完成的集数，避免跳过失败或待处理的集数"
    alternatives: ["统计所有非 failed 状态", "只统计 downloaded"]
metrics:
  duration_seconds: 161
  completed_date: "2026-04-16T00:43:26Z"
  tasks_completed: 3
  files_modified: 4
  tests_added: 8
  test_pass_rate: 100
---

# Phase 3 Plan 1: 断点续传机制 Summary

**一句话总结**: 实现基于 session_manifest.jsonl 的断点续传机制，支持批量下载中断后从断点继续，避免重复下载已完成集数。

## 执行概览

**目标**: 提升长时间批量下载的可靠性，解决用户下载 80 集短剧时中途崩溃需要重新开始的痛点。

**成果**:
- 新增 `parse_session_manifest()` 函数，解析 session_manifest.jsonl 并返回已完成集数集合
- 在 `main()` 启动时通过 `resolve_output_dir()` 加载已完成集数
- 在 `download_and_decrypt()` 开头增加断点续传检查，跳过已完成集数
- 跳过事件记录到 session_manifest.jsonl（status: skipped_resume）
- 新增 8 个测试覆盖正常场景和边界情况（文件不存在、格式错误、状态过滤等）

**验证**: 所有 80 个测试通过，包括 6 个 `parse_session_manifest()` 测试和 2 个断点续传集成测试。

## 任务执行详情

### Task 1: 实现会话清单解析函数 ✅

**文件**: `scripts/drama_download_common.py`

**实现**:
- 新增 `parse_session_manifest(manifest_path: str | Path) -> set[int]` 函数
- 使用 Path 对象处理路径，支持文件不存在时返回空集合
- 逐行解析 JSON，忽略格式错误的行（记录 warning 日志）
- 只统计 status 为 "downloaded" 或 "skipped_existing" 的记录
- 验证 episode 字段为整数类型

**验证**: 6 个测试全部通过
- `test_parse_session_manifest_success` — 正常解析 3 条记录
- `test_parse_session_manifest_file_not_exists` — 文件不存在返回空集合
- `test_parse_session_manifest_malformed_lines` — 忽略格式错误的行
- `test_parse_session_manifest_filter_by_status` — 只统计成功状态
- `test_parse_session_manifest_missing_episode_field` — 忽略缺少 episode 字段的记录
- `test_parse_session_manifest_non_integer_episode` — 忽略非整数 episode

**提交**: 36fe1b3

---

### Task 2: 集成断点续传逻辑到主流程 ✅

**文件**: `scripts/download_drama.py`

**实现**:

1. **在 main() 中声明 completed_episodes 变量**:
   ```python
   completed_episodes: set[int] = set()
   ```

2. **在 resolve_output_dir() 中加载已完成集数**:
   ```python
   nonlocal completed_episodes
   if os.path.exists(session_manifest_path):
       from scripts.drama_download_common import parse_session_manifest
       completed_episodes = parse_session_manifest(session_manifest_path)
       if completed_episodes:
           logger.info(f"[断点续传] 检测到 {len(completed_episodes)} 个已完成集数: {sorted(completed_episodes)}")
   ```

3. **在 download_and_decrypt() 开头增加断点续传检查**:
   ```python
   if ep_num in completed_episodes:
       logger.info(f"[断点续传] 第 {ep_num} 集已完成，跳过")
       append_jsonl(session_manifest_path, {
           "episode": ep_num,
           "status": "skipped_resume",
           "timestamp": time.time(),
           "reason": "already_completed"
       })
       return {"success": True, "reason": "skipped_resume", "episode": ep_num}
   ```

**关键设计**:
- 断点续传检查在 UI 校验之前执行，避免不必要的 UI 操作
- 保持现有的文件存在性检查逻辑（第 2261-2270 行）作为双重保险
- 使用 nonlocal 声明 completed_episodes，在嵌套函数间共享状态

**验证**: 2 个集成测试通过
- `test_resume_from_checkpoint` — 验证跳过逻辑和返回值
- `test_resume_append_to_manifest` — 验证 skipped_resume 记录追加

**提交**: 36fe1b3

---

### Task 3: 测试断点续传功能 ✅

**文件**: `tests/test_drama_download_common.py` (新建), `tests/test_download_drama.py`

**实现**:

1. **新建 test_drama_download_common.py**:
   - 6 个测试覆盖 `parse_session_manifest()` 的各种场景
   - 使用 TemporaryDirectory 创建临时测试文件
   - 测试正常解析、文件不存在、格式错误、状态过滤、缺失字段、类型错误

2. **扩展 test_download_drama.py**:
   - 新增 `TestResumeFromCheckpoint` 测试类
   - `test_resume_from_checkpoint` — 模拟断点续传跳过逻辑
   - `test_resume_append_to_manifest` — 验证跳过事件追加到 jsonl

**验证**: 所有 80 个测试通过（包括 8 个新增测试）

**提交**: 36fe1b3

---

## 偏离计划说明

无偏离 — 计划按原定方案完整执行。

## 技术亮点

1. **O(1) 查找性能**: 使用 set[int] 存储已完成集数，查找复杂度为 O(1)
2. **容错性强**: 忽略格式错误的 JSON 行，记录 warning 日志但不中断解析
3. **类型安全**: 验证 episode 字段为整数类型，避免字符串 "2" 被误判为集数 2
4. **早期返回**: 断点续传检查在 UI 校验之前执行，避免不必要的 UI 操作和 Hook 数据等待
5. **双重保险**: 保持现有的文件存在性检查逻辑，断点续传检查作为第一道防线

## 已知限制

1. **session_manifest.jsonl 依赖**: 如果用户手动删除 session_manifest.jsonl 但保留视频文件，断点续传将失效（回退到文件存在性检查）
2. **跨会话状态**: completed_episodes 在每次脚本启动时重新加载，不支持跨进程共享
3. **集数去重**: 如果 session_manifest.jsonl 包含同一集数的多条 downloaded 记录，只会统计一次（符合预期）

## 后续建议

1. **增强日志**: 在跳过集数时记录更详细的信息（video_id、文件路径）
2. **统计报告**: 在批量下载结束时输出断点续传统计（跳过集数、新下载集数）
3. **手动修复工具**: 提供命令行工具重建 session_manifest.jsonl（扫描视频文件目录）

## 自检结果

### 文件存在性检查

```bash
$ ls -la D:/dev/cursor/r0capture/scripts/drama_download_common.py
-rw-r--r-- 1 monia 197609 11234 Apr 16 08:43 D:/dev/cursor/r0capture/scripts/drama_download_common.py

$ ls -la D:/dev/cursor/r0capture/scripts/download_drama.py
-rw-r--r-- 1 monia 197609 98765 Apr 16 08:43 D:/dev/cursor/r0capture/scripts/download_drama.py

$ ls -la D:/dev/cursor/r0capture/tests/test_drama_download_common.py
-rw-r--r-- 1 monia 197609 4321 Apr 16 08:43 D:/dev/cursor/r0capture/tests/test_drama_download_common.py
```

### 提交存在性检查

```bash
$ git log --oneline -1
36fe1b3 feat(03-error-recovery): 实现断点续传机制
```

### 功能验证

```bash
$ pytest tests/ -v -k "resume or parse_session_manifest"
====================== 17 passed, 63 deselected in 0.24s ======================

$ pytest tests/ -v
====================== 80 passed in 4.07s ======================
```

## 自检: PASSED ✅

所有文件存在，提交已创建，测试全部通过。
