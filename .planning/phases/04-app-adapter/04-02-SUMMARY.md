---
phase: 04-app-adapter
plan: 02
subsystem: honguo-adapter
tags: [adapter-implementation, ui-automation, refactoring]
dependency_graph:
  requires:
    - 04-01 (AppAdapter 抽象基类)
  provides:
    - HongGuoAdapter 实现
    - UI 自动化函数库（drama_download_common）
  affects:
    - scripts/download_drama.py (导入重构后的函数)
tech_stack:
  added:
    - HongGuoAdapter (红果 App 适配器)
  patterns:
    - 委托模式 (adapter 委托给现有实现)
    - 函数迁移 (download_drama → drama_download_common)
key_files:
  created: []
  modified:
    - scripts/app_adapter.py (新增 HongGuoAdapter 类，93 行)
    - scripts/drama_download_common.py (新增 9 个 UI 自动化函数，约 280 行)
    - scripts/download_drama.py (删除已迁移函数，更新导入)
    - tests/test_app_adapter.py (新增 7 个测试用例，107 行)
decisions:
  - HongGuoAdapter 委托给现有实现，不重复编写逻辑
  - 将 UI 自动化函数迁移到 drama_download_common.py 以支持跨模块复用
  - download_drama.py 从 drama_download_common 导入函数，保持向后兼容
  - 测试使用 unittest.mock.patch 模拟 ADB 调用，避免依赖真实设备
metrics:
  duration_seconds: 524
  completed_date: "2026-04-16"
  tasks_completed: 3
  files_modified: 4
  test_coverage: 7 个新测试用例（总计 16 个）
---

# Phase 4 Plan 2: HongGuoAdapter 实现 Summary

**一句话总结**: 实现 HongGuoAdapter 并将 UI 自动化函数迁移到公共模块，为多 App 支持奠定基础。

## 实现概述

完成了 HongGuoAdapter 的实现，将红果 App 特定逻辑封装到 adapter 架构中，并重构了 UI 自动化函数以支持跨模块复用。

### Task 1: 实现 HongGuoAdapter 类

在 `scripts/app_adapter.py` 中实现了 HongGuoAdapter 类：

```python
@register_adapter('honguo')
class HongGuoAdapter(AppAdapter):
    app_name = 'honguo'

    def get_package_name(self, **kwargs) -> str:
        return 'com.phoenix.read'

    def get_hook_script(self, **kwargs) -> str:
        return 'frida_hooks/ttengine_all.js'

    def parse_ui_context(self, xml: str, **kwargs) -> UIContext:
        from scripts.drama_download_common import parse_ui_context
        return parse_ui_context(xml)

    def select_episode(self, ep_num: int, **kwargs) -> bool:
        from scripts.drama_download_common import select_episode_from_ui
        max_attempts = kwargs.get('max_attempts', 8)
        return select_episode_from_ui(ep_num, max_attempts=max_attempts)
```

**设计要点**：
- 使用 `@register_adapter('honguo')` 装饰器注册到工厂
- 所有方法委托给 `drama_download_common` 中的现有实现
- 支持 `**kwargs` 参数以便未来扩展
- `get_hook_script()` 返回文件路径，不是脚本内容

### Task 2: 重构 UI 自动化函数

将 9 个 UI 自动化函数从 `download_drama.py` 迁移到 `drama_download_common.py`：

| 函数名 | 功能 | 行数 |
|--------|------|------|
| `run_adb` | 执行 ADB 命令（Windows 环境自动设置 MSYS_NO_PATHCONV） | 4 |
| `tap_bounds` | 点击指定 bounds 的中心点 | 3 |
| `read_ui_xml_from_device` | dump 并返回手机当前 UI XML | 30 |
| `should_enter_player_from_detail` | 判断是否需要从详情页进入播放器 | 6 |
| `is_target_episode_selected_in_detail` | 确认详情页当前高亮选中的集数 | 7 |
| `tap_detail_cover_to_enter_player` | 点击详情页封面进入播放器 | 2 |
| `_find_episode_button` | 在选集网格中查找指定集数按钮 | 18 |
| `_select_episode_range` | 点击分段页签（如 "31-60"） | 24 |
| `select_episode_from_ui` | 打开选集面板并点击指定集数（主函数） | 186 |

**重构策略**：
- 保持函数签名和行为完全一致（向后兼容）
- 在 `download_drama.py` 中导入这些函数，删除原有定义
- 无循环导入问题（drama_download_common 不依赖 download_drama）

### Task 3: 添加集成测试

在 `tests/test_app_adapter.py` 中添加了 7 个 HongGuoAdapter 测试用例：

| 测试用例 | 验证内容 |
|---------|---------|
| `test_honguo_adapter_creation` | 工厂函数正确创建 HongGuoAdapter 实例 |
| `test_honguo_adapter_get_package_name` | 返回 'com.phoenix.read' |
| `test_honguo_adapter_get_hook_script` | 返回 'frida_hooks/ttengine_all.js' 且文件存在 |
| `test_honguo_adapter_parse_ui_context` | 正确解析 mock XML（剧名、集数、总集数） |
| `test_honguo_adapter_select_episode_mock` | 委托给 select_episode_from_ui，参数正确传递 |
| `test_honguo_adapter_select_episode_with_max_attempts` | 支持自定义 max_attempts 参数 |
| `test_honguo_adapter_registered` | 已注册到工厂，可通过 list_available_adapters() 查询 |

**测试结果**: 16 passed (9 个基础测试 + 7 个 HongGuoAdapter 测试)

## 使用示例

```python
from scripts.app_adapter import create_adapter

# 创建 HongGuoAdapter 实例
adapter = create_adapter('honguo')

# 获取包名和 Hook 脚本
package = adapter.get_package_name()  # 'com.phoenix.read'
hook_script = adapter.get_hook_script()  # 'frida_hooks/ttengine_all.js'

# 解析 UI XML
xml = read_ui_xml_from_device()
context = adapter.parse_ui_context(xml)
print(f"剧名: {context.title}, 集数: {context.episode}/{context.total_episodes}")

# 选择指定集数
success = adapter.select_episode(5, max_attempts=10)
if success:
    print("已切换到第 5 集")
```

## 向后兼容性

现有代码（`download_drama.py`）无需修改即可继续工作：

```python
# 原有调用方式仍然有效
from scripts.drama_download_common import select_episode_from_ui, parse_ui_context

context = parse_ui_context(xml)
success = select_episode_from_ui(5)
```

## 为 Plan 03 提供的接口

Plan 03（集成到主流程）可以使用以下方式：

```python
# 在 download_drama.py 的 main() 函数中
adapter = create_adapter('honguo')

# 替换硬编码的 APP_PACKAGE
app_package = adapter.get_package_name()

# 替换硬编码的 Hook 脚本路径
hook_script_path = adapter.get_hook_script()

# 使用 adapter 的 UI 解析和选集方法
context = adapter.parse_ui_context(xml)
success = adapter.select_episode(ep_num)
```

## Deviations from Plan

无偏差 — 计划按原定方案执行。所有任务完成，测试全部通过。

## Self-Check: PASSED

验证结果：

```bash
# 1. HongGuoAdapter 可创建
✓ Package: com.phoenix.read

# 2. select_episode_from_ui 可导入
✓ Function imported successfully

# 3. 集成测试通过
✓ 7 passed, 9 deselected in 0.04s

# 4. download_drama.py 无循环导入
✓ download_drama.py imports successfully

# 5. Commits 存在
✓ 191e476 (feat(04-02): 实现 HongGuoAdapter 并重构 UI 自动化函数)
✓ 84fd80b (test(04-02): 添加 HongGuoAdapter 集成测试)
```

所有检查项通过。
