---
phase: 04-app-adapter
plan: 01
subsystem: adapter-framework
tags: [architecture, abstraction, multi-app]
dependency_graph:
  requires: []
  provides:
    - AppAdapter 抽象基类
    - create_adapter 工厂函数
    - register_adapter 装饰器
  affects:
    - scripts/download_drama.py (未来集成)
tech_stack:
  added:
    - abc.ABC (抽象基类)
    - typing (类型注解)
  patterns:
    - 工厂模式 (create_adapter)
    - 注册表模式 (装饰器注册)
    - 抽象基类模式 (ABC)
key_files:
  created:
    - scripts/app_adapter.py (196 行)
    - tests/test_app_adapter.py (246 行)
  modified: []
decisions:
  - 使用 ABC 和 @abstractmethod 强制子类实现所有方法
  - 所有方法预留 **kwargs 参数支持未来扩展
  - 工厂函数在 app_name 不存在时抛出 ValueError 并列出可用 adapter
  - 装饰器检查被装饰类是否为 AppAdapter 子类，防止注册无效类型
metrics:
  duration_seconds: 119
  completed_date: "2026-04-16"
  tasks_completed: 3
  files_created: 2
  test_coverage: 9 个测试用例
---

# Phase 4 Plan 1: AppAdapter 接口定义 Summary

**一句话总结**: 定义 AppAdapter 抽象基类和工厂函数，为多 App 支持奠定架构基础。

## 实现概述

创建了 `scripts/app_adapter.py` 模块，包含：

1. **AppAdapter 抽象基类** — 定义 4 个抽象方法：
   - `get_package_name() -> str` — 返回 App 包名
   - `get_hook_script() -> str` — 返回 Frida Hook 脚本路径
   - `parse_ui_context(xml: str) -> UIContext` — 解析 UI XML
   - `select_episode(ep_num: int) -> bool` — 选择指定集数

2. **注册机制** — 装饰器 + 注册表模式：
   - `@register_adapter(name: str)` 装饰器用于注册新 adapter
   - `_ADAPTER_REGISTRY` 字典存储已注册的 adapter 类
   - 装饰器检查被装饰类是否为 AppAdapter 子类

3. **工厂函数** — 根据 app_name 创建实例：
   - `create_adapter(app_name: str) -> AppAdapter`
   - 未知 app_name 抛出 ValueError，错误信息包含可用 adapter 列表
   - `list_available_adapters() -> List[str]` 返回已注册的 adapter

## 接口方法签名

```python
class AppAdapter(ABC):
    app_name: str = ''

    @abstractmethod
    def get_package_name(self, **kwargs) -> str:
        """返回 App 的 Android 包名"""
        pass

    @abstractmethod
    def get_hook_script(self, **kwargs) -> str:
        """返回 Frida Hook 脚本的路径"""
        pass

    @abstractmethod
    def parse_ui_context(self, xml: str, **kwargs) -> UIContext:
        """解析 uiautomator XML，提取剧名、集数、总集数等信息"""
        pass

    @abstractmethod
    def select_episode(self, ep_num: int, **kwargs) -> bool:
        """在 App 内选择指定集数（通过 ADB UI 自动化）"""
        pass
```

## 使用示例

```python
from scripts.app_adapter import AppAdapter, register_adapter, create_adapter

# 1. 注册新的 adapter
@register_adapter('honguo')
class HongGuoAdapter(AppAdapter):
    app_name = 'honguo'

    def get_package_name(self, **kwargs) -> str:
        return 'com.phoenix.read'

    def get_hook_script(self, **kwargs) -> str:
        return 'frida_hooks/ttengine_all.js'

    def parse_ui_context(self, xml: str, **kwargs) -> UIContext:
        # 解析红果 App 的 UI XML
        ...

    def select_episode(self, ep_num: int, **kwargs) -> bool:
        # 通过 ADB 在红果 App 中选择集数
        ...

# 2. 使用工厂函数创建实例
adapter = create_adapter('honguo')
package = adapter.get_package_name()  # 'com.phoenix.read'
hook_script = adapter.get_hook_script()  # 'frida_hooks/ttengine_all.js'

# 3. 查看可用的 adapter
from scripts.app_adapter import list_available_adapters
print(list_available_adapters())  # ['honguo']
```

## 测试覆盖

创建了 `tests/test_app_adapter.py`，包含 9 个测试用例：

| 测试类 | 测试用例 | 验证内容 |
|--------|---------|---------|
| TestAppAdapterAbstract | test_app_adapter_is_abstract | AppAdapter 不能直接实例化 |
| TestAppAdapterAbstract | test_adapter_subclass_must_implement_all_methods | 未实现所有抽象方法的子类无法实例化 |
| TestAdapterRegistry | test_register_adapter_decorator | 装饰器正确注册 adapter |
| TestAdapterRegistry | test_register_non_adapter_class_raises_error | 注册非 AppAdapter 子类时抛出 TypeError |
| TestAdapterRegistry | test_list_available_adapters | 返回已注册的 adapter 列表（按字母顺序） |
| TestCreateAdapter | test_create_adapter_success | 工厂函数正确实例化 adapter |
| TestCreateAdapter | test_create_adapter_unknown_app | 未知 app_name 抛出 ValueError |
| TestCreateAdapter | test_create_adapter_empty_registry | 注册表为空时抛出 ValueError |
| TestAdapterInterface | test_adapter_methods_accept_kwargs | 所有方法支持 **kwargs 参数 |

**测试结果**: 9 passed in 0.03s

## 威胁缓解

根据 PLAN.md 中的威胁模型，实现了以下缓解措施：

| 威胁 ID | 缓解措施 | 实现位置 |
|---------|---------|---------|
| T-04-01 | 验证 app_name 在白名单中（注册表），拒绝未注册的值 | `create_adapter()` 函数 |
| T-04-02 | 装饰器检查被装饰类是否为 AppAdapter 子类 | `register_adapter()` 装饰器 |

## 为 Plan 02 提供的接口

Plan 02（HongGuoAdapter 实现）需要：

1. **导入 AppAdapter 和装饰器**:
   ```python
   from scripts.app_adapter import AppAdapter, register_adapter
   from scripts.drama_download_common import UIContext
   ```

2. **实现 HongGuoAdapter 类**:
   ```python
   @register_adapter('honguo')
   class HongGuoAdapter(AppAdapter):
       app_name = 'honguo'
       # 实现 4 个抽象方法...
   ```

3. **复用现有的 UI 解析函数**:
   - `find_text_bounds()`, `find_element_by_class()` 等辅助函数
   - 现有的 `parse_ui_context()` 实现（从 drama_download_common.py 迁移）

4. **实现选集逻辑**:
   - 将现有的 ADB UI 自动化代码封装到 `select_episode()` 方法中

## Deviations from Plan

无偏差 — 计划按原定方案执行。

## Self-Check: PASSED

验证结果：

```bash
# 1. 文件存在
FOUND: scripts/app_adapter.py
FOUND: tests/test_app_adapter.py

# 2. AppAdapter 是抽象类
PASS: AppAdapter is abstract

# 3. 测试通过
9 passed in 0.03s

# 4. 工厂函数可用
PASS: Factory functions available
Available adapters: []

# 5. Commits 存在
FOUND: 268cf8d (feat(04-01): 定义 AppAdapter 抽象基类)
FOUND: 8fa79e5 (test(04-01): 添加 AppAdapter 单元测试)
```

所有检查项通过。
