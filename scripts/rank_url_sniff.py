#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""rank_url_sniff — 只记录 URL, 不读 body. 用于找榜单 API 路径."""
import subprocess, time, threading, sys

APP_PACKAGE = "com.phoenix.read"

JS = r"""
'use strict';
send({t:'ready'});
setTimeout(function() {
    Java.perform(function() {
        try {
            var Chain = Java.use('okhttp3.internal.http.RealInterceptorChain');
            // URL 内部 filter 关键词, 避免高频 send 堵塞 Frida IPC
            var KW = /rank|series|saas|novel|ugc|short_video|shortvideo|video_model|epis/i;
            Chain.proceed.overload('okhttp3.Request').implementation = function(req) {
                var resp = this.proceed(req);
                try {
                    var url = String(req.url().toString());
                    if (KW.test(url)) {
                        send({t:'url', u: url, ts: Date.now()});
                    }
                } catch(e){}
                return resp;
            };
            send({t:'log', msg:'Chain.proceed hooked (filtered)'});
        } catch(e) { send({t:'err', err:String(e)}); }
    });
}, 200);
"""


def main():
    serial = '4d53df1f'
    r = subprocess.run(['adb','-s',serial,'shell','pidof',APP_PACKAGE],
                       capture_output=True, text=True, timeout=5)
    pid = int(r.stdout.strip().split()[0])
    print(f'attach pid={pid}')

    import frida
    dev = frida.get_device(serial)
    sess = dev.attach(pid)
    script = sess.create_script(JS)

    urls = []
    ready = threading.Event()
    def on_msg(m, d):
        if m.get('type') != 'send':
            if m.get('type') == 'error':
                print('[JS err]', m.get('description','')[:300])
            return
        p = m['payload']
        if p.get('t') == 'ready':
            ready.set(); print('[hook] ready')
        elif p.get('t') == 'log':
            print('[hook]', p['msg'])
        elif p.get('t') == 'err':
            print('[hook err]', p['err'])
        elif p.get('t') == 'url':
            urls.append(p['u'])

    script.on('message', on_msg)
    script.load()
    ready.wait(timeout=10)

    def cleanup():
        try: script.unload()
        except Exception: pass
        try: sess.detach()
        except Exception: pass

    try:
        def adb(*a, timeout=8):
            return subprocess.run(['adb','-s',serial]+list(a),
                                  capture_output=True, text=True, timeout=timeout)
        def tap(x,y): adb('shell','input','tap',str(x),str(y))
        def key(k): adb('shell','input','keyevent',k)
        def swipe(x1,y1,x2,y2,dur):
            adb('shell','input','swipe',str(x1),str(y1),str(x2),str(y2),str(dur))
        def focus():
            try:
                r = adb('shell','dumpsys window windows')
                for ln in r.stdout.splitlines():
                    if 'mCurrentFocus' in ln:
                        return ln.strip()
            except Exception:
                pass
            return ''

        # 确保在主界面
        for _ in range(6):
            if 'MainFragmentActivity' in focus(): break
            key('KEYCODE_BACK'); time.sleep(0.7)
        time.sleep(1)
        urls.clear()
        print(f'\n[T=0] 当前 focus: {focus()}')

        # 进排行榜
        tap(324, 1820); time.sleep(1.5)   # 剧场
        print(f'[T=1] 剧场后 focus: {focus()}  URLs so far: {len(urls)}')
        tap(442, 381); time.sleep(2.5)    # 排行榜入口
        print(f'[T=2] 排行榜后 focus: {focus()}  URLs so far: {len(urls)}')

        # tap 热播榜
        tap(528, 516); time.sleep(2.5)
        print(f'[T=3] 热播榜后 focus: {focus()}  URLs so far: {len(urls)}')

        # 下滑一次
        swipe(540,1550,540,900,700); time.sleep(2.0)
        print(f'[T=4] swipe 后 URLs so far: {len(urls)}')

        # 漫剧榜
        tap(732, 516); time.sleep(2.5)
        print(f'[T=5] 漫剧榜后 URLs so far: {len(urls)}')
        swipe(540,1550,540,900,700); time.sleep(2.0)
        print(f'[T=6] swipe 后 URLs so far: {len(urls)}')

    finally:
        cleanup()

    # 过滤输出
    print(f'\n=== 共 {len(urls)} 个 URL ===')
    import re
    # 只保留含 rank/ranking/series/video/novel/saas 的
    KW = ['rank','ranking','series','saas','novelvideo','novel_video','/api/']
    matched = [u for u in urls if any(k.lower() in u.lower() for k in KW)]
    print(f'\n=== 疑似榜单相关 URL ({len(matched)}) ===')
    seen_path = set()
    for u in matched:
        # 去 query 后的 path + host
        path = u.split('?',1)[0]
        if path in seen_path: continue
        seen_path.add(path)
        print(u[:300])

    # 全量 host 统计
    from collections import Counter
    hosts = Counter()
    for u in urls:
        try:
            host = u.split('//',1)[1].split('/',1)[0]
            hosts[host] += 1
        except Exception:
            pass
    print(f'\n=== host 统计 ===')
    for h, c in hosts.most_common(15):
        print(f'  {c:4d}  {h}')


if __name__ == '__main__':
    main()
