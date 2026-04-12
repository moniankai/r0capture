"""
List drama rankings from the HongGuo (红果免费短剧) app.

Attaches Frida to the running app, hooks JSON parsing at the Java level,
and captures ranking API responses as the user navigates ranking tabs.


    python scripts/list_rankings.py
    python scripts/list_rankings.py --spawn      # Frida App回退
    python scripts/list_rankings.py --timeout 60  # 处理
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import frida
from loguru import logger

APP_PACKAGE = "com.phoenix.read"

# ---------------------------------------------------------------------------
# Frida Hook JSON 
# ---------------------------------------------------------------------------
HOOK_SCRIPT = r"""
'use strict';

var seen = {};       // 处理 hash 
var count = 0;
var urlLog = [];

function trySend(str, source) {
    var s;
    try {
        s = (typeof str === 'string')  str : str.toString();
    } catch(e) { return; }

    if (s.length < 200 || s.length > 5000000) return;

    // { [ 
    var ch = s.charAt(0);
    if (ch !== '{' && ch !== '[') return;

    var hash = s.length + ':' + s.substring(0, 300);
    if (seen[hash]) return;
    seen[hash] = true;

    count++;
    send({t: 'json', src: source, len: s.length, n: count, data: s});
    console.log('[Capture #' + count + '] ' + source + ' | ' + s.length + ' bytes');
}

Java.perform(function() {

    // ---- 1. org.json.JSONObject(String) ----
    try {
        var JSONObject = Java.use('org.json.JSONObject');
        JSONObject.$init.overload('java.lang.String').implementation = function(s) {
            trySend(s, 'JSONObject');
            return this.$init(s);
        };
        console.log('[+] org.json.JSONObject hooked');
    } catch(e) {
        console.log('[-] JSONObject: ' + e.message);
    }

    // ---- 2. org.json.JSONArray(String) ----
    try {
        var JSONArray = Java.use('org.json.JSONArray');
        JSONArray.$init.overload('java.lang.String').implementation = function(s) {
            trySend(s, 'JSONArray');
            return this.$init(s);
        };
        console.log('[+] org.json.JSONArray hooked');
    } catch(e) {
        console.log('[-] JSONArray: ' + e.message);
    }

    // ---- 3. Gson.fromJson (all overloads) ----
    try {
        var Gson = Java.use('com.google.gson.Gson');
        Gson.fromJson.overloads.forEach(function(ov) {
            ov.implementation = function() {
                try {
                    var a = arguments[0];
                    if (a !== null && a !== undefined) trySend(a, 'Gson');
                } catch(e) {}
                return ov.apply(this, arguments);
            };
        });
        console.log('[+] Gson hooked');
    } catch(e) {
        console.log('[-] Gson: ' + e.message);
    }

    // ---- 4. Fastjson (alibaba) ----
    try {
        var FastJSON = Java.use('com.alibaba.fastjson.JSON');
        FastJSON.parseObject.overloads.forEach(function(ov) {
            ov.implementation = function() {
                try { trySend(arguments[0], 'Fastjson'); } catch(e) {}
                return ov.apply(this, arguments);
            };
        });
        console.log('[+] Fastjson hooked');
    } catch(e) {
        console.log('[-] Fastjson: ' + e.message);
    }

    // ---- 5. Fastjson2 ----
    try {
        var FastJSON2 = Java.use('com.alibaba.fastjson2.JSON');
        FastJSON2.parseObject.overloads.forEach(function(ov) {
            ov.implementation = function() {
                try { trySend(arguments[0], 'Fastjson2'); } catch(e) {}
                return ov.apply(this, arguments);
            };
        });
        console.log('[+] Fastjson2 hooked');
    } catch(e) {
        console.log('[-] Fastjson2: ' + e.message);
    }

    // ---- 6. String(byte[], String) catch-all for JSON bytes ----
    try {
        var Str = Java.use('java.lang.String');
        Str.$init.overload('[B', 'java.lang.String').implementation = function(bytes, cs) {
            var result = this.$init(bytes, cs);
            try {
                var len = result.length();
                if (len > 800 && len < 1000000) {
                    var c0 = result.charAt(0);
                    if (c0 === 0x7B || c0 === 0x5B) {   // '{' or '['
                        trySend(result, 'String(B[])');
                    }
                }
            } catch(e) {}
            return result;
        };
        console.log('[+] String(byte[],charset) hooked');
    } catch(e) {
        console.log('[-] String(byte[]): ' + e.message);
    }

    // ---- 7. URL 处理 ----
    try {
        var URL = Java.use('java.net.URL');
        URL.$init.overload('java.lang.String').implementation = function(s) {
            try {
                var u = s.toString();
                if (u.indexOf('rank') !== -1 || u.indexOf('list') !== -1 ||
                    u.indexOf('hot') !== -1 || u.indexOf('top') !== -1 ||
                    u.indexOf('recommend') !== -1 || u.indexOf('drama') !== -1 ||
                    u.indexOf('comic') !== -1 || u.indexOf('bangdan') !== -1) {
                    send({t: 'url', url: u});
                    console.log('[URL] ' + u.substring(0, 200));
                }
            } catch(e) {}
            return this.$init(s);
        };
        console.log('[+] URL logger hooked');
    } catch(e) {
        console.log('[-] URL: ' + e.message);
    }

    console.log('\n[*] All hooks installed. Navigate to ranking tabs now.');
    send({t: 'ready'});
});
"""


# ---------------------------------------------------------------------------
# Ranking data analysis — adapted to HongGuo's actual API structure
#
# 处理 Gson 处理
# title, cover, series_id, play_cnt, score, episode_cnt, sub_title,
# recommend_info (JSON string with "rank" and "request_id"),
# rec_text_item (dict with "RecommendText" like "6899万热度"),
# tag_info, category_schema, etc.
# ---------------------------------------------------------------------------
from collections import defaultdict


def extract_ranked_items(captured: list[dict]) -> dict[str, list[dict]]:
    """Group captured Gson items by request_id into ranking lists.

    Returns {request_id: [sorted drama dicts]} where each drama dict
    has standardised keys.
    """
    groups: dict[str, list[dict]] = defaultdict(list)

    for item in captured:
        try:
            d = json.loads(item["data"]) if isinstance(item.get("data"), str) else item.get("data")
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(d, dict):
            continue

        rec_info_str = d.get("recommend_info", "")
        if not isinstance(rec_info_str, str) or '"rank"' not in rec_info_str:
            continue

        try:
            rec_info = json.loads(rec_info_str)
        except (json.JSONDecodeError, TypeError):
            continue

        rank_str = rec_info.get("rank", "")
        req_id = rec_info.get("request_id", "")
        if not rank_str or not req_id:
            continue

        title = d.get("title", "")
        if not title:
            continue

        # / 
        rec_text = ""
        rec_item = d.get("rec_text_item")
        if isinstance(rec_item, dict):
            rec_text = rec_item.get("RecommendText", "")

        # Tag (新剧, etc.)
        tag = ""
        tag_info = d.get("tag_info")
        if isinstance(tag_info, dict):
            tag = tag_info.get("text", "")

        groups[req_id].append({
            "rank": int(rank_str),
            "title": title,
            "series_id": str(d.get("series_id", "")),
            "cover": str(d.get("cover", ""))[:200],
            "play_cnt": d.get("play_cnt", 0),
            "score": d.get("score", ""),
            "episode_cnt": d.get("episode_cnt", ""),
            "sub_title": d.get("sub_title", ""),
            "heat_text": rec_text,
            "tag": tag,
        })

    # rank 处理
    for items in groups.values():
        items.sort(key=lambda x: x["rank"])

    return dict(groups)


def format_play_count(raw) -> str:
    """Format play count for display."""
    if not raw:
        return ""
    try:
        n = int(raw)
        if n >= 100_000_000:
            return f"{n / 100_000_000:.1f}亿"
        elif n >= 10_000:
            return f"{n / 10_000:.0f}万"
        else:
            return str(n)
    except (ValueError, TypeError):
        return str(raw)


def display_ranking(name: str, items: list[dict]) -> None:
    """Pretty-print a ranking list."""
    print(f"\n{'=' * 62}")
    print(f"  {name}（共 {len(items)} 部）")
    print(f"{'=' * 62}")

    for d in items[:10]:
        tag_str = f" [{d['tag']}]" if d.get("tag") else ""
        heat_str = f"  {d['heat_text']}" if d.get("heat_text") else ""
        print(f"  {d['rank']:2d}. {d['title']}{tag_str}")
        parts = []
        if d.get("play_cnt"):
            parts.append(f"播放: {format_play_count(d['play_cnt'])}")
        if d.get("score"):
            parts.append(f"评分: {d['score']}")
        if d.get("sub_title"):
            parts.append(d["sub_title"])
        if heat_str:
            parts.append(heat_str.strip())
        if parts:
            print(f"      {'  |  '.join(parts)}")


# ---------------------------------------------------------------------------
# ADB 
# ---------------------------------------------------------------------------
def run_adb(args: list[str]) -> subprocess.CompletedProcess:
    env = {**os.environ, "MSYS_NO_PATHCONV": "1"}
    return subprocess.run(
        ["adb"] + args, capture_output=True, text=True, timeout=15,
        check=False, env=env,
    )


# ---------------------------------------------------------------------------
# 
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="红果短剧 · 排行榜数据抓取")
    parser.add_argument("--spawn", action="store_true",
                        help="重启 App 并注入 Frida（更可靠）")
    parser.add_argument("--timeout", "-t", type=int, default=120,
                        help="最长捕获时间（秒）")
    parser.add_argument("--output", "-o", default="rankings_output.json",
                        help="输出 JSON 路径")
    args = parser.parse_args()

    captured: list[dict] = []      # JSON 
    urls_seen: list[str] = []      # API URL
    ready_event = threading.Event()
    lock = threading.Lock()

    def on_message(msg, _data):
        if msg["type"] == "error":
            logger.error(f"Frida error: {msg.get('description', msg)}")
            return
        if msg["type"] != "send":
            return
        p = msg["payload"]
        t = p.get("t", "")
        if t == "ready":
            ready_event.set()
        elif t == "json":
            with lock:
                captured.append(p)
        elif t == "url":
            with lock:
                urls_seen.append(p["url"])

    # ---- ----
    logger.info("=" * 55)
    logger.info("  红果短剧 · 排行榜数据抓取")
    logger.info("=" * 55)

    device = frida.get_usb_device()

    if args.spawn:
        logger.info("[1] 重启 App 并注入 Frida...")
        run_adb(["shell", "su", "-c", f"am force-stop {APP_PACKAGE}"])
        time.sleep(1)
        pid = device.spawn([APP_PACKAGE])
        session = device.attach(pid)
        script = session.create_script(HOOK_SCRIPT)
        script.on("message", on_message)
        script.load()
        device.resume(pid)
        logger.info(f"  App 已启动, PID: {pid}")
    else:
        logger.info("[1] Attach 到运行中的 App...")
        # Frida 16.x attach 处理
        # 处理 App 处理 PID
        target_pid = None
        for proc in device.enumerate_processes():
            # name identifier 
            if getattr(proc, "identifier", None) == APP_PACKAGE:
                target_pid = proc.pid
                break
            if proc.name == APP_PACKAGE:
                target_pid = proc.pid
                break
        if target_pid is None:
            # 处理 `adb shell ps` PID
            ps_result = run_adb(["shell", "ps"])
            for line in ps_result.stdout.splitlines():
                parts = line.split()
                if len(parts) >= 9 and parts[-1] == APP_PACKAGE:
                    try:
                        target_pid = int(parts[1])
                    except ValueError:
                        pass
                    break
        if target_pid is None:
            logger.error(f"未找到 {APP_PACKAGE} 进程，请确认 App 正在运行，或使用 --spawn 参数")
            sys.exit(1)
        logger.info(f"  找到进程 PID: {target_pid}")
        try:
            session = device.attach(target_pid)
        except Exception as e:
            logger.error(f"Attach 失败: {e}")
            logger.info("  建议使用 --spawn 参数重启 App")
            sys.exit(1)
        script = session.create_script(HOOK_SCRIPT)
        script.on("message", on_message)
        script.load()
        logger.info("  已附加到进程")

    if not ready_event.wait(15):
        logger.error("Hook 安装超时，请检查 Frida 版本兼容性")
        session.detach()
        sys.exit(1)

    logger.info("[2] Hook 已就绪!\n")

    # ---- ----
    print("  请在手机上执行以下操作：")
    print("  ──────────────────────────")
    print("  1. 进入排行榜页面")
    print("  2. 点击【热播榜】tab，等待 3 秒")
    print("  3. 点击【漫剧榜】tab，等待 3 秒")
    print("  4. 点击【必看榜】tab，等待 3 秒")
    print("  5. 如果数据不全，尝试下拉刷新每个榜单")
    print("  ──────────────────────────")
    print(f"  （最长等待 {args.timeout}s，按回车提前结束捕获）\n")

    # 处理
    stop = threading.Event()

    def wait_for_enter():
        try:
            input()
            stop.set()
        except EOFError:
            pass

    t = threading.Thread(target=wait_for_enter, daemon=True)
    t.start()

    start = time.time()
    last_count = 0
    while not stop.is_set() and (time.time() - start) < args.timeout:
        stop.wait(3)
        with lock:
            cur = len(captured)
        if cur != last_count:
            logger.info(f"  已捕获 {cur} 个 JSON 响应... (按回车结束)")
            last_count = cur

    # ---- ----
    logger.info(f"\n[3] 捕获完成，共 {len(captured)} 个 JSON 响应")

    if urls_seen:
        logger.info(f"  发现 {len(urls_seen)} 个相关 URL:")
        for u in urls_seen[:20]:
            logger.info(f"    {u[:150]}")

    if not captured:
        logger.warning("未捕获到任何 JSON 数据！")
        logger.info("  可能的原因：")
        logger.info("  1. App 使用了 Protobuf 而非 JSON")
        logger.info("  2. App 有 Frida 检测导致 hook 失效")
        logger.info("  3. 排行榜数据已缓存，未发起新请求（尝试下拉刷新）")
        logger.info("  建议使用 --spawn 参数重启 App")
        session.detach()
        sys.exit(1)

    # ---- API 处理 ----
    ranking_groups = extract_ranked_items(captured)

    if not ranking_groups:
        logger.warning("未捕获到排行榜数据（含 recommend_info + rank 字段的 JSON）")
        logger.info("  可能原因:")
        logger.info("  1. 未在手机上打开排行榜页面")
        logger.info("  2. 排行榜已缓存，未触发新请求（尝试下拉刷新）")
        logger.info("  建议: 进入排行榜页面并下拉刷新后重试")
        _save_raw(captured, args.output)
        session.detach()
        return

    # Keep only groups with ≥4 real items (filter out noise)
    valid_groups = {k: v for k, v in ranking_groups.items() if len(v) >= 4}
    if not valid_groups:
        valid_groups = ranking_groups  # : show all

    # request_id 逻辑回退
    sorted_groups = sorted(valid_groups.items(), key=lambda x: x[0])

    logger.info(f"\n[4] 找到 {len(sorted_groups)} 个排行榜列表:\n")

    # 处理
    for gid, (req_id, items) in enumerate(sorted_groups):
        ts_part = req_id[:14]
        complete = "✓" if len(items) >= 10 else f"(仅{len(items)}项)"
        name = f"榜单 {gid + 1} {complete}"
        display_ranking(name, items)

    # to file
    output = {
        "capture_time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "app": APP_PACKAGE,
        "ranking_groups": {},
    }
    for req_id, items in sorted_groups:
        ts = req_id[:14]
        output["ranking_groups"][req_id] = items

    out_path = Path(args.output)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    logger.info(f"\n[*] 数据已保存到 {out_path}")

    _save_raw(captured, args.output)

    try:
        session.detach()
    except Exception:
        pass

    logger.info("\n  完成!")


def _save_raw(captured: list[dict], output_name: str) -> None:
    """ raw captured JSONs for debugging."""
    raw_path = Path(output_name).with_name("rankings_raw.json")
    raw_out = []
    for item in captured:
        try:
            raw_out.append({
                "src": item.get("src"), "len": item.get("len"),
                "data": json.loads(item["data"]),
            })
        except Exception:
            raw_out.append({"src": item.get("src"), "len": item.get("len"),
                            "data_preview": item.get("data", "")[:2000]})
    with open(raw_path, "w", encoding="utf-8") as f:
        json.dump(raw_out, f, indent=2, ensure_ascii=False)
    logger.info(f"  原始数据已保存到 {raw_path}")


if __name__ == "__main__":
    main()
