#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""one-shot: hook c0, dump j30 的所有 40 个 getter 返回值, 对比肉眼可见剧名反推字段含义."""
import subprocess, time, threading

JS = r"""
'use strict';
send({t:'ready'});
setTimeout(function() {
    Java.perform(function() {
        try {
            var VM = Java.use('com.dragon.read.kmp.shortvideo.distribution.page.tab.SeriesRankTabViewModel');
            var tc4e = Java.use('tc4.e');
            var y34c = Java.use('y34.c');
            var j30 = Java.use('com.bytedance.kmp.reading.model.j30');

            var fired = false;

            VM.c0.overload('java.util.List').implementation = function(list) {
                var ret = this.c0(list);
                if (fired) return ret;
                try {
                    var size = list.size();
                    if (size === 0) return ret;
                    fired = true;
                    var items_dump = [];
                    var N = Math.min(size, 5);
                    for (var i = 0; i < N; i++) {
                        var el = Java.cast(list.get(i), tc4e);
                        // Frida: field 'a' (y34.c) 需 _a.value 访问 (避免与 a() 方法冲突)
                        var vtm = el._a.value;
                        if (!vtm) { items_dump.push({_err:'vtm null'}); continue; }
                        var vtmCast = Java.cast(vtm, y34c);
                        var video = vtmCast.g();  // j30
                        if (!video) { items_dump.push({_err:'video null'}); continue; }
                        var v = Java.cast(video, j30);
                        // getter names: a-z + A-M (前 40 个, 去掉 a(), N(...))
                        var getter_names = [
                            'A','B','C','D','E','F','G','H','I','J','K','L','M',
                            'b','c','d','e','f','g','h','i','j','k','l','m','n',
                            'o','p','q','r','s','t','u','v','w','x','y','z'
                        ];
                        var row = {};
                        for (var gi = 0; gi < getter_names.length; gi++) {
                            var gn = getter_names[gi];
                            try {
                                var rv = v[gn]();
                                if (rv === null || rv === undefined) {
                                    row[gn] = null;
                                } else {
                                    var s = String(rv);
                                    if (s.length > 300) s = s.substring(0, 300) + '...';
                                    row[gn] = s;
                                }
                            } catch(e) {
                                row[gn] = 'ERR:' + String(e).substring(0, 80);
                            }
                        }
                        items_dump.push(row);
                    }
                    send({t:'dump', size: size, items: items_dump});
                } catch(e) { send({t:'err', e:String(e)}); }
                return ret;
            };

            send({t:'log', msg:'c0 hooked, waiting...'});
        } catch(e) { send({t:'err', e:String(e)}); }
    });
}, 200);
"""

def main():
    serial = '4d53df1f'
    r = subprocess.run(['adb','-s',serial,'shell','pidof','com.phoenix.read'],
                       capture_output=True, text=True, timeout=5)
    pid = int(r.stdout.strip().split()[0])
    print(f'pid={pid}', flush=True)

    import frida
    dev = frida.get_device(serial)
    sess = dev.attach(pid)
    script = sess.create_script(JS)

    done = threading.Event()
    def on_msg(m, d):
        if m.get('type') != 'send':
            if m.get('type') == 'error':
                print('[JS err]', m.get('description','')[:300], flush=True)
            return
        p = m['payload']
        t = p.get('t')
        if t == 'ready': print('[ready]', flush=True)
        elif t == 'log': print(f'[hook] {p["msg"]}', flush=True)
        elif t == 'err': print(f'[err] {p["e"]}', flush=True)
        elif t == 'dump':
            print(f'\n=== c0 triggered size={p["size"]}  dumping {len(p["items"])} items ===',
                  flush=True)
            for idx, row in enumerate(p['items']):
                print(f'\n--- item[{idx}] ---', flush=True)
                for k, v in row.items():
                    if v is None: continue
                    print(f'  {k}() = {v[:200] if isinstance(v,str) else v}', flush=True)
            done.set()

    script.on('message', on_msg)
    try:
        script.load()
        time.sleep(1)

        def adb(*a, timeout=8):
            return subprocess.run(['adb','-s',serial]+list(a),
                                  capture_output=True, text=True, timeout=timeout)
        def tap(x,y): adb('shell','input','tap',str(x),str(y))
        def key(k): adb('shell','input','keyevent',k)
        def focus():
            try:
                r = adb('shell','dumpsys window windows')
                for ln in r.stdout.splitlines():
                    if 'mCurrentFocus' in ln:
                        return ln.strip()
            except Exception:
                pass
            return ''

        # Step 1: BACK 到主界面 (最多按 8 次)
        for _ in range(8):
            f = focus()
            if 'MainFragmentActivity' in f:
                break
            if 'com.phoenix.read' not in f:
                adb('shell','monkey','-p','com.phoenix.read',
                    '-c','android.intent.category.LAUNCHER','1')
                time.sleep(3)
                continue
            key('KEYCODE_BACK'); time.sleep(0.7)
        print(f'[nav] main focus: {focus()}', flush=True)

        # Step 2: tap 剧场 -> tap 排行榜入口
        tap(324, 1820); time.sleep(1.5)
        tap(442, 381); time.sleep(3)
        print(f'[nav] rank focus: {focus()}', flush=True)

        # Step 3: tap 热播榜 tab -> 应触发 c0
        print('\n--- tap 热播榜 ---', flush=True)
        tap(528, 516); time.sleep(3)

        done.wait(timeout=8)
    finally:
        try: script.unload()
        except Exception: pass
        try: sess.detach()
        except Exception: pass

if __name__ == '__main__':
    main()
