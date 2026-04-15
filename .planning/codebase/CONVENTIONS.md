# 编码规范

**分析日期:** 2026-04-15

## 命名模式

**文件:**
- Python 模块使用 snake_case：`download_drama.py`、`decrypt_video.py`、`drama_download_common.py`
- JavaScript Hook 脚本使用 snake_case：`aes_hook.js`、`ttengine_all.js`、`anti_detection.js`
- 测试文件使用 `test_` 前缀：`test_download_drama.py`、`test_audit_drama_downloads.py`

**函数:**
- 使用 snake_case：`sanitize_drama_name()`、`parse_ui_context()`、`decrypt_mp4()`
- 私有/内部函数使用单下划线前缀：`_extract_nodes()`、`_parse_episode_value()`、`_looks_like_title()`
- 主入口函数统一命名为 `main()`

**变量:**
- 使用 snake_case：`video_id`、`episode_number`、`output_dir`
- 常量使用 UPPER_SNAKE_CASE：`APP_PACKAGE`、`INVALID_PATH_CHARS`、`SKIP_TITLE_TEXTS`
- 集合常量使用复数形式：`KNOWN_TITLE_RESOURCE_IDS`、`KNOWN_EPISODE_RESOURCE_IDS`

**类:**
- 使用 PascalCase：`UIContext`、`SessionValidationState`、`DownloadTask`、`BatchManager`
- 枚举类使用 PascalCase：`DownloadStatus`

**类型:**
- 使用 dataclass 定义数据结构：`@dataclass` 装饰器
- 类型注解使用 `from __future__ import annotations` 启用延迟求值
- 使用 `Optional[T]`、`list[T]`、`dict[K, V]` 等标准类型提示

## 代码风格

**格式化:**
- 无显式格式化工具配置（未检测到 .prettierrc、.black 等）
- 缩进：4 空格（Python 标准）
- 行长度：实际代码中存在较长行（100-120 字符），无严格限制
- 字符串：优先使用双引号 `""`，docstring 使用三引号 `"""`

**Linting:**
- 无显式 linter 配置文件
- 代码中存在 `# pylint: disable=g-import-not-at-top` 等注释，表明曾使用 pylint

**Python 版本特性:**
- 所有模块使用 `from __future__ import annotations` 启用 PEP 563
- 使用现代类型提示语法：`list[str]` 而非 `List[str]`
- 使用 `|` 联合类型：`str | Path`、`int | None`
- 使用 dataclass 替代传统类定义

## 导入组织

**顺序:**
1. `from __future__ import annotations`（必须在首行）
2. 标准库导入（按字母顺序）
3. 第三方库导入（按字母顺序）
4. 本地模块导入（使用 `sys.path.insert` 或相对导入）

**示例:**
```python
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import frida
import requests
from loguru import logger
from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).parent.parent))
from scripts.decrypt_video import decrypt_mp4, fix_metadata
from scripts.drama_download_common import (
    UIContext,
    SessionValidationState,
    sanitize_drama_name,
)
```

**路径别名:**
- 无配置路径别名
- 使用 `sys.path.insert(0, str(Path(__file__).parent.parent))` 动态添加项目根目录到搜索路径

## 错误处理

**模式:**
- 使用 try-except 捕获特定异常：`frida.InvalidArgumentError`、`frida.TimedOutError`、`subprocess.TimeoutExpired`、`ET.ParseError`
- 通用异常捕获使用 `except Exception as e:`，并记录错误信息
- 静默捕获仅用于非关键路径：`except Exception: pass`（如 Hook 脚本中的容错）
- 不使用 `raise` 重新抛出，而是记录日志后继续或返回默认值

**示例:**
```python
try:
    resp = requests.get(url, headers=headers, stream=True, timeout=30)
    resp.raise_for_status()
except Exception as e:
    logger.error(f"下载失败: {e}")
    return False
```

**返回值约定:**
- 失败时返回 `None`、空列表 `[]`、空字典 `{}`、`False` 等默认值
- 不抛出异常到调用方，由函数内部处理并记录日志

## 日志记录

**框架:** loguru

**模式:**
- 使用 `logger.info()`、`logger.warning()`、`logger.error()` 记录不同级别日志
- 日志消息使用中文描述，包含关键上下文信息
- 使用 f-string 格式化日志消息：`logger.info(f"下载完成: {output_path}")`
- 错误日志包含异常对象：`logger.error(f"下载失败: {e}")`

**示例:**
```python
logger.info(f"捕获到视频 URL: {url[:100]}")
logger.warning(f"未找到 AES 密钥，跳过解密")
logger.error(f"Frida 连接失败: {exc}")
```

**进度显示:**
- 使用 `tqdm` 显示下载进度：`tqdm(total=total_size, unit='B', unit_scale=True)`
- 使用 `click.secho()` 输出彩色终端信息（在 `r0capture.py` 中）

## 注释

**何时注释:**
- 复杂业务逻辑前添加中文注释说明意图
- 关键算法步骤添加行内注释
- 临时方案或已知问题使用 `# TODO:` 或 `# FIXME:` 标记
- Frida Hook 脚本使用 JSDoc 风格注释

**Docstring:**
- 模块级 docstring 使用三引号，包含功能说明和使用示例
- 函数 docstring 较少使用，仅在复杂函数中添加
- 格式：简短描述 + 参数说明 + 返回值说明

**示例:**
```python
"""
红果免费短剧 一键下载工具

功能流程:
  1. 启动 App 并注入 Frida 双 Hook（Java 层 URL + Native 层 AES 密钥）
  2. 用户在手机上播放目标短剧
  3. 自动捕获视频 CDN 地址 + AES-128 解密密钥
  4. 下载 CENC 加密的 MP4
  5. 解密视频+音频轨道（AES-CTR-128）
  6. 输出可播放的 MP4 文件

用法:
  python scripts/download_drama.py
  python scripts/download_drama.py -b 5
"""
```

**中文注释:**
- 代码注释默认使用中文
- 变量名和函数名使用英文
- 日志消息使用中文

## 函数设计

**大小:** 
- 主函数 `main()` 通常 100-300 行，包含完整工作流编排
- 工具函数 10-50 行，单一职责
- 复杂函数（如 `parse_ui_context()`）可达 60-80 行

**参数:** 
- 使用类型注解：`def sanitize_drama_name(name: str) -> str:`
- 可选参数使用默认值：`def parse_ui_context(xml_text: str) -> UIContext:`
- 复杂参数使用 dataclass 封装：`UIContext`、`DownloadTask`

**返回值:** 
- 明确返回类型：`-> str`、`-> Optional[int]`、`-> tuple[str, str]`
- 失败时返回 `None` 或空容器，不抛出异常
- 复杂返回值使用 dataclass 或 dict

**示例:**
```python
def build_episode_paths(
    output_dir: str, episode: int, video_id: str, drama_name: str = ''
) -> tuple[str, str]:
    """构建集数文件路径（视频 + 元数据）。"""
    folder_name = os.path.basename(output_dir) if not drama_name else drama_name
    suffix = video_id_suffix(video_id)
    video_path = os.path.join(output_dir, f'{folder_name}_episode_{episode:03d}_{suffix}.mp4')
    meta_path = os.path.join(output_dir, f'meta_ep{episode:03d}_{suffix}.json')
    return video_path, meta_path
```

## 模块设计

**导出:** 
- 无显式 `__all__` 定义
- 公共函数直接定义在模块顶层
- 私有函数使用单下划线前缀

**Barrel 文件:** 
- `scripts/__init__.py` 为空文件
- 无集中导出模式，直接从模块导入

**常量定义:**
- 模块级常量定义在导入语句后、函数定义前
- 使用 UPPER_SNAKE_CASE 命名
- 集合常量使用 set 或 dict：`SKIP_TITLE_TEXTS = {...}`

**示例:**
```python
APP_PACKAGE = "com.phoenix.read"

INVALID_PATH_CHARS = '<>:"/\\|?*'

SKIP_TITLE_TEXTS = {
    '全屏观看',
    '选集',
    '展开',
    # ...
}
```

## 数据结构

**dataclass 使用:**
- 优先使用 `@dataclass` 定义数据结构
- 使用 `field(default_factory=...)` 定义可变默认值
- 使用 `frozen=True` 定义不可变对象

**示例:**
```python
@dataclass
class UIContext:
    title: str = ''
    episode: Optional[int] = None
    total_episodes: Optional[int] = None
    raw_texts: list[str] = field(default_factory=list)

@dataclass
class SessionValidationState:
    locked_title: str = ''
    seen_video_ids: set[str] = field(default_factory=set)
    last_episode: int = 0
```

## 字符串处理

**路径清理:**
- 使用 `''.join()` 替换非法字符：`'_' if ch in INVALID_PATH_CHARS else ch`
- 使用 `Path` 对象处理路径：`Path(__file__).parent.parent`

**正则表达式:**
- 使用 `re.search()`、`re.fullmatch()` 进行模式匹配
- 预编译复杂正则：`EPISODE_PATTERNS = [re.compile(...), ...]`
- 中文字符范围：`r'[\u4e00-\u9fff]'`

**格式化:**
- 优先使用 f-string：`f"episode_{episode:03d}_{suffix}.mp4"`
- 数字格式化：`{episode:03d}`（三位零填充）、`{size / 1024 / 1024:.1f}`（保留一位小数）

---

*规范分析: 2026-04-15*
