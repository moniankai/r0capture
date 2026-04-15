# 测试指南

## 运行所有测试

```bash
pytest tests/ -v
```

## 运行特定测试文件

```bash
# 单例模式和时间戳过滤测试
pytest tests/test_capture_state.py -v

# UI 稳定性检查测试
pytest tests/test_ui_stability.py -v

# 现有功能回归测试
pytest tests/test_download_drama.py -v

# 审计功能测试
pytest tests/test_audit_drama_downloads.py -v
```

## 测试覆盖率

```bash
pytest tests/ --cov=scripts --cov-report=html
# 查看 htmlcov/index.html
```

## 测试结构

- `test_capture_state.py` — 测试 CaptureState 单例模式和时间戳过滤（Phase 1）
- `test_ui_stability.py` — 测试 UI 稳定性检查逻辑（Phase 1）
- `test_download_drama.py` — 测试 UI 解析、会话校验、文件名生成（现有）
- `test_audit_drama_downloads.py` — 测试审计功能（现有）

## Phase 1 测试覆盖

### 单例模式（01-01-PLAN）
- ✓ `get_capture_state()` 返回同一实例
- ✓ `reset_capture_state()` 创建新实例
- ✓ 状态隔离

### 时间戳过滤（01-02-PLAN）
- ✓ VideoRef 包含 timestamp 字段
- ✓ 过滤最近 5 秒内的数据
- ✓ 选择最新的数据
- ✓ 所有数据过期的情况

### UI 稳定性检查（01-03-PLAN）
- ✓ UI 立即稳定
- ✓ UI 延迟后稳定
- ✓ UI 超时
- ✓ UI 解析失败
- ✓ 轮询间隔生效

## 添加新测试

1. 在 `tests/` 目录创建 `test_<module>.py` 文件
2. 使用 pytest 框架编写测试函数
3. 运行 `pytest tests/test_<module>.py -v` 验证
4. 更新本文档

## 持续集成

测试应在每次提交前运行：

```bash
# 快速测试（跳过慢速测试）
pytest tests/ -v -m "not slow"

# 完整测试
pytest tests/ -v
```
