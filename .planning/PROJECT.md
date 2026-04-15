# 项目：重构红果短剧下载器

## 概述

r0capture 是一个安卓 SSL 流量抓包框架，扩展支持红果短剧 App（`com.phoenix.read`）的 CENC 加密视频下载。本项目旨在通过系统性重构，提升下载器的稳定性、可扩展性和用户体验。

## 项目背景

### 当前实现

项目融合了以下技术：
- **Frida Hook 注入**：拦截 Java 层（TTVideoEngine）和 Native 层（libttffmpeg）
- **ADB UI 自动化**：通过 uiautomator 解析界面，自动选集
- **MP4 CENC 解密**：AES-CTR-128 双轨解密（视频 + 音频）
- **会话管理**：防止剧名漂移、video_id 去重、集数单调递增校验

### 核心问题

1. **UI lag 导致集数错位**（高优先级）
   - EP2→EP3 转换时，picker 重试 + preload Hook 竞争导致内容与文件名不匹配
   - `resolve_actual_episode` 逻辑在 UI 延迟场景下误判
   - 影响：下载的文件内容与文件名不一致

2. **状态管理混乱**（高优先级）
   - `CaptureState` 在 `main()` 内部，模块级函数无法访问
   - 需要通过回调传递 `clear_state_fn`，代码复杂度高
   - 影响：可维护性差，容易引入作用域错误

3. **Hook 数据竞争**（中优先级）
   - 目标集和 preload 集的 Hook 几乎同时触发
   - 依赖时序选择 `_snap_refs[0]` vs `[-1]`
   - 影响：在特定时序下可能选错数据

4. **硬编码红果 App**（低优先级）
   - 包名、UI 元素、Hook 目标都是红果专用
   - 影响：无法扩展到其他短剧 App（快手、抖音等）

## 重构目标

### 高优先级：稳定性修复

1. **修复 UI lag 导致的集数错位 bug**
   - 在 `download_and_decrypt` 中增加 Hook 数据与 UI 的双重校验
   - 引入 Hook 数据时间戳，只接受最近 5 秒内的数据
   - 重构为"先确认 UI 稳定 → 再读取 Hook 数据"的两阶段模式

2. **重构状态管理**
   - 将 `CaptureState` 提升为模块级单例
   - 或使用依赖注入模式，显式传递状态对象
   - 消除回调传递 `clear_state_fn` 的需求

3. **增强 Hook 数据校验**
   - 在 Hook 端增加 `episode_number` 字段（从 UI 或播放器状态提取）
   - 在 Python 端根据 `episode_number` 精确匹配，而非依赖时序

### 中优先级：架构改进

4. **改进错误恢复能力**
   - 增加 `max_retries` 配置
   - 每次重试前清空 state
   - 记录重试历史到 `session_manifest.jsonl`

5. **优化 Hook 数据竞争处理**
   - 实现更健壮的数据选择逻辑
   - 考虑使用队列 + 超时机制

6. **增强日志和进度可见性**
   - 结构化日志输出
   - 实时进度报告（非仅日志）

### 低优先级：扩展性

7. **抽象 AppAdapter 接口**
   - 为每个 App 实现独立的 adapter（红果、快手、抖音等）
   - 通过配置文件选择 adapter

8. **并行下载支持**
   - 使用 `ThreadPoolExecutor` 或 `asyncio`
   - 限制并发数（避免触发 App 的反爬机制）

9. **配置化 Hook 目标**
   - 将 Hook 目标（类名、方法名）移到配置文件
   - 支持运行时动态加载

## 技术约束

### 必须保持

- **Frida 16.5.9**：Android 9 兼容性要求（17.x 版本 Java bridge 不可用）
- **CENC 解密逻辑**：现有的 AES-CTR-128 双轨解密实现
- **视频文件格式**：向后兼容现有下载的 MP4 文件

### 可以改变

- 状态管理架构
- UI 解析和自动化逻辑
- 错误处理和重试机制
- 代码组织和模块划分

## 成功标准

1. **稳定性**：批量下载 50+ 集无内容错位
2. **可维护性**：状态管理清晰，无作用域问题
3. **可扩展性**：支持至少 2 个不同的短剧 App
4. **向后兼容**：现有下载的视频文件仍可正常播放

## 项目范围

### 包含

- 核心下载器逻辑重构
- 状态管理重构
- Hook 数据处理优化
- 错误恢复机制改进
- 基础扩展性架构（AppAdapter）

### 不包含

- UI 界面开发（保持 CLI）
- 视频播放器功能
- 云存储集成
- 多设备同步

## 参考资料

- 现有代码库映射：`.planning/codebase/`
- 主要模块：`scripts/download_drama.py`, `scripts/drama_download_common.py`
- Hook 脚本：`frida_hooks/ttengine_all.js`, `frida_hooks/aes_hook.js`
- 测试套件：`tests/test_download_drama.py`
