# Roadmap: 重构红果短剧下载器

## Overview

本项目通过系统性重构提升红果短剧下载器的稳定性、可维护性和可扩展性。重构分为 4 个阶段：首先修复核心稳定性问题（UI lag bug + 状态管理），然后增强数据准确性（Hook 校验），接着改进健壮性（错误恢复），最后为未来扩展奠定基础（AppAdapter 抽象）。

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [ ] **Phase 1: 核心稳定性修复** - 修复 UI lag 导致的集数错位 bug + 重构状态管理
- [ ] **Phase 2: Hook 数据校验增强** - 增加时间戳和集数字段，实现精确匹配
- [ ] **Phase 3: 错误恢复机制改进** - 增强重试逻辑和会话持久化
- [ ] **Phase 4: AppAdapter 抽象** - 为多 App 支持奠定架构基础

## Phase Details

### Phase 1: 核心稳定性修复
**Goal**: 消除批量下载中的内容错位问题，建立清晰的状态管理架构
**Depends on**: Nothing (first phase)
**Requirements**: 修复 UI lag bug、重构状态管理、消除作用域问题
**Success Criteria** (what must be TRUE):
  1. 用户批量下载 50+ 集时，每个文件的内容与文件名完全一致（无错位）
  2. 开发者修改代码时，所有模块都能直接访问 CaptureState，无需通过回调传递
  3. 在 EP2→EP3 转换等 UI 延迟场景下，下载器能正确等待 UI 稳定后再读取 Hook 数据
**Plans**: 4 plans in 3 waves
**UI hint**: yes

Plans:
- [x] 01-01-PLAN.md — 状态管理重构（模块级单例）
- [x] 01-02-PLAN.md — Hook 数据结构增强（时间戳字段）
- [x] 01-03-PLAN.md — UI 稳定性检查（两阶段模式）
- [x] 01-04-PLAN.md — 测试覆盖和回归验证

### Phase 2: Hook 数据校验增强
**Goal**: 通过增加上下文信息，消除 Hook 数据竞争和时序依赖
**Depends on**: Phase 1
**Requirements**: Hook 端增加 episode_number 字段、Python 端精确匹配、消除时序依赖
**Success Criteria** (what must be TRUE):
  1. 当目标集和 preload 集的 Hook 同时触发时，Python 端能根据 episode_number 精确选择正确的数据
  2. Hook 数据包含时间戳，Python 端只接受最近 5 秒内的数据（过期数据自动丢弃）
  3. 在多集预加载场景下，AES 密钥与 video_id 能通过 episode_number 准确关联（无错配）
**Plans**: 3 plans in 3 waves

Plans:
- [x] 02-01-PLAN.md — Hook 端增加 episode_number 字段（Java + Native）
- [x] 02-02-PLAN.md — Python 端精确匹配逻辑
- [ ] 02-03-PLAN.md — 集成测试和真实设备验证

### Phase 3: 错误恢复机制改进
**Goal**: 提升长时间批量下载的可靠性，支持断点续传和自动重试
**Depends on**: Phase 2
**Requirements**: 增加 max_retries 配置、会话持久化、重试历史记录
**Success Criteria** (what must be TRUE):
  1. 用户下载 80 集短剧时，如果中途脚本崩溃或手动中断，重新运行后能从断点继续（无需重新下载已完成的集数）
  2. 当单集下载失败时（网络超时、解密失败等），脚本自动重试最多 3 次，每次重试前清空 state
  3. session_manifest.jsonl 中记录每次重试的历史（包括失败原因和重试次数），便于离线审计
**Plans**: 3 plans in 3 waves

Plans:
- [x] 03-01-PLAN.md — 断点续传机制（读取 session_manifest.jsonl 识别已下载集数）
- [x] 03-02-PLAN.md — 自动重试机制（max_retries=3，每次重试前清空状态）
- [x] 03-03-PLAN.md — 会话持久化增强（标准化记录格式，更新 README）

### Phase 4: AppAdapter 抽象
**Goal**: 建立多 App 支持的架构基础，为扩展到快手、抖音等平台做准备
**Depends on**: Phase 3
**Requirements**: 抽象 AppAdapter 接口、实现红果 adapter、配置化 Hook 目标
**Success Criteria** (what must be TRUE):
  1. 开发者可以通过实现 AppAdapter 接口（定义 UI 元素定位、Hook 目标、文件名规则）来支持新的短剧 App
  2. 用户可以通过配置文件（如 `--app honguo` 或 `--app kuaishou`）选择目标 App，无需修改代码
  3. 红果 App 的所有现有功能（搜索、批量下载、解密）在新架构下仍正常工作（向后兼容）
**Plans**: 3 plans in 3 waves

Plans:
- [x] 04-01-PLAN.md — AppAdapter 接口定义（抽象基类 + 工厂函数）
- [x] 04-02-PLAN.md — HongGuoAdapter 实现（迁移现有红果逻辑）
- [ ] 04-03-PLAN.md — 配置文件加载和集成（config.yaml + 主流程集成）

## Progress

**Execution Order:**
Phases execute in numeric order: 1 → 2 → 3 → 4

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. 核心稳定性修复 | 4/4 | Complete | 2026-04-15 |
| 2. Hook 数据校验增强 | 2/3 | In progress | - |
| 3. 错误恢复机制改进 | 0/3 | Not started | - |
| 4. AppAdapter 抽象 | 0/3 | Not started | - |
