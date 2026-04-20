"""精确 Hook 红果的 multi_video_model / series_detail_info 接口响应，目标：拿完整 vid 列表。

用 Java.choose 枚举 Retrofit 的 SsResponse 对象太难。改走反射：
每次 SsHttpCall.execute 返回后，dump 其返回对象的所有字段和类名。
"""
from __future__ import annotations
import sys
import time
from pathlib import Path

import frida

sys.path.insert(0, str(Path(__file__).parent.parent))
from scripts.download_drama import select_running_app_pid

APP_PACKAGE = "com.phoenix.read"
TARGETS = ('multi_video_model', 'multi_video_detail', 'video_detail', 'series_detail_info',
           'all_video_id', 'chapter_list', 'episode_list')

HOOK = r"""
var KEYWORDS = __KEYWORDS__;

Java.perform(function() {
    var Call = Java.use('com.bytedance.retrofit2.SsHttpCall');
    var Request = Java.use('com.bytedance.retrofit2.client.Request');

    function urlMatches(u) {
        for (var i = 0; i < KEYWORDS.length; i++) if (u.indexOf(KEYWORDS[i]) !== -1) return true;
        return false;
    }

    Call.execute.implementation = function() {
        var req = null, url = '';
        try {
            var f = Call.class.getDeclaredField('originalRequest');
            f.setAccessible(true);
            req = f.get(this);
            if (req) url = String(Request.getUrl.call(req) || '');
        } catch (e) {}
        var resp = this.execute();
        if (!urlMatches(url)) return resp;
        send({t:'match_req', url: url.substring(0, 300)});
        try {
            var clsName = resp ? resp.getClass().getName() : 'null';
            send({t:'resp_class', cls: clsName});
            // 枚举字段
            var fields = resp.getClass().getDeclaredFields();
            var out = {};
            for (var i = 0; i < fields.length; i++) {
                var fld = fields[i];
                fld.setAccessible(true);
                try {
                    var v = fld.get(resp);
                    out[fld.getName()+':'+fld.getType().getName()] = v === null ? 'null' : String(v).substring(0, 500);
                } catch (e) {}
            }
            send({t:'resp_fields', url: url.substring(0, 120), fields: out});
            // 特别地：尝试 body() 方法
            try {
                var body = resp.body();
                if (body) {
                    send({t:'body_class', cls: body.getClass().getName()});
                    var bodyFields = body.getClass().getDeclaredFields();
                    var bo = {};
                    for (var j = 0; j < bodyFields.length; j++) {
                        var bf = bodyFields[j];
                        bf.setAccessible(true);
                        try {
                            var bv = bf.get(body);
                            bo[bf.getName()+':'+bf.getType().getName()] = bv === null ? 'null' : String(bv).substring(0, 2000);
                        } catch (e) {}
                    }
                    send({t:'body_fields', url: url.substring(0,120), fields: bo});
                }
            } catch (e) {
                send({t:'body_err', url: url.substring(0,120), err: e.toString()});
            }
        } catch (e) {
            send({t:'dump_err', err: e.toString()});
        }
        return resp;
    };
    send({t:'hook_ready'});
});
""".replace('__KEYWORDS__', str(list(TARGETS)))

def main():
    device = frida.get_usb_device(timeout=10)
    pid = select_running_app_pid(device.enumerate_processes(), APP_PACKAGE)
    if pid is None:
        print('App 未运行'); return
    session = device.attach(pid)
    script = session.create_script(HOOK)

    def on_msg(msg, data):
        if msg.get('type') != 'send':
            print('[RAW]', msg); return
        p = msg['payload']
        t = p.get('t')
        if t == 'hook_ready':
            print('[READY]')
        elif t == 'match_req':
            print(f"\n[MATCH] {p['url']}")
        elif t == 'resp_class':
            print(f"  resp class: {p['cls']}")
        elif t == 'resp_fields':
            print(f"  resp fields:")
            for k, v in p['fields'].items():
                print(f"    {k} = {v[:200]}")
        elif t == 'body_class':
            print(f"  body class: {p['cls']}")
        elif t == 'body_fields':
            print(f"  body fields:")
            for k, v in p['fields'].items():
                print(f"    {k} = {v[:1500]}")
        elif t == 'body_err':
            print(f"  body err: {p['err']}")
        elif t == 'dump_err':
            print(f"  dump err: {p['err']}")

    script.on('message', on_msg)
    script.load()
    print('监听 30s, 请在 App 里触发相关请求...')
    time.sleep(30)

if __name__ == '__main__':
    main()
