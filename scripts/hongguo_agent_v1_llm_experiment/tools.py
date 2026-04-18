"""tools.py: 把 HongguoService 的方法暴露成 Claude tool_use 可调用的 tool。

两部分:
1. TOOL_SCHEMAS: list[dict] - 传给 anthropic.messages.create(tools=...)
2. dispatch(name, args) -> str - Agent 主循环拿到 tool_use block 后调用,
   返回 JSON 字符串塞进 tool_result.content

注意:
- 每个 tool 返回结构化 dict; dispatch 统一 json.dumps。
- verify_screen 的实现在 vision.py,但 schema 和 dispatch 入口在这里注册。
- 避免把大体积数据(完整 XML、base64 图片)塞进 tool_result; 只返回路径。
"""
from __future__ import annotations

import json
from typing import Any

from .service import HongguoService


TOOL_SCHEMAS: list[dict] = [
    # ---------------- 生命周期 ----------------
    {
        "name": "start_session",
        "description": (
            "启动下载会话:初始化输出目录,force-stop 红果 App,"
            "spawn 新进程,加载 Frida Hook。必须在所有其他 tool 之前调用一次。"
            "如果 attach_running=true,则挂到已运行的 App(跳过 spawn)。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "drama": {"type": "string", "description": "剧名,用于目录和 VLM 校验"},
                "total_eps": {"type": "integer", "description": "总集数"},
                "attach_running": {
                    "type": "boolean",
                    "description": "true=挂到已运行 App,false=force-stop+spawn 冷启动",
                    "default": False,
                },
            },
            "required": ["drama", "total_eps"],
        },
    },
    {
        "name": "end_session",
        "description": "结束会话:卸载 Frida script,detach。最后调用一次。",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "restart_app",
        "description": (
            "force-stop + spawn 重启 App,并重建 Frida session。"
            "用于怀疑 App 陷入异常状态(UI 卡死、Hook 失效等)时的重置。"
            "重启后 cells 会被清空,需要重新 navigate + scan。"
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_state_snapshot",
        "description": (
            "查看当前服务状态:剧名、总集数、是否已扫描面板、"
            "已捕获的 kid 数、cluster、当前 Activity、已下载集列表。"
            "建议每个决策步骤开头调用一次。"
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    # ---------------- UI 原语 ----------------
    {
        "name": "screenshot",
        "description": (
            "截取手机当前屏幕,保存为 PNG,返回本地路径。"
            "用于 verify_screen(VLM 读集数/剧名)或调试。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "label": {
                    "type": "string",
                    "description": "文件名标签(可选),例如 'before_tap_ep3'",
                },
            },
        },
    },
    {
        "name": "dump_ui_xml",
        "description": (
            "dump uiautomator UI 层级。返回感兴趣的节点摘要(带 text/desc/id/bounds)。"
            "视频播放时此调用经常失败(播放器禁 idle state);在搜索页/面板时稳定。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "include_full": {
                    "type": "boolean",
                    "description": "true 则把完整 XML 写到文件并返回路径",
                    "default": False,
                },
            },
        },
    },
    {
        "name": "tap",
        "description": "tap 绝对坐标。用于无需专用 tool 的临时操作(如点搜索框)。",
        "input_schema": {
            "type": "object",
            "properties": {
                "x": {"type": "integer"},
                "y": {"type": "integer"},
                "settle_s": {
                    "type": "number",
                    "description": "tap 后等待秒数",
                    "default": 0.6,
                },
            },
            "required": ["x", "y"],
        },
    },
    {
        "name": "press_back",
        "description": "发送系统返回键。常用于关闭选集面板。",
        "input_schema": {"type": "object", "properties": {}},
    },
    # ---------------- 导航 ----------------
    {
        "name": "navigate_to_drama",
        "description": (
            "完成'冷启动主页 → 打开搜索 → 搜索历史命中或输入剧名 → "
            "点搜索结果海报 → 等播放器 Activity 就绪'全套导航。"
            "成功返回 ok=true。drama 在 start_session 时已指定。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "timeout_s": {
                    "type": "number",
                    "description": "等播放器就绪的总超时",
                    "default": 25.0,
                },
            },
        },
    },
    # ---------------- 选集面板 ----------------
    {
        "name": "open_episode_panel",
        "description": "tap 播放器底部选集按钮(540,1820)打开选集面板。",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "scan_episode_panel",
        "description": (
            "扫描选集面板所有集的坐标(按段切换+滚动补齐),结果存 service 并落 cells.json。"
            "后续 tap_episode_cell 依赖此扫描。扫完自动关面板。"
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "tap_episode_cell",
        "description": (
            "打开面板 → 切到目标集所在段 → tap 格子。不等 Hook,"
            "调用方需接 wait_capture 读取 Frida 捕获结果。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ep": {"type": "integer", "description": "要切到的集数(从 1 开始)"},
            },
            "required": ["ep"],
        },
    },
    # ---------------- 捕获 & 下载 ----------------
    {
        "name": "wait_capture",
        "description": (
            "等 Frida Hook(TTVideoEngine.setVideoModel)fire,返回最新 Capture 元数据"
            "(kid、是否有 key、可选码率/分辨率)。"
            "策略:等到第一个 cap 后再 settle_s 吸收后续预加载 fire,返回末个。"
            "典型用法:tap_episode_cell 之后立刻调用。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "timeout_s": {"type": "number", "default": 4.0},
                "settle_s": {"type": "number", "default": 1.5},
            },
        },
    },
    {
        "name": "list_recent_captures",
        "description": (
            "列出 state 里最近 N 个 Capture 的元数据(kid/cluster/时间戳/是否有 key)。"
            "用于调试或在 wait_capture 失败时回溯。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "default": 5}},
        },
    },
    {
        "name": "download_episode",
        "description": (
            "按 kid 从 state 找 Capture,拉流 + CENC 解密,写到 "
            "episode_NNN_XXXXXXXX.mp4。max_short_side 控制画质上限(较低=文件更小)。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ep": {"type": "integer"},
                "kid": {"type": "string", "description": "32-char hex(小写)"},
                "max_short_side": {"type": "integer", "default": 1080},
            },
            "required": ["ep", "kid"],
        },
    },
    {
        "name": "verify_playable",
        "description": "用 ffprobe 验证视频文件可解码,检查时长和分辨率。",
        "input_schema": {
            "type": "object",
            "properties": {"file_path": {"type": "string"}},
            "required": ["file_path"],
        },
    },
    {
        "name": "extract_first_frame",
        "description": (
            "用 opencv 从下载的 mp4 抽取指定时刻(默认 3 秒)的画面帧,保存为 JPEG。"
            "通常配合 compare_download_with_screen 用,单独调用较少。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string"},
                "time_s": {"type": "number", "default": 3.0},
            },
            "required": ["file_path"],
        },
    },
    {
        "name": "compare_download_with_screen",
        "description": (
            "**内容对齐黄金校验**:一步完成(1)抽 mp4 的第 time_s 秒帧,"
            "(2)截 App 当前屏幕,(3)VLM 对比是否来自同一集内容。"
            "调用前必须先让 App 实际播放 expected_episode 集(tap_episode_cell(ep)+verify_screen 确认 OSD)。"
            "返回 {ok, same_episode:bool, confidence, reason, frame_a, frame_b}。"
            "same_episode=false 意味着下载内容和 App 当前播放集不一致,应丢弃 mp4 或试别的 kid。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string"},
                "expected_episode": {"type": "integer"},
                "time_s": {"type": "number", "default": 3.0},
            },
            "required": ["file_path", "expected_episode"],
        },
    },
    {
        "name": "write_manifest",
        "description": (
            "写一条 manifest 记录(session_manifest_agent.jsonl),"
            "记录集的最终状态(ok/skipped/failed/mismatched)。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ep": {"type": "integer"},
                "kid": {"type": "string"},
                "status": {
                    "type": "string",
                    "enum": ["ok", "skipped", "failed", "mismatched", "retry"],
                },
                "note": {"type": "string", "description": "可选说明"},
            },
            "required": ["ep", "kid", "status"],
        },
    },
    # ---------------- 校验(VLM) ----------------
    # 实现在 vision.py,这里只注册 schema
    {
        "name": "verify_screen",
        "description": (
            "用 Claude Vision 读当前屏幕截图,识别:"
            "(1) 顶部/中央是否显示目标剧名,"
            "(2) 播放器左上角/顶部是否显示'第 N 集'并与 expected_episode 对齐。"
            "返回 {drama_match, observed_drama, observed_episode, confidence, evidence_path}。"
            "drama_match=false 或 observed_episode != expected_episode 时 Agent 应处理错位。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "expected_episode": {
                    "type": "integer",
                    "description": "Agent 期望当前屏幕在播放的集数",
                },
            },
            "required": ["expected_episode"],
        },
    },
]


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

def _call_service_method(name: str, args: dict[str, Any]) -> dict:
    svc = HongguoService.get()
    method = getattr(svc, name, None)
    if method is None or not callable(method):
        return {"ok": False, "reason": f"service 无方法 {name}"}
    try:
        return method(**args)
    except TypeError as e:
        return {"ok": False, "reason": f"参数错误: {e}"}
    except Exception as e:
        return {"ok": False, "reason": f"{type(e).__name__}: {e}"}


# tool 名 → service 方法名 的映射。1:1 时无需重命名,这里放差异映射。
_TOOL_TO_METHOD: dict[str, str] = {
    # 这两个 tool 里参数名不同于 service,特殊处理在下面
    "navigate_to_drama": "navigate_to_drama",
    "wait_capture": "wait_capture",
    "download_episode": "download_episode",
    "write_manifest": "write_manifest",
    # 其余默认 tool_name == method_name
}


def dispatch(tool_name: str, tool_input: dict[str, Any]) -> str:
    """Agent 主循环调用: 根据 tool_name 路由到 service 方法,返回 JSON 字符串。"""
    args = dict(tool_input)  # 避免污染

    if tool_name == "verify_screen":
        # 延迟 import,避免未装 anthropic 时崩 tools.py 导入
        from .vision import verify_screen_impl
        expected = args.get("expected_episode")
        result = verify_screen_impl(expected_episode=expected)
        return json.dumps(result, ensure_ascii=False)

    # 参数名从 tool schema 映射到 service 方法签名
    if tool_name == "navigate_to_drama":
        mapped = {"timeout": args.pop("timeout_s", 25.0)}
        result = _call_service_method("navigate_to_drama", mapped)
    elif tool_name == "wait_capture":
        mapped = {
            "timeout_s": args.pop("timeout_s", 4.0),
            "settle_s": args.pop("settle_s", 1.5),
        }
        result = _call_service_method("wait_capture", mapped)
    elif tool_name == "write_manifest":
        ep = args.pop("ep")
        kid = args.pop("kid")
        status = args.pop("status")
        note = args.pop("note", None)
        mapped = {"ep": ep, "kid": kid, "status": status,
                  "extra": {"note": note} if note else None}
        result = _call_service_method("write_manifest", mapped)
    else:
        method = _TOOL_TO_METHOD.get(tool_name, tool_name)
        result = _call_service_method(method, args)

    return json.dumps(result, ensure_ascii=False)


def list_tool_names() -> list[str]:
    return [t["name"] for t in TOOL_SCHEMAS]
