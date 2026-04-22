#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""查 SeriesRankTabViewModel / SeriesRankViewModel 方法签名, 不挂 hook."""
import subprocess, time, threading

JS = r"""
'use strict';
send({t:'ready'});
setTimeout(function() {
    Java.perform(function() {
        var classes = [
            'com.bytedance.kmp.reading.model.j30',
            'y34.c'
        ];
        classes.forEach(function(cls) {
            try {
                var C = Java.use(cls);
                var all = C.class.getDeclaredMethods();
                var summary = [];
                for (var i = 0; i < all.length; i++) {
                    var m = all[i];
                    var sig = m.getName() + '(';
                    var params = m.getParameterTypes();
                    var parts = [];
                    for (var j = 0; j < params.length; j++) {
                        parts.push(String(params[j].getName()));
                    }
                    sig += parts.join(', ') + '): ' + String(m.getReturnType().getName());
                    summary.push(sig);
                }
                send({t:'methods', cls:cls, list:summary});

                // Fields 也看看
                var fields = C.class.getDeclaredFields();
                var fs = [];
                for (var k = 0; k < fields.length; k++) {
                    fs.push(String(fields[k].getType().getName()) + ' ' +
                            String(fields[k].getName()));
                }
                send({t:'fields', cls:cls, list:fs});
            } catch(e) {
                send({t:'err', cls:cls, err:String(e)});
            }
        });
        send({t:'done'});
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
        if t == 'ready':
            print('[ready]', flush=True)
        elif t == 'methods':
            print(f'\n=== {p["cls"]} methods ({len(p["list"])}) ===', flush=True)
            for s in p['list']:
                print(f'  {s}', flush=True)
        elif t == 'fields':
            print(f'\n=== {p["cls"]} fields ({len(p["list"])}) ===', flush=True)
            for s in p['list']:
                print(f'  {s}', flush=True)
        elif t == 'err':
            print(f'[err] {p["cls"]}: {p["err"]}', flush=True)
        elif t == 'done':
            done.set()

    script.on('message', on_msg)
    try:
        script.load()
        done.wait(timeout=20)
    finally:
        try: script.unload()
        except Exception: pass
        try: sess.detach()
        except Exception: pass

if __name__ == '__main__':
    main()
