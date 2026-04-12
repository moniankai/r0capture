## Context

当前 r0capture 项目提供了基础的安卓应用层抓包能力，但缺乏针对视频应用的专门工具链。"红果免费短剧"是一个典型的短视频应用，使用 HLS (M3U8) 流媒体协议，可能包含 AES 加密和动态 Token 验证。

**当前状态**：
- r0capture 可以捕获 SSL/TLS 流量并导出 PCAP
- 缺少自动化的视频 URL 提取和下载工具
- 需要手动使用 Wireshark 分析流量
- 没有针对 HLS 加密视频的解密方案

**约束**：
- 目标设备：小米6（已 root），安卓版本 7-12
- 仅用于个人学习和技术研究
- 需要保持对 r0capture 核心代码的最小侵入

**利益相关者**：
- 用户：学习抓包技术，批量下载短剧用于离线观看

## Goals / Non-Goals

**Goals:**
- 提供一键式环境配置脚本，自动安装 Frida Server
- 实现实时流量监控，自动识别视频请求
- 支持 HLS (M3U8/TS) 视频下载，包括 AES-128 解密
- 提供批量下载管理，支持多线程和断点续传
- 提供 Frida Hook 脚本库，用于绕过复杂加密
- 生成详细的技术文档和故障排查指南

**Non-Goals:**
- 不支持 DRM (Widevine/PlayReady) 加密视频
- 不提供 GUI 界面（命令行工具即可）
- 不支持实时流媒体录制（仅下载点播内容）
- 不提供视频格式转换（保持原始格式）
- 不绕过付费内容的商业保护机制

## Decisions

### 决策 1: 三层架构设计

**选择**：采用分层架构 - 捕获层 / 解析层 / 下载层

**理由**：
- **捕获层**（r0capture）：复用现有能力，无需重复开发
- **解析层**（Python 脚本）：独立模块，易于测试和维护
- **下载层**（Python 脚本）：可插拔设计，支持多种视频格式

**替代方案**：
- ❌ 修改 r0capture 核心代码：侵入性强，维护困难
- ❌ 使用第三方抓包工具：无法绕过 SSL Pinning

### 决策 2: 视频格式支持策略

**选择**：优先支持 HLS (M3U8/TS)，其次支持 MP4 直链

**理由**：
- 短视频应用 90% 使用 HLS 协议
- HLS 支持自适应码率，符合移动端场景
- M3U8 解析库成熟（python-m3u8）

**替代方案**：
- ❌ 支持 DASH：复杂度高，短剧应用很少使用
- ❌ 支持 RTMP：实时流协议，不适用于点播场景

### 决策 3: 加密处理方案

**选择**：分层处理 - 简单加密用脚本解密，复杂加密用 Frida Hook

**理由**：
- **AES-128 标准加密**：密钥在 M3U8 中，直接下载解密
- **自定义加密**：Hook ExoPlayer 或解密函数，导出密钥
- **DRM 加密**：明确标记为 Non-Goal，不支持

**实现**：
```python
# 简单加密：直接解密
if playlist.keys:
    key = download_key(playlist.keys[0].uri)
    decrypt_ts_segments(key, iv)

# 复杂加密：Frida Hook
frida_script = """
Java.perform(function() {
    var Cipher = Java.use('javax.crypto.Cipher');
    Cipher.doFinal.overload('[B').implementation = function(data) {
        var result = this.doFinal(data);
        send({type: 'key', data: this.getIV()});
        return result;
    };
});
"""
```

### 决策 4: 并发下载策略

**选择**：使用 ThreadPoolExecutor，默认 5 个线程

**理由**：
- 平衡下载速度和服务器压力
- 避免触发反爬虫机制
- 支持动态调整线程数

**替代方案**：
- ❌ asyncio：对于 I/O 密集型任务，线程池更简单
- ❌ 单线程：速度太慢，用户体验差

### 决策 5: 数据流设计

**选择**：实时流式处理，边抓包边下载

```
┌─────────────────────────────────────────────────────┐
│                   数据流设计                          │
├─────────────────────────────────────────────────────┤
│                                                     │
│  App 播放视频                                        │
│       │                                             │
│       ▼                                             │
│  r0capture 捕获流量 ──────► PCAP 文件               │
│       │                         │                   │
│       │                         ▼                   │
│       │                    实时解析器                │
│       │                         │                   │
│       ▼                         ▼                   │
│  实时监控模式              离线分析模式               │
│       │                         │                   │
│       └─────────┬───────────────┘                   │
│                 ▼                                   │
│           URL 提取器                                 │
│                 │                                   │
│                 ▼                                   │
│           下载队列                                   │
│                 │                                   │
│                 ▼                                   │
│           多线程下载器                               │
│                 │                                   │
│                 ▼                                   │
│           本地视频文件                               │
│                                                     │
└─────────────────────────────────────────────────────┘
```

**理由**：
- 实时模式：边播放边下载，效率高
- 离线模式：分析历史 PCAP，用于调试

### 决策 6: 错误处理和重试机制

**选择**：指数退避重试 + 失败队列

```python
@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(RequestException)
)
def download_segment(url):
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    return response.content
```

**理由**：
- 网络不稳定时自动重试
- 避免因临时错误导致整个下载失败
- 失败的任务可以手动重试

## Risks / Trade-offs

### 风险 1: Frida 被检测

**风险**：部分应用会检测 Frida 进程，导致无法注入

**缓解措施**：
- 重命名 frida-server 为系统进程名（如 `system_server`）
- 使用非标准端口（如 27043 而非 27042）
- 提供 Frida 反检测脚本

### 风险 2: 视频加密升级

**风险**：应用可能升级加密方案，导致现有方案失效

**缓解措施**：
- 提供 Frida Hook 脚本模板，便于快速适配
- 文档化逆向分析流程
- 建立加密方案数据库

### 风险 3: 动态 URL 失效

**风险**：视频 URL 包含时效性 Token，延迟下载会失败

**缓解措施**：
- 实时下载模式，捕获后立即下载
- 记录完整请求头，支持 Token 重放
- 提供 URL 有效期检测

### 风险 4: 法律和道德风险

**风险**：批量下载可能涉及版权问题

**缓解措施**：
- 明确标注"仅用于个人学习和技术研究"
- 不提供商业化功能
- 不分发下载的视频内容
- 在文档中添加法律免责声明

### Trade-off 1: 实时性 vs 稳定性

**选择**：优先稳定性

- 实时模式可能因网络波动导致丢包
- 提供离线分析模式作为备选
- 用户可以根据场景选择

### Trade-off 2: 功能完整性 vs 复杂度

**选择**：聚焦核心场景（HLS 下载）

- 不支持所有视频格式（如 DASH、RTMP）
- 不支持 DRM 加密
- 保持工具简单易用

### Trade-off 3: 性能 vs 服务器压力

**选择**：限制并发数为 5

- 避免触发反爬虫
- 牺牲部分下载速度
- 提供可配置选项

## Migration Plan

### 部署步骤

1. **环境准备**（用户手动）
   - 确认设备已 root
   - 安装 Python 3.6+
   - 安装 ADB 工具

2. **自动化安装**（脚本执行）
   ```bash
   ./setup.sh
   ```
   - 检测安卓版本
   - 下载对应 Frida Server
   - 安装 Python 依赖
   - 验证环境

3. **首次运行**
   ```bash
   python3 honguo_capture.py --setup
   ```
   - 推送 Frida Server 到设备
   - 启动 Frida Server
   - 测试连接

4. **正常使用**
   ```bash
   python3 honguo_capture.py --app "红果短剧" --output ./videos/
   ```

### 回滚策略

- 本项目为独立工具，不影响 r0capture 核心
- 如果出现问题，直接删除新增文件即可
- 保留原始 PCAP 文件，可以手动分析

### 兼容性

- 向后兼容：不修改 r0capture 核心代码
- 向前兼容：模块化设计，易于扩展新功能

## Open Questions

1. **Q: 红果短剧是否使用自定义加密协议？**
   - A: 需要实际抓包后确认，预留 Frida Hook 方案

2. **Q: 是否需要支持多设备并发抓包？**
   - A: 暂不支持，聚焦单设备场景

3. **Q: 是否需要提供 Web UI？**
   - A: 暂不需要，命令行工具足够

4. **Q: 如何处理应用更新导致的兼容性问题？**
   - A: 提供版本检测和适配指南
