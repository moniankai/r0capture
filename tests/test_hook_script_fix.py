"""
单元测试：验证 get_url_test.py 的 Hook 脚本语法正确性
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

def test_hook_script_syntax():
    """测试 Hook 脚本是否包含正确的类名和方法调用"""
    from scripts.get_url_test import HOOK_SCRIPT

    # 验证使用了正确的类名
    assert "com.ss.ttvideoengine.TTVideoEngine" in HOOK_SCRIPT, \
        "Hook 脚本应该使用 com.ss.ttvideoengine.TTVideoEngine"

    # 验证使用了 overloads.forEach 而不是直接赋值
    assert "overloads.forEach" in HOOK_SCRIPT, \
        "Hook 脚本应该使用 overloads.forEach 处理方法重载"

    # 验证不再尝试多个类名
    assert "com.bytedance.ies.xelement" not in HOOK_SCRIPT, \
        "Hook 脚本不应该尝试 bytedance 类名"

    # 验证使用了 ov.apply(this, arguments)
    assert "ov.apply(this, arguments)" in HOOK_SCRIPT, \
        "Hook 脚本应该使用 ov.apply 调用原始方法"

    # 验证包含 findFieldInHierarchy 函数
    assert "findFieldInHierarchy" in HOOK_SCRIPT, \
        "Hook 脚本应该包含 findFieldInHierarchy 函数"

    # 验证包含 vodVideoRef 字段查找
    assert "vodVideoRef" in HOOK_SCRIPT, \
        "Hook 脚本应该查找 vodVideoRef 字段"

    # 验证包含 mVideoList 字段查找
    assert "mVideoList" in HOOK_SCRIPT, \
        "Hook 脚本应该查找 mVideoList 字段"

    print("✓ Hook 脚本语法验证通过")
    print("✓ 使用正确的类名: com.ss.ttvideoengine.TTVideoEngine")
    print("✓ 使用 overloads.forEach 处理方法重载")
    print("✓ 移除了错误的 bytedance 类名尝试")
    print("✓ 包含所有必要的字段查找逻辑")


def test_hook_script_structure():
    """测试 Hook 脚本结构与 download_drama.py 一致"""
    from scripts.get_url_test import HOOK_SCRIPT
    from scripts.download_drama import COMBINED_HOOK

    # 验证关键结构相同
    key_patterns = [
        "Java.use(\"com.ss.ttvideoengine.TTVideoEngine\")",
        "Engine.setVideoModel.overloads.forEach",
        "findFieldInHierarchy(model, \"vodVideoRef\")",
        "findFieldInHierarchy(ref, \"mVideoList\")",
    ]

    for pattern in key_patterns:
        assert pattern in HOOK_SCRIPT, f"Hook 脚本缺少关键模式: {pattern}"
        assert pattern in COMBINED_HOOK, f"COMBINED_HOOK 缺少关键模式: {pattern}"

    print("✓ Hook 脚本结构与 download_drama.py 一致")


if __name__ == "__main__":
    test_hook_script_syntax()
    test_hook_script_structure()
    print("\n所有测试通过！修复已验证。")
