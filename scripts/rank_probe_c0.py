#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""探测 SeriesRankTabViewModel.c0(List) 被调用时 List 元素实际类型 + 字段."""
import subprocess, time, threading

JS = r"""
'use strict';
send({t:'ready'});
setTimeout(function() {
    Java.perform(function() {
        try {
            var VM = Java.use('com.dragon.read.kmp.shortvideo.distribution.page.tab.SeriesRankTabViewModel');
            VM.c0.overload('java.util.List').implementation = function(list) {
                try {
                    var size = (list === null) ? -1 : list.size();
                    var elemClass = null;
                    var first_dump = {};
                    if (size > 0) {
                        var el0 = list.get(0);
                        elemClass = String(el0.getClass().getName());
                        // 反射所有字段
                        var fields = el0.getClass().getDeclaredFields();
                        for (var i = 0; i < fields.length; i++) {
                            var f = fields[i];
                            f.setAccessible(true);
                            try {
                                var val = f.get(el0);
                                var s = (val === null) ? 'null' : String(val);
                                if (s.length > 200) s = s.substring(0, 200) + '...';
                                first_dump[String(f.getName()) + ':' + String(f.getType().getName())] = s;
                            } catch(e) {
                                first_dump[String(f.getName())] = 'ERR:'+String(e);
                            }
                        }
                    }
                    send({t:'c0', size:size, elem_class:elemClass, first: first_dump});
                } catch(e) { send({t:'err', e:String(e)}); }
                return this.c0(list);
            };

            // 也 hook .m() getter 作为辅助 (调用后读 list)
            // 不 hook, 因为 getter 高频

            // 同时 hook VM 的 P 方法 (分页)
            try {
                var params = VM.P.overloads;
                params.forEach(function(ov) {
                    ov.implementation = function() {
                        try {
                            var args = [];
                            for (var i = 0; i < arguments.length; i++) {
                                var a = arguments[i];
                                if (a === null) args.push('null');
                                else if (a.size !== undefined) args.push('List(size=' + a.size() + ')');
                                else args.push(String(a.getClass().getName()));
                            }
                            send({t:'P_called', args: args});
                        } catch(e){}
                        return ov.apply(this, arguments);
                    };
                });
            } catch(e) {}

            send({t:'log', msg:'c0 + P hooks installed'});
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

    ready = threading.Event()
    events = []
    def on_msg(m, d):
        if m.get('type') != 'send':
            if m.get('type') == 'error':
                print('[JS err]', m.get('description','')[:300], flush=True)
            return
        p = m['payload']
        t = p.get('t')
        if t == 'ready': ready.set(); print('[ready]', flush=True)
        elif t == 'log': print(f'[hook] {p["msg"]}', flush=True)
        elif t == 'err': print(f'[err] {p["e"]}', flush=True)
        elif t == 'c0':
            print(f'\n[c0 called] size={p["size"]} elem_class={p["elem_class"]}', flush=True)
            print('[c0 first item fields]:', flush=True)
            for k, v in (p.get('first') or {}).items():
                print(f'    {k} = {v[:180]}', flush=True)
            events.append(p)
        elif t == 'P_called':
            print(f'[P called] args={p["args"]}', flush=True)

    script.on('message', on_msg)
    try:
        script.load()
        ready.wait(timeout=10)

        # 导航操作会触发 c0
        def adb(*a, timeout=8):
            return subprocess.run(['adb','-s',serial]+list(a),
                                  capture_output=True, text=True, timeout=timeout)
        def tap(x,y): adb('shell','input','tap',str(x),str(y))
        def swipe(x1,y1,x2,y2,dur):
            adb('shell','input','swipe',str(x1),str(y1),str(x2),str(y2),str(dur))

        print('\n--- 第一步: tap 热播榜 ---', flush=True)
        tap(528, 516); time.sleep(3)

        print('\n--- 第二步: 下滑一次 ---', flush=True)
        swipe(540,1550,540,900,700); time.sleep(3)

        print('\n--- 第三步: tap 漫剧榜 ---', flush=True)
        tap(732, 516); time.sleep(3)

        print(f'\n=== 共 {len(events)} 次 c0 调用 ===', flush=True)
    finally:
        try: script.unload()
        except Exception: pass
        try: sess.detach()
        except Exception: pass

if __name__ == '__main__':
    main()
