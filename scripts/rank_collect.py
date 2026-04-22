#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""rank_collect — 从红果排行榜采集剧 metadata.

对应 HANDOFF_METADATA.md P1/P2 实现:
  1. attach 运行中 App (不 spawn, 避免破坏登录态)
  2. 单 Frida session + 单 hook (SaasVideoData setter + ViewHolder .j2)
  3. Python 驱动 adb tap/swipe 进指定排行榜并向下滚动
  4. 按 hook 首次出现顺序作 rank_pos (RecyclerView prefetch 可能 ±1,
     HANDOFF schema 的 source_ranks 是 UNION list,精度可接受)
  5. 产出:
       .planning/rankings/dramas.json     — 主档 (series_id 为 key, 覆盖写)
       .planning/rankings/snapshots.jsonl — 历史快照 (append-only)

用法:
  python scripts/rank_collect.py --ranks 热播榜,漫剧榜
  python scripts/rank_collect.py --ranks 热播榜 --per-rank-limit 20
  python scripts/rank_collect.py --ranks 必看榜 --per-rank-limit 0 --max-swipes 120

前提:
  - App (com.phoenix.read) 已启动, 任意页面均可
  - ADB 连通 (--serial 可指定设备)
  - frida-server 运行在手机上
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import queue
import re
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

APP_PACKAGE = "com.phoenix.read"
RANK_ACTIVITY = "ShortSeriesKmpRankingActivity"
MAIN_ACTIVITY = "MainFragmentActivity"

# 排行榜页 UI 坐标 (1080x1920) — 见 .planning/rankings/P0_探测结果.md
COORD_THEATER_TAB = (324, 1820)
COORD_RANK_ENTRY = (442, 381)
COORD_REWARD_POPUP_CLOSE = (540, 1472)
RANK_TAB_Y = 516
# 每榜: (tap_x, horizontal_swipe_count_before_tap)
# 横滑后新 tab 中心 x 参考 .planning/rankings/P0_探测结果.md (rank2.xml / rank3.xml 实测)
RANK_TAB_LAYOUT = {
    # 首屏可见 (无需横滑)
    "预约榜": (120, 0),
    "推荐榜": (324, 0),
    "热播榜": (528, 0),
    "漫剧榜": (732, 0),
    "新剧榜": (936, 0),
    # 横滑 1 次后可见 (tab 行左移, 以下 x 来自横滑 1 次后的 bounds)
    "演员榜": (390, 1),  # 按演员聚合, series_id 可能拿不到, 谨慎使用
    "必看榜": (594, 1),
    "收藏榜": (798, 1),
    # 横滑 2 次后可见
    "热搜榜": (960, 2),
}
RANK_PAGE_TEXTS = tuple(RANK_TAB_LAYOUT)
THEATER_RANK_ENTRY_TEXTS = ("排行榜", "筛选", "新剧", "预约")

# 横滑触发 tab 行滚动 (右边 tab 进入视野)
TAB_HSWIPE_START = (900, 516)
TAB_HSWIPE_END = (200, 516)
TAB_HSWIPE_DURATION_MS = 600

# 下滚 swipe 参数 (参考 v5_lean 经验)
SWIPE_START = (540, 1550)
SWIPE_END = (540, 900)
SWIPE_DURATION_MS = 700
SETTLE_AFTER_SWIPE = 1.0  # 等 RecyclerView bind + hook emit

NO_NEW_ROUNDS_STOP = 3
DEFAULT_MAX_SWIPES = 120
DEFAULT_PER_RANK_LIMIT = 50  # 0 = 不限
FULL_RANK_EXPECTED_MIN = 100

OUT_DIR = Path(".planning/rankings")
DRAMAS_JSON = OUT_DIR / "dramas.json"
SNAPSHOTS_JSONL = OUT_DIR / "snapshots.jsonl"


JS_HOOK = r"""
'use strict';
// Compose 榜单页无 SaasVideoData setter/ViewHolder.
// 数据通路: SeriesRankTabViewModel.c0(List<tc4.e>)  (分页 append 时触发)
//   tc4.e._a (y34.c wrapper) -> .g() -> j30 (120+ 字段的剧 data class)
//
// ⚠️ 字段映射修正 (2026-04-20 bug fix):
//   先前版本把 series_id 和 first_vid 搞反了. 真相 (对照 ground truth 验证):
//     《疯美人》真 series_id = 7624372698860227646, ep1 biz_vid = 7624374611039226905
//     j30.u() / j30.x() 返回 7624372698860227646  ← 真 series_id
//     j30.J() 返回 7624374611039226905            ← ep1 biz_vid (不是 sid)
//
// 正确映射:
//   j30.E() -> series_name     j30.x() -> series_id (u() 等价 fallback)
//   j30.i() -> total_eps       j30.g() -> cover_url
//   j30.A() -> recommend_text  j30.J() -> first_vid (ep1 biz_vid)
//   j30.v() -> JSON meta       j30.L() -> top_comment
//   j30.n() -> popularity (play count)
//
// 辅助字段 ep1_biz_vid_J 和 sid_fallback_u 保留供 debug, 不参与主逻辑.
Java.perform(function() {
    try {
        var VM = Java.use('com.dragon.read.kmp.shortvideo.distribution.page.tab.SeriesRankTabViewModel');
        var tc4e = Java.use('tc4.e');
        var y34c = Java.use('y34.c');
        var j30 = Java.use('com.bytedance.kmp.reading.model.j30');

        function safeS(v) {
            if (v === null || v === undefined) return null;
            var s = String(v);
            return (s === 'null' || s === '') ? null : s;
        }
        function safeN(v) {
            if (v === null || v === undefined) return null;
            var n = Number(v);
            return isNaN(n) ? null : n;
        }

        VM.c0.overload('java.util.List').implementation = function(list) {
            var ret = this.c0(list);
            try {
                if (list === null) return ret;
                var size = list.size();
                var batch = [];
                for (var i = 0; i < size; i++) {
                    var el = null;
                    try { el = Java.cast(list.get(i), tc4e); } catch(e) { continue; }
                    var vtm = null;
                    try { vtm = el._a.value; } catch(e) {}
                    if (!vtm) continue;
                    var video = null;
                    try { video = Java.cast(vtm, y34c).g(); } catch(e) {}
                    if (!video) continue;
                    var v = Java.cast(video, j30);

                    // series_id: 主用 x(), u() 作 fallback (实测两者等值)
                    var sid = null;
                    try { sid = safeS(v.x()); } catch(e) {}
                    if (!sid) {
                        try { sid = safeS(v.u()); } catch(e) {}
                    }
                    if (!sid) continue;

                    var row = { idx_in_batch: i, sid: sid };
                    try { row.name = safeS(v.E()); } catch(e) {}
                    try { row.total = safeN(v.i()); } catch(e) {}
                    try { row.cover = safeS(v.g()); } catch(e) {}
                    try { row.recommend_text = safeS(v.A()); } catch(e) {}
                    // first_vid: ep1 biz_vid, 来自 J() (19 位纯数字字符串)
                    try { row.first_vid = safeS(v.J()); } catch(e) {}
                    try { row.meta_json = safeS(v.v()); } catch(e) {}
                    try { row.top_comment = safeS(v.L()); } catch(e) {}
                    try { row.popularity = safeN(v.n()); } catch(e) {}
                    // debug aux: 保留 u() 验证等于 sid, 未来 App 变更时第一时间发现
                    try { row._sid_u = safeS(v.u()); } catch(e) {}
                    batch.push(row);
                }
                send({t:'batch', size: size, items: batch});
            } catch(e) {
                send({t:'warn', msg:'c0 parse fail: ' + String(e)});
            }
            return ret;
        };

        send({t:'ready'});
    } catch(e) {
        send({t:'err', err: String(e)});
    }
});
"""


# ─────────────────────── ADB 封装 ───────────────────────

def _adb(serial: str | None, *args: str, timeout: float = 10) -> subprocess.CompletedProcess:
    cmd = ["adb"]
    if serial:
        cmd += ["-s", serial]
    cmd += list(args)
    # MSYS_NO_PATHCONV 防 Git Bash 路径转换 (CLAUDE.md 项目约定)
    env = {**os.environ, "MSYS_NO_PATHCONV": "1"}
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout, env=env)


def adb_tap(serial: str | None, x: int, y: int) -> None:
    _adb(serial, "shell", "input", "tap", str(x), str(y))


def adb_swipe(serial: str | None, x1: int, y1: int, x2: int, y2: int, dur_ms: int) -> None:
    _adb(serial, "shell", "input", "swipe", str(x1), str(y1), str(x2), str(y2), str(dur_ms))


def adb_keyevent(serial: str | None, keycode: str) -> None:
    _adb(serial, "shell", "input", "keyevent", keycode)


def get_app_pid(serial: str | None) -> int | None:
    r = _adb(serial, "shell", "pidof", APP_PACKAGE)
    if r.returncode != 0:
        return None
    tok = r.stdout.strip().split()
    return int(tok[0]) if tok else None


def current_focus(serial: str | None) -> str:
    r = _adb(serial, "shell", "dumpsys window windows")
    for line in r.stdout.splitlines():
        if "mCurrentFocus" in line:
            return line.strip()
    return ""


def dump_ui_xml(serial: str | None) -> str:
    remote = "/sdcard/rank_collect_ui.xml"
    r = _adb(serial, "shell", "uiautomator", "dump", remote, timeout=8)
    if r.returncode != 0:
        return ""
    r = _adb(serial, "shell", "cat", remote, timeout=8)
    return (r.stdout or "") if r.returncode == 0 else ""


def _xml_has_text(xml: str, text: str) -> bool:
    return f'text="{text}"' in xml or f'content-desc="{text}"' in xml


def _theater_rank_entry_visible(xml: str) -> bool:
    return any(_xml_has_text(xml, text) for text in THEATER_RANK_ENTRY_TEXTS)


def _rank_page_visible(xml: str) -> bool:
    return any(_xml_has_text(xml, text) for text in RANK_PAGE_TEXTS)


def is_rank_page(serial: str | None) -> bool:
    focus = current_focus(serial)
    if RANK_ACTIVITY in focus:
        return True
    if APP_PACKAGE not in focus:
        return False
    return _rank_page_visible(dump_ui_xml(serial))


def dismiss_theater_overlay_if_blocked(serial: str | None) -> bool:
    xml = dump_ui_xml(serial)
    if _theater_rank_entry_visible(xml):
        return False

    focus = current_focus(serial)
    if MAIN_ACTIVITY not in focus or APP_PACKAGE not in focus:
        return False

    print("[nav] 剧场入口被弹窗/遮挡层阻断, 尝试关闭", flush=True)
    adb_tap(serial, *COORD_REWARD_POPUP_CLOSE)
    time.sleep(0.8)
    return True


# ─────────────────── 存储: dramas.json / snapshots.jsonl ───────────────────

def _now_iso() -> str:
    return _dt.datetime.now().replace(microsecond=0).isoformat()


def _atomic_write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def load_dramas(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def save_dramas(path: Path, dramas: dict[str, dict]) -> None:
    # source_ranks 从 set 转 list (按字母序稳定)
    serial_copy: dict[str, dict] = {}
    for sid, entry in dramas.items():
        e = dict(entry)
        if isinstance(e.get("source_ranks"), set):
            e["source_ranks"] = sorted(e["source_ranks"])
        serial_copy[sid] = e
    _atomic_write_json(path, serial_copy)


def append_snapshot_line(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


# ─────────────────── Hook 事件 → catalog merge ───────────────────

def _server_rank_pos(meta_json: str | None) -> int | None:
    """从 j30.v() 的 JSON 字符串里提取 rank (服务端权威排名)."""
    if not meta_json:
        return None
    try:
        d = json.loads(meta_json)
        r = d.get("rank")
        if r is None:
            return None
        return int(r)
    except (json.JSONDecodeError, ValueError, TypeError):
        return None


def _target_reached(collected: int, per_rank_limit: int) -> bool:
    return per_rank_limit > 0 and collected >= per_rank_limit


def _can_accept_rank_item(*, collected: int, per_rank_limit: int) -> bool:
    return per_rank_limit == 0 or collected < per_rank_limit


def _should_continue_collecting(*, no_new_rounds: int, swipes_done: int,
                                max_swipes: int, collected: int,
                                per_rank_limit: int,
                                stop_requested: bool) -> bool:
    if stop_requested or swipes_done >= max_swipes:
        return False
    if _target_reached(collected, per_rank_limit):
        return False
    if per_rank_limit > 0:
        return True
    if (collected >= FULL_RANK_EXPECTED_MIN
            and no_new_rounds >= NO_NEW_ROUNDS_STOP):
        return False
    return True


def _collect_stop_reason(*, no_new_rounds: int, swipes_done: int,
                         max_swipes: int, collected: int,
                         per_rank_limit: int) -> str:
    if _target_reached(collected, per_rank_limit):
        return "limit"
    if (per_rank_limit > 0 and swipes_done >= max_swipes
            and collected < per_rank_limit):
        return "max_swipes_before_limit"
    if (per_rank_limit == 0 and collected >= FULL_RANK_EXPECTED_MIN
            and no_new_rounds >= NO_NEW_ROUNDS_STOP):
        return "full_rank_no_new"
    if per_rank_limit > 0 and no_new_rounds >= NO_NEW_ROUNDS_STOP:
        return "no_new"
    if swipes_done >= max_swipes:
        return "max_swipes"
    return "interrupt"


def _merge_catalog_event(
    dramas: dict[str, dict],
    evt: dict,
    now_iso: str,
) -> bool:
    """把一个 catalog 事件 merge 进 dramas dict. 返回 True 如果是首见 sid."""
    sid = evt["sid"]
    first_seen = sid not in dramas
    entry = dramas.setdefault(sid, {
        "series_id": sid,
        "first_seen_at": now_iso,
        "source_ranks": set(),
    })

    def _upd(field: str, val: Any, *, only_if_empty: bool = False) -> None:
        if val is None or val == "":
            return
        if only_if_empty and entry.get(field):
            return
        entry[field] = val

    _upd("name", evt.get("name"))
    total = evt.get("total")
    if isinstance(total, (int, float)) and total and total > 0 and total != -1:
        entry["total"] = int(total)
    if not entry.get("first_vid"):
        _upd("first_vid", evt.get("first_vid"))
    _upd("cover_url", evt.get("cover"), only_if_empty=True)
    _upd("recommend_text", evt.get("recommend_text"), only_if_empty=True)
    _upd("top_comment", evt.get("top_comment"), only_if_empty=True)

    pop = evt.get("popularity")
    if isinstance(pop, (int, float)) and pop and pop > 0:
        entry["popularity"] = int(pop)

    # is_locked 启发式: 本轮信号不足, 先留 None, 由 P3 补
    entry.setdefault("is_locked", None)
    entry.setdefault("unlocked_eps", None)

    entry["last_updated_at"] = now_iso
    if isinstance(entry.get("source_ranks"), list):
        entry["source_ranks"] = set(entry["source_ranks"])
    return first_seen


# ─────────────────── 导航 ───────────────────

def back_to_main(serial: str | None, max_attempts: int = 6) -> bool:
    """连按 BACK 直到回到 MainFragmentActivity."""
    for _ in range(max_attempts):
        focus = current_focus(serial)
        if MAIN_ACTIVITY in focus:
            return True
        if APP_PACKAGE not in focus:
            # App 被 BACK 退出或 focus 离开 App → 重新拉起
            _adb(serial, "shell", "monkey", "-p", APP_PACKAGE, "-c",
                 "android.intent.category.LAUNCHER", "1")
            time.sleep(2.0)
            continue
        adb_keyevent(serial, "KEYCODE_BACK")
        time.sleep(0.7)
    return MAIN_ACTIVITY in current_focus(serial)


def nav_to_rank_activity(serial: str | None, *, force_reenter: bool = False) -> None:
    """保证当前在 ShortSeriesKmpRankingActivity.

    force_reenter=True: 无论当前在哪, 都先 BACK 到主界面再重新进入,
    确保排行榜 Fragment/RecyclerView 新建 → hook 能捕到首屏 bind.
    """
    if is_rank_page(serial) and not force_reenter:
        return
    if not back_to_main(serial):
        raise RuntimeError(f"无法回到主界面, 当前 focus: {current_focus(serial)}")
    # 多次尝试 (tap 动画期间可能 miss)
    for attempt in range(3):
        adb_tap(serial, *COORD_THEATER_TAB)
        time.sleep(2.2)  # 剧场 Fragment 切换动画 + 内容加载
        dismiss_theater_overlay_if_blocked(serial)
        adb_tap(serial, *COORD_RANK_ENTRY)
        time.sleep(3.0)  # Compose 页面加载稍慢
        if is_rank_page(serial):
            return
        focus = current_focus(serial)
        print(f"[nav] attempt {attempt+1} 未进排行榜, focus={focus}", flush=True)
        # 回主页再试
        back_to_main(serial)
        time.sleep(1.0)
    raise RuntimeError(f"未进入排行榜页 (3 次重试失败), 当前 focus: {focus}")


def tap_rank_tab(serial: str | None, rank_name: str) -> None:
    if rank_name not in RANK_TAB_LAYOUT:
        raise ValueError(f"不支持的榜单: {rank_name}  (已知: {list(RANK_TAB_LAYOUT)})")
    x, hswipe_count = RANK_TAB_LAYOUT[rank_name]
    for i in range(hswipe_count):
        adb_swipe(serial, *TAB_HSWIPE_START, *TAB_HSWIPE_END, TAB_HSWIPE_DURATION_MS)
        time.sleep(1.2)  # 等 tab 行滚动稳定 + uiautomator idle
    adb_tap(serial, x, RANK_TAB_Y)
    time.sleep(1.5)


# ─────────────────── 主采集流程 ───────────────────

class RankCollector:
    def __init__(self, serial: str | None, session_id: str,
                 per_rank_limit: int, max_swipes: int):
        self.serial = serial
        self.session_id = session_id
        self.per_rank_limit = per_rank_limit
        self.max_swipes = max_swipes
        self.events: "queue.Queue[dict]" = queue.Queue()
        self.dramas: dict[str, dict] = load_dramas(DRAMAS_JSON)
        self._normalize_source_ranks()
        self.script = None
        self.session = None
        self._hook_ready = threading.Event()
        self._stop = threading.Event()

    def _normalize_source_ranks(self) -> None:
        for v in self.dramas.values():
            sr = v.get("source_ranks")
            if isinstance(sr, list):
                v["source_ranks"] = set(sr)
            elif sr is None:
                v["source_ranks"] = set()

    # ── Frida ──
    def _on_msg(self, msg, data):
        if msg.get("type") != "send":
            if msg.get("type") == "error":
                print(f"[JS err] {msg.get('description', '')[:200]}", flush=True)
            return
        p = msg["payload"]
        t = p.get("t")
        if t == "ready":
            self._hook_ready.set()
            print("[rank_collect] hook ready", flush=True)
        elif t == "warn":
            print(f"[JS warn] {p.get('msg')}", flush=True)
        elif t == "batch":
            # 批处理: c0(List) 的一整批, 里面 items 按 j30 字段已提取好
            self.events.put(p)
        elif t == "err":
            print(f"[JS err payload] {p.get('err')}", flush=True)

    def _frida_server_user(self) -> str | None:
        """返回 frida-server 进程的 USER (root/shell/None)."""
        r = _adb(self.serial, "shell", "ps", "-A")
        for line in r.stdout.splitlines():
            if "frida-server" in line:
                parts = line.split()
                if parts:
                    return parts[0]  # 第一列是 USER
        return None

    def _start_frida_server(self) -> None:
        """用 -D (daemonize) 启动 frida-server 保持 root 上下文.

        不能用 `su -c 'nohup ... &'`: MIUI/Android 9 下 `&` 脱离 su 上下文,
        子进程变 shell user, attach App 时抛 PermissionDeniedError.
        frida-server 自带 -D 选项, fork 在 su 内部完成, 保证 root.
        """
        _adb(self.serial, "shell", "su", "-c",
             "killall -9 frida-server 2>/dev/null; sleep 1; "
             "/data/local/tmp/frida-server -D")

    def _ensure_frida_server(self) -> bool:
        """确保 frida-server 以 root 运行. 失败返回 False."""
        user = self._frida_server_user()
        if user == "root":
            return True
        if user == "shell":
            print("[rank_collect] frida-server 以 shell user 运行 (非 root), "
                  "重启为 root 模式...", flush=True)
        else:
            print("[rank_collect] frida-server 未运行, 自动拉起中 (需 root)...",
                  flush=True)
        self._start_frida_server()
        for i in range(10):
            time.sleep(1)
            user = self._frida_server_user()
            if user == "root":
                print(f"[rank_collect] frida-server 已启动 (user=root, "
                      f"等待 {i+1}s), 再等 3s 让 IPC listener ready...",
                      flush=True)
                time.sleep(3)
                return True
            if user == "shell":
                # 进程起来但降权了 — su 上下文丢失, 继续等
                continue
        print("[rank_collect] frida-server 自动拉起失败. 常见原因:", file=sys.stderr)
        print("  - 设备未 root (su 不可用)", file=sys.stderr)
        print("  - /data/local/tmp/frida-server 不存在或无执行权限",
              file=sys.stderr)
        print(f"  - frida-server 进程最终 user = {user} (期望 root)",
              file=sys.stderr)
        print("  → 手动检查: adb shell 'su -c \"ls -la /data/local/tmp/frida-server\"'",
              file=sys.stderr)
        return False

    def attach(self) -> None:
        # 1. 确保 frida-server 在跑
        if not self._ensure_frida_server():
            raise RuntimeError("frida-server 不可用")

        # 2. 确保 App 在跑
        pid = get_app_pid(self.serial)
        if not pid:
            print(f"[rank_collect] {APP_PACKAGE} 未运行, 自动拉起中...", flush=True)
            _adb(self.serial, "shell", "monkey", "-p", APP_PACKAGE,
                 "-c", "android.intent.category.LAUNCHER", "1")
            for i in range(15):
                time.sleep(1)
                pid = get_app_pid(self.serial)
                if pid:
                    print(f"[rank_collect] App 已启动 pid={pid} (等待 {i+1}s)",
                          flush=True)
                    time.sleep(3)  # splash/广告过掉, Java bridge 稳定
                    break
            if not pid:
                raise RuntimeError(f"{APP_PACKAGE} 自动拉起失败 (15s 超时)")

        # 3. attach (失败时重启 frida-server + 重试, 最多 2 次)
        import frida
        # PermissionDeniedError: frida-server 不是 root / listener 未 ready
        # ServerNotRunningError: 进程在但 socket 挂了
        # TransportError: IPC timeout (hook session 残留)
        retryable = (frida.ServerNotRunningError,
                     frida.PermissionDeniedError,
                     frida.TransportError)
        dev = (frida.get_device(self.serial) if self.serial
               else frida.get_usb_device())
        print(f"[rank_collect] attach pid={pid} on {dev.id}", flush=True)
        last_err = None
        for attempt in range(2):
            try:
                self.session = dev.attach(pid)
                break
            except retryable as e:
                last_err = e
                print(f"[rank_collect] attach 失败 ({type(e).__name__}: {e}), "
                      f"强制重启 frida-server 重试 ({attempt+1}/2)...", flush=True)
                self._start_frida_server()  # 用 -D 保持 root
                time.sleep(4)  # listener ready
                dev = (frida.get_device(self.serial) if self.serial
                       else frida.get_usb_device())
        else:
            raise RuntimeError(f"attach 重试 2 次后仍失败: {last_err}")

        self.script = self.session.create_script(JS_HOOK)
        self.script.on("message", self._on_msg)
        self.script.load()
        if not self._hook_ready.wait(timeout=10):
            raise RuntimeError("hook ready 超时")

    def detach(self) -> None:
        try:
            if self.script:
                self.script.unload()
        except Exception:
            pass
        try:
            if self.session:
                self.session.detach()
        except Exception:
            pass

    # ── 单榜采集 ──
    def collect_one_rank(self, rank_name: str) -> dict:
        print(f"\n[rank_collect] ===== 开始采集: {rank_name} =====", flush=True)
        # 等 nav/previous-rank c0 事件全部送达后再 clear (IPC 延迟 ~几百 ms)
        time.sleep(1.5)
        drained = 0
        try:
            while True:
                self.events.get_nowait()
                drained += 1
        except queue.Empty:
            pass
        if drained:
            print(f"[rank_collect] drained {drained} stale events", flush=True)

        tap_rank_tab(self.serial, rank_name)
        time.sleep(2.5)  # 等列表初次 bind + Compose 数据到达

        # 榜级 state
        rank_order: list[str] = []  # 首见顺序 = rank_pos
        rank_seen: set[str] = set()
        no_new_rounds = 0
        swipes_done = 0

        now_iso = _now_iso()

        def drain() -> int:
            """处理队列里的 batch 事件, 返回本轮新增 sid 数.

            rank_pos 用 Python 首见顺序作全局位置 (服务端的 rank 只是 batch 内的
            1-N, 不是全局排名). server_batch_pos 作为辅助字段存下来.
            """
            new_count = 0
            while True:
                try:
                    evt = self.events.get_nowait()
                except queue.Empty:
                    break
                if evt.get("t") != "batch":
                    continue
                for item in evt.get("items", []):
                    sid = item.get("sid")
                    if not sid:
                        continue
                    if not _can_accept_rank_item(
                            collected=len(rank_order),
                            per_rank_limit=self.per_rank_limit):
                        return new_count
                    # Sanity check: u() 和 x() 应等价. 若不等, 说明 App 版本变化,
                    # 新版本字段映射可能需要重新探测.
                    sid_u = item.get("_sid_u")
                    if sid_u and sid_u != sid:
                        print(f"[WARN] sid 字段不一致! x()={sid} u()={sid_u} "
                              f"name={item.get('name')!r} — 重跑 rank_probe_sid.py 验证",
                              flush=True)
                    was_first_global = _merge_catalog_event(
                        self.dramas, item, _now_iso())
                    if sid in rank_seen:
                        continue
                    rank_seen.add(sid)
                    rank_order.append(sid)
                    new_count += 1
                    rank_pos = len(rank_order)  # 全局首见顺序
                    srv_batch_pos = _server_rank_pos(item.get("meta_json"))
                    self.dramas[sid].setdefault("source_ranks", set()).add(
                        f"{rank_name}/{rank_pos}"
                    )
                    append_snapshot_line(SNAPSHOTS_JSONL, {
                        "ts": _now_iso(),
                        "rank_type": rank_name,
                        "rank_pos": rank_pos,
                        "server_batch_pos": srv_batch_pos,
                        "series_id": sid,
                        "session_id": self.session_id,
                    })
                    name = self.dramas[sid].get("name", "?")
                    total = self.dramas[sid].get("total", "?")
                    marker = "★" if was_first_global else " "
                    batch_tag = f"b{srv_batch_pos}" if srv_batch_pos else "-"
                    print(f"  {marker} [{rank_name}#{rank_pos:2d} {batch_tag}] {sid}  "
                          f"{name!r}  total={total}", flush=True)
            return new_count

        # 采首屏
        drain()

        while _should_continue_collecting(
                no_new_rounds=no_new_rounds,
                swipes_done=swipes_done,
                max_swipes=self.max_swipes,
                collected=len(rank_order),
                per_rank_limit=self.per_rank_limit,
                stop_requested=self._stop.is_set()):
            adb_swipe(self.serial, *SWIPE_START, *SWIPE_END, SWIPE_DURATION_MS)
            swipes_done += 1
            time.sleep(SETTLE_AFTER_SWIPE)
            added = drain()
            if added == 0:
                no_new_rounds += 1
                suffix = ""
                if self.per_rank_limit > 0 and len(rank_order) < self.per_rank_limit:
                    suffix = f", 继续至目标 {self.per_rank_limit}"
                elif self.per_rank_limit == 0:
                    suffix = (f", 不限模式继续至 Top {FULL_RANK_EXPECTED_MIN} "
                              f"或 max-swipes {self.max_swipes}")
                print(f"  . swipe#{swipes_done} no new "
                      f"({no_new_rounds}/{NO_NEW_ROUNDS_STOP}{suffix})", flush=True)
            else:
                no_new_rounds = 0

        # 榜结束, save 一次
        save_dramas(DRAMAS_JSON, self.dramas)

        summary = {
            "rank_name": rank_name,
            "unique_collected": len(rank_order),
            "swipes": swipes_done,
            "stopped_by": _collect_stop_reason(
                no_new_rounds=no_new_rounds,
                swipes_done=swipes_done,
                max_swipes=self.max_swipes,
                collected=len(rank_order),
                per_rank_limit=self.per_rank_limit,
            ),
        }
        print(f"[rank_collect] {rank_name} 采完: {summary}", flush=True)
        return summary

    # ── 多榜编排 ──
    def run(self, ranks: list[str]) -> list[dict]:
        summaries = []
        for idx, rank in enumerate(ranks):
            if self._stop.is_set():
                break
            # 每榜都 force_reenter: 上一榜可能把列表滚到底, tab bar 不在屏幕 y=516,
            # 直接 tap 会打到剧卡进入播放页. reenter 保证列表回到顶部 + tab bar 可见.
            # reenter 会带来 "上次选中 tab 的 c0" stale 事件, 由 collect_one_rank
            # 内的 sleep+drain 过滤.
            nav_to_rank_activity(self.serial, force_reenter=True)
            summaries.append(self.collect_one_rank(rank))
        return summaries

    def request_stop(self) -> None:
        self._stop.set()


# ─────────────────── main ───────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ranks", type=str, default="热播榜,漫剧榜",
                    help="榜单列表, 逗号分隔 (默认: 热播榜,漫剧榜)")
    ap.add_argument("--serial", type=str, default=os.environ.get("ADB_SERIAL"),
                    help="ADB 设备序列号")
    ap.add_argument("--per-rank-limit", type=int, default=DEFAULT_PER_RANK_LIMIT,
                    help=f"每榜采集上限 (默认: {DEFAULT_PER_RANK_LIMIT}; 0=不限, "
                         "用于尽量遍历完整榜单)")
    ap.add_argument("--max-swipes", type=int, default=DEFAULT_MAX_SWIPES,
                    help=f"每榜最大 swipe 次数/安全上限 (默认: {DEFAULT_MAX_SWIPES}; "
                         "不是采集条数)")
    args = ap.parse_args()

    ranks = [r.strip() for r in args.ranks.split(",") if r.strip()]
    unsupported = [r for r in ranks if r not in RANK_TAB_LAYOUT]
    if unsupported:
        print(f"[rank_collect] 不支持的榜: {unsupported}  "
              f"(已知: {list(RANK_TAB_LAYOUT)})", file=sys.stderr)
        return 2
    # 演员榜警告
    if "演员榜" in ranks:
        print("[rank_collect] ⚠️  演员榜按演员聚合, j30 字段可能拿不到 series_id",
              file=sys.stderr)

    serial = args.serial
    if serial is None:
        # 自动取第一个 device
        r = _adb(None, "devices")
        devs = [line.split()[0] for line in r.stdout.splitlines()[1:]
                if line.strip() and "device" in line.split()]
        if not devs:
            print("[rank_collect] 未发现 ADB 设备", file=sys.stderr)
            return 2
        serial = devs[0]
        print(f"[rank_collect] 自动选中设备: {serial}")

    session_id = _dt.datetime.now().strftime("sess_%Y%m%d_%H%M%S")
    print(f"[rank_collect] session_id={session_id}  ranks={ranks}  "
          f"per_rank_limit={args.per_rank_limit}  max_swipes={args.max_swipes}")

    collector = RankCollector(serial, session_id,
                              per_rank_limit=args.per_rank_limit,
                              max_swipes=args.max_swipes)

    def _sigint(sig, frame):
        print("\n[rank_collect] 收到 Ctrl-C, 清理中...", flush=True)
        collector.request_stop()
    signal.signal(signal.SIGINT, _sigint)

    try:
        collector.attach()
        summaries = collector.run(ranks)
    finally:
        collector.detach()
        save_dramas(DRAMAS_JSON, collector.dramas)

    # 汇总
    print("\n[rank_collect] ========== 汇总 ==========")
    total_unique_in_session = sum(s["unique_collected"] for s in summaries)
    print(f"  session_id       : {session_id}")
    print(f"  榜数             : {len(summaries)}")
    print(f"  本会话 rank 条目 : {total_unique_in_session}")
    print(f"  dramas.json 总计 : {len(collector.dramas)}")
    for s in summaries:
        print(f"  · {s['rank_name']:8s} unique={s['unique_collected']:3d}  "
              f"swipes={s['swipes']:2d}  stopped={s['stopped_by']}")
    print(f"\n产物:\n  {DRAMAS_JSON}\n  {SNAPSHOTS_JSONL}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
