# Phase 3: 错误恢复机制改进 — 实现上下文

**生成时间**: 2026-04-15
**模式**: Auto

## 阶段目标

提升长时间批量下载的可靠性，支持断点续传和自动重试。

## 成功标准

1. 用户下载 80 集短剧时，如果中途脚本崩溃或手动中断，重新运行后能从断点继续
2. 当单集下载失败时，脚本自动重试最多 3 次，每次重试前清空 state
3. session_manifest.jsonl 中记录每次重试的历史，便于离线审计

## Phase 1-2 成果回顾

- ✓ 状态管理重构（模块级单例）
- ✓ UI 稳定性检查（两阶段模式）
- ✓ Hook 数据时间戳过滤
- ✓ episode_number 精确匹配

## 实现决策

### 决策 1: 断点续传机制

**具体实现**：
1. 读取 `session_manifest.jsonl` 识别已下载集数
2. 跳过已存在且完整的视频文件
3. 支持 `--resume` 标志显式启用断点续传

### 决策 2: 自动重试机制

**具体实现**：
1. 增加 `max_retries=3` 配置
2. 每次重试前调用 `reset_capture_state()`
3. 记录重试历史到 `session_manifest.jsonl`

### 决策 3: 会话持久化

**具体实现**：
1. 每次下载成功后立即追加到 `session_manifest.jsonl`
2. 记录字段：episode, video_id, resolution, success, retry_count, timestamp

## 交付物

1. `scripts/download_drama.py` — 断点续传和重试逻辑
2. `tests/test_error_recovery.py` — 新增测试
3. 更新 `README.md` — 说明 `--resume` 标志

## 下游 Agent 指引

### 给 gsd-planner
- 优先处理断点续传（风险低，用户价值高）
- 自动重试依赖断点续传完成
- 会话持久化可以与断点续传并行
