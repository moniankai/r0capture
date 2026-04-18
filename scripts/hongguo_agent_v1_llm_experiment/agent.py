"""agent.py: anthropic SDK tool_use 主循环。

用法:
    python -m scripts.hongguo_agent.agent -n "乡下御厨" --total 28 \\
        --start 1 --end 3 --model claude-opus-4-7

环境变量(至少一个 API key):
    ANTHROPIC_API_KEY       官方 API(最高优先)
    AICODE_API_KEY          Aicode 中转站(.env 自动加载)
    YUNYI_API_KEY           云译中转站(.env 自动加载,兜底)
    HONGGUO_MODEL           可选,默认 claude-opus-4-7(1M 上下文)

循环策略:
- while stop_reason == 'tool_use' 持续对话
- 每轮打印 Agent 的 reasoning 文本 + 每个 tool_use 的 name/input
- 硬上限 max_steps(默认 400)防止失控
- Ctrl+C 优雅退出并调 end_session
- LLM 调用通过 FallbackAnthropic: 首选失败自动降级到下一家
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from typing import Any

from loguru import logger

from .llm import build_default_client
from .prompts import build_initial_user_message, build_system_prompt
from .service import HongguoService
from .tools import TOOL_SCHEMAS, dispatch, list_tool_names


DEFAULT_MODEL = os.environ.get("HONGGUO_MODEL", "claude-sonnet-4-6")
DEFAULT_MAX_TOKENS = 4096
# Agent 场景强制 yunyi 优先: aicode 中转站对"大 tools schema + 长 system + 多轮"的
# 响应会丢失 tool_use block(stop_reason=tool_use 但 content 里没有 tool_use),已复现
# 不分 sonnet/opus 模型都触发。yunyi 对相同输入返回正确。
_AGENT_ENDPOINT_PREFERENCE = ["yunyi", "aicode"]


def _truncate(s: str, n: int = 400) -> str:
    return s if len(s) <= n else s[:n] + f"... ({len(s) - n} more)"


def run(
    drama: str,
    total_eps: int,
    start_ep: int = 1,
    end_ep: int | None = None,
    max_short_side: int = 1080,
    model: str = DEFAULT_MODEL,
    max_steps: int = 400,
    verbose: bool = True,
) -> dict:
    """启动 Agent 主循环。返回 {ok, steps, summary}。"""
    try:
        client = build_default_client(prefer=_AGENT_ENDPOINT_PREFERENCE)
    except RuntimeError as e:
        return {"ok": False, "reason": str(e)}

    system = build_system_prompt(drama, total_eps, start_ep, end_ep, max_short_side)
    user0 = build_initial_user_message(drama, total_eps, start_ep, end_ep)

    messages: list[dict[str, Any]] = [{"role": "user", "content": user0}]

    # 安装信号处理,Ctrl+C 时优雅结束 Frida
    interrupted = {"flag": False}

    def _sig(sig, frame):
        logger.warning("[agent] 收到中断信号,会在当前 tool 调用后停止")
        interrupted["flag"] = True

    signal.signal(signal.SIGINT, _sig)

    steps = 0
    last_stop_reason: str | None = None
    try:
        while steps < max_steps:
            if interrupted["flag"]:
                logger.warning("[agent] 用户中断,停止循环")
                break
            steps += 1
            if verbose:
                logger.info(f"[agent] --- step {steps} ---")
            try:
                resp = client.messages.create(
                    model=model,
                    max_tokens=DEFAULT_MAX_TOKENS,
                    system=system,
                    tools=TOOL_SCHEMAS,
                    messages=messages,
                )
            except Exception as e:
                logger.error(f"[agent] API 调用失败: {e}")
                return {"ok": False, "reason": f"API error: {e}", "steps": steps}

            last_stop_reason = resp.stop_reason
            # 把 assistant 回复原样追加(保留 tool_use 块给下次请求)
            messages.append({
                "role": "assistant",
                "content": [b.model_dump() for b in resp.content],
            })

            # 输出文本 & 处理 tool_use + 诊断日志:打印所有 block type
            tool_use_blocks = []
            block_types_seen = []
            for block in resp.content:
                btype = getattr(block, "type", "")
                block_types_seen.append(btype)
                if btype == "text" and verbose:
                    logger.info(f"[agent.reasoning] {_truncate(block.text, 600)}")
                elif btype == "thinking" and verbose:
                    # Claude 4.x extended thinking block
                    thinking_text = getattr(block, "thinking", "") or ""
                    logger.info(f"[agent.thinking] {_truncate(thinking_text, 300)}")
                elif btype == "tool_use":
                    tool_use_blocks.append(block)
            if verbose:
                logger.info(
                    f"[agent.diag] stop_reason={last_stop_reason} "
                    f"blocks={block_types_seen} tool_uses={len(tool_use_blocks)}"
                )

            # ---- 异常: stop_reason=tool_use 但 resp.content 无 tool_use block ----
            # 中转站代理抖动 / SDK 解析错位。重试 1 次,仍空才标失败。
            if last_stop_reason == "tool_use" and not tool_use_blocks:
                consecutive = getattr(run, "_empty_tool_use_count", 0) + 1
                run._empty_tool_use_count = consecutive
                if consecutive <= 2:
                    logger.warning(
                        f"[agent] stop_reason=tool_use 但无 tool_use block "
                        f"(blocks={block_types_seen}),重试第 {consecutive} 次"
                    )
                    # 回退 assistant 消息(别污染 history),加一条 user 提示请求 tool_use
                    messages.pop()
                    messages.append({
                        "role": "user",
                        "content": "请继续按流程调用工具。如果刚在说明计划,直接发 tool_use 调用即可。",
                    })
                    continue
                else:
                    logger.error(
                        "[agent] stop_reason=tool_use 但连续 2 次无 tool_use block,放弃"
                    )
                    return {
                        "ok": False, "completed": False, "steps": steps,
                        "stop_reason": "tool_use_without_block",
                        "interrupted": interrupted["flag"], "hit_max_steps": False,
                    }
            # 成功拿到 tool_use_blocks 或其他情况,重置计数
            run._empty_tool_use_count = 0

            # ---- 分类处理 stop_reason ----
            # 有 tool_use 要执行 → 下面继续跑 tool。
            # 无 tool_use 时按 stop_reason 决策:
            #   end_turn    → 完成(completed=True)
            #   max_tokens  → 让模型续写
            #   pause_turn  → 服务器端主动 pause,同样让它继续
            #   refusal     → 模型拒绝,明确标记未完成
            #   其他        → 未完成,明确标记
            completed: bool | None = None
            if not tool_use_blocks:
                if last_stop_reason == "end_turn":
                    if verbose:
                        logger.info("[agent] end_turn,循环结束")
                    completed = True
                elif last_stop_reason in ("max_tokens", "pause_turn"):
                    if verbose:
                        logger.info(f"[agent] stop_reason={last_stop_reason},让模型续写")
                    messages.append({"role": "user", "content": "继续"})
                    continue
                elif last_stop_reason == "refusal":
                    logger.error("[agent] 模型 refusal,未完成任务")
                    completed = False
                else:
                    logger.warning(
                        f"[agent] 未识别的 stop_reason={last_stop_reason} 且无 tool_use,"
                        f"视作未完成"
                    )
                    completed = False
                # 跳出循环
                return {
                    "ok": bool(completed),
                    "completed": bool(completed),
                    "steps": steps,
                    "stop_reason": last_stop_reason,
                    "interrupted": interrupted["flag"],
                    "hit_max_steps": False,
                }

            # 逐个执行 tool_use,合并成单个 user 回复
            tool_results = []
            for block in tool_use_blocks:
                name = block.name
                tool_input = block.input or {}
                if verbose:
                    logger.info(f"[agent.tool_use] {name} {json.dumps(tool_input, ensure_ascii=False)}")
                t0 = time.time()
                try:
                    result_json = dispatch(name, tool_input)
                except Exception as e:
                    result_json = json.dumps({"ok": False, "reason": f"dispatch crash: {e}"},
                                             ensure_ascii=False)
                dt = time.time() - t0
                if verbose:
                    logger.info(f"[agent.tool_result] {name} ({dt:.2f}s) {_truncate(result_json, 300)}")
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result_json,
                })

            messages.append({"role": "user", "content": tool_results})

        return {
            "ok": True,
            "steps": steps,
            "stop_reason": last_stop_reason,
            "interrupted": interrupted["flag"],
            "hit_max_steps": steps >= max_steps,
        }
    finally:
        # 不管是否 end_turn 都尝试关掉 Frida。Agent 可能没调 end_session。
        try:
            svc = HongguoService.get()
            if svc._started:
                svc.end_session()
                logger.info("[agent] end_session 兜底调用")
        except Exception as e:
            logger.warning(f"[agent] end_session 兜底失败: {e}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", "--drama", required=True)
    ap.add_argument("--total", type=int, required=True, help="总集数")
    ap.add_argument("--start", type=int, default=1)
    ap.add_argument("--end", type=int, default=None, help="下载到第几集(默认=total)")
    ap.add_argument("--max-short-side", type=int, default=1080)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--max-steps", type=int, default=400)
    ap.add_argument("-q", "--quiet", action="store_true")
    args = ap.parse_args()

    print(f"Model: {args.model}")
    print(f"Drama: {args.drama}  Episodes: {args.start}..{args.end or args.total}/{args.total}")
    print(f"Tools available: {len(TOOL_SCHEMAS)} {list_tool_names()}")
    print("-" * 60)

    result = run(
        drama=args.drama,
        total_eps=args.total,
        start_ep=args.start,
        end_ep=args.end,
        max_short_side=args.max_short_side,
        model=args.model,
        max_steps=args.max_steps,
        verbose=not args.quiet,
    )
    print("-" * 60)
    print("Agent result:", json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result.get("ok") else 1)


if __name__ == "__main__":
    main()
