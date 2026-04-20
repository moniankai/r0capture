"""Probe 红果 App: 找出 playItemOfN / doSetPlayItem / playPrev / playNext 所在的 Java 类。

策略：spawn + 同步枚举 + 分片 send(每 100 hits 批量发一次)
"""
from __future__ import annotations
import sys, time, subprocess, os
from pathlib import Path
import frida

APP_PACKAGE = "com.phoenix.read"

HOOK = r"""
var TARGET_METHODS = {
    'playItemOfN': 1, 'doSetPlayItem': 1, 'setPlayEpisode': 1, 'getPlayEpisode': 1,
    'setCurrentPlayIndex': 1, 'playNext': 1, 'playPrev': 1, 'playPrevious': 1,
    'playPrevChapter': 1, 'replayItem': 1
};

function doScan() {
    Java.perform(function() {
        send({t:'start'});
        var candidates = Java.enumerateLoadedClassesSync();
        send({t:'enum_done', count: candidates.length});

        var hits = [];
        var scanned = 0, errors = 0, filtered = 0;
        for (var i = 0; i < candidates.length; i++) {
            var cls = candidates[i];
            if (cls.indexOf('com.phoenix') < 0 &&
                cls.indexOf('com.bytedance') < 0 &&
                cls.indexOf('com.ss.') < 0 &&
                cls.indexOf('com.dragon.') < 0) continue;
            filtered++;
            try {
                var JCls = Java.use(cls);
                var methods = JCls.class.getDeclaredMethods();
                for (var j = 0; j < methods.length; j++) {
                    var m = methods[j];
                    var name = String(m.getName());
                    if (TARGET_METHODS[name]) {
                        hits.push({cls: cls, method: name, sig: String(m.toString())});
                    }
                }
                scanned++;
            } catch (e) { errors++; }

            if (filtered > 0 && filtered % 2000 === 0) {
                send({t:'progress', filtered: filtered, scanned: scanned, errors: errors, hit_count: hits.length});
                // 分片发 hits
                while (hits.length >= 100) {
                    send({t:'hits_chunk', items: hits.splice(0, 100)});
                }
            }
        }
        // 剩余
        while (hits.length > 0) {
            send({t:'hits_chunk', items: hits.splice(0, 100)});
        }
        send({t:'done', filtered: filtered, scanned: scanned, errors: errors});
    });
}

rpc.exports = {
    scan: function() { doScan(); return true; }
};
send({t:'ready'});
"""


def main():
    device = frida.get_usb_device(timeout=10)
    env = {**os.environ, "MSYS_NO_PATHCONV": "1"}
    subprocess.run(["adb", "shell", "am", "force-stop", APP_PACKAGE],
                   capture_output=True, check=False, env=env)
    time.sleep(1)
    pid = device.spawn([APP_PACKAGE])
    print(f"spawned pid={pid}", flush=True)
    s = device.attach(pid)
    sc = s.create_script(HOOK)

    hits: list[dict] = []
    events = {"done": False, "start": False}

    def on_msg(msg, data):
        if msg.get("type") != "send":
            if msg.get("type") == "error":
                print(f"[JS ERR] {msg.get('description','')[:300]}", flush=True)
            return
        p = msg["payload"]
        t = p.get("t")
        if t == "ready":
            print("[hook ready]", flush=True)
        elif t == "start":
            print("[scan start]", flush=True)
            events["start"] = True
        elif t == "enum_done":
            print(f"[enum] total={p['count']}", flush=True)
        elif t == "progress":
            print(f"[prog] filtered={p['filtered']} scanned={p['scanned']} "
                  f"errors={p['errors']} hits={p['hit_count']}", flush=True)
        elif t == "hits_chunk":
            hits.extend(p["items"])
        elif t == "done":
            print(f"[done] filtered={p['filtered']} scanned={p['scanned']} "
                  f"errors={p['errors']}", flush=True)
            events["done"] = True

    sc.on("message", on_msg)
    sc.load()
    device.resume(pid)
    print("已 resume, 等 15s 让 App 初始化...", flush=True)
    time.sleep(15)
    print("[RPC] 调 scan()...", flush=True)
    sc.post({"type": "noop"})  # keep-alive
    # 用 async 方式调用，避免阻塞
    import threading
    def call_scan():
        try:
            sc.exports_sync.scan()
        except Exception as e:
            print(f"[scan err] {e}", flush=True)
    th = threading.Thread(target=call_scan, daemon=True)
    th.start()

    t0 = time.time()
    while time.time() - t0 < 300:
        time.sleep(3)
        if events["done"]:
            break

    print(f"\n======= 共 {len(hits)} hits =======", flush=True)
    by_method: dict[str, list[tuple[str, str]]] = {}
    for h in hits:
        by_method.setdefault(h["method"], []).append((h["cls"], h["sig"]))
    for m, items in by_method.items():
        print(f"\n## {m} ({len(items)} hits)", flush=True)
        for cls, sig in items[:15]:
            print(f"  [{cls}]", flush=True)
            print(f"    {sig}", flush=True)

    out = Path("d:/tmp/play_method_hits.txt")
    with out.open("w", encoding="utf-8") as f:
        for m, items in by_method.items():
            f.write(f"## {m} ({len(items)} hits)\n")
            for cls, sig in items:
                f.write(f"  [{cls}]\n    {sig}\n")
    print(f"\n写入 {out}", flush=True)


if __name__ == "__main__":
    main()
