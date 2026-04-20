"""Hook 字节 TTNet / Retrofit 层，捕获 App 业务请求（目标：剧集列表接口）。

通过 Hook com.bytedance.retrofit2.SsHttpCall 的 execute/enqueue 捕获完整请求 URL
和响应体，便于定位 "剧集列表" 接口。
"""
from __future__ import annotations
import sys
import time
from pathlib import Path

import frida

sys.path.insert(0, str(Path(__file__).parent.parent))
from scripts.download_drama import select_running_app_pid

APP_PACKAGE = "com.phoenix.read"

HOOK = r"""
Java.perform(function() {
    var targets = [
        // 字节 Retrofit 核心类
        'com.bytedance.retrofit2.SsHttpCall',
        'com.bytedance.retrofit2.RealCall',
        'com.bytedance.retrofit2.intercept.RealInterceptorChain',
        // 字节 TTNet 底层
        'com.bytedance.ttnet.http.SsHttpConnection',
        'com.bytedance.ttnet.http.HttpClient',
    ];

    function tryHook(name) {
        try {
            var cls = Java.use(name);
            send({t:'cls_ok', name: name});
            // 枚举方法看有哪些可以 Hook
            var m = cls.class.getDeclaredMethods();
            var picks = [];
            for (var i = 0; i < m.length; i++) {
                var s = m[i].toGenericString();
                if (/execute|enqueue|intercept|proceed/i.test(s)) picks.push(s);
            }
            send({t:'methods', cls: name, picks: picks.slice(0, 10)});
        } catch (e) {
            // silent
        }
    }
    targets.forEach(tryHook);

    // 主要 Hook: SsHttpCall.execute()
    try {
        var Call = Java.use('com.bytedance.retrofit2.SsHttpCall');
        var Request = Java.use('com.bytedance.retrofit2.client.Request');

        Call.execute.implementation = function() {
            var req = null;
            try {
                // SsHttpCall 持有 Request
                var f = Call.class.getDeclaredField('originalRequest');
                f.setAccessible(true);
                req = f.get(this);
            } catch (e) {
                try {
                    var f = Call.class.getDeclaredField('request');
                    f.setAccessible(true);
                    req = f.get(this);
                } catch (e2) {}
            }
            var url = '';
            try {
                if (req) url = String(Request.getUrl.call(req) || '');
            } catch (e) { url = '[err:'+e.toString()+']'; }
            send({t:'exec', url: url.substring(0, 300)});
            var resp = this.execute();
            try {
                // 响应体
                if (resp) {
                    var body = resp.body ? resp.body() : null;
                    if (body) {
                        var inp = body.in();
                        // 预读前 2KB
                        var ba = Java.array('byte', new Array(2048).fill(0));
                        var n = 0;
                        try {
                            n = inp.read(ba, 0, 2048);
                        } catch (e) {}
                        if (n > 0) {
                            var str = '';
                            for (var i = 0; i < n; i++) {
                                var b = ba[i] & 0xff;
                                if (b >= 0x20 && b < 0x7f) str += String.fromCharCode(b);
                            }
                            send({t:'resp', url: url.substring(0, 200), body: str.substring(0, 2000)});
                        }
                    }
                }
            } catch (e) {
                send({t:'resp_err', url: url.substring(0,120), err: e.toString()});
            }
            return resp;
        };
        send({t:'hooked_exec'});
    } catch (e) {
        send({t:'hook_exec_err', err: e.toString()});
    }
});
"""

def main():
    device = frida.get_usb_device(timeout=10)
    pid = select_running_app_pid(device.enumerate_processes(), APP_PACKAGE)
    if pid is None:
        print('App 未运行'); return
    session = device.attach(pid)
    script = session.create_script(HOOK)

    urls = []
    def on_msg(msg, data):
        if msg.get('type') != 'send':
            return
        p = msg['payload']
        t = p.get('t')
        if t == 'cls_ok':
            print(f"[OK] {p['name']}")
        elif t == 'methods':
            print(f"--- {p['cls']}: {len(p['picks'])} methods match exec/intercept")
            for m in p['picks'][:5]:
                print(f"   {m}")
        elif t == 'hooked_exec':
            print('[HOOKED] SsHttpCall.execute')
        elif t == 'exec':
            url = p['url']
            print(f'[REQ] {url}')
            urls.append(url)
        elif t == 'resp':
            print(f"[RESP] {p['url']}")
            print(f"       body[0:500]: {p['body'][:500]}")
        elif t == 'resp_err':
            print(f"[RESP_ERR] {p['url']}: {p['err']}")
        elif t == 'hook_exec_err':
            print(f"[ERR] {p['err']}")

    script.on('message', on_msg)
    script.load()
    print('[Hooking] listening 60s — do something in App...')
    time.sleep(60)
    print(f'\n=== captured {len(urls)} requests ===')

if __name__ == '__main__':
    main()
