"""AppAdapter 接口和工厂函数的单元测试"""

import unittest
from typing import Optional

from scripts.app_adapter import (
    AppAdapter,
    create_adapter,
    list_available_adapters,
    register_adapter,
    _ADAPTER_REGISTRY,
)
from scripts.drama_download_common import UIContext


class TestAppAdapterAbstract(unittest.TestCase):
    """测试 AppAdapter 抽象类特性"""

    def test_app_adapter_is_abstract(self):
        """验证 AppAdapter 不能直接实例化"""
        with self.assertRaises(TypeError) as ctx:
            AppAdapter()

        # 错误信息应提示缺少抽象方法实现
        error_msg = str(ctx.exception)
        self.assertIn("abstract", error_msg.lower())

    def test_adapter_subclass_must_implement_all_methods(self):
        """验证未实现所有抽象方法的子类无法实例化"""

        # 只实现部分方法的子类
        class IncompleteAdapter(AppAdapter):
            app_name = 'incomplete'

            def get_package_name(self, **kwargs) -> str:
                return 'com.example.incomplete'

            # 缺少其他三个抽象方法

        with self.assertRaises(TypeError) as ctx:
            IncompleteAdapter()

        error_msg = str(ctx.exception)
        self.assertIn("abstract", error_msg.lower())


class TestAdapterRegistry(unittest.TestCase):
    """测试 adapter 注册机制"""

    def setUp(self):
        """每个测试前清空注册表"""
        _ADAPTER_REGISTRY.clear()

    def tearDown(self):
        """每个测试后清空注册表"""
        _ADAPTER_REGISTRY.clear()

    def test_register_adapter_decorator(self):
        """验证装饰器正确注册 adapter"""

        @register_adapter('test_app')
        class TestAdapter(AppAdapter):
            app_name = 'test_app'

            def get_package_name(self, **kwargs) -> str:
                return 'com.example.test'

            def get_hook_script(self, **kwargs) -> str:
                return 'hooks/test.js'

            def parse_ui_context(self, xml: str, **kwargs) -> UIContext:
                return UIContext(title='Test Drama', episode=1, total_episodes=10)

            def select_episode(self, ep_num: int, **kwargs) -> bool:
                return True

        # 验证已注册
        self.assertIn('test_app', _ADAPTER_REGISTRY)
        self.assertEqual(_ADAPTER_REGISTRY['test_app'], TestAdapter)

    def test_register_non_adapter_class_raises_error(self):
        """验证注册非 AppAdapter 子类时抛出 TypeError"""

        with self.assertRaises(TypeError) as ctx:
            @register_adapter('invalid')
            class NotAnAdapter:
                pass

        error_msg = str(ctx.exception)
        self.assertIn("AppAdapter", error_msg)

    def test_list_available_adapters(self):
        """验证返回已注册的 adapter 列表"""

        # 注册多个 adapter
        @register_adapter('app_a')
        class AdapterA(AppAdapter):
            app_name = 'app_a'
            def get_package_name(self, **kwargs) -> str: return 'com.a'
            def get_hook_script(self, **kwargs) -> str: return 'a.js'
            def parse_ui_context(self, xml: str, **kwargs) -> UIContext: return UIContext()
            def select_episode(self, ep_num: int, **kwargs) -> bool: return True

        @register_adapter('app_b')
        class AdapterB(AppAdapter):
            app_name = 'app_b'
            def get_package_name(self, **kwargs) -> str: return 'com.b'
            def get_hook_script(self, **kwargs) -> str: return 'b.js'
            def parse_ui_context(self, xml: str, **kwargs) -> UIContext: return UIContext()
            def select_episode(self, ep_num: int, **kwargs) -> bool: return True

        available = list_available_adapters()
        self.assertEqual(available, ['app_a', 'app_b'])  # 按字母顺序排序


class TestCreateAdapter(unittest.TestCase):
    """测试工厂函数 create_adapter"""

    def setUp(self):
        """每个测试前清空注册表"""
        _ADAPTER_REGISTRY.clear()

    def tearDown(self):
        """每个测试后清空注册表"""
        _ADAPTER_REGISTRY.clear()

    def test_create_adapter_success(self):
        """验证工厂函数能正确实例化 adapter"""

        @register_adapter('mock_app')
        class MockAdapter(AppAdapter):
            app_name = 'mock_app'

            def get_package_name(self, **kwargs) -> str:
                return 'com.example.mock'

            def get_hook_script(self, **kwargs) -> str:
                return 'hooks/mock.js'

            def parse_ui_context(self, xml: str, **kwargs) -> UIContext:
                return UIContext(title='Mock Drama', episode=5, total_episodes=20)

            def select_episode(self, ep_num: int, **kwargs) -> bool:
                return ep_num > 0

        # 创建实例
        adapter = create_adapter('mock_app')

        # 验证类型
        self.assertIsInstance(adapter, AppAdapter)
        self.assertIsInstance(adapter, MockAdapter)

        # 验证方法可调用
        self.assertEqual(adapter.get_package_name(), 'com.example.mock')
        self.assertEqual(adapter.get_hook_script(), 'hooks/mock.js')

        ctx = adapter.parse_ui_context('<xml/>')
        self.assertEqual(ctx.title, 'Mock Drama')
        self.assertEqual(ctx.episode, 5)

        self.assertTrue(adapter.select_episode(10))
        self.assertFalse(adapter.select_episode(0))

    def test_create_adapter_unknown_app(self):
        """验证未知 app_name 抛出 ValueError"""

        # 注册一个 adapter
        @register_adapter('known_app')
        class KnownAdapter(AppAdapter):
            app_name = 'known_app'
            def get_package_name(self, **kwargs) -> str: return 'com.known'
            def get_hook_script(self, **kwargs) -> str: return 'known.js'
            def parse_ui_context(self, xml: str, **kwargs) -> UIContext: return UIContext()
            def select_episode(self, ep_num: int, **kwargs) -> bool: return True

        # 尝试创建未注册的 adapter
        with self.assertRaises(ValueError) as ctx:
            create_adapter('unknown_app')

        error_msg = str(ctx.exception)
        self.assertIn('unknown_app', error_msg)
        self.assertIn('known_app', error_msg)  # 错误信息应包含可用的 adapter

    def test_create_adapter_empty_registry(self):
        """验证注册表为空时抛出 ValueError"""

        with self.assertRaises(ValueError) as ctx:
            create_adapter('any_app')

        error_msg = str(ctx.exception)
        self.assertIn('any_app', error_msg)


class TestAdapterInterface(unittest.TestCase):
    """测试 adapter 接口的完整性"""

    def setUp(self):
        """每个测试前清空注册表"""
        _ADAPTER_REGISTRY.clear()

    def tearDown(self):
        """每个测试后清空注册表"""
        _ADAPTER_REGISTRY.clear()

    def test_adapter_methods_accept_kwargs(self):
        """验证所有抽象方法支持 **kwargs 参数"""

        @register_adapter('kwargs_test')
        class KwargsAdapter(AppAdapter):
            app_name = 'kwargs_test'

            def get_package_name(self, **kwargs) -> str:
                # 验证可以接收额外参数
                config = kwargs.get('config', {})
                return config.get('package', 'com.default')

            def get_hook_script(self, **kwargs) -> str:
                mode = kwargs.get('mode', 'default')
                return f'hooks/{mode}.js'

            def parse_ui_context(self, xml: str, **kwargs) -> UIContext:
                fallback_title = kwargs.get('fallback_title', 'Unknown')
                return UIContext(title=fallback_title)

            def select_episode(self, ep_num: int, **kwargs) -> bool:
                max_retries = kwargs.get('max_retries', 3)
                return ep_num <= max_retries

        adapter = create_adapter('kwargs_test')

        # 测试带额外参数调用
        pkg = adapter.get_package_name(config={'package': 'com.custom'})
        self.assertEqual(pkg, 'com.custom')

        script = adapter.get_hook_script(mode='advanced')
        self.assertEqual(script, 'hooks/advanced.js')

        ctx = adapter.parse_ui_context('<xml/>', fallback_title='Fallback Drama')
        self.assertEqual(ctx.title, 'Fallback Drama')

        result = adapter.select_episode(5, max_retries=10)
        self.assertTrue(result)


if __name__ == '__main__':
    unittest.main()
