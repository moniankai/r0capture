"""多家中转站 Anthropic 客户端 + Fallback。

对外暴露 build_default_client() -> FallbackAnthropic,对调用方等价于 Anthropic 实例
(即 .messages.create(...) 可直接调用)。

Fallback 策略:
- 按配置顺序尝试每个 endpoint。
- 网络/超时/限流/5xx/401/403 → 降级到下一家。
- 其他 4xx(400 参数错、404 模型不存在等) → 立即抛,不降级(因为换家也一样)。
- 每次 create 都从头试起,上一次故障的家恢复后自动回来。

环境变量加载顺序(高优先放前):
1. ANTHROPIC_API_KEY (+ ANTHROPIC_BASE_URL) - 官方 API,若设置
2. AICODE_API_KEY   (+ AICODE_BASE_URL)     - Aicode 中转站
3. YUNYI_API_KEY    (+ YUNYI_BASE_URL)      - 云译中转站
至少配一家。
"""
from __future__ import annotations

import os
from pathlib import Path

from anthropic import (
    Anthropic,
    APIConnectionError,
    APIError,
    APIStatusError,
    APITimeoutError,
    RateLimitError,
)
from loguru import logger


# ---------------------------------------------------------------------------
# .env 加载(不引入 python-dotenv 依赖)
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DOTENV_LOADED = False


def _load_dotenv(path: Path | None = None) -> None:
    """加载 .env。只设置未定义的环境变量,不覆盖已有值。幂等。"""
    global _DOTENV_LOADED
    if _DOTENV_LOADED:
        return
    _DOTENV_LOADED = True
    path = path or (_PROJECT_ROOT / ".env")
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


# ---------------------------------------------------------------------------
# FallbackAnthropic
# ---------------------------------------------------------------------------

class _MessagesProxy:
    def __init__(self, parent: "FallbackAnthropic"):
        self._parent = parent

    def create(self, **kwargs):
        return self._parent._call_messages_create(**kwargs)


class FallbackAnthropic:
    """按顺序尝试多个 Anthropic endpoint,首个成功即返回。"""

    # 判定为"可降级"的状态码(中转站自身故障或认证问题)
    _FALLBACK_STATUS = {401, 403, 408, 429, 500, 502, 503, 504}

    def __init__(self, endpoints: list[tuple[str, str, str]]):
        """endpoints: [(name, base_url, api_key), ...] 按优先顺序。"""
        if not endpoints:
            raise ValueError("至少提供一个 endpoint")
        self.endpoints = endpoints
        self.clients: list[tuple[str, Anthropic]] = [
            (name, Anthropic(base_url=base_url, api_key=api_key))
            for name, base_url, api_key in endpoints
        ]
        self.messages = _MessagesProxy(self)
        logger.info(f"[llm] FallbackAnthropic endpoints: {[n for n,_,_ in endpoints]}")

    def _call_messages_create(self, **kwargs):
        last_error: Exception | None = None
        for i, (name, client) in enumerate(self.clients):
            try:
                resp = client.messages.create(**kwargs)
                if i > 0:
                    logger.info(f"[llm] fallback to endpoint {name} succeeded")
                return resp
            except (APIConnectionError, APITimeoutError) as e:
                last_error = e
                logger.warning(
                    f"[llm] endpoint {name} 网络失败 ({type(e).__name__}): {e}。"
                    f"尝试下一家..." if i + 1 < len(self.clients) else "。无更多 endpoint。"
                )
                continue
            except RateLimitError as e:
                last_error = e
                logger.warning(f"[llm] endpoint {name} 限流 429,降级")
                continue
            except APIStatusError as e:
                last_error = e
                status = getattr(e, "status_code", None)
                if status in self._FALLBACK_STATUS:
                    logger.warning(f"[llm] endpoint {name} status={status} 可降级: {e}")
                    continue
                # 其他 4xx(如 400 参数错)立即抛
                logger.error(f"[llm] endpoint {name} status={status} 不可降级: {e}")
                raise
            except APIError as e:
                # 父类 fallthrough(未归类),保守降级
                last_error = e
                logger.warning(f"[llm] endpoint {name} {type(e).__name__}: {e}。降级")
                continue
        # 所有 endpoint 都失败
        assert last_error is not None
        logger.error(f"[llm] 所有 endpoint 都失败,最后错误: {last_error}")
        raise last_error


# ---------------------------------------------------------------------------
# 默认构造
# ---------------------------------------------------------------------------

def build_default_client(prefer: list[str] | None = None) -> FallbackAnthropic:
    """从环境变量 / .env 读 endpoint,按优先级组装 FallbackAnthropic。

    Args:
        prefer: 可选,按 endpoint 名字指定优先顺序。
                名字可选: 'anthropic_official' | 'aicode' | 'yunyi'。
                未出现在 prefer 中的 endpoint 仍会按默认顺序加在后面作为 fallback。
                例子:
                  prefer=['yunyi', 'aicode']  # agent 场景(aicode 对复杂 tools 有 bug)
                  prefer=None                  # 默认:官方 → aicode → yunyi
    """
    _load_dotenv()
    all_endpoints: dict[str, tuple[str, str, str]] = {}

    if os.environ.get("ANTHROPIC_API_KEY"):
        all_endpoints["anthropic_official"] = (
            "anthropic_official",
            os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com"),
            os.environ["ANTHROPIC_API_KEY"],
        )
    if os.environ.get("AICODE_API_KEY"):
        all_endpoints["aicode"] = (
            "aicode",
            os.environ.get("AICODE_BASE_URL", "https://web.codetab.cc"),
            os.environ["AICODE_API_KEY"],
        )
    if os.environ.get("YUNYI_API_KEY"):
        all_endpoints["yunyi"] = (
            "yunyi",
            os.environ.get("YUNYI_BASE_URL", "https://yunyi.rdzhvip.com/claude"),
            os.environ["YUNYI_API_KEY"],
        )

    if not all_endpoints:
        raise RuntimeError(
            "未找到 API key。请在 .env 或环境变量中配置以下之一:"
            " ANTHROPIC_API_KEY / AICODE_API_KEY / YUNYI_API_KEY"
        )

    endpoints: list[tuple[str, str, str]] = []
    used = set()
    if prefer:
        for name in prefer:
            if name in all_endpoints:
                endpoints.append(all_endpoints[name])
                used.add(name)
    # 剩余 endpoint 按默认顺序追加
    for name in ("anthropic_official", "aicode", "yunyi"):
        if name in all_endpoints and name not in used:
            endpoints.append(all_endpoints[name])

    return FallbackAnthropic(endpoints)
