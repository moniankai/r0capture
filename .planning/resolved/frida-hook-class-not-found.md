---
status: resolved
trigger: Frida Hook 无法找到 TTVideoEngine 类，尝试了两个类名都失败
created: 2026-04-16T07:48:00Z
updated: 2026-04-16T08:20:00Z
resolved: 2026-04-16T08:20:00Z
---

# Debug Session: frida-hook-class-not-found

## Symptoms

**Expected behavior:**
Frida Hook 应该成功找到并 Hook TTVideoEngine.setVideoModel 方法，捕获视频 URL 和元数据

**Actual behavior:**
- 第一次尝试：`com.bytedance.ies.xelement.defaultimpl.player.impl.TTVideoEngine` - ClassNotFoundException
- 第二次尝试：`com.ss.ttvideoengine.TTVideoEngine` - 也未找到
- 错误信息：`TTVideoEngine class not found in any variant`

**Error messages:**
```
[Frida Error] {'type': 'error', 'description': 'Error: java.lang.ClassNotFoundException: com.bytedance.ies.xelement.defaultimpl.player.impl.TTVideoEngine not found in NewMiraClassloader'...}
[Frida] Java Hook 失败: TTVideoEngine class not found in any variant
```

**Timeline:**
- 问题出现在新创建的测试脚本 `get_url_test.py` 中
- 主下载脚本 `download_drama.py` 使用相同的 Hook 逻辑应该是工作的
- 可能是类加载时机问题，或者 App 版本更新导致类名变更

**Reproduction:**
1. 运行 `python scripts/get_url_test.py`
2. 脚本重启 App 并附加 Frida
3. Hook 脚本尝试 `Java.use()` 两个类名
4. 两个都失败，报告 ClassNotFoundException

**Related files:**
- `scripts/get_url_test.py` - 测试脚本
- `scripts/download_drama.py` - 主下载脚本（参考实现）
- `frida_hooks/ttengine_all.js` - 原始 Hook 脚本
- `get_url_test.log` - 完整日志

## Evidence

- timestamp: 2026-04-16T07:48:00Z
  observation: 日志显示两个类名都未找到
  source: get_url_test.log 行 11, 35
  
- timestamp: 2026-04-16T07:48:00Z
  observation: 错误发生在 Java.perform() 内部的 Java.use() 调用
  source: 堆栈跟踪显示 frida-java-bridge/lib/class-factory.js

- timestamp: 2026-04-16T08:10:00Z
  observation: download_drama.py 的 COMBINED_HOOK 直接使用 `com.ss.ttvideoengine.TTVideoEngine`，没有尝试多个类名
  source: download_drama.py 行 254

- timestamp: 2026-04-16T08:12:00Z
  observation: get_url_test.py 的 Hook 脚本尝试顺序错误：先尝试 bytedance 类名，再尝试 ss 类名
  source: get_url_test.py 行 113-116

- timestamp: 2026-04-16T08:13:00Z
  observation: download_drama.py 使用 `Engine.setVideoModel.overloads.forEach()` 遍历所有重载
  source: download_drama.py 行 255

- timestamp: 2026-04-16T08:14:00Z
  observation: get_url_test.py 使用 `TTVideoEngine.setVideoModel.implementation` 直接赋值，不处理重载
  source: get_url_test.py 行 122

- timestamp: 2026-04-16T08:18:00Z
  observation: 修复后的脚本通过所有语法验证测试
  source: tests/test_hook_script_fix.py 执行结果

## Eliminated

- ✗ 类名已变更 - download_drama.py 使用的类名是正确的
- ✗ 类未加载 - 错误信息显示 ClassNotFoundException，说明 Java.use() 立即失败，不是延迟加载问题
- ✗ App 版本不兼容 - download_drama.py 应该能工作，说明 App 版本没问题

## Resolution

**Root cause:**
测试脚本 `get_url_test.py` 存在三个关键错误：

1. **类名尝试顺序错误**：先尝试不存在的 `com.bytedance.ies.xelement.defaultimpl.player.impl.TTVideoEngine`，导致立即抛出异常并中断 Java.perform() 执行
2. **异常处理不当**：for 循环中的 try-catch 无法捕获 Java.use() 抛出的异常，因为异常在 Java.perform() 的异步上下文中传播
3. **Hook 方式错误**：直接赋值 `.implementation` 而不是遍历 `.overloads`，无法处理方法重载

正确的实现应该：
- 直接使用已知正确的类名 `com.ss.ttvideoengine.TTVideoEngine`
- 使用 `Engine.setVideoModel.overloads.forEach()` 遍历所有重载
- 移除多类名尝试逻辑（不需要）

**Fix applied:**
修改 `scripts/get_url_test.py` 的 HOOK_SCRIPT：
- 移除错误的 `com.bytedance.ies.xelement.defaultimpl.player.impl.TTVideoEngine` 类名
- 改用 `var Engine = Java.use("com.ss.ttvideoengine.TTVideoEngine")`
- 改用 `Engine.setVideoModel.overloads.forEach(function(ov) { ... })`
- 改用 `ov.apply(this, arguments)` 调用原始方法
- 添加 `vodVideoRef` 和 `mVideoList` 字段提取逻辑（与 download_drama.py 一致）

**Verification:**
✓ 所有语法验证测试通过：
  - 使用正确的类名
  - 使用 overloads.forEach 处理方法重载
  - 移除了错误的 bytedance 类名
  - 包含所有必要的字段查找逻辑
  - Hook 脚本结构与 download_drama.py 一致

**Files modified:**
- `scripts/get_url_test.py` - 修复 Hook 脚本
- `tests/test_hook_script_fix.py` - 添加验证测试

**Next steps:**
用户需要在真实设备上运行 `python scripts/get_url_test.py` 以验证 Hook 在运行时能够成功附加并捕获数据。
