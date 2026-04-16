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


class TestHongGuoAdapter(unittest.TestCase):
    """测试 HongGuoAdapter 实现"""

    def setUp(self):
        """每个测试前确保 HongGuoAdapter 已注册"""
        # 导入 HongGuoAdapter 以触发 @register_adapter 装饰器
        from scripts.app_adapter import HongGuoAdapter
        # 如果未注册，手动注册
        if 'honguo' not in _ADAPTER_REGISTRY:
            _ADAPTER_REGISTRY['honguo'] = HongGuoAdapter

    def test_honguo_adapter_creation(self):
        """验证可以通过工厂函数创建 HongGuoAdapter"""
        adapter = create_adapter('honguo')

        # 验证 app_name 属性
        self.assertEqual(adapter.app_name, 'honguo')
        # 验证是 AppAdapter 的实例（通过检查方法存在性）
        self.assertTrue(hasattr(adapter, 'get_package_name'))
        self.assertTrue(hasattr(adapter, 'get_hook_script'))
        self.assertTrue(hasattr(adapter, 'parse_ui_context'))
        self.assertTrue(hasattr(adapter, 'select_episode'))

    def test_honguo_adapter_get_package_name(self):
        """验证返回正确的包名"""
        adapter = create_adapter('honguo')
        package = adapter.get_package_name()

        self.assertEqual(package, 'com.phoenix.read')

    def test_honguo_adapter_get_hook_script(self):
        """验证返回正确的 Hook 脚本路径"""
        from pathlib import Path

        adapter = create_adapter('honguo')
        hook_script = adapter.get_hook_script()

        self.assertEqual(hook_script, 'frida_hooks/ttengine_all.js')

        # 验证文件存在
        script_path = Path(hook_script)
        self.assertTrue(script_path.exists(), f"Hook script not found: {hook_script}")

    def test_honguo_adapter_parse_ui_context(self):
        """验证 UI 解析委托给 drama_download_common.parse_ui_context"""
        adapter = create_adapter('honguo')

        # 创建一个简单的 mock XML（包含红果 App 的 resource-id）
        # 使用实际的 UI 结构：剧名在 d4，当前集在 jjj，总集数在 jr1
        mock_xml = '''<?xml version="1.0" encoding="UTF-8"?>
<hierarchy rotation="0">
  <node text="测试剧名" resource-id="com.phoenix.read:id/d4" bounds="[0,100][1080,200]" />
  <node text="第5集" resource-id="com.phoenix.read:id/jjj" bounds="[0,200][100,300]" />
  <node text="共80集" resource-id="com.phoenix.read:id/jr1" bounds="[100,200][200,300]" />
</hierarchy>'''

        # 调用 parse_ui_context
        context = adapter.parse_ui_context(mock_xml)

        # 验证解析结果
        self.assertIsInstance(context, UIContext)
        self.assertEqual(context.title, '测试剧名')
        # 注意：parse_ui_context 从 "第5集" 和 "共80集" 中提取数字
        self.assertEqual(context.episode, 5)
        self.assertEqual(context.total_episodes, 80)

    def test_honguo_adapter_select_episode_mock(self):
        """验证 select_episode 委托给 drama_download_common.select_episode_from_ui"""
        from unittest.mock import patch

        adapter = create_adapter('honguo')

        # Mock select_episode_from_ui 函数
        with patch('scripts.drama_download_common.select_episode_from_ui') as mock_select:
            mock_select.return_value = True

            # 调用 adapter 的 select_episode
            result = adapter.select_episode(5)

            # 验证底层函数被调用
            mock_select.assert_called_once_with(5, max_attempts=8)
            self.assertTrue(result)

    def test_honguo_adapter_select_episode_with_max_attempts(self):
        """验证 select_episode 支持 max_attempts 参数"""
        from unittest.mock import patch

        adapter = create_adapter('honguo')

        # Mock select_episode_from_ui 函数
        with patch('scripts.drama_download_common.select_episode_from_ui') as mock_select:
            mock_select.return_value = False

            # 调用 adapter 的 select_episode，传递自定义 max_attempts
            result = adapter.select_episode(10, max_attempts=15)

            # 验证底层函数被调用，且 max_attempts 参数正确传递
            mock_select.assert_called_once_with(10, max_attempts=15)
            self.assertFalse(result)

    def test_honguo_adapter_registered(self):
        """验证 HongGuoAdapter 已注册到工厂"""
        available = list_available_adapters()

        self.assertIn('honguo', available)


if __name__ == '__main__':
    unittest.main()
