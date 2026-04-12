## 1. 环境配置

- [ ] 1.1 创建环境检测脚本 `scripts/check_environment.py`
- [ ] 1.2 实现安卓版本检测功能（通过 adb shell getprop）
- [ ] 1.3 实现设备架构检测（arm64/arm/x86）
- [ ] 1.4 创建 Frida Server 下载脚本 `scripts/download_frida.py`
- [ ] 1.5 实现版本映射逻辑（安卓版本 → Frida 版本）
- [ ] 1.6 创建 Frida Server 安装脚本 `scripts/install_frida.sh`
- [ ] 1.7 实现 root 权限检测
- [ ] 1.8 实现 Frida Server 启动和验证
- [ ] 1.9 创建 Python 依赖安装脚本 `requirements.txt`
- [ ] 1.10 创建一键安装脚本 `setup.sh`

## 2. 流量捕获模块

- [ ] 2.1 创建流量捕获包装脚本 `scripts/capture_wrapper.py`
- [ ] 2.2 实现应用名称到进程 ID 的映射
- [ ] 2.3 集成 r0capture.py 作为子进程
- [ ] 2.4 实现实时 PCAP 监控（使用 watchdog 库）
- [ ] 2.5 添加捕获中断处理（Ctrl+C 信号）
- [ ] 2.6 实现捕获统计信息收集
- [ ] 2.7 添加详细日志记录（使用 loguru）

## 3. PCAP 解析模块

- [ ] 3.1 创建 PCAP 解析器 `scripts/pcap_parser.py`
- [ ] 3.2 实现 PCAP 文件读取（使用 scapy）
- [ ] 3.3 实现 HTTP/HTTPS 流量提取
- [ ] 3.4 实现 M3U8 URL 正则匹配和提取
- [ ] 3.5 实现 TS 分片 URL 提取
- [ ] 3.6 实现 MP4 直链提取
- [ ] 3.7 实现 URL 去重逻辑（基于 URL hash）
- [ ] 3.8 实现请求头提取（Authorization, User-Agent, Referer）
- [ ] 3.9 实现 URL 参数提取（token, signature）
- [ ] 3.10 创建视频格式识别器
- [ ] 3.11 实现加密检测（检查 M3U8 中的 #EXT-X-KEY）
- [ ] 3.12 生成 JSON 格式的分析报告

## 4. HLS 下载模块

- [ ] 4.1 创建 HLS 下载器 `scripts/hls_downloader.py`
- [ ] 4.2 实现 M3U8 解析（使用 m3u8 库）
- [ ] 4.3 实现主播放列表解析（多码率选择）
- [ ] 4.4 实现媒体播放列表解析（TS 分片列表）
- [ ] 4.5 实现加密密钥下载
- [ ] 4.6 实现 AES-128-CBC 解密（使用 pycryptodome）
- [ ] 4.7 实现 TS 分片下载（多线程，ThreadPoolExecutor）
- [ ] 4.8 实现下载进度条（使用 tqdm）
- [ ] 4.9 实现断点续传（检查已下载分片）
- [ ] 4.10 实现下载重试机制（指数退避）
- [ ] 4.11 实现 TS 分片合并（使用 ffmpeg-python）
- [ ] 4.12 添加下载速度限制（可选）

## 5. 批量管理模块

- [ ] 5.1 创建批量管理器 `scripts/batch_manager.py`
- [ ] 5.2 实现下载队列数据结构
- [ ] 5.3 实现 URL 去重（基于 URL hash）
- [ ] 5.4 实现剧集编号识别（正则匹配）
- [ ] 5.5 实现系列文件夹自动创建
- [ ] 5.6 实现下载状态持久化（JSON 文件）
- [ ] 5.7 实现状态恢复和断点续传
- [ ] 5.8 实现失败队列管理
- [ ] 5.9 实现并发控制（限制同时下载数）
- [ ] 5.10 实现批量下载报告生成
- [ ] 5.11 实现失败列表导出

## 6. Frida Hook 脚本

- [ ] 6.1 创建 ExoPlayer Hook 脚本 `frida_hooks/exoplayer_hook.js`
- [ ] 6.2 实现 MediaSource 拦截
- [ ] 6.3 实现播放器状态监控
- [ ] 6.4 创建 AES 解密 Hook 脚本 `frida_hooks/aes_hook.js`
- [ ] 6.5 实现 javax.crypto.Cipher 拦截
- [ ] 6.6 实现密钥和 IV 导出
- [ ] 6.7 创建 OkHttp Hook 脚本 `frida_hooks/okhttp_hook.js`
- [ ] 6.8 实现请求拦截
- [ ] 6.9 实现响应拦截
- [ ] 6.10 创建 Frida 反检测脚本 `frida_hooks/anti_detection.js`
- [ ] 6.11 实现进程名检测绕过
- [ ] 6.12 实现端口检测绕过
- [ ] 6.13 创建 Hook 结果导出模块

## 7. 主程序集成

- [ ] 7.1 创建主程序入口 `honguo_capture.py`
- [ ] 7.2 实现命令行参数解析（使用 argparse）
- [ ] 7.3 集成环境检测模块
- [ ] 7.4 集成流量捕获模块
- [ ] 7.5 集成 PCAP 解析模块
- [ ] 7.6 集成 HLS 下载模块
- [ ] 7.7 集成批量管理模块
- [ ] 7.8 实现实时模式（边捕获边下载）
- [ ] 7.9 实现离线模式（分析已有 PCAP）
- [ ] 7.10 实现 Hook 模式（使用 Frida 脚本）
- [ ] 7.11 添加全局异常处理
- [ ] 7.12 实现日志系统配置

## 8. 文档编写

- [ ] 8.1 创建环境配置指南 `docs/setup_guide.md`
- [ ] 8.2 编写 Frida Server 安装步骤
- [ ] 8.3 编写 Python 环境配置步骤
- [ ] 8.4 创建使用指南 `docs/usage_guide.md`
- [ ] 8.5 编写实时抓包模式使用说明
- [ ] 8.6 编写离线分析模式使用说明
- [ ] 8.7 编写 Frida Hook 模式使用说明
- [ ] 8.8 创建故障排查指南 `docs/troubleshooting.md`
- [ ] 8.9 编写常见错误和解决方案
- [ ] 8.10 创建 FAQ 文档
- [ ] 8.11 添加法律免责声明

## 9. 测试验证

- [ ] 9.1 在小米6设备上测试环境配置
- [ ] 9.2 测试 Frida Server 安装和启动
- [ ] 9.3 测试红果短剧 App 流量捕获
- [ ] 9.4 测试 PCAP 解析和 URL 提取
- [ ] 9.5 测试无加密 HLS 视频下载
- [ ] 9.6 测试 AES-128 加密视频下载
- [ ] 9.7 测试批量下载功能
- [ ] 9.8 测试断点续传功能
- [ ] 9.9 测试 Frida Hook 脚本
- [ ] 9.10 测试错误处理和重试机制
- [ ] 9.11 编写测试报告

## 10. 优化和完善

- [ ] 10.1 优化下载速度（调整线程数）
- [ ] 10.2 优化内存使用（流式处理大文件）
- [ ] 10.3 添加下载进度持久化
- [ ] 10.4 添加视频完整性校验（MD5/SHA256）
- [ ] 10.5 优化日志输出格式
- [ ] 10.6 添加配置文件支持（YAML/JSON）
- [ ] 10.7 添加代理支持（HTTP/SOCKS5）
- [ ] 10.8 代码重构和清理
- [ ] 10.9 添加单元测试（pytest）
- [ ] 10.10 生成最终项目报告
