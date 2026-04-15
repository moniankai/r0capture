# 测试模式

**分析日期:** 2026-04-15

## 测试框架

**Runner:**
- pytest 9.0.2
- 配置: 无显式配置文件（未检测到 pytest.ini、setup.cfg、pyproject.toml）

**断言库:**
- unittest.TestCase（标准库）

**运行命令:**
```bash
pytest tests/                                    # 运行所有测试
pytest tests/test_download_drama.py -v           # 运行单个测试文件（详细输出）
pytest tests/test_audit_drama_downloads.py -v    # 运行审计测试
python -m unittest tests.test_download_drama     # 使用 unittest 运行
```

## 测试文件组织

**位置:**
- 集中式测试目录：`tests/`
- 测试文件与源文件分离（非 co-located）

**命名:**
- 测试文件使用 `test_` 前缀：`test_download_drama.py`、`test_audit_drama_downloads.py`
- 测试类使用 `Tests` 后缀：`ParseUiContextTests`、`FileNamingTests`、`SessionValidationTests`
- 测试方法使用 `test_` 前缀：`test_parse_ui_context_extracts_title_episode_total()`

**结构:**
```
tests/
├── __init__.py                      # 空文件
├── test_download_drama.py           # 主下载器测试（633 行，51 个测试）
└── test_audit_drama_downloads.py    # 审计工具测试（156 行）
```

## 测试结构

**Suite 组织:**
```python
import unittest
from unittest.mock import Mock, patch

class ParseUiContextTests(unittest.TestCase):
    def test_parse_ui_context_extracts_title_episode_total(self):
        ctx = parse_ui_context(SAMPLE_UI_XML)
        self.assertEqual(ctx.title, '爹且慢，我来了')
        self.assertEqual(ctx.episode, 3)
        self.assertEqual(ctx.total_episodes, 60)

    def test_parse_ui_context_handles_alt_total_format(self):
        ctx = parse_ui_context(ALT_TOTAL_UI_XML)
        self.assertEqual(ctx.title, '十八岁太奶奶驾到，重整家族荣耀第三部')
        self.assertEqual(ctx.total_episodes, 84)

if __name__ == '__main__':
    unittest.main()
```

**模式:**
- 每个测试类专注于一个功能模块：UI 解析、文件命名、会话校验、批量导航等
- 测试方法名描述测试场景：`test_<function>_<scenario>`
- 使用 `self.assertEqual()`、`self.assertTrue()`、`self.assertFalse()` 等断言方法
- 每个测试方法包含多个相关断言（非严格的"一个断言"原则）

## Mock 使用

**框架:** unittest.mock

**模式:**
```python
from unittest.mock import Mock, patch

class FridaDeviceTests(unittest.TestCase):
    def test_select_running_app_pid_returns_first_match(self):
        processes = [
            Mock(pid=1234, name='com.phoenix.read'),
            Mock(pid=5678, name='com.phoenix.read'),
        ]
        pid = download_drama.select_running_app_pid(processes, 'com.phoenix.read')
        self.assertEqual(pid, 1234)

    @patch('scripts.download_drama.frida.get_usb_device')
    def test_frida_connection_with_mock(self, mock_get_device):
        mock_device = Mock()
        mock_get_device.return_value = mock_device
        # 测试逻辑
```

**Mock 对象:**
- 使用 `Mock()` 创建简单 mock 对象
- 使用 `Mock(pid=1234, name='...')` 设置属性
- 使用 `@patch()` 装饰器 mock 外部依赖（Frida、subprocess）

**Mock 范围:**
- Mock Frida 设备和进程对象
- Mock subprocess 调用（ADB 命令）
- Mock 文件系统操作（使用 `tempfile.TemporaryDirectory`）
- 不 mock 核心业务逻辑（UI 解析、文件命名、会话校验）

## 测试数据与 Fixtures

**测试数据:**
```python
SAMPLE_UI_XML = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node text="爹且慢，我来了" resource-id="com.phoenix.read:id/d4" />
  <node text="第3集" resource-id="com.phoenix.read:id/jjj" />
  <node text=" · 已完结 · 全60集" resource-id="com.phoenix.read:id/jr1" />
</hierarchy>
"""

ALT_TOTAL_UI_XML = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node text="十八岁太奶奶驾到，重整家族荣耀第三部" resource-id="com.phoenix.read:id/d4" />
  <node text="已完结 共84集" />
</hierarchy>
"""
```

**模式:**
- 测试数据定义为模块级常量（UPPER_SNAKE_CASE）
- 使用真实 XML 结构模拟 Android UI 层次
- 使用 `tempfile.TemporaryDirectory()` 创建临时文件系统
- 使用 `Path.write_text()` 和 `Path.write_bytes()` 创建测试文件

**Fixture 位置:**
- 无独立 fixtures 目录
- 测试数据内联在测试文件中

**示例:**
```python
def test_analyze_drama_directory_reports_missing_episodes(self):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / '十八岁太奶奶驾到，重整家族荣耀第三部'
        root.mkdir()
        
        (root / 'episode_001.mp4').write_bytes(b'video-1')
        (root / 'meta_ep001.json').write_text(
            json.dumps({
                'drama': '爹且慢，我来了',
                'episode': 1,
                'video_id': 'abcdef1234567890',
                'ui_total_episodes': 3,
            }, ensure_ascii=False, indent=2),
            encoding='utf-8',
        )
        
        report = analyze_drama_directory(root)
        
        self.assertEqual(report['missing_episodes'], [2, 3])
```

## 覆盖率

**要求:** 无强制覆盖率目标

**查看覆盖率:**
```bash
pytest --cov=scripts --cov-report=html tests/
# 或
coverage run -m pytest tests/
coverage report
coverage html
```

**当前状态:**
- 核心模块有测试覆盖：`download_drama.py`、`audit_drama_downloads.py`、`drama_download_common.py`
- 工具模块部分覆盖：`decrypt_video.py`、`batch_manager.py`
- Frida Hook 脚本无自动化测试（JavaScript）

## 测试类型

**单元测试:**
- 范围：纯函数、数据解析、文件命名、会话校验
- 方法：隔离测试，使用 mock 替代外部依赖
- 示例：
  ```python
  def test_sanitize_drama_name_replaces_invalid_chars(self):
      result = sanitize_drama_name('剧名<>:"/\\|?*测试')
      self.assertEqual(result, '剧名_________测试')
  
  def test_build_episode_base_name_formats_correctly(self):
      result = build_episode_base_name(5, 'v02ebeg10000d3stuavog65u8i75lvc0')
      self.assertEqual(result, 'episode_005_8i75lvc0')
  ```

**集成测试:**
- 范围：完整工作流、CLI 调用、文件系统操作
- 方法：使用临时目录，调用真实函数，验证输出文件
- 示例：
  ```python
  def test_cli_can_run_via_script_path(self):
      repo_root = Path(__file__).resolve().parents[1]
      with tempfile.TemporaryDirectory() as tmp:
          root = Path(tmp) / 'sample'
          root.mkdir()
          # 创建测试文件
          
          result = subprocess.run(
              [sys.executable, 'scripts/audit_drama_downloads.py', str(root)],
              cwd=repo_root,
              capture_output=True,
              text=True,
          )
          
          self.assertEqual(result.returncode, 0, result.stderr)
          self.assertIn('"expected_total_episodes": 1', result.stdout)
  ```

**E2E 测试:**
- 框架：无自动化 E2E 测试
- 方法：手动测试（在真实设备上运行 Frida Hook）

## 常用模式

**异步测试:**
- 不适用（项目未使用 asyncio）

**错误测试:**
```python
def test_parse_ui_context_handles_malformed_xml(self):
    ctx = parse_ui_context('<invalid xml')
    self.assertEqual(ctx.title, '')
    self.assertIsNone(ctx.episode)

def test_validate_round_rejects_duplicate_video_id(self):
    state = SessionValidationState(seen_video_ids={'vid123'})
    result = validate_round(state, 'vid123', 2, '剧名')
    self.assertFalse(result)
```

**参数化测试:**
- 无显式参数化框架（未使用 pytest.mark.parametrize）
- 通过多个测试方法覆盖不同场景

**测试隔离:**
- 使用 `tempfile.TemporaryDirectory()` 确保文件系统隔离
- 使用 `Mock()` 隔离外部依赖
- 每个测试方法独立运行，无共享状态

## 测试命名约定

**描述性命名:**
- `test_parse_ui_context_extracts_title_episode_total` — 描述功能和预期结果
- `test_analyze_drama_directory_reports_missing_mismatched_and_rename_targets` — 描述复杂场景
- `test_cli_can_run_via_script_path` — 描述集成测试场景
- `test_metadata_title_mismatch_does_not_imply_folder_mismatch` — 描述边界条件

**模式:**
- `test_<function_name>_<scenario>_<expected_result>`
- 使用完整单词，避免缩写
- 使用下划线分隔，保持可读性

## 测试数据管理

**内联数据:**
- XML 测试数据定义为模块级字符串常量
- JSON 数据使用 `json.dumps()` 动态生成
- 二进制数据使用 `b'video-1'` 等占位符

**临时文件:**
```python
with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp) / 'drama_name'
    root.mkdir()
    (root / 'episode_001.mp4').write_bytes(b'video-1')
    (root / 'meta_ep001.json').write_text(json.dumps({...}), encoding='utf-8')
    # 执行测试
```

**真实数据:**
- 不使用真实视频文件（使用占位符字节）
- 不使用真实 API 响应（使用 mock）
- 使用真实 XML 结构（从 Android UI 提取）

## 测试组织策略

**按功能分组:**
- `ParseUiContextTests` — UI 解析逻辑
- `FileNamingTests` — 文件命名规则
- `SessionValidationTests` — 会话校验逻辑
- `BatchNavigationStrategyTests` — 批量导航策略
- `AuditDramaDownloadsTests` — 审计工具

**测试数量:**
- `test_download_drama.py`：51 个测试方法
- `test_audit_drama_downloads.py`：5 个测试方法
- 总计：56 个测试

**覆盖范围:**
- 核心业务逻辑：✓ 完整覆盖
- UI 解析：✓ 多场景覆盖
- 文件操作：✓ 集成测试覆盖
- Frida Hook：✗ 无自动化测试
- 视频解密：△ 部分覆盖

## 持续集成

**CI 配置:**
- 无检测到 CI 配置文件（.github/workflows、.gitlab-ci.yml 等）

**本地运行:**
```bash
# 运行所有测试
pytest tests/

# 运行特定测试类
pytest tests/test_download_drama.py::ParseUiContextTests -v

# 运行特定测试方法
pytest tests/test_download_drama.py::ParseUiContextTests::test_parse_ui_context_extracts_title_episode_total -v

# 生成覆盖率报告
pytest --cov=scripts --cov-report=term-missing tests/
```

## 测试最佳实践

**遵循的模式:**
- 测试名称清晰描述意图
- 使用临时目录隔离文件系统操作
- Mock 外部依赖（Frida、ADB）
- 测试真实业务逻辑（不 mock 核心函数）
- 使用真实数据结构（XML、JSON）

**改进空间:**
- 添加覆盖率目标和 CI 集成
- 参数化测试减少重复代码
- 添加 Frida Hook 脚本的 JavaScript 测试
- 添加视频解密的端到端测试
- 使用 pytest fixtures 替代模块级常量

---

*测试分析: 2026-04-15*
