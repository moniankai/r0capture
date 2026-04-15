# Codebase Concerns

**Analysis Date:** 2026-04-15

## Tech Debt

**Frida 版本锁定在 16.5.9**
- Issue: 项目强制依赖 frida 16.5.9 以兼容 Android 9，无法使用 17.x 及更高版本的新特性和安全修复
- Files: `requirements.txt`, `scripts/check_environment.py`, `CLAUDE.md`
- Impact: 安全漏洞无法修复，新设备兼容性受限，社区支持逐渐减少
- Fix approach: 需要等待 Frida 官方修复 Android 9 Java bridge 问题，或放弃 Android 9 支持升级到 Android 10+ 设备

**download_drama.py 单文件过大（2173 行）**
- Issue: 主下载脚本包含 UI 自动化、Frida Hook 管理、状态机、下载逻辑、解密调用等多个职责
- Files: `scripts/download_drama.py`
- Impact: 难以维护、测试覆盖不足、修改风险高、新人上手困难
- Fix approach: 按职责拆分为独立模块：`ui_automation.py`（ADB 操作）、`capture_manager.py`（Frida 会话）、`batch_controller.py`（批量下载状态机）、`download_drama.py`（主入口编排）

**硬编码延迟遍布代码（66+ 处 sleep/timeout）**
- Issue: 大量 `time.sleep()` 硬编码等待时间（0.5s ~ 3.5s），无法适应不同设备性能
- Files: `scripts/download_drama.py` (20+ 处)
- Impact: 快速设备浪费时间，慢速设备出现竞态条件，批量下载效率低
- Fix approach: 引入轮询 + 超时机制替代固定延迟，例如 `wait_until(condition, timeout=10, poll_interval=0.3)`

**UI 导航依赖脆弱的 XML 解析**
- Issue: 通过 `uiautomator dump` 解析 XML 查找元素，resource-id 硬编码（如 `com.phoenix.read:id/jjj`），App 更新后失效
- Files: `scripts/download_drama.py`, `scripts/drama_download_common.py`
- Impact: App 版本更新后 UI 自动化完全失效，需要人工逆向新版本重新定位 resource-id
- Fix approach: 增加多重定位策略（text + resource-id + bounds），记录历史 ID 映射表，提供 UI 元素诊断工具

**批量下载状态机复杂且缺乏文档**
- Issue: `download_drama.py` 中批量下载逻辑包含多层嵌套的恢复路径、重试逻辑、异常处理，无状态图文档
- Files: `scripts/download_drama.py` (第 2021-2173 行)
- Impact: 边界条件处理不一致，恢复路径可能死循环，调试困难
- Fix approach: 绘制状态转移图，提取状态机为独立类，每个状态转移添加单元测试

**AES 密钥与 video_id 绑定不可靠**
- Issue: Native Hook 捕获的 AES 密钥与 Java Hook 捕获的 video_id 通过时序关联，无法保证一一对应
- Files: `scripts/download_drama.py` (COMBINED_HOOK)
- Impact: 多集预加载时密钥可能错配，导致解密失败或下载错误集数
- Fix approach: 在 Hook 层增加上下文关联（例如通过线程 ID 或调用栈回溯），或在解密失败时自动尝试其他捕获的密钥

**异常处理过于宽泛**
- Issue: 多处使用 `except Exception:` 捕获所有异常并静默忽略或仅记录日志
- Files: `scripts/download_drama.py` (第 479, 576, 617, 620, 1499, 1727, 1983 行)
- Impact: 真实错误被掩盖，调试困难，可能导致数据损坏或状态不一致
- Fix approach: 细化异常类型（`except (OSError, ValueError):`），关键路径不捕获异常让其向上传播

## Known Bugs

**uiautomator dump 在视频播放时超时**
- Symptoms: `adb shell uiautomator dump` 在全屏视频播放时挂起或超时（12s），导致 UI 状态检测失败
- Files: `scripts/download_drama.py` (第 625-653 行 `read_ui_xml_from_device`)
- Trigger: 播放器全屏渲染时 UI 层级捕获被阻塞
- Workaround: 已添加 12s 超时，失败时回退到 `dumpsys window` 获取 Activity 名称

**选集面板导航在第 44+ 集失败**
- Symptoms: `select_episode_from_ui()` 无法定位到第 44 集及以后的集数，即使 XML 中存在对应元素
- Files: `scripts/download_drama.py` (第 450-622 行)
- Trigger: 选集面板使用 GridView 懒加载，未滚动到的集数不在 XML 中
- Workaround: 当前实现尝试滚动但不可靠，建议改用坐标计算 + 多次滚动 + 重新 dump

**批量下载在第 2-3 集后停止**
- Symptoms: `--search -b` 模式下载第 1 集成功，第 2 或 3 集后停止，日志显示 `duplicate_video_id` 或 `title_mismatch`
- Files: `scripts/download_drama.py`, `docs/plans/2026-04-12-download-drama-batch-debug.md`
- Trigger: 上滑手势后 UI 集数未更新，预加载缓存污染 `CaptureState`
- Workaround: 已切换到选集面板导航，但恢复逻辑仍不稳定（Stage 8 In Progress）

## Security Considerations

**ADB 命令注入风险**
- Risk: 虽然当前代码未使用 `shell=True`，但 ADB 命令参数来自用户输入（剧名、集数），未做充分转义
- Files: `scripts/download_drama.py` (第 288-290, 372-380 行)
- Current mitigation: 使用列表形式传递参数给 `subprocess.run`，避免 shell 解析
- Recommendations: 对剧名输入增加白名单校验（仅允许 CJK、字母、数字、空格），拒绝特殊字符

**Frida Hook 脚本可被 App 检测**
- Risk: 红果 App 可能检测 Frida 存在并拒绝运行或返回假数据
- Files: `frida_hooks/anti_detection.js`
- Current mitigation: 提供反检测 Hook 脚本，但未默认启用
- Recommendations: 将反检测 Hook 合并到主 Hook 脚本，监控 App 更新的检测手段

**下载的视频文件无完整性校验**
- Risk: CDN 下载中断或篡改时无法检测，解密后的 MP4 可能损坏
- Files: `scripts/download_drama.py` (第 1930-1934 行 `download_file`)
- Current mitigation: 无
- Recommendations: 在元数据中记录文件 SHA256，解密后验证 MP4 文件头和 moov box 完整性

**密钥明文存储在元数据文件**
- Risk: `meta_ep*.json` 中以明文存储 AES 密钥（32 位 hex），泄露后可解密所有视频
- Files: `scripts/download_drama.py` (第 1890-1903 行)
- Current mitigation: 无
- Recommendations: 元数据文件权限设为 600，或使用用户密钥加密存储的 AES 密钥

## Performance Bottlenecks

**单线程下载 + 解密**
- Problem: 每集视频串行下载和解密，批量下载 80 集耗时数小时
- Files: `scripts/download_drama.py` (第 1921-1973 行)
- Cause: 主循环中同步调用 `download_file` 和 `decrypt_mp4`
- Improvement path: 下载和解密分离为生产者-消费者队列，下载用 `ThreadPoolExecutor`，解密用 `ProcessPoolExecutor`

**AES-CTR 解密未使用硬件加速**
- Problem: `decrypt_mp4` 使用 pycryptodome 纯 Python 实现，单个 100MB 视频解密耗时 10-20 秒
- Files: `scripts/decrypt_video.py` (第 103-145 行)
- Cause: 未使用 AES-NI 硬件指令
- Improvement path: 切换到 `cryptography` 库（基于 OpenSSL，自动使用 AES-NI），或用 Cython 重写热点循环

**UI XML 解析重复读取**
- Problem: 每次 UI 操作后都调用 `uiautomator dump` + 解析完整 XML（数百 KB），即使只需要一个元素
- Files: `scripts/download_drama.py`, `scripts/drama_download_common.py`
- Cause: 无缓存机制，每次都重新 dump
- Improvement path: 在同一 UI 状态下缓存 XML，或直接使用 `adb shell uiautomator` 的 selector API

**批量下载中频繁清空 CaptureState**
- Problem: 每集下载前调用 `state.clear()`，丢弃已捕获的其他集数据（预加载）
- Files: `scripts/download_drama.py` (第 2095 行)
- Cause: 为避免脏数据，采用激进清空策略
- Improvement path: 改为按 `video_id` 标记已使用，保留未使用的捕获数据供后续集使用

## Fragile Areas

**Frida Hook 注入时序**
- Files: `scripts/download_drama.py` (第 70-198 行 COMBINED_HOOK)
- Why fragile: Native 库 `libttffmpeg.so` 延迟加载，Hook 必须先监控 `dlopen` 再挂载 `av_aes_init`，时序错误导致密钥捕获失败
- Safe modification: 修改 Hook 脚本前在真实设备上验证，增加 Hook 成功/失败的明确日志
- Test coverage: 无自动化测试，依赖人工验证

**选集面板元素定位**
- Files: `scripts/download_drama.py` (第 450-622 行 `select_episode_from_ui`)
- Why fragile: 依赖多个 resource-id（`joj`, `ivi`, `d0d`, `zu`），任一 ID 变化导致功能失效
- Safe modification: 修改前记录当前 App 版本，保留旧版本兼容代码，增加元素定位失败时的诊断日志
- Test coverage: 有单元测试但使用静态 XML，无法覆盖真实 App 的 UI 变化

**会话状态验证逻辑**
- Files: `scripts/drama_download_common.py` (第 54-59, 200-280 行)
- Why fragile: `validate_round` 和 `apply_valid_round` 包含多个隐式假设（标题锁定、集数单调递增、video_id 去重），边界条件未完全测试
- Safe modification: 修改验证规则前增加对应的单元测试，确保不破坏现有通过的场景
- Test coverage: 部分覆盖（`tests/test_download_drama.py`），但缺少恢复路径和异常场景测试

**MP4 CENC 解密**
- Files: `scripts/decrypt_video.py`
- Why fragile: 手动解析 MP4 box 结构（stsz/stco/stsc/senc），依赖固定偏移和字节序，不同编码器生成的 MP4 可能失败
- Safe modification: 修改前准备多个不同来源的 CENC 样本文件，确保解密后 ffprobe 无错误
- Test coverage: 无自动化测试，依赖人工播放验证

## Scaling Limits

**单设备串行下载**
- Current capacity: 单台 Android 设备，单线程下载，约 3-5 分钟/集（含捕获、下载、解密）
- Limit: 下载 100 集短剧需要 5-8 小时
- Scaling path: 支持多设备并行（每设备负责不同集数段），或离线模式（批量捕获 URL+密钥后断开设备，PC 端并行下载）

**输出目录无分片**
- Current capacity: 所有剧集存储在 `videos/<剧名>/` 单一目录
- Limit: 单剧 100+ 集时目录列表性能下降，Windows 文件系统限制单目录文件数
- Scaling path: 按季度或集数范围分片（`videos/<剧名>/season_01/`, `videos/<剧名>/ep_001-050/`）

**内存中缓存所有捕获数据**
- Current capacity: `CaptureState` 在内存中保存所有捕获的 URL、密钥、元数据
- Limit: 长时间运行或预加载过多时内存占用持续增长
- Scaling path: 捕获数据持久化到 SQLite，内存中仅保留当前集所需数据

## Dependencies at Risk

**frida / frida-tools 版本锁定**
- Risk: 锁定在 16.5.9，无法获取安全更新和 bug 修复
- Impact: 新 Android 版本不兼容，已知漏洞无法修复
- Migration plan: 监控 Frida 官方 issue tracker，Android 9 Java bridge 修复后立即升级到最新稳定版

**pycryptodome 性能不足**
- Risk: 纯 Python 实现，AES 解密性能是 OpenSSL 的 1/10
- Impact: 批量解密成为瓶颈
- Migration plan: 切换到 `cryptography` 库（基于 OpenSSL，C 扩展），API 兼容性好

**scapy PCAP 解析未使用**
- Risk: `requirements.txt` 包含 scapy 但仅 `pcap_parser.py` 使用，该脚本非主工作流
- Impact: 增加依赖体积和安装复杂度
- Migration plan: 将 PCAP 相关功能标记为可选依赖，主工作流不依赖 scapy

## Missing Critical Features

**断点续传不完整**
- Problem: 下载中断后无法从失败集数恢复，必须重新从第 1 集开始
- Blocks: 长剧集下载（80+ 集）的可靠性
- Priority: High

**无视频质量验证**
- Problem: 解密后的 MP4 未验证完整性，损坏文件直到播放时才发现
- Blocks: 批量下载的可信度
- Priority: Medium

**无离线模式**
- Problem: 必须保持设备连接直到所有集下载完成，无法先批量捕获元数据再离线下载
- Blocks: 多设备并行、远程下载场景
- Priority: Medium

**无进度持久化**
- Problem: 脚本崩溃或手动中断后，已捕获的 URL/密钥丢失
- Blocks: 长时间运行任务的可靠性
- Priority: High

## Test Coverage Gaps

**Frida Hook 脚本无测试**
- What's not tested: `frida_hooks/*.js` 中的所有 Hook 逻辑
- Files: `frida_hooks/ttengine_all.js`, `frida_hooks/aes_hook.js`
- Risk: Hook 脚本修改后只能在真实设备上验证，无法自动化回归测试
- Priority: High

**UI 自动化边界条件**
- What's not tested: 选集面板滚动、搜索结果为空、网络超时、App 崩溃恢复
- Files: `scripts/download_drama.py` (UI 导航相关函数)
- Risk: 边界条件下行为未定义，可能死循环或静默失败
- Priority: High

**批量下载恢复路径**
- What's not tested: `duplicate_video_id` 恢复、`title_mismatch` 重试、选集面板恢复失败后的降级策略
- Files: `scripts/download_drama.py` (第 2034-2173 行)
- Risk: 恢复逻辑复杂且未测试，实际运行中可能进入未预期的状态
- Priority: High

**解密算法正确性**
- What's not tested: 不同编码器、不同分辨率、音视频轨道缺失、IV 格式变化
- Files: `scripts/decrypt_video.py`
- Risk: 仅在特定样本上验证，其他 MP4 格式可能解密失败
- Priority: Medium

**异常场景处理**
- What's not tested: 网络中断、磁盘满、ADB 断开、Frida 崩溃、App 强制更新
- Files: 所有脚本
- Risk: 生产环境中常见异常未覆盖，用户体验差
- Priority: Medium

---

*Concerns audit: 2026-04-15*
