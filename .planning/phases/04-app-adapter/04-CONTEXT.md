# Phase 4: AppAdapter 抽象 — 实现上下文

**生成时间**: 2026-04-15
**模式**: Auto

## 阶段目标

建立多 App 支持的架构基础，为扩展到快手、抖音等平台做准备。

## 成功标准

1. 开发者可以通过实现 AppAdapter 接口来支持新的短剧 App
2. 用户可以通过配置文件选择目标 App，无需修改代码
3. 红果 App 的所有现有功能在新架构下仍正常工作（向后兼容）

## Phase 1-3 成果回顾

- ✓ 核心稳定性修复
- ✓ Hook 数据校验增强
- ✓ 错误恢复机制改进

## 实现决策

### 决策 1: AppAdapter 接口设计

**具体实现**：
```python
class AppAdapter(ABC):
    @abstractmethod
    def get_package_name(self) -> str:
        pass
    
    @abstractmethod
    def get_hook_script(self) -> str:
        pass
    
    @abstractmethod
    def parse_ui_context(self, xml: str) -> UIContext:
        pass
    
    @abstractmethod
    def select_episode(self, ep_num: int) -> bool:
        pass
```

### 决策 2: 配置文件格式

**具体实现**：
```yaml
# config.yaml
app: honguo  # 或 kuaishou, douyin

apps:
  honguo:
    package: com.phoenix.read
    hook_script: frida_hooks/ttengine_all.js
  kuaishou:
    package: com.kuaishou.nebula
    hook_script: frida_hooks/kuaishou_hook.js
```

### 决策 3: 红果 App 适配器

**具体实现**：
1. 创建 `HongGuoAdapter` 类
2. 将现有逻辑迁移到 adapter 方法中
3. 保持向后兼容（默认使用 HongGuoAdapter）

## 交付物

1. `scripts/app_adapter.py` — AppAdapter 接口和 HongGuoAdapter 实现
2. `config.yaml` — 配置文件模板
3. `scripts/download_drama.py` — 集成 AppAdapter
4. `tests/test_app_adapter.py` — 新增测试

## 下游 Agent 指引

### 给 gsd-planner
- 优先定义接口（风险低，影响大）
- HongGuoAdapter 实现依赖接口定义
- 配置文件加载可以并行
