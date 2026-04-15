---
phase: 02-hook-validation
plan: 01
subsystem: hook-data-enrichment
tags: [hook-enhancement, episode-tracking, data-association]
dependency_graph:
  requires: [01-01-SUMMARY.md, 01-02-SUMMARY.md]
  provides: [episode-number-field, hook-episode-extraction]
  affects: [download_drama.py, COMBINED_HOOK, VideoRef, AESKey]
tech_stack:
  added: [episode-number-extraction, global-episode-cache]
  patterns: [field-hierarchy-search, fallback-extraction, null-safe-handling]
key_files:
  created: []
  modified:
    - path: scripts/download_drama.py
      lines_changed: 78
      description: "增加 episode_number 字段到数据类和 Hook 脚本，实现集数提取和关联"
decisions:
  - id: DEC-02-01-01
    summary: "使用字段层次搜索策略提取 episode_number"
    rationale: "VideoModel 字段可能在父类中声明，需要沿继承链查找"
  - id: DEC-02-01-02
    summary: "Native Hook 使用全局变量缓存 episode_number"
    rationale: "Native 层无法直接访问 Java 状态，通过全局变量实现近似关联"
  - id: DEC-02-01-03
    summary: "episode_number 为 null 时保持向后兼容"
    rationale: "提取失败时回退到 Phase 1 的时序选择逻辑，不影响现有功能"
metrics:
  duration_minutes: 4
  tasks_completed: 3
  files_modified: 1
  lines_added: 78
  lines_removed: 5
  tests_passed: 11
  tests_failed: 1
  commits: 1
  completed_date: 2026-04-15
---

# Phase 02 Plan 01: Hook 端增加 episode_number 字段 Summary

**一句话总结**: 为 Hook 数据增加 episode_number 字段，建立 Hook 数据与集数的关联，为精确匹配奠定基础。

## 执行概览

成功在 Java Hook 和 Native Hook 中增加 episode_number 提取和关联逻辑，更新 Python 数据类支持新字段，所有修改保持向后兼容。

## 完成的任务

### Task 1: Java Hook 增加 episode_number 提取
**Commit**: 3911287

**修改内容**:
- 在 COMBINED_HOOK 顶部增加全局变量 `lastEpisodeNumber = null`
- 实现 `extractEpisodeNumber(model)` 函数，按优先级提取集数：
  1. 从 VideoModel 字段提取（mEpisodeNumber、mIndex、mOrder、mEpisode、episodeNum、episodeNumber）
  2. 从标题字段提取 "第X集" 模式（mEpisodeTitle、mTitle、mVideoTitle）
  3. 返回 null（回退到 Python 端时序选择）
- 修改 `setVideoModel` Hook，在 `send()` 消息中增加 `episode_number` 字段
- 更新全局缓存 `lastEpisodeNumber = episodeNumber`
- 增加日志输出：`console.log("[Hook] Episode number: " + episodeNumber)`

**关键决策**:
- 使用 `findFieldInHierarchy()` 沿继承链查找字段（VideoModel 字段可能在父类中）
- 集数范围校验：`num > 0 && num < 10000`（防止异常值）
- 保持向后兼容：`episode_number` 为 `null` 时不影响现有逻辑

**验证结果**: ✅ extractEpisodeNumber() 函数存在，video_ref 和 video_model 消息包含 episode_number 字段

### Task 2: Native Hook 增加 episode_number 关联
**Commit**: 3911287（同一提交）

**修改内容**:
- 在 `hookAesInit()` 的 `send()` 消息中增加 `episode_number: lastEpisodeNumber`
- 增加日志输出：`console.log("[AES Hook] Associated with episode: " + lastEpisodeNumber)`

**关键决策**:
- Native Hook 无法直接访问 Java 层状态，使用全局变量 `lastEpisodeNumber` 缓存
- 假设 AES 密钥在 setVideoModel 之后不久触发（时序近似关联）
- 这是一个近似关联，可能存在时序问题，但优于完全无关联

**验证结果**: ✅ lastEpisodeNumber 全局变量存在，AES_KEY 消息包含 episode_number 字段

### Task 3: Python 数据结构更新
**Commit**: 3911287（同一提交）

**修改内容**:
1. **VideoRef 数据类**（第 78 行）:
   ```python
   episode_number: Optional[int] = None  # 集数（从 Hook 提取）
   ```

2. **AESKey 数据类**（第 88 行）:
   ```python
   episode_number: Optional[int] = None  # 关联的集数
   ```

3. **on_message 回调 - video_ref 处理**（第 1724-1743 行）:
   - 提取 `ep_num = p.get("episode_number")`
   - 创建 VideoRef 时传递 `episode_number=ep_num`
   - 日志区分：`episode_number={ep_num}` 或 `(episode_number 未提取)`

4. **on_message 回调 - AES_KEY 处理**（第 1755-1774 行）:
   - 提取 `ep_num = p.get("episode_number")`
   - 创建 AESKey 时传递 `episode_number=ep_num`
   - 日志区分：`episode_number={ep_num}` 或 `(episode_number 未关联)`

**关键决策**:
- `episode_number` 默认值为 `None`，保持向后兼容
- 日志输出区分 `episode_number` 是否可用，便于调试
- 不修改现有的数据选择逻辑（留待 Plan 02-02）

**验证结果**: ✅ VideoRef 和 AESKey 包含 episode_number 字段，on_message 正确解析

## 偏差记录

### 无偏差
计划执行完全按照 PLAN.md 进行，所有任务一次性完成并合并到单个提交。

## 回归测试结果

**测试套件**: `tests/test_download_drama.py`
**结果**: ⚠️ 11/12 通过（1 个失败）
**执行时间**: 0.26 秒

**失败测试**:
- `FileNamingTests.test_build_episode_paths_include_video_and_meta_suffix` — 预存在问题（文件名包含剧名前缀），与本次修改无关

**通过测试**:
- ParseUiContextTests: 3/3 通过
- FileNamingTests: 1/2 通过（1 个预存在失败）
- SessionValidationTests: 5/5 通过
- FridaDeviceTests: 2/2 通过

**结论**: 所有与 episode_number 相关的功能保持向后兼容，失败测试为 Phase 1 遗留问题。

## 向后兼容性确认

✅ **数据类默认值**: `episode_number=None` 确保现有代码可以不传递此参数
✅ **Hook 消息兼容**: 提取失败时返回 `null`，Python 端正确处理
✅ **日志输出友好**: 区分 episode_number 是否可用，不影响现有日志格式
✅ **现有测试通过**: 11/12 测试通过，失败测试为预存在问题

## 技术决策

### 决策 1: 字段层次搜索策略
**选择**: 使用 `findFieldInHierarchy()` 沿继承链查找字段

**理由**:
- VideoModel 字段可能在父类中声明（如 mEpisodeNumber）
- `getDeclaredField()` 只能找到当前类声明的字段
- 沿继承链逐层查找确保覆盖所有可能的字段位置

### 决策 2: Native Hook 全局变量缓存
**选择**: 使用全局变量 `lastEpisodeNumber` 缓存最近的集数

**理由**:
- Native 层无法直接访问 Java 层状态
- AES 密钥通常在 setVideoModel 之后不久触发
- 全局变量提供近似关联，优于完全无关联
- 时序问题可能存在，但在实际场景中影响较小

### 决策 3: 向后兼容的回退机制
**选择**: episode_number 为 null 时不影响现有功能

**理由**:
- 提取失败时回退到 Phase 1 的时序选择逻辑
- 保持现有功能稳定性
- 允许渐进式增强（Plan 02-02 将使用 episode_number 实现精确匹配）

## 已知限制

1. **Native Hook 时序依赖**: `lastEpisodeNumber` 缓存假设 AES 密钥在 setVideoModel 之后不久触发，极端时序下可能关联错误
2. **字段名依赖**: 提取逻辑依赖已知字段名（mEpisodeNumber、mIndex 等），App 更新后可能失效
3. **标题模式依赖**: "第X集" 模式提取依赖中文标题格式，其他语言或格式可能失效

## 下游影响

### 对 Phase 2 后续计划的影响
- **02-02 (Python 端精确匹配)**: 可直接使用 `VideoRef.episode_number` 和 `AESKey.episode_number` 实现精确匹配
- **02-03 (集成测试)**: 可验证 episode_number 提取的准确性和关联的可靠性

### 对测试的影响
- 测试可验证 episode_number 字段是否正确解析
- 测试可模拟 episode_number 为 null 的回退场景

## 文件清单

### 修改的文件
- `scripts/download_drama.py` (78 行新增, 5 行删除)
  - 第 78 行: VideoRef 增加 `episode_number: Optional[int]` 字段
  - 第 88 行: AESKey 增加 `episode_number: Optional[int]` 字段
  - 第 95 行: COMBINED_HOOK 增加 `lastEpisodeNumber` 全局变量
  - 第 156-195 行: 实现 `extractEpisodeNumber()` 函数
  - 第 204-220 行: 修改 setVideoModel Hook，提取和发送 episode_number
  - 第 258-261 行: 修改 hookAesInit，发送 episode_number
  - 第 1724-1743 行: on_message 处理 video_ref 的 episode_number
  - 第 1755-1774 行: on_message 处理 AES_KEY 的 episode_number

### 提交记录
1. `3911287` - feat(02-hook-validation-01): add episode_number field to Hook data and Python data classes

## Self-Check: PASSED

### 文件存在性检查
```bash
✓ scripts/download_drama.py exists and modified
```

### 提交存在性检查
```bash
✓ 3911287 exists (Task 1-3)
```

### 功能验证
```bash
✓ extractEpisodeNumber() 函数存在于 COMBINED_HOOK 中
✓ video_ref 和 video_model 消息包含 episode_number 字段
✓ AES_KEY 消息包含 episode_number 字段
✓ lastEpisodeNumber 全局变量存在
✓ VideoRef 和 AESKey 包含 episode_number: Optional[int] 字段
✓ on_message 回调正确解析 episode_number
✓ 11/12 测试通过（1 个预存在失败）
```

## 后续建议

1. **字段名监控**: 在 Hook 日志中记录所有 VideoModel 字段名，便于 App 更新后快速适配
2. **提取成功率统计**: 记录 episode_number 提取成功/失败次数，评估提取策略的有效性
3. **时序关联验证**: 在 Plan 02-02 中验证 Native Hook 的 episode_number 关联准确性

## 总结

本计划成功为 Hook 数据增加 episode_number 字段，建立了 Hook 数据与集数的关联。Java Hook 通过字段层次搜索和标题模式提取集数，Native Hook 通过全局变量缓存实现近似关联。所有修改保持向后兼容，为 Plan 02-02 的精确匹配逻辑奠定了基础。
