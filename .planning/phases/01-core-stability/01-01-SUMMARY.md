---
phase: 01-core-stability
plan: 01
subsystem: state-management
tags: [refactoring, singleton, testing]
dependency_graph:
  requires: []
  provides: [global-capture-state-singleton]
  affects: [download_drama.py, all-module-functions]
tech_stack:
  added: [singleton-pattern, dependency-injection]
  patterns: [lazy-initialization, optional-injection]
key_files:
  created: []
  modified:
    - path: scripts/download_drama.py
      lines_changed: 50
      description: "添加模块级单例访问器，移除回调参数，更新 main() 初始化"
decisions:
  - id: DEC-01-01-01
    summary: "使用懒加载单例模式而非立即初始化"
    rationale: "避免模块导入时的副作用，支持测试时的状态重置"
  - id: DEC-01-01-02
    summary: "保留可选 state 参数用于测试注入"
    rationale: "平衡全局访问便利性和测试友好性"
  - id: DEC-01-01-03
    summary: "reset_capture_state() 创建新实例而非调用 clear()"
    rationale: "更彻底的重置，避免残留引用问题"
metrics:
  duration_minutes: 8
  tasks_completed: 3
  files_modified: 1
  lines_added: 50
  lines_removed: 3
  tests_passed: 47
  commits: 3
  completed_date: 2026-04-15
---

# Phase 01 Plan 01: 状态管理重构 Summary

**一句话总结**: 将 CaptureState 从 main() 局部变量提升为模块级单例，消除回调传递复杂度，保持测试友好性。

## 执行概览

成功将 `CaptureState` 从 `main()` 函数内部提升为模块级单例，移除了所有 `clear_state_fn` 回调参数，使所有模块级函数都能直接访问全局状态。所有 47 个现有测试通过，确认无破坏性变更。

## 完成的任务

### Task 1: 创建模块级单例访问器
- **提交**: a99e24d
- **文件**: scripts/download_drama.py (第 275-297 行)
- **内容**:
  - 添加 `_global_capture_state: Optional[CaptureState] = None` 模块级变量
  - 实现 `get_capture_state()` 懒加载单例访问器
  - 实现 `reset_capture_state()` 状态重置函数
- **验证**: 单例模式测试通过（多次调用返回同一实例，重置后返回新实例）

### Task 2: 移除 clear_state_fn 回调参数
- **提交**: fc2ef0d
- **文件**: scripts/download_drama.py
- **修改的函数**:
  1. `_try_start_episode_on_drama_page(ep_num, state=None)` (第 811 行)
     - 移除 `clear_state_fn` 参数
     - 添加可选 `state` 参数用于测试注入
     - 函数内部使用 `get_capture_state()` 获取全局单例
  2. `search_drama_in_app(name, start_episode, state=None)` (第 913 行)
     - 移除 `clear_state_fn` 参数
     - 添加可选 `state` 参数用于测试注入
     - 函数内部使用 `get_capture_state()` 获取全局单例
- **验证**: grep 确认无 `clear_state_fn` 残留

### Task 3: 更新 main() 函数使用全局状态
- **提交**: f9a7d56
- **文件**: scripts/download_drama.py (第 1129-1131 行)
- **修改前**:
  ```python
  state = CaptureState()
  ```
- **修改后**:
  ```python
  # 初始化全局状态
  reset_capture_state()
  state = get_capture_state()  # 获取全局单例引用
  ```
- **效果**: main() 和所有模块级函数现在共享同一个 CaptureState 实例

## 偏差记录

无偏差 — 计划按原定方案完整执行。

## 验证结果

### 单元测试
```bash
✓ Singleton pattern verified
  - 多次调用 get_capture_state() 返回同一实例
  - reset_capture_state() 后返回新实例
```

### 代码审查
```bash
✓ All clear_state_fn references removed
✓ get_capture_state() 和 reset_capture_state() 函数存在
✓ 函数签名已更新（添加可选 state 参数）
```

### 回归测试
```bash
✓ 47/47 tests passed in 0.20s
  - ParseUiContextTests: 4/4 passed
  - FileNamingTests: 2/2 passed
  - SessionValidationTests: 5/5 passed
  - FridaDeviceTests: 2/2 passed
  - RunningPidSelectionTests: 3/3 passed
  - BatchNavigationStrategyTests: 3/3 passed
  - PlayerEntryStrategyTests: 5/5 passed
  - EpisodeResolutionTests: 6/6 passed
  - ResumeAndTotalTests: 9/9 passed
  - TaskStateTests: 8/8 passed
```

## 向后兼容性

✓ **完全兼容** — 所有现有功能保持不变：
- 现有测试套件 100% 通过
- 函数签名保持向后兼容（新增可选参数）
- 状态管理行为一致（仅改变访问方式）

## 技术决策

### 决策 1: 懒加载 vs 立即初始化
**选择**: 懒加载（首次调用 `get_capture_state()` 时创建）

**理由**:
- 避免模块导入时的副作用
- 支持测试时通过 `reset_capture_state()` 重置状态
- 符合 Python 单例模式最佳实践

### 决策 2: 全局单例 vs 依赖注入
**选择**: 混合方案（全局单例 + 可选注入）

**理由**:
- 全局单例：简化生产代码，所有模块直接访问
- 可选注入：保持测试友好性，允许测试时注入 mock 实例
- 平衡便利性和可测试性

### 决策 3: reset_capture_state() 实现
**选择**: 创建新实例而非调用 `clear()`

**理由**:
- 更彻底的重置，避免残留引用
- 确保每次重置后的状态完全独立
- 简化实现（无需维护 clear() 逻辑）

## 已知限制

1. **线程安全**: 当前实现未加锁，仅适用于单线程场景（符合当前项目需求）
2. **全局状态**: 模块级单例在多进程场景下不共享（当前项目为单进程设计）

## 下游影响

### 对 Phase 1 后续计划的影响
- **01-02 (UI lag 修复)**: 可直接使用 `get_capture_state()` 访问状态，无需传递参数
- **01-03 (Hook 数据增强)**: 可直接修改 CaptureState 数据结构，所有模块自动生效
- **01-04 (集成测试)**: 可通过 `reset_capture_state()` 在测试间隔离状态

### 对测试的影响
- 测试可通过 `reset_capture_state()` 确保干净的初始状态
- 测试可通过可选 `state` 参数注入 mock 实例
- 无需修改现有测试（向后兼容）

## 文件清单

### 修改的文件
- `scripts/download_drama.py` (50 行新增, 3 行删除)
  - 第 275-297 行: 模块级单例实现
  - 第 811-826 行: `_try_start_episode_on_drama_page` 签名更新
  - 第 913-936 行: `search_drama_in_app` 签名更新
  - 第 1129-1131 行: `main()` 状态初始化

### 提交记录
1. `a99e24d` - feat(01-core-stability-01): add module-level singleton accessors for CaptureState
2. `fc2ef0d` - refactor(01-core-stability-01): update function signatures to use global state
3. `f9a7d56` - refactor(01-core-stability-01): update main() to use global state singleton

## Self-Check: PASSED

### 文件存在性检查
```bash
✓ scripts/download_drama.py exists and modified
```

### 提交存在性检查
```bash
✓ a99e24d exists (Task 1)
✓ fc2ef0d exists (Task 2)
✓ f9a7d56 exists (Task 3)
```

### 功能验证
```bash
✓ get_capture_state() 返回单例
✓ reset_capture_state() 创建新实例
✓ 无 clear_state_fn 残留
✓ 所有测试通过 (47/47)
```

## 后续建议

1. **文档更新**: 在 README.md 中说明新的状态管理模式
2. **测试增强**: 添加专门的单例模式测试用例到测试套件
3. **线程安全**: 如果未来需要多线程支持，在 `get_capture_state()` 和 `reset_capture_state()` 中添加线程锁

## 总结

本计划成功将 CaptureState 从局部变量提升为模块级单例，消除了回调传递的复杂度，使代码更简洁、更易维护。所有修改保持向后兼容，47 个现有测试全部通过，确认无破坏性变更。为 Phase 1 后续计划奠定了坚实的状态管理基础。
