"""AppAdapter 抽象接口 — 多 App 支持的架构基础

本模块定义了 AppAdapter 抽象基类，用于将短剧 App 特定的逻辑（包名、Hook 脚本、
UI 解析、选集操作）抽象为统一接口。开发者可以通过实现 AppAdapter 子类来支持
新的短剧平台（如快手、抖音等）。

典型用法：
    # 注册新的 adapter
    @register_adapter('honguo')
    class HongGuoAdapter(AppAdapter):
        app_name = 'honguo'

        def get_package_name(self) -> str:
            return 'com.phoenix.read'

        def get_hook_script(self) -> str:
            return 'frida_hooks/ttengine_all.js'

        def parse_ui_context(self, xml: str, **kwargs) -> UIContext:
            # 解析 UI XML，提取剧名、集数等信息
            ...

        def select_episode(self, ep_num: int, **kwargs) -> bool:
            # 通过 ADB UI 自动化选择指定集数
            ...

    # 使用工厂函数创建 adapter 实例
    adapter = create_adapter('honguo')
    package = adapter.get_package_name()
"""

from abc import ABC, abstractmethod
from typing import Dict, List, Type

from scripts.drama_download_common import UIContext


class AppAdapter(ABC):
    """短剧 App 适配器抽象基类

    子类需实现所有抽象方法以支持新的短剧 App。每个方法封装了 App 特定的逻辑，
    使主下载流程与具体 App 解耦。

    Attributes:
        app_name: App 标识符（如 'honguo', 'kuaishou', 'douyin'），子类需设置
    """

    app_name: str = ''

    @abstractmethod
    def get_package_name(self, **kwargs) -> str:
        """返回 App 的 Android 包名

        Args:
            **kwargs: 预留参数，用于未来扩展（如传递配置对象）

        Returns:
            Android 包名字符串（如 'com.phoenix.read'）

        Example:
            >>> adapter.get_package_name()
            'com.phoenix.read'
        """
        pass

    @abstractmethod
    def get_hook_script(self, **kwargs) -> str:
        """返回 Frida Hook 脚本的路径

        Args:
            **kwargs: 预留参数，用于未来扩展（如传递 Hook 配置）

        Returns:
            Hook 脚本相对路径（如 'frida_hooks/ttengine_all.js'）

        Example:
            >>> adapter.get_hook_script()
            'frida_hooks/ttengine_all.js'
        """
        pass

    @abstractmethod
    def parse_ui_context(self, xml: str, **kwargs) -> UIContext:
        """解析 uiautomator XML，提取剧名、集数、总集数等信息

        Args:
            xml: uiautomator dump 输出的 XML 字符串
            **kwargs: 预留参数，用于未来扩展（如传递会话状态）

        Returns:
            UIContext 数据类实例，包含提取的 UI 信息

        Raises:
            ValueError: 如果 XML 格式无效或无法提取必要信息

        Example:
            >>> xml = '<hierarchy>...</hierarchy>'
            >>> ctx = adapter.parse_ui_context(xml)
            >>> print(ctx.title, ctx.episode, ctx.total_episodes)
            '剧名' 1 80
        """
        pass

    @abstractmethod
    def select_episode(self, ep_num: int, **kwargs) -> bool:
        """在 App 内选择指定集数（通过 ADB UI 自动化）

        Args:
            ep_num: 目标集数（从 1 开始）
            **kwargs: 预留参数，用于未来扩展（如传递 ADB 连接对象、重试配置）

        Returns:
            True 表示选集成功，False 表示失败

        Example:
            >>> success = adapter.select_episode(5)
            >>> if success:
            ...     print('已切换到第 5 集')
        """
        pass


# ============================================================================
# Adapter 注册机制
# ============================================================================

_ADAPTER_REGISTRY: Dict[str, Type[AppAdapter]] = {}


def register_adapter(name: str):
    """装饰器：注册 AppAdapter 子类到全局注册表

    Args:
        name: Adapter 标识符（如 'honguo', 'kuaishou'）

    Returns:
        装饰器函数

    Raises:
        TypeError: 如果被装饰的类不是 AppAdapter 子类

    Example:
        >>> @register_adapter('honguo')
        ... class HongGuoAdapter(AppAdapter):
        ...     app_name = 'honguo'
        ...     # 实现所有抽象方法...
    """
    def decorator(cls: Type[AppAdapter]):
        if not issubclass(cls, AppAdapter):
            raise TypeError(
                f"Cannot register {cls.__name__}: must be a subclass of AppAdapter"
            )
        _ADAPTER_REGISTRY[name] = cls
        return cls
    return decorator


def create_adapter(app_name: str, **kwargs) -> AppAdapter:
    """工厂函数：根据 app_name 创建对应的 adapter 实例

    Args:
        app_name: App 标识符（如 'honguo', 'kuaishou', 'douyin'）
        **kwargs: 传递给 adapter 构造函数的参数

    Returns:
        AppAdapter 实例

    Raises:
        ValueError: 如果 app_name 不在注册表中

    Example:
        >>> adapter = create_adapter('honguo')
        >>> print(adapter.get_package_name())
        'com.phoenix.read'
    """
    if app_name not in _ADAPTER_REGISTRY:
        available = list_available_adapters()
        raise ValueError(
            f"Unknown app '{app_name}'. Available adapters: {available}"
        )

    adapter_class = _ADAPTER_REGISTRY[app_name]
    return adapter_class(**kwargs)


def list_available_adapters() -> List[str]:
    """返回已注册的 adapter 名称列表

    Returns:
        已注册的 adapter 标识符列表（按字母顺序排序）

    Example:
        >>> list_available_adapters()
        ['douyin', 'honguo', 'kuaishou']
    """
    return sorted(_ADAPTER_REGISTRY.keys())
