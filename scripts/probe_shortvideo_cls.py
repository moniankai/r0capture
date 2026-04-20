"""Probe: attach 到正在运行的红果 App,针对 com.dragon.read.component.shortvideo 命名空间
枚举已加载类,找出目标方法所在的类.

前置:
  - App 已运行,且已进入 ShortSeriesActivity (播放过视频,使播放器类加载)
"""
from __future__ import annotations
import sys, time
from pathlib import Path
import frida

APP_PACKAGE = "com.phoenix.read"

HOOK = r"""
var TARGET_METHODS = {
    'playItemOfN': 1, 'doSetPlayItem': 1, 'setPlayEpisode': 1, 'getPlayEpisode': 1,
    'setCurrentPlayIndex': 1, 'playNext': 1, 'playPrev': 1, 'playPrevious': 1,
    'playPrevChapter': 1, 'replayItem': 1, 'doPlayItemOfN': 1, 'doPlay': 1,
    'onItemClick': 1, 'onEpisodeClick': 1, 'selectEpisode': 1
};

function doScan(nsFilter) {
    Java.perform(function() {
        send({t:'scan_start', ns: nsFilter});
        var all = Java.enumerateLoadedClassesSync();
        send({t:'enum_done', total: all.length});

        var filtered = [];
        for (var i = 0; i < all.length; i++) {
            if (all[i].indexOf(nsFilter) >= 0) filtered.push(all[i]);
        }
        send({t:'filter_done', matched: filtered.length});

        var hits = [];
        var errs = 0;
        for (var i = 0; i < filtered.length; i++) {
            try {
                var JCls = Java.use(filtered[i]);
                var methods = JCls.class.getDeclaredMethods();
                for (var j = 0; j < methods.length; j++) {
                    var name = String(methods[j].getName());
                    if (TARGET_METHODS[name]) {
                        hits.push({
                            cls: filtered[i],
                            method: name,
                            sig: String(methods[j].toString())
                        });
                    }
                }
            } catch (e) { errs++; }
            if (i > 0 && i % 500 === 0) {
                send({t:'progress', done: i, total: filtered.length, hits: hits.length, errs: errs});
            }
        }
        send({t:'hits', data: hits});
        send({t:'done', errs: errs});
    });
}

rpc.exports = {
    scan: function(ns) { doScan(ns); return true; }
};
send({t:'ready'});
"""


def _get_main_pid_via_adb() -> int | None:
    import subprocess, os
    env = {**os.environ, "MSYS_NO_PATHCONV": "1"}
    r = subprocess.run(["adb", "shell", "pidof", APP_PACKAGE],
                       capture_output=True, text=True, env=env)
    s = (r.stdout or "").strip().split()
    # pidof 可能返回多个 PID (main + sub-process),取最小 (最早启动的通常是 main)
    pids = [int(x) for x in s if x.isdigit()]
    return min(pids) if pids else None


def main():
    device = frida.get_usb_device(timeout=10)
    main_pid = _get_main_pid_via_adb()
    if main_pid is None:
        print("ERROR: App 未运行", flush=True)
        return
    print(f"attach pid={main_pid} (via adb pidof)", flush=True)
    s = device.attach(main_pid)
    sc = s.create_script(HOOK)

    hits: list[dict] = []
    done = {"v": False}

    def on_msg(msg, data):
        if msg.get("type") != "send":
            if msg.get("type") == "error":
                print(f"[JS ERR] {msg.get('description','')[:300]}", flush=True)
            return
        p = msg["payload"]
        t = p.get("t")
        if t == "ready":
            print("[hook ready]", flush=True)
        elif t == "scan_start":
            print(f"[scan] ns={p['ns']}", flush=True)
        elif t == "enum_done":
            print(f"[enum] total classes loaded = {p['total']}", flush=True)
        elif t == "filter_done":
            print(f"[filter] matched = {p['matched']}", flush=True)
        elif t == "progress":
            print(f"[prog] {p['done']}/{p['total']} hits={p['hits']} errs={p['errs']}", flush=True)
        elif t == "hits":
            hits.extend(p["data"])
        elif t == "done":
            print(f"[done] errs={p['errs']}", flush=True)
            done["v"] = True

    sc.on("message", on_msg)
    sc.load()

    import threading
    def run():
        try:
            sc.exports_sync.scan("com.dragon.read")
        except Exception as e:
            print(f"[scan err] {e}", flush=True)
    th = threading.Thread(target=run, daemon=True)
    th.start()

    t0 = time.time()
    while time.time() - t0 < 240:
        time.sleep(3)
        if done["v"]:
            break

    print(f"\n======= 共 {len(hits)} hits =======", flush=True)
    by_method: dict[str, list[tuple[str, str]]] = {}
    for h in hits:
        by_method.setdefault(h["method"], []).append((h["cls"], h["sig"]))
    for m, items in by_method.items():
        print(f"\n## {m}  ({len(items)})", flush=True)
        for cls, sig in items[:15]:
            print(f"  [{cls}]", flush=True)
            print(f"    {sig[:200]}", flush=True)

    out = Path("d:/tmp/shortvideo_hits.txt")
    with out.open("w", encoding="utf-8") as f:
        for m, items in by_method.items():
            f.write(f"## {m}  ({len(items)})\n")
            for cls, sig in items:
                f.write(f"  [{cls}]\n    {sig}\n")
    print(f"\n写入 {out}", flush=True)


if __name__ == "__main__":
    main()
