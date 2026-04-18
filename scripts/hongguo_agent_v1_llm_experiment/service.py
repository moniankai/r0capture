"""HongguoService: 单进程持有 Frida session + ADB + State 的服务单例。

Agent 的所有 tool 最终都落到这个类的方法上。
本模块不承担 Agent 决策,仅提供"原子能力 + 状态查询"。

设计原则:
- 单例。避免重复起 Frida session。
- 无业务循环。循环/重试/校验交给 Agent。
- 返回结构化 dict,供 Claude tool_result 使用。
"""
from __future__ import annotations

import glob as _glob
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

import frida
from loguru import logger

# 复用现有实现,不搬代码
from scripts.download_drama import select_running_app_pid
from scripts.download_v4 import HOOK_JS, Capture, State
from scripts.download_v4 import (
    scan_panel as _scan_panel_impl,
    ep_to_segment as _ep_to_segment,
    download_and_decrypt as _download_and_decrypt,
)
from scripts.download_hongguo import verify_playable as _verify_playable
from scripts.download_hongguo2 import (
    APP_PACKAGE, PLAYER_ACTIVITY, _current_activity, navigate_to_drama_v2,
)
from scripts.drama_download_common import (
    append_jsonl,
    read_ui_xml_from_device,
    run_adb,
    sanitize_drama_name,
)

_ENV = {**os.environ, "MSYS_NO_PATHCONV": "1"}


class HongguoService:
    _instance: Optional["HongguoService"] = None
    _lock = threading.Lock()

    def __init__(self):
        if getattr(self, "_initialized", False):
            return
        self._initialized = True
        self.device: frida.core.Device | None = None
        self.session = None
        self.script = None
        self.pid: int | None = None
        self.state: State = State()
        # 运行上下文
        self.drama: str | None = None
        self.total_eps: int | None = None
        self.output_dir: Path | None = None
        # 选集面板扫描结果(由 scan_panel tool 填)
        self.cells: dict[int, tuple[int, int]] | None = None
        self.seg_btn: dict[str, tuple[int, int]] = {}
        self.cur_seg: list = [None]  # 当前显示的段("1-30" / "31-60" / ...)
        # 落盘路径
        self.manifest_path: Path | None = None
        self.agent_log_path: Path | None = None
        self._started = False

    @classmethod
    def get(cls) -> "HongguoService":
        with cls._lock:
            if cls._instance is None:
                # 先在临时对象上初始化,成功后再赋给 _instance,
                # 避免 __init__ 中途异常留下半初始化实例
                inst = cls.__new__(cls)
                inst.__init__()
                cls._instance = inst
            return cls._instance

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    def start_session(
        self,
        drama: str,
        total_eps: int,
        attach_running: bool = False,
        output_root: str = "./videos",
    ) -> dict:
        """启动 Frida session、绑定 HOOK_JS、初始化输出目录。

        Args:
            drama: 剧名(用于建目录、写 manifest、VLM 校验)
            total_eps: 总集数(确定段数、作为遍历边界)
            attach_running: True=挂到运行中的 App; False=force-stop 后 spawn
            output_root: 输出根目录(默认 ./videos)

        Returns: {ok, pid, mode, output_dir, [reason]}
        """
        if self._started:
            return {"ok": False, "reason": "session 已启动,先 end_session"}

        # === 全量重置会话态,防止复用单例跑第二部剧时带入旧 cluster / cells ===
        self.state = State()
        self.cells = None
        self.seg_btn = {}
        self.cur_seg = [None]
        self.pid = None
        self.session = None
        self.script = None

        self.drama = drama
        self.total_eps = total_eps
        self.output_dir = Path(output_root) / sanitize_drama_name(drama)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.manifest_path = self.output_dir / "session_manifest_agent.jsonl"
        self.agent_log_path = self.output_dir / "agent_trace.jsonl"

        log_file = self.output_dir / "agent.log"
        logger.add(
            log_file,
            rotation="10 MB",
            encoding="utf-8",
            format="{time:YYYY-MM-DD HH:mm:ss} | {level:<8} | {message}",
            level="INFO",
        )

        self.device = frida.get_usb_device(timeout=10)
        spawned = False
        pending_pid = None
        pending_session = None
        pending_script = None
        try:
            if attach_running:
                pid = select_running_app_pid(
                    self.device.enumerate_processes(), APP_PACKAGE
                )
                if pid is None:
                    return {"ok": False, "reason": f"{APP_PACKAGE} 未运行"}
                pending_pid = pid
                pending_session = self.device.attach(pid)
                spawned = False
            else:
                subprocess.run(
                    ["adb", "shell", "am", "force-stop", APP_PACKAGE],
                    capture_output=True, check=False, env=_ENV,
                )
                time.sleep(1)
                pending_pid = self.device.spawn([APP_PACKAGE])
                pending_session = self.device.attach(pending_pid)
                spawned = True

            pending_script = pending_session.create_script(HOOK_JS)
            pending_script.on("message", self._on_message)
            pending_script.load()

            if spawned:
                self.device.resume(pending_pid)
                time.sleep(10)  # 等 App 启动完成
            else:
                time.sleep(3)

            # 全部成功才对外发布
            self.pid = pending_pid
            self.session = pending_session
            self.script = pending_script
            self._started = True
        except Exception as e:
            # 回滚:清理已 attach 的 session/script,把 spawned 的进程 kill
            logger.error(f"[service] start_session 失败,回滚: {e}")
            try:
                if pending_script is not None:
                    pending_script.unload()
            except Exception:
                pass
            try:
                if pending_session is not None:
                    pending_session.detach()
            except Exception:
                pass
            if spawned and pending_pid is not None:
                try:
                    self.device.kill(pending_pid)
                except Exception:
                    pass
            return {"ok": False, "reason": f"start_session 异常: {e}"}

        mode = "spawn" if spawned else "attach"
        logger.info(
            f"[service] start drama={drama} total={total_eps} pid={self.pid} mode={mode}"
        )
        self.log_trace("session_start", {
            "drama": drama, "total_eps": total_eps, "pid": self.pid, "mode": mode,
        })
        return {
            "ok": True,
            "pid": self.pid,
            "mode": mode,
            "output_dir": str(self.output_dir),
        }

    def end_session(self) -> dict:
        captured = len(self.state.by_kid) if self.state else 0
        try:
            if self.script:
                self.script.unload()
            if self.session:
                self.session.detach()
        except Exception as e:
            logger.warning(f"[service] end_session detach 异常: {e}")
        self._started = False
        self.log_trace("session_end", {"captured": captured})
        return {"ok": True, "captured_kids": captured}

    def restart_app(self) -> dict:
        """force-stop → spawn → 重建 session。"""
        if not self.device or not self.drama or self.total_eps is None:
            return {"ok": False, "reason": "需要先 start_session 至少一次"}
        try:
            if self.script:
                self.script.unload()
            if self.session:
                self.session.detach()
        except Exception:
            pass
        subprocess.run(
            ["adb", "shell", "am", "force-stop", APP_PACKAGE],
            capture_output=True, check=False, env=_ENV,
        )
        time.sleep(1)
        self.pid = self.device.spawn([APP_PACKAGE])
        self.session = self.device.attach(self.pid)
        self.script = self.session.create_script(HOOK_JS)
        self.script.on("message", self._on_message)
        self.script.load()
        self.device.resume(self.pid)
        time.sleep(10)
        # 清掉旧的 cells / cur_seg / state.last_new
        self.cells = None
        self.seg_btn = {}
        self.cur_seg = [None]
        with self.state.lock:
            self.state.last_new = None
        self.log_trace("app_restart", {"pid": self.pid})
        logger.info(f"[service] restart_app pid={self.pid}")
        return {"ok": True, "pid": self.pid}

    # ------------------------------------------------------------------
    # Frida 消息回调
    # ------------------------------------------------------------------

    def _on_message(self, msg, _data):
        if msg.get("type") != "send":
            if msg.get("type") == "error":
                logger.warning(f"[JS ERR] {msg.get('description','')[:200]}")
            return
        p = msg["payload"]
        t = p.get("t")
        if t == "cap":
            # 诊断日志:每个 cap 到达都单独记录,便于排查 fire 序列和集数对齐
            kid = p.get("kid", "")
            spd = p.get("spadea", "")
            has_key = bool(p.get("key"))
            streams = p.get("streams") or []
            first_url = (streams[0].get("main_url") if streams else "") or ""
            # 从 URL 片段里找文件 hash / video_id 的最后 8 字符
            url_tail = first_url[-80:] if first_url else ""
            logger.info(
                f"[cap] kid={kid[:8]} cluster={kid[8:12] if len(kid)>=12 else '?'} "
                f"has_key={has_key} n_streams={len(streams)} spd8={spd[:8]} "
                f"url_tail={url_tail!r}"
            )
            self.log_trace("cap_ingest", {
                "kid": kid, "kid_short": kid[:8],
                "kid_cluster": kid[8:12] if len(kid) >= 12 else "",
                "spadea8": spd[:8], "has_key": has_key,
                "n_streams": len(streams),
                "url_tail": url_tail,
            })
            self.state.ingest(p)
        elif t == "ready":
            logger.info(f"[Hook] {p.get('msg')}")
        elif t == "err":
            logger.warning(f"[Hook err] {p['msg']}")

    # ------------------------------------------------------------------
    # 状态查询
    # ------------------------------------------------------------------

    def get_state_snapshot(self) -> dict:
        """快照当前服务状态。Agent 每步开头都可以调。"""
        return {
            "drama": self.drama,
            "total_eps": self.total_eps,
            "output_dir": str(self.output_dir) if self.output_dir else None,
            "frida_pid": self.pid,
            "started": self._started,
            "cluster_locked": self.state.cluster if self.state else None,
            "captured_kids": len(self.state.by_kid) if self.state else 0,
            "has_cells": self.cells is not None,
            "num_cells": len(self.cells) if self.cells else 0,
            "current_segment": self.cur_seg[0],
            "current_activity": _current_activity(),
            "existing_episodes": self._list_existing_episodes(),
        }

    def _list_existing_episodes(self) -> list[int]:
        if not self.output_dir:
            return []
        eps = set()
        for p in _glob.glob(str(self.output_dir / "episode_*.mp4")):
            name = os.path.basename(p)
            # episode_NNN_XXXXXXXX.mp4
            if len(name) > 15 and name[:8] == "episode_" and name[8:11].isdigit():
                if os.path.getsize(p) > 100 * 1024:
                    eps.add(int(name[8:11]))
        return sorted(eps)

    # ------------------------------------------------------------------
    # Trace(Agent 决策日志,便于复盘)
    # ------------------------------------------------------------------

    def log_trace(self, event: str, payload: dict):
        if not self.agent_log_path:
            return
        append_jsonl(self.agent_log_path, {
            "ts": time.time(),
            "event": event,
            **payload,
        })

    # ------------------------------------------------------------------
    # UI 原语
    # ------------------------------------------------------------------

    def screenshot(self, label: str | None = None) -> dict:
        """pull 一张截图到 output_dir/screenshots/。返回 {ok, path}。"""
        if not self.output_dir:
            return {"ok": False, "reason": "未 start_session"}
        shot_dir = self.output_dir / "screenshots"
        shot_dir.mkdir(exist_ok=True)
        ts = int(time.time() * 1000)
        name = f"{ts}_{label}.png" if label else f"{ts}.png"
        local = shot_dir / name
        remote = f"/sdcard/_hongguo_agent_{ts}.png"
        try:
            subprocess.run(
                ["adb", "shell", "screencap", "-p", remote],
                capture_output=True, check=True, env=_ENV, timeout=10,
            )
            subprocess.run(
                ["adb", "pull", remote, str(local)],
                capture_output=True, check=True, env=_ENV, timeout=15,
            )
            subprocess.run(
                ["adb", "shell", "rm", remote],
                capture_output=True, check=False, env=_ENV, timeout=5,
            )
        except subprocess.CalledProcessError as e:
            return {"ok": False, "reason": f"adb screencap/pull 失败: {e}"}
        except subprocess.TimeoutExpired:
            return {"ok": False, "reason": "adb screencap 超时"}
        return {"ok": True, "path": str(local)}

    def dump_ui_xml(self, include_full: bool = False) -> dict:
        """dump uiautomator XML。返回摘要(节点数 + 有 text/resource-id 的节点列表)。
        include_full=True 时把完整 XML 写到文件返回路径,避免塞进 tool_result。
        """
        xml = read_ui_xml_from_device()
        if not xml:
            return {"ok": False, "reason": "uiautomator dump 失败(可能在播放器或系统忙)"}
        import xml.etree.ElementTree as ET
        try:
            root = ET.fromstring(xml)
        except ET.ParseError as e:
            return {"ok": False, "reason": f"XML 解析失败: {e}", "raw_len": len(xml)}
        nodes = []
        total = 0
        for n in root.iter("node"):
            total += 1
            text = (n.get("text") or "").strip()
            rid = n.get("resource-id") or ""
            desc = (n.get("content-desc") or "").strip()
            bounds = n.get("bounds") or ""
            if text or desc or rid.endswith(("drv", "jy3", "h65", "d1")):
                nodes.append({
                    "text": text[:60],
                    "desc": desc[:60],
                    "rid": rid.rsplit("/", 1)[-1] if rid else "",
                    "bounds": bounds,
                    "cls": (n.get("class") or "").rsplit(".", 1)[-1],
                })
        out = {"ok": True, "total_nodes": total, "interesting_nodes": nodes[:80]}
        if include_full and self.output_dir:
            xml_path = self.output_dir / "screenshots" / f"ui_{int(time.time()*1000)}.xml"
            xml_path.parent.mkdir(exist_ok=True)
            xml_path.write_text(xml, encoding="utf-8")
            out["xml_path"] = str(xml_path)
        return out

    def tap(self, x: int, y: int, settle_s: float = 0.6) -> dict:
        run_adb(["shell", "input", "tap", str(x), str(y)])
        if settle_s > 0:
            time.sleep(settle_s)
        return {"ok": True, "tapped": [x, y]}

    def swipe(self, x1: int, y1: int, x2: int, y2: int,
              duration_ms: int = 500, settle_s: float = 0.8) -> dict:
        run_adb(["shell", "input", "swipe", str(x1), str(y1),
                 str(x2), str(y2), str(duration_ms)])
        if settle_s > 0:
            time.sleep(settle_s)
        return {"ok": True}

    def press_back(self, settle_s: float = 1.0) -> dict:
        run_adb(["shell", "input", "keyevent", "KEYCODE_BACK"])
        if settle_s > 0:
            time.sleep(settle_s)
        return {"ok": True}

    # ------------------------------------------------------------------
    # 导航(B 模式: 冷启动 → 搜索 → 点海报 → 进播放器)
    # ------------------------------------------------------------------

    def navigate_to_drama(self, timeout: float = 25.0) -> dict:
        """按 navigate_to_drama_v2: deeplink → 历史/输入 → 点海报 → 等播放器。"""
        if not self.drama:
            return {"ok": False, "reason": "未 start_session 传入 drama"}
        ok = navigate_to_drama_v2(self.drama, timeout=timeout)
        act = _current_activity()
        self.log_trace("navigate_to_drama", {"ok": ok, "activity": act[:120]})
        return {"ok": ok, "current_activity": act[:200]}

    # ------------------------------------------------------------------
    # 选集面板
    # ------------------------------------------------------------------

    def open_episode_panel(self, settle_s: float = 1.2) -> dict:
        """动态定位"选集" tab/按钮并 tap。支持两种 App UI 模式:
        - 紧凑模式:底部独立"选集"按钮召唤 bottom sheet 面板
        - 详情模式:播放器下方"简介/选集"双 tab 切换
        返回 {ok, panel_visible, tapped_at, attempts}。
        """
        import re as _re
        import xml.etree.ElementTree as ET

        def _parse_bounds(s: str | None) -> tuple[int, int, int, int] | None:
            m = _re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", s or "")
            return tuple(int(x) for x in m.groups()) if m else None  # type: ignore

        attempts = []

        def _find_xuanji_tap() -> tuple[int, int] | None:
            xml = read_ui_xml_from_device()
            if not xml:
                return None
            try:
                root = ET.fromstring(xml)
            except ET.ParseError:
                return None
            cands: list[tuple[int, int]] = []
            for n in root.iter("node"):
                txt = (n.get("text") or "").strip()
                if txt != "选集":
                    continue
                b = _parse_bounds(n.get("bounds") or "")
                if not b:
                    continue
                cands.append(((b[0] + b[2]) // 2, (b[1] + b[3]) // 2))
            if not cands:
                return None
            # 若多个"选集",优先最下方(更可能是主入口)
            cands.sort(key=lambda xy: -xy[1])
            return cands[0]

        def _panel_visible() -> bool:
            xml = read_ui_xml_from_device()
            if not xml:
                return False
            try:
                root = ET.fromstring(xml)
            except ET.ParseError:
                return False
            ivi_count = sum(
                1 for n in root.iter("node")
                if (n.get("resource-id") or "").endswith("/ivi")
                and (n.get("text") or "").strip().isdigit()
            )
            return ivi_count >= 5

        # 已经可见就不重复 tap
        if _panel_visible():
            return {"ok": True, "panel_visible": True, "attempts": attempts,
                    "note": "panel 已可见,跳过 tap"}

        # 方案 1:动态找"选集"节点
        tap = _find_xuanji_tap()
        if tap:
            attempts.append({"strategy": "xml_xuanji", "xy": list(tap)})
            run_adb(["shell", "input", "tap", str(tap[0]), str(tap[1])])
            time.sleep(settle_s)
            if _panel_visible():
                return {"ok": True, "panel_visible": True,
                        "tapped_at": list(tap), "attempts": attempts}

        # 方案 2:旧坐标 (540, 1820) fallback,重试 3 次(详情页模式首次 tap 可能仅切换 tab,
        # 面板在第二次 tap 才弹出;实测 navigate 后进入详情页 UI 时常见此行为)
        fallback = (540, 1820)
        for attempt_idx in range(1, 4):
            attempts.append({
                "strategy": f"legacy_540_1820_try{attempt_idx}", "xy": list(fallback),
            })
            run_adb(["shell", "input", "tap", str(fallback[0]), str(fallback[1])])
            time.sleep(settle_s + 0.4)  # 详情页模式 UI 动画较慢
            if _panel_visible():
                return {"ok": True, "panel_visible": True,
                        "tapped_at": list(fallback), "attempts": attempts}

        return {"ok": True, "panel_visible": False, "attempts": attempts,
                "reason": "所有策略均未检测到 /ivi 格子>=5"}

    def scan_episode_panel(self) -> dict:
        """扫描选集面板,填充 self.cells / self.seg_btn。
        依赖 self.total_eps。会关闭面板(发 BACK)。
        """
        if self.total_eps is None:
            return {"ok": False, "reason": "total_eps 未设置"}
        try:
            cells, seg_btn = _scan_panel_impl(self.total_eps)
        except Exception as e:
            return {"ok": False, "reason": f"扫描异常: {e}"}
        self.cells = cells
        self.seg_btn = seg_btn
        self.cur_seg = [None]
        # 落盘 cells.json
        if self.output_dir:
            import json as _json
            (self.output_dir / "cells.json").write_text(
                _json.dumps({
                    "cells": {str(k): list(v) for k, v in cells.items()},
                    "seg_btn": {k: list(v) for k, v in seg_btn.items()},
                }, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        missing = [i for i in range(1, self.total_eps + 1) if i not in cells]
        self.log_trace("scan_panel", {
            "got": len(cells), "expected": self.total_eps, "missing_count": len(missing),
        })
        return {
            "ok": True,
            "cells_count": len(cells),
            "expected": self.total_eps,
            "seg_buttons": list(seg_btn.keys()),
            "missing_episodes": missing[:20],
        }

    def tap_episode_cell(self, ep: int) -> dict:
        """唤起面板 → 切段 → tap 对应格子。
        不等 Hook,调用方用 wait_capture 读取结果。
        """
        if self.cells is None:
            return {"ok": False, "reason": "未 scan_episode_panel"}
        if ep not in self.cells:
            return {"ok": False, "reason": f"ep{ep} 无坐标"}
        if self.total_eps is None:
            return {"ok": False, "reason": "total_eps 未设置"}
        # 唤起面板:先检查是否已可见,否则用动态定位
        panel_result = self.open_episode_panel(settle_s=1.2)
        if not panel_result.get("panel_visible"):
            return {"ok": False,
                    "reason": f"面板无法打开: {panel_result.get('reason','未知')}",
                    "panel_result": panel_result}
        # 切段
        target_seg = _ep_to_segment(ep, self.total_eps)
        seg_switched = False
        if self.cur_seg[0] != target_seg and target_seg in self.seg_btn:
            sx, sy = self.seg_btn[target_seg]
            run_adb(["shell", "input", "tap", str(sx), str(sy)])
            time.sleep(0.8)
            self.cur_seg[0] = target_seg
            seg_switched = True
        # 武装 state:下一个 ingest 会保存到 first_cap_after_arm
        # 即使 wait_capture 调用晚于 fire,也能拿到首个 cap 而非末个预加载
        self.state.arm_for_tap()
        x, y = self.cells[ep]
        run_adb(["shell", "input", "tap", str(x), str(y)])
        self.log_trace("tap_episode_cell", {
            "ep": ep, "xy": [x, y], "seg": target_seg, "seg_switched": seg_switched,
        })
        return {"ok": True, "tapped_at": [x, y], "segment": target_seg,
                "seg_switched": seg_switched}

    # ------------------------------------------------------------------
    # 捕获 & 下载
    # ------------------------------------------------------------------

    def wait_capture(self, timeout_s: float = 4.0, settle_s: float = 1.5) -> dict:
        """等 Hook fire: 首个 cap → settle → 取末个。"""
        cap = self.state.wait_new(timeout=timeout_s, settle=settle_s)
        if cap is None:
            return {"ok": False, "reason": "timeout"}
        best = cap.best_stream(max_short_side=1080) if cap.streams else None
        streams_meta = [{
            "vheight": s.get("vheight", 0),
            "vwidth": s.get("vwidth", 0),
            "bitrate": s.get("bitrate", 0),
            "has_main_url": bool(s.get("main_url")),
            "has_backup_url": bool(s.get("backup_url")),
        } for s in cap.streams]
        return {
            "ok": True,
            "kid": cap.kid,
            "kid_short": cap.kid[:8],
            "kid_cluster": cap.kid[8:12],
            "has_key": bool(cap.key) and len(cap.key) == 32,
            "num_streams": len(cap.streams),
            "streams": streams_meta,
            "best_stream_height": best.get("vheight") if best else 0,
            "best_stream_bitrate": best.get("bitrate") if best else 0,
            "captured_at": cap.captured_at,
        }

    def download_episode(self, ep: int, kid: str, max_short_side: int = 1080) -> dict:
        """按 kid 从 state.by_kid 找 Capture,下载并解密到 episode_NNN_XXXXXXXX.mp4。"""
        if not self.output_dir:
            return {"ok": False, "reason": "未 start_session"}
        cap = self.state.by_kid.get(kid)
        if cap is None:
            return {"ok": False, "reason": f"kid={kid[:8]} 未在 state 中"}
        if not cap.key or len(cap.key) != 32:
            return {"ok": False, "reason": f"kid={kid[:8]} key 缺失或长度错"}
        out = self.output_dir / f"episode_{ep:03d}_{kid[:8]}.mp4"
        try:
            ok = _download_and_decrypt(cap, str(out), max_short_side)
        except Exception as e:
            return {"ok": False, "reason": f"download_and_decrypt 异常: {e}"}
        if not ok:
            return {"ok": False, "reason": "下载或解密失败(见 log)"}
        size_mb = os.path.getsize(out) / 1024 / 1024
        self.log_trace("download_episode", {
            "ep": ep, "kid_short": kid[:8], "size_mb": round(size_mb, 2),
            "path": str(out),
        })
        return {
            "ok": True, "path": str(out), "size_mb": round(size_mb, 2),
            "kid_short": kid[:8],
        }

    def verify_playable(self, file_path: str) -> dict:
        try:
            ok = _verify_playable(file_path)
        except Exception as e:
            return {"ok": False, "reason": f"verify 异常: {e}"}
        return {"ok": bool(ok), "path": file_path}

    def extract_first_frame(self, file_path: str, time_s: float = 3.0) -> dict:
        """用 opencv 从 mp4 抽指定时刻的帧,保存为 JPEG。"""
        if not os.path.exists(file_path):
            return {"ok": False, "reason": f"文件不存在: {file_path}"}
        try:
            import cv2
        except ImportError:
            return {"ok": False, "reason": "opencv-python 未安装"}
        cap = cv2.VideoCapture(file_path)
        if not cap.isOpened():
            return {"ok": False, "reason": "无法打开视频"}
        # 尝试定位到 time_s
        cap.set(cv2.CAP_PROP_POS_MSEC, time_s * 1000)
        ret, frame = cap.read()
        if not ret:
            # 退回首帧
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ret, frame = cap.read()
        cap.release()
        if not ret:
            return {"ok": False, "reason": "读取帧失败"}
        out_dir = (self.output_dir / "frames") if self.output_dir else Path("./frames")
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / (
            f"{Path(file_path).stem}_frame_{int(time_s * 1000)}.jpg"
        )
        cv2.imwrite(str(out_path), frame, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
        return {"ok": True, "path": str(out_path)}

    def compare_download_with_screen(self, file_path: str,
                                      expected_episode: int,
                                      time_s: float = 3.0) -> dict:
        """一步式对齐校验:
        1. 抽 mp4 time_s 秒处的帧
        2. 截 App 当前屏幕(此时应显示 expected_episode 集的画面)
        3. VLM 对比是否同集内容
        返回 {ok, same_episode, confidence, reason, frame_a, frame_b}。
        """
        r = self.extract_first_frame(file_path, time_s=time_s)
        if not r.get("ok"):
            return {"ok": False, "reason": f"抽帧失败: {r.get('reason')}"}
        frame_a = r["path"]
        shot = self.screenshot(label=f"compare_ep{expected_episode}")
        if not shot.get("ok"):
            return {"ok": False, "reason": f"截图失败: {shot.get('reason')}"}
        from .vision import compare_two_images_impl
        result = compare_two_images_impl(frame_a, shot["path"], expected_episode)
        return result

    def write_manifest(self, ep: int, kid: str, status: str,
                       extra: dict | None = None) -> dict:
        if not self.manifest_path:
            return {"ok": False, "reason": "manifest_path 未初始化"}
        payload = {
            "episode": ep, "kid": kid, "status": status,
            "timestamp": time.time(),
        }
        if extra:
            payload.update(extra)
        append_jsonl(self.manifest_path, payload)
        return {"ok": True}

    # ------------------------------------------------------------------
    # 便捷: 把 state.by_kid 里最近的 Capture 列给 Agent(仅元数据)
    # ------------------------------------------------------------------

    def list_recent_captures(self, limit: int = 5) -> dict:
        with self.state.lock:
            caps = sorted(
                self.state.by_kid.values(),
                key=lambda c: c.captured_at, reverse=True,
            )[:limit]
        return {
            "ok": True,
            "count": len(caps),
            "items": [{
                "kid": c.kid, "kid_short": c.kid[:8], "kid_cluster": c.kid[8:12],
                "has_key": bool(c.key) and len(c.key) == 32,
                "captured_at": c.captured_at,
                "num_streams": len(c.streams),
            } for c in caps],
        }
