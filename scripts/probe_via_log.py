"""通过 hook Log.* 系列方法抓 "playItemOfN"/"doSetPlayItem"/"playNext"/"playPrev"
等关键字符串的日志来源，获得调用栈,从而反推所在类。

策略：
  1. spawn 红果
  2. hook android.util.Log.d/i/w/v/e
  3. 过滤 msg/tag 里包含目标关键字的调用
  4. 打印当前 Java stack trace

用法：
  python scripts/probe_via_log.py
  (然后手动操作 App:搜索剧 → 进详情 → 点选集,触发日志)
"""
from __future__ import annotations
import sys, time, subprocess, os
from pathlib import Path
import frida

APP_PACKAGE = "com.phoenix.read"

HOOK = r"""
Java.perform(function() {
    var KEYWORDS = [
        'playItemOfN', 'doSetPlayItem',
        'playNext', 'playPrev', 'playPrevChapter', 'playPrevious',
        'setPlayEpisode', 'setCurrentPlayIndex',
        'replayItem'
    ];

    function containsAny(s) {
        if (!s) return false;
        for (var i = 0; i < KEYWORDS.length; i++) {
            if (s.indexOf(KEYWORDS[i]) !== -1) return KEYWORDS[i];
        }
        return null;
    }

    var Log = Java.use('android.util.Log');
    var Throwable = Java.use('java.lang.Throwable');

    function dumpTrace() {
        var tr = Throwable.$new();
        var frames = tr.getStackTrace();
        var out = [];
        // 跳过 Frida/Log 相关栈
        for (var i = 0; i < Math.min(20, frames.length); i++) {
            out.push(String(frames[i].toString()));
        }
        return out;
    }

    function tryHook(methodName, overloadSig) {
        try {
            var impl = Log[methodName].overload.apply(Log[methodName], overloadSig);
            impl.implementation = function() {
                var tag = arguments[0] != null ? String(arguments[0]) : '';
                var msg = arguments[1] != null ? String(arguments[1]) : '';
                var kw = containsAny(tag) || containsAny(msg);
                if (kw) {
                    var frames = dumpTrace();
                    send({t:'log_hit', level: methodName, kw: kw,
                          tag: tag, msg: msg.substring(0, 300), frames: frames});
                }
                return impl.apply(this, arguments);
            };
            return true;
        } catch (e) { return false; }
    }

    // Log 有多种签名,常见: (String, String) / (String, String, Throwable)
    var hooked = 0;
    ['d','i','w','v','e'].forEach(function(lv) {
        if (tryHook(lv, ['java.lang.String', 'java.lang.String'])) hooked++;
        if (tryHook(lv, ['java.lang.String', 'java.lang.String', 'java.lang.Throwable'])) hooked++;
    });
    send({t:'ready', hooked: hooked});
});
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

    def on_msg(msg, data):
        if msg.get("type") != "send":
            if msg.get("type") == "error":
                print(f"[JS ERR] {msg.get('description','')[:300]}", flush=True)
            return
        p = msg["payload"]
        t = p.get("t")
        if t == "ready":
            print(f"[hook ready] hooked={p['hooked']} Log overloads", flush=True)
        elif t == "log_hit":
            hits.append(p)
            kw = p['kw']
            tag = p['tag'][:60]
            msg_ = p['msg'][:120]
            print(f"\n>>> [{kw}] tag='{tag}' msg='{msg_}'", flush=True)
            for f in p["frames"][:8]:
                print(f"    at {f}", flush=True)

    sc.on("message", on_msg)
    sc.load()
    device.resume(pid)
    print("已 resume. 请手动: 搜索《乡下御厨》→ 进详情页 → 点选集/点某集播放.", flush=True)
    print("观察期 180s (期间 hit 会实时打印).", flush=True)

    t0 = time.time()
    while time.time() - t0 < 180:
        time.sleep(2)

    out = Path("d:/tmp/log_probe_hits.txt")
    with out.open("w", encoding="utf-8") as f:
        for h in hits:
            f.write(f"## [{h['kw']}] tag={h['tag']} msg={h['msg'][:200]}\n")
            for fr in h["frames"]:
                f.write(f"  {fr}\n")
            f.write("\n")
    print(f"\n共 {len(hits)} hits. 写入 {out}", flush=True)


if __name__ == "__main__":
    main()
