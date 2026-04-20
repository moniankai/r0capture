"""Hook multi_video_model 响应，拿到完整 JSON。

关键：a$a.in() 返回的 InputStream 只能读一次；我们先全读到 byte[]，保存，
再用 ByteArrayInputStream 替换回 body。
"""
from __future__ import annotations
import sys, time
from pathlib import Path
import frida

sys.path.insert(0, str(Path(__file__).parent.parent))
from scripts.download_drama import select_running_app_pid

APP_PACKAGE = "com.phoenix.read"
OUTPUT_FILE = Path("d:/tmp/mvm_response_log.txt")

HOOK = r"""
Java.perform(function() {
    var Call = Java.use('com.bytedance.retrofit2.SsHttpCall');
    var Request = Java.use('com.bytedance.retrofit2.client.Request');
    var BodyCls;
    try {
        BodyCls = Java.use('com.bytedance.frameworks.baselib.network.http.impl.a$a');
    } catch (e) {
        send({t:'err', e: 'no a$a: '+e.toString()});
        return;
    }

    var KEYS = ['multi_video_model', 'multi_video_detail', 'video_detail', 'series_detail_info'];

    function urlMatches(u) {
        for (var i = 0; i < KEYS.length; i++) if (u.indexOf(KEYS[i]) !== -1) return true;
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

        try {
            var bodyObj = resp.body();
            if (bodyObj) {
                var body = Java.cast(bodyObj, BodyCls);
                // 读整条流
                var is = body.in();
                var BIS = Java.use('java.io.ByteArrayOutputStream');
                var buf = BIS.$new();
                var ba = Java.array('byte', new Array(8192).fill(0));
                var total = 0;
                while (true) {
                    var n = is.read(ba, 0, 8192);
                    if (n <= 0) break;
                    buf.write(ba, 0, n);
                    total += n;
                    if (total > 4 * 1024 * 1024) break; // 4MB 上限
                }
                var bytes = buf.toByteArray();
                // 反序列化字符串（UTF-8）
                var Str = Java.use('java.lang.String');
                var text = Str.$new(bytes, 'UTF-8');
                // 分块发送避免单条消息过大
                var fullText = String(text);
                var CHUNK = 60000;
                var id = Math.floor(Math.random() * 1e9);
                var parts = Math.ceil(fullText.length / CHUNK);
                for (var k = 0; k < parts; k++) {
                    send({t:'json_chunk', id: id, idx: k, total: parts, url: url.substring(0,300), len: total, body: fullText.substring(k*CHUNK, (k+1)*CHUNK)});
                }
                // 无法重置原流，业务可能重试或已经使用缓存——这次接受副作用
            }
        } catch (e) {
            send({t:'read_err', url: url.substring(0,120), err: e.toString()});
        }
        return resp;
    };
    send({t:'ready'});
});
"""

def main():
    device = frida.get_usb_device(timeout=10)
    pid = select_running_app_pid(device.enumerate_processes(), APP_PACKAGE)
    if pid is None:
        print('App 未运行'); return
    s = device.attach(pid)
    sc = s.create_script(HOOK)

    OUTPUT_FILE.write_text('', encoding='utf-8')

    def on_msg(msg, data):
        if msg.get('type') != 'send':
            print(msg); return
        p = msg['payload']
        t = p.get('t')
        if t == 'ready':
            print('[READY]')
        elif t == 'json_chunk':
            idx = p['idx']; tot = p['total']
            if idx == 0:
                print(f"[JSON] len={p['len']} parts={tot} url={p['url'][:150]}")
                with OUTPUT_FILE.open('a', encoding='utf-8') as f:
                    f.write(f"===\nURL: {p['url']}\nLEN: {p['len']}\n")
            with OUTPUT_FILE.open('a', encoding='utf-8') as f:
                f.write(p['body'])
            if idx == tot - 1:
                with OUTPUT_FILE.open('a', encoding='utf-8') as f:
                    f.write('\n\n')
        elif t == 'read_err':
            print(f"[ERR] {p['url']}: {p['err']}")

    sc.on('message', on_msg)
    sc.load()
    print('监听 40s...')
    time.sleep(40)
    print(f'\n=== 日志写入 {OUTPUT_FILE} ===')

if __name__ == '__main__': main()
