# Phase 2: Hook 数据校验增强 — 实现上下文

**生成时间**: 2026-04-15
**模式**: Auto（基于 Phase 1 成果和项目目标自动生成）

## 阶段目标

通过增加上下文信息，消除 Hook 数据竞争和时序依赖。

## 成功标准

1. 当目标集和 preload 集的 Hook 同时触发时，Python 端能根据 episode_number 精确选择正确的数据
2. Hook 数据包含时间戳，Python 端只接受最近 5 秒内的数据（过期数据自动丢弃）✓ **Phase 1 已实现**
3. 在多集预加载场景下，AES 密钥与 video_id 能通过 episode_number 准确关联（无错配）

## Phase 1 成果回顾

Phase 1 已实现：
- ✓ 时间戳字段（`VideoRef.timestamp`, `AESKey.timestamp`）
- ✓ 过期数据过滤（5 秒阈值）
- ✓ 模块级状态管理
- ✓ UI 稳定性检查

**遗留问题**：
- Hook 数据仍依赖时序选择（`_snap_refs[0]`）
- 无法区分目标集和 preload 集的 Hook 数据
- AES 密钥与 video_id 的关联仍不可靠

## 核心问题分析

### 问题：Hook 数据竞争

**根因**（来自 CONCERNS.md 第 37-41 行）：
- 目标集和 preload 集的 Hook 几乎同时触发
- Python 端依赖时序选择 `_snap_refs[0]`（假设第一个是目标集）
- 在特定时序下可能选错数据

**影响范围**：
- `frida_hooks/ttengine_all.js` — Java Hook 脚本
- `frida_hooks/aes_hook.js` — Native Hook 脚本
- `scripts/download_drama.py` — Python 端数据选择逻辑

## 实现决策

### 决策 1: Hook 端增加 episode_number 字段

**选择**: 从播放器状态提取集数信息

**具体实现**：

1. **Java Hook 增强**（`ttengine_all.js`）：
   ```javascript
   Java.perform(function() {
       var TTVideoEngine = Java.use("com.ss.ttvideoengine.TTVideoEngine");
       
       TTVideoEngine.setVideoModel.implementation = function(model) {
           var result = this.setVideoModel(model);
           
           // 提取 episode_number（从播放器状态或 UI）
           var episodeNumber = extractEpisodeNumber(this);
           
           send({
               type: 'video_model',
               mVideoId: model.mVideoId.value,
               url: extractUrl(model),
               resolution: extractResolution(model),
               episode_number: episodeNumber,  // 新增
               timestamp: Date.now()
           });
           
           return result;
       };
   });
   
   function extractEpisodeNumber(engine) {
       // 方案 A: 从播放器状态提取
       try {
           var playerState = engine.getPlayerState();
           if (playerState && playerState.currentEpisode) {
               return playerState.currentEpisode.value;
           }
       } catch (e) {}
       
       // 方案 B: 从 UI 层提取（通过 Activity）
       try {
           var activity = Java.use("android.app.ActivityThread")
               .currentApplication().getApplicationContext();
           // ... 提取当前显示的集数
       } catch (e) {}
       
       // 方案 C: 返回 null，由 Python 端处理
       return null;
   }
   ```

2. **Native Hook 增强**（`aes_hook.js`）：
   ```javascript
   Interceptor.attach(av_aes_init_addr, {
       onEnter: function(args) {
           this.key = args[1];
           this.key_bits = args[2].toInt32();
           
           // 尝试从调用栈回溯关联的 video_id
           var backtrace = Thread.backtrace(this.context, Backtracer.ACCURATE);
           var episodeNumber = extractEpisodeFromBacktrace(backtrace);
           
           this.episodeNumber = episodeNumber;
       },
       onLeave: function(retval) {
           if (this.key && this.key_bits === 128) {
               var keyHex = readKeyHex(this.key);
               send({
                   type: 'aes_key',
                   key: keyHex,
                   episode_number: this.episodeNumber,  // 新增
                   timestamp: Date.now()
               });
           }
       }
   });
   ```

3. **Python 端数据结构更新**：
   ```python
   @dataclass
   class VideoRef:
       video_id: str
       url: str
       resolution: str
       timestamp: float
       episode_number: Optional[int]  # 新增
       context: Dict[str, Any]
   
   @dataclass
   class AESKey:
       key_hex: str
       timestamp: float
       episode_number: Optional[int]  # 新增
       context: Dict[str, Any]
   ```

### 决策 2: Python 端精确匹配逻辑

**选择**: 优先使用 episode_number，回退到时序

**具体实现**：
```python
def download_and_decrypt(ep_num: int):
    state = get_capture_state()
    now = time.time()
    
    # 过滤最近 5 秒内的数据
    recent_refs = [r for r in state.video_refs if now - r.timestamp < 5.0]
    recent_keys = [k for k in state.aes_keys if now - k.timestamp < 5.0]
    
    # 策略 1: 精确匹配 episode_number
    matched_refs = [r for r in recent_refs if r.episode_number == ep_num]
    matched_keys = [k for k in recent_keys if k.episode_number == ep_num]
    
    if matched_refs and matched_keys:
        logger.info(f"[数据选择] 通过 episode_number={ep_num} 精确匹配")
        vid_ref = matched_refs[0]
        aes_key = matched_keys[0]
    else:
        # 策略 2: 回退到时序选择（Phase 1 逻辑）
        logger.warning(f"[数据选择] episode_number 不可用，回退到时序选择")
        if not recent_refs:
            return False
        vid_ref = recent_refs[0]  # 最新的数据
        aes_key = recent_keys[0] if recent_keys else None
    
    # ... 继续下载和解密
```

### 决策 3: Hook 端 episode_number 提取策略

**优先级**：
1. **播放器状态**（最可靠）— 从 `TTVideoEngine.getPlayerState()` 提取
2. **UI 层提取**（次选）— 从当前 Activity 的 UI 元素提取
3. **返回 null**（回退）— 由 Python 端使用时序选择

**实现细节**：
- 播放器状态提取需要逆向 `TTVideoEngine` 的内部 API
- UI 层提取可以复用 Python 端的 `detect_ui_context_from_device` 逻辑
- 如果 Hook 端无法提取，Python 端仍可使用 Phase 1 的时序选择作为回退

## 技术约束

### 必须保持
- Frida 16.5.9（Android 9 兼容性）
- Phase 1 的时间戳过滤逻辑（作为回退机制）
- 向后兼容现有下载的视频文件格式

### 可以改变
- Hook 脚本的数据结构
- Python 端的数据选择逻辑
- 错误处理和日志记录

## 不在本阶段范围内

以下内容推迟到后续阶段：
- 错误恢复机制改进（Phase 3）
- AppAdapter 抽象（Phase 4）
- 并行下载支持（Phase 5，可选）

## 测试策略

### 单元测试
1. `test_episode_number_matching()` — 验证精确匹配逻辑
2. `test_fallback_to_timestamp()` — 验证回退到时序选择
3. `test_hook_data_with_episode_number()` — 验证 Hook 数据结构

### 集成测试
1. `test_multi_episode_preload()` — 模拟多集预加载场景
2. `test_episode_number_extraction()` — 验证 Hook 端提取逻辑
3. `test_key_video_association()` — 验证 AES 密钥与 video_id 关联

### 回归测试
1. 运行 Phase 1 的 68 个测试
2. 手动测试：下载"凡人仙葫第一季" EP1-10，验证内容正确性

## 交付物

1. **Hook 脚本修改**：
   - `frida_hooks/ttengine_all.js` — 增加 episode_number 提取
   - `frida_hooks/aes_hook.js` — 增加 episode_number 关联

2. **Python 代码修改**：
   - `scripts/download_drama.py` — 更新数据结构和匹配逻辑

3. **测试**：
   - `tests/test_episode_matching.py` — 新增单元测试
   - `tests/test_hook_integration.py` — 新增集成测试

4. **文档**：
   - 更新 `README.md` — 说明 episode_number 字段
   - 更新 `CLAUDE.md` — 记录 Hook 增强决策

## 下游 Agent 指引

### 给 gsd-phase-researcher
- 研究 TTVideoEngine 的内部 API（播放器状态提取）
- 调查 Frida 调用栈回溯的最佳实践
- 查找其他 Frida 项目如何关联 Java 和 Native Hook 数据

### 给 gsd-planner
- 优先处理 Hook 端增强（风险中等，影响大）
- Python 端匹配逻辑依赖 Hook 端完成
- 每个修改点都需要对应的单元测试
- 考虑分阶段提交：先 Hook 端，再 Python 端

### 给 gsd-executor
- Hook 脚本修改需要在测试设备验证后再合并
- Python 端匹配逻辑可以独立提交（保持回退机制）
- 注意保持向后兼容：episode_number 为 null 时回退到时序选择
