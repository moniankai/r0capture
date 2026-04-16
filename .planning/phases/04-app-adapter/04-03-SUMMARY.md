---
phase: 04-app-adapter
plan: 03
subsystem: config-integration
tags: [configuration, integration, backward-compatibility]
dependency_graph:
  requires:
    - 04-01 (AppAdapter 抽象基类)
    - 04-02 (HongGuoAdapter 实现)
  provides:
    - config.yaml 配置文件
    - download_drama.py 使用 adapter 架构
    - 配置化的 App 选择能力
  affects:
    - scripts/download_drama.py (集成 adapter)
    - tests/test_download_drama.py (新增 7 个测试)
tech_stack:
  added:
    - PyYAML (配置文件解析)
    - 全局 adapter 单例模式
  patterns:
    - 配置驱动架构 (config.yaml → adapter)
    - 单例模式 (get_adapter/set_adapter)
    - 向后兼容设计 (默认配置)
key_files:
  created:
    - config.yaml (43 行配置文件模板)
  modified:
    - scripts/download_drama.py (新增配置加载和 adapter 集成，70 行变更)
    - tests/test_download_drama.py (新增 7 个集成测试，88 行)
decisions:
  - 配置文件不存在时使用默认值 {"app": "honguo"}，保持向后兼容
  - 使用全局单例模式管理 adapter 实例（与 CaptureState 模式一致）
  - 所有 APP_PACKAGE 使用替换为 adapter.get_package_name()
  - 所有 parse_ui_context 和 select_episode_from_ui 调用替换为 adapter 方法
  - 配置文件使用 UTF-8 编码以支持中文注释
metrics:
  duration_seconds: 338
  completed_date: "2026-04-16"
  tasks_completed: 3
  files_modified: 3
  test_coverage: 7 个新测试（总计 70 个，全部通过）
---

# Phase 4 Plan 3: 配置文件加载和集成 Summary

**一句话总结**: 创建 config.yaml 配置文件并将 AppAdapter 集成到 download_drama.py 主流程，实现配置化的 App 选择。

## 实现概述

完成了 AppAdapter 架构的最终集成，用户现在可以通过配置文件选择目标 App，download_drama.py 使用 adapter 架构而非硬编码的红果逻辑。

### Task 1: 创建配置文件模板

创建了 `config.yaml` 配置文件（43 行）：

```yaml
# 红果短剧下载器配置文件
app: honguo

apps:
  honguo:
    package: com.phoenix.read
    hook_script: frida_hooks/ttengine_all.js
  # kuaishou: ...
  # douyin: ...

download:
  quality: 720p
  max_retries: 3
  output_dir: videos

frida:
  version: 16.5.9
  timeout: 10
```

**设计要点**：
- 顶层 `app` 字段指定当前使用的 App（默认 honguo）
- `apps` 字段包含所有支持的 App 配置（包名、Hook 脚本）
- 预留未来扩展字段（ui_selectors、download、frida 配置）
- 使用 UTF-8 编码支持中文注释

### Task 2: 集成 adapter 到 download_drama.py

在 `scripts/download_drama.py` 中实现了完整的 adapter 集成（70 行变更）：

**1. 添加配置加载函数**：

```python
def load_config(config_path: str = "config.yaml") -> dict:
    """加载配置文件，不存在时返回默认配置"""
    path = Path(config_path)
    if not path.exists():
        logger.warning(f"配置文件 {config_path} 不存在，使用默认配置")
        return {"app": "honguo"}  # 向后兼容
    
    with open(path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)
```

**2. 添加全局 adapter 管理**：

```python
_adapter_instance: Optional[AppAdapter] = None

def get_adapter() -> AppAdapter:
    """获取全局 adapter 实例"""
    if _adapter_instance is None:
        raise RuntimeError("Adapter not initialized. Call set_adapter() first.")
    return _adapter_instance

def set_adapter(adapter: AppAdapter):
    """设置全局 adapter 实例"""
    global _adapter_instance
    _adapter_instance = adapter
```

**3. 在 main() 中初始化 adapter**：

```python
def main() -> None:
    # 加载配置并创建 adapter
    config = load_config()
    app_name = config.get('app', 'honguo')
    adapter = create_adapter(app_name)
    set_adapter(adapter)
    logger.info(f"使用 {app_name} adapter (包名: {adapter.get_package_name()})")
    
    # 原有的参数解析逻辑...
```

**4. 替换硬编码逻辑**：

| 原有代码 | 替换为 |
|---------|--------|
| `APP_PACKAGE` | `adapter.get_package_name()` |
| `parse_ui_context(xml)` | `get_adapter().parse_ui_context(xml)` |
| `select_episode_from_ui(ep_num)` | `get_adapter().select_episode(ep_num)` |

**替换位置**：
- Frida 注入时的包名参数（spawn、attach）
- ADB 命令中的包名（force-stop、am start）
- UI 解析调用（detect_ui_context_from_device）
- 选集调用（_try_start_episode_on_drama_page、search_drama_in_app、try_player_panel_recovery）

### Task 3: 添加集成测试

在 `tests/test_download_drama.py` 中新增 7 个测试用例（88 行）：

| 测试用例 | 验证内容 |
|---------|---------|
| `test_load_config_success` | 配置文件加载成功，返回正确的配置字典 |
| `test_load_config_missing_file` | 配置文件不存在时返回默认配置 `{"app": "honguo"}` |
| `test_adapter_initialization` | adapter 初始化和全局访问（set_adapter/get_adapter） |
| `test_get_adapter_before_initialization_raises_error` | 未初始化时访问 adapter 抛出 RuntimeError |
| `test_adapter_parse_ui_context_integration` | adapter.parse_ui_context 正确解析 UI XML |
| `test_adapter_select_episode_integration` | adapter.select_episode 正确委托给底层实现 |
| `test_backward_compatibility_without_config` | 无配置文件时默认使用 honguo adapter |

**测试结果**: 70 passed (63 个现有 + 7 个新增)

## 使用示例

### 基本用法（使用默认配置）

```bash
# 配置文件不存在时，自动使用 honguo adapter
python scripts/download_drama.py -n "剧名" --search -e 1 -b 10
```

### 自定义配置

编辑 `config.yaml`：

```yaml
app: honguo  # 或 kuaishou、douyin（未来支持）

apps:
  honguo:
    package: com.phoenix.read
    hook_script: frida_hooks/ttengine_all.js
```

### 添加新的 App adapter

1. 在 `scripts/app_adapter.py` 中实现新的 adapter 类：

```python
@register_adapter('kuaishou')
class KuaiShouAdapter(AppAdapter):
    app_name = 'kuaishou'
    
    def get_package_name(self, **kwargs) -> str:
        return 'com.kuaishou.nebula'
    
    def get_hook_script(self, **kwargs) -> str:
        return 'frida_hooks/kuaishou_hook.js'
    
    def parse_ui_context(self, xml: str, **kwargs) -> UIContext:
        # 实现快手 App 的 UI 解析逻辑
        ...
    
    def select_episode(self, ep_num: int, **kwargs) -> bool:
        # 实现快手 App 的选集逻辑
        ...
```

2. 在 `config.yaml` 中添加配置：

```yaml
app: kuaishou

apps:
  kuaishou:
    package: com.kuaishou.nebula
    hook_script: frida_hooks/kuaishou_hook.js
```

3. 运行下载器（无需修改 download_drama.py）：

```bash
python scripts/download_drama.py -n "剧名" --search
```

## 向后兼容性

### 无配置文件场景

```python
# config.yaml 不存在
config = load_config()  # 返回 {"app": "honguo"}
adapter = create_adapter(config['app'])  # 创建 HongGuoAdapter
```

### 现有功能不受影响

所有现有功能在新架构下正常工作：
- Frida Hook 注入（使用 adapter.get_package_name()）
- UI 解析（使用 adapter.parse_ui_context()）
- 选集操作（使用 adapter.select_episode()）
- 搜索功能（使用 adapter 方法）
- 批量下载（使用 adapter 方法）

### 测试验证

70 个测试全部通过，包括：
- 63 个现有测试（验证向后兼容）
- 7 个新增测试（验证 adapter 集成）

## 架构优势

### 1. 可扩展性

添加新 App 支持只需：
- 实现 AppAdapter 子类（4 个方法）
- 在 config.yaml 中添加配置
- 无需修改 download_drama.py 主流程

### 2. 配置驱动

用户可以通过配置文件切换目标 App，无需修改代码：

```yaml
app: honguo  # 切换到 kuaishou 只需修改这一行
```

### 3. 向后兼容

配置文件不存在时自动使用默认配置，现有用户无需任何修改即可继续使用。

### 4. 测试友好

全局 adapter 单例模式支持测试注入：

```python
# 测试中可以注入 mock adapter
mock_adapter = Mock(spec=AppAdapter)
set_adapter(mock_adapter)
```

## 威胁缓解

根据 PLAN.md 中的威胁模型，实现了以下缓解措施：

| 威胁 ID | 缓解措施 | 实现位置 |
|---------|---------|---------|
| T-04-07 | 使用 yaml.safe_load（不执行任意代码） | `load_config()` |
| T-04-08 | 捕获异常并返回默认配置 | `load_config()` 的 try-except |
| T-04-09 | 工厂函数验证 app_name 在注册表中 | `create_adapter()` |

## Deviations from Plan

无偏差 — 计划按原定方案执行。所有任务完成，测试全部通过。

## Self-Check: PASSED

验证结果：

```bash
# 1. 配置文件存在且可解析
✓ App: honguo

# 2. download_drama.py 可导入
✓ Functions imported successfully

# 3. adapter 集成成功
✓ Adapter integrated successfully

# 4. 集成测试通过
✓ 7 passed in 0.18s

# 5. 所有测试通过（向后兼容）
✓ 70 passed in 0.20s

# 6. Commits 存在
✓ 4945ef2 (feat(04-03): 创建配置文件模板)
✓ 2e29c45 (feat(04-03): 集成 AppAdapter 到 download_drama.py 主流程)
✓ d162402 (test(04-03): 添加 AppAdapter 集成测试)
```

所有检查项通过。
