#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""rank_diag — 探测榜单 Kmp 页面真正用的数据类 + ViewHolder.

不做任何 hook 修改 App 行为, 只枚举:
  1. com.dragon.read.kmp.shortvideo.distribution.* 下所有类
  2. 从中找出有 getSeries/getName/getEpisodes* 的类
  3. 找 RecyclerView Holder 绑定方法 (onBind/bind/j2/...)
"""
import sys, time, subprocess

APP_PACKAGE = "com.phoenix.read"

JS = r"""
'use strict';

// 先立即 send ready, 让 Python 的 load() 快速返回, 再异步做重活
send({t:'ready'});

setTimeout(function() {
    Java.perform(function() {
        var found = [];
        try {
            Java.enumerateLoadedClasses({
                onMatch: function(name) {
                    if (/com\.dragon\.read\.kmp\.shortvideo\.distribution/.test(name) ||
                        /kmp.*rank/i.test(name) ||
                        /SeriesRank/.test(name) ||
                        /RankItem/i.test(name) ||
                        /RankModel/i.test(name) ||
                        /RankData/i.test(name)) {
                        found.push(name);
                    }
                },
                onComplete: function() {
                    send({t:'classes', list: found});
                    found.forEach(function(cls) {
                        try {
                            var C = Java.use(cls);
                            var methods = C.class.getDeclaredMethods();
                            var hits = [];
                            for (var i = 0; i < methods.length; i++) {
                                var m = methods[i].getName();
                                if (/series|episode|seriesid|rank|book_name|title/i.test(m)) {
                                    hits.push(m);
                                }
                            }
                            if (hits.length > 0) {
                                send({t:'class_methods', cls: cls, methods: hits});
                            }
                        } catch(e) {}
                    });
                    send({t:'done'});
                }
            });
        } catch(e) {
            send({t:'err', err: String(e)});
            send({t:'done'});
        }
    });
}, 200);
"""


def main():
    r = subprocess.run(['adb', '-s', '4d53df1f', 'shell', 'pidof', APP_PACKAGE],
                       capture_output=True, text=True, timeout=5)
    pid = int(r.stdout.strip().split()[0])
    print(f'attach pid={pid}')
    import frida
    dev = frida.get_device('4d53df1f')
    sess = dev.attach(pid)
    script = sess.create_script(JS)
    done = [False]
    class_list = []

    def on_msg(msg, data):
        if msg.get('type') != 'send':
            if msg.get('type') == 'error':
                print('[JS err]', msg.get('description', '')[:300])
            return
        p = msg['payload']
        if p.get('t') == 'classes':
            class_list.extend(p['list'])
            print(f'\n=== {len(p["list"])} 个候选类 ===')
            for c in p['list'][:40]:
                print(f'  {c}')
            if len(p['list']) > 40:
                print(f'  ... (+{len(p["list"])-40} more)')
        elif p.get('t') == 'class_methods':
            print(f'\n[METHODS] {p["cls"]}')
            for m in p['methods']:
                print(f'    {m}')
        elif p.get('t') == 'done':
            done[0] = True

    script.on('message', on_msg)
    script.load()
    # 异步枚举可能耗时, 最多等 60s
    for _ in range(600):
        if done[0]: break
        time.sleep(0.1)
    print('\n=== done ===')
    try: script.unload()
    except Exception: pass
    try: sess.detach()
    except Exception: pass


if __name__ == '__main__':
    main()
