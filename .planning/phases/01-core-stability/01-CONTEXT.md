# Phase 1: 核心稳定性修复 — 实现上下文

**生成时间**: 2026-04-15
**模式**: Auto（基于代码库映射和项目目标自动生成）

## 阶段目标

消除批量下载中的内容错位问题，建立清晰的状态管理架构。

## 成功标准

1. 用户批量下载 50+ 集时，每个文件的内容与文件名完全一致（无错位）
2. 开发者修改代码时，所有模块都能直接访问 CaptureState，无需通过回调传递
3. 在 EP2→EP3 转换等 UI 延迟场景下，下载器能正确等待 UI 稳定后再读取 Hook 数据

## 核心问题分析

### 问题 1: UI lag 导致集数错位

**根因**（来自 CONCERNS.md 和调试日志）：
- EP2→EP3 转换时，picker 重试 + preload Hook 竞争导致内容与文件名不匹配
- `resolve_actual_episode` 逻辑在 UI 延迟场景下误判：UI 显示 ep3 但 Hook 数据是 ep2
- `should_accept_out_of_order_episode` 允许跳集填补，但在 UI 延迟时会误判

**影响范围**：
- `scripts/download_drama.py` 第 1801 行（`_snap_refs[0]` 选择）
- `scripts/download_drama.py` 第 1869 行（`_snap_keys[0]` 选择）
- `scripts/download_drama.py` 第 1750-1850 行（`resolve_actual_episode` 逻辑）

### 问题 2: 状态管理混乱

**根因**（来自 CONCERNS.md）：
- `CaptureState` 在 `main()` 内部，模块级函数无法访问
- 需要通过回调传递 `clear_state_fn`，代码复杂度高
- 作用域问题导致 NameError（run11 崩溃）

**影响范围**：
- `scripts/download_drama.py` 第 1206 行（`_try_start_episode_on_drama_page` 签名）
- `scripts/download_drama.py` 第 1322 行（`search_drama_in_app` 签名）
- `scripts/download_drama.py` 第 1735, 2006, 2113 行（三个调用点）

## 实现决策

### 决策 1: UI lag 修复方案

**选择**: 两阶段模式 + Hook 数据时间戳校验

**理由**：
- 方案 A（双重校验）：增加复杂度但不解决根本问题
- 方案 B（时间戳）：简单有效，但仍依赖时序
- **方案 C（两阶段模式）**：最健壮，先确认 UI 稳定再读取 Hook 数据

**具体实现**：
1. 在 `download_and_decrypt` 中增加 UI 稳定性检查：
   - 选集后等待 UI 更新（轮询 `detect_ui_context_from_device`，超时 10s）
   - 确认 UI 显示的集数与预期一致后，再读取 `_snap_refs[0]`
   - 如果 UI 集数不匹配，清空 state 并重试 picker

2. 增加 Hook 数据时间戳：
   - 在 `on_message` 中记录每个 Hook 数据的捕获时间
   - 在 `download_and_decrypt` 中只接受最近 5 秒内的数据
   - 过期数据自动丢弃并触发重新捕获

3. 移除 `should_accept_out_of_order_episode` 的自动覆盖：
   - 保留跳集检测逻辑，但不自动覆盖 `ep_num`
   - 改为记录警告并要求用户确认

### 决策 2: 状态管理重构方案

**选择**: 模块级单例 + 依赖注入

**理由**：
- 单例模式：简单直接，所有模块都能访问
- 依赖注入：更清晰，但需要修改所有函数签名
- **混合方案**：单例用于全局访问，依赖注入用于测试

**具体实现**：
1. 将 `CaptureState` 提升为模块级单例：
   ```python
   # 模块顶部
   _global_capture_state: Optional[CaptureState] = None
   
   def get_capture_state() -> CaptureState:
       global _global_capture_state
       if _global_capture_state is None:
           _global_capture_state = CaptureState()
       return _global_capture_state
   
   def reset_capture_state():
       global _global_capture_state
       _global_capture_state = CaptureState()
   ```

2. 移除所有 `clear_state_fn` 回调参数：
   - `_try_start_episode_on_drama_page` 直接调用 `reset_capture_state()`
   - `search_drama_in_app` 直接调用 `reset_capture_state()`
   - 删除三个调用点的 `clear_state_fn=state.clear` 参数

3. 在 `main()` 中初始化全局状态：
   ```python
   def main():
       reset_capture_state()  # 初始化全局状态
       state = get_capture_state()  # 获取引用
       # ... 现有逻辑
   ```

4. 为测试提供依赖注入接口：
   ```python
   def _try_start_episode_on_drama_page(
       ep_num: int,
       state: Optional[CaptureState] = None  # 测试时可注入
   ) -> bool:
       if state is None:
           state = get_capture_state()
       # ... 使用 state
   ```

### 决策 3: Hook 数据结构增强

**选择**: 增加 `timestamp` 和 `context` 字段

**具体实现**：
1. 修改 `CaptureState` 数据结构：
   ```python
   @dataclass
   class VideoRef:
       video_id: str
       url: str
       resolution: str
       timestamp: float  # 新增：捕获时间戳
       context: Dict[str, Any]  # 新增：上下文信息（预留）
   
   @dataclass
   class AESKey:
       key_hex: str
       timestamp: float  # 新增：捕获时间戳
       context: Dict[str, Any]  # 新增：上下文信息（预留）
   ```

2. 在 `on_message` 中记录时间戳：
   ```python
   def on_message(message, data):
       if message['type'] == 'send':
           payload = message['payload']
           if payload.get('type') == 'video_model':
               ref = VideoRef(
                   video_id=payload['mVideoId'],
                   url=payload['url'],
                   resolution=payload['resolution'],
                   timestamp=time.time(),  # 新增
                   context={}  # 新增
               )
               state.video_refs.append(ref)
   ```

3. 在 `download_and_decrypt` 中过滤过期数据：
   ```python
   def download_and_decrypt(ep_num: int):
       state = get_capture_state()
       now = time.time()
       
       # 过滤最近 5 秒内的数据
       recent_refs = [r for r in state.video_refs if now - r.timestamp < 5.0]
       recent_keys = [k for k in state.aes_keys if now - k.timestamp < 5.0]
       
       if not recent_refs:
           logger.warning("[下载] 无最近的 Hook 数据，等待重新捕获...")
           time.sleep(2)
           return False
       
       # 使用最新的数据
       vid_ref = recent_refs[0]
       aes_key = recent_keys[0] if recent_keys else None
   ```

## 技术约束

### 必须保持
- Frida 16.5.9（Android 9 兼容性）
- 现有 CENC 解密逻辑
- 向后兼容现有下载的视频文件格式

### 可以改变
- 状态管理架构
- UI 解析和自动化逻辑
- 错误处理和重试机制

## 不在本阶段范围内

以下内容推迟到后续阶段：
- Hook 端增加 `episode_number` 字段（Phase 2）
- 错误恢复机制改进（Phase 3）
- AppAdapter 抽象（Phase 4）
- 并行下载支持（Phase 5，可选）

## 测试策略

### 单元测试
1. `test_capture_state_singleton()` — 验证单例模式正确性
2. `test_hook_data_timestamp_filtering()` — 验证时间戳过滤逻辑
3. `test_ui_stability_check()` — 验证 UI 稳定性检查逻辑

### 集成测试
1. `test_batch_download_50_episodes()` — 批量下载 50 集，验证无内容错位
2. `test_ui_lag_scenario()` — 模拟 UI 延迟场景，验证两阶段模式
3. `test_state_access_from_all_modules()` — 验证所有模块都能访问状态

### 回归测试
1. 运行现有测试套件（`tests/test_download_drama.py`）
2. 手动测试：下载"凡人仙葫第一季" EP1-10，验证内容正确性

## 交付物

1. **代码修改**：
   - `scripts/download_drama.py` — 状态管理重构 + UI lag 修复
   - `scripts/drama_download_common.py` — 数据结构增强（如需要）

2. **测试**：
   - `tests/test_capture_state.py` — 新增单元测试
   - `tests/test_ui_stability.py` — 新增集成测试

3. **文档**：
   - 更新 `README.md` — 说明状态管理变更
   - 更新 `CLAUDE.md` — 记录架构决策

## 下游 Agent 指引

### 给 gsd-phase-researcher
- 研究其他 Frida 项目如何处理 Hook 数据时序问题
- 调查 uiautomator 的稳定性检查最佳实践
- 查找 Python 单例模式的测试友好实现

### 给 gsd-planner
- 优先处理状态管理重构（风险低，影响大）
- UI lag 修复依赖状态管理重构完成
- 每个修改点都需要对应的单元测试
- 考虑分阶段提交：先重构状态管理，再修复 UI lag

### 给 gsd-executor
- 状态管理重构可以独立提交（不影响现有功能）
- UI lag 修复需要在测试环境验证后再合并
- 注意保持向后兼容：现有下载的视频文件仍可正常播放
