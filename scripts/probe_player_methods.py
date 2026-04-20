"""Probe: Hook 《与堂哥散伙后》等"异常剧"的播放器方法调用链.
记录所有 TTVideoEngine.*set* / play* / prepare* 方法被调用的顺序+参数类型.

用法:
  1. 手动打开 App, 用脚本 spawn 模式跑
  2. 手动进入目标剧, 等 10+ 秒
  3. 脚本 dump 所有命中的方法签名
"""
from __future__ import annotations
import sys, time, subprocess, os, json
from pathlib import Path
import frida

APP_PACKAGE = "com.phoenix.read"

HOOK = r"""
Java.perform(function() {
    var TTE = Java.use('com.ss.ttvideoengine.TTVideoEngine');
    var methods = TTE.class.getDeclaredMethods();

    // keywords: 播放资源设置相关
    var keywords = ['set', 'play', 'prepare', 'url', 'source', 'direct', 'videomodel',
                    'data', 'key', 'token', 'spadea', 'kid', 'decrypt', 'info', 'load'];
    var hookedCount = 0;

    for (var i = 0; i < methods.length; i++) {
        var m = methods[i];
        var name = m.getName();
        var lo = name.toLowerCase();
        var match = false;
        for (var k = 0; k < keywords.length; k++) {
            if (lo.indexOf(keywords[k]) >= 0) { match = true; break; }
        }
        if (!match) continue;

        try {
            var ovs = TTE[name].overloads;
            if (!ovs || ovs.length === 0) continue;
            ovs.forEach(function(ov) {
                ov.implementation = function() {
                    var argTypes = [];
                    for (var j = 0; j < arguments.length; j++) {
                        var a = arguments[j];
                        if (a === null) argTypes.push('null');
                        else if (typeof a === 'string') argTypes.push('str:' + a.substring(0, 40));
                        else if (typeof a === 'number') argTypes.push('num:' + a);
                        else if (typeof a === 'boolean') argTypes.push('bool:' + a);
                        else {
                            try { argTypes.push(a.getClass ? a.getClass().getName() : typeof a); }
                            catch(e) { argTypes.push('?'); }
                        }
                    }
                    send({t:'call', m: name, args: argTypes});
                    return ov.apply(this, arguments);
                };
            });
            hookedCount++;
        } catch(e) {}
    }
    send({t:'ready', hooked: hookedCount});

    // 也 hook setVideoID(String) 专门看
    try {
        var svi = TTE.setVideoID.overloads;
        svi.forEach(function(ov) {
            ov.implementation = function(v) {
                send({t:'setVideoID', arg: v ? String(v) : 'null'});
                return ov.call(this, v);
            };
        });
    } catch(e) {}
});
"""


def _adb_pidof() -> int | None:
    env = {**os.environ, "MSYS_NO_PATHCONV": "1"}
    r = subprocess.run(["adb", "shell", "pidof", APP_PACKAGE],
                       capture_output=True, text=True, env=env)
    pids = [int(x) for x in (r.stdout or "").strip().split() if x.isdigit()]
    return min(pids) if pids else None


def main():
    device = frida.get_usb_device(timeout=10)
    pid = _adb_pidof()
    if not pid:
        print("App 未运行,请先 python scripts/hongguo_v5.py ... 让它 spawn 后再停在搜索页")
        return
    print(f"attach pid={pid}")
    s = device.attach(pid)
    sc = s.create_script(HOOK)

    calls: list[dict] = []
    def on_msg(msg, _data):
        if msg.get('type') != 'send':
            if msg.get('type') == 'error':
                print(f"[ERR] {msg.get('description','')[:200]}", flush=True)
            return
        p = msg['payload']
        t = p.get('t')
        if t == 'ready':
            print(f"[hook ready] hooked {p['hooked']} methods", flush=True)
        elif t == 'call':
            calls.append(p)
            print(f"[call] {p['m']}({', '.join(p['args'])})", flush=True)
        elif t == 'setVideoID':
            print(f"[setVideoID] {p['arg']}", flush=True)

    sc.on('message', on_msg)
    sc.load()
    print("观察 60s,期间手动进入目标剧并切集")
    time.sleep(60)

    print(f"\n共 {len(calls)} 次方法调用")
    # 聚合: 每个方法 hit 次数
    by_method = {}
    for c in calls:
        by_method[c['m']] = by_method.get(c['m'], 0) + 1
    for m, n in sorted(by_method.items(), key=lambda x: -x[1]):
        print(f"  {m}: {n}")


if __name__ == '__main__':
    main()
