"""红果 API 探索器——attach 到已运行 App，拦截所有 HTTP 请求，筛选与剧集列表相关的 API。

用法：
  1. 先手动/通过 ADB 把 App 打开到本剧详情页
  2. python scripts/api_explorer.py

Hook okhttp3.RealCall 打印每个 request 的 URL。筛选条件：
  - 包含 book / novel / drama / episode / series / chapter / video 等关键词
  - 排除常见 CDN 和日志上报 URL
输出：控制台实时打印 + 保存到 ./api_trace.jsonl
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import frida

sys.path.insert(0, str(Path(__file__).parent.parent))
from scripts.download_drama import select_running_app_pid

APP_PACKAGE = "com.phoenix.read"

HOOK = r"""
'use strict';
Java.perform(function() {
    try {
        var RealCall = Java.use('okhttp3.RealCall');
        var Response = Java.use('okhttp3.Response');
        var Request = Java.use('okhttp3.Request');
        var Buffer = Java.use('okio.Buffer');
        var ResponseBody = Java.use('okhttp3.ResponseBody');

        function logCall(request, response) {
            try {
                var url = request.url().toString();
                var method = request.method();
                // 排除：日志/上报/CDN/资源下载
                var url_lower = url.toLowerCase();
                var drop_patterns = ['dispatch.snssdk', 'log/sdk', 'applog', 'applog.',
                                     'applog-', 'mon.byteoversea', 'monitor_browser',
                                     '/sdk_log', 'dispatcher/', '/stats',
                                     '.m3u8', '.ts?', '.mp4?', '.jpg', '.png',
                                     '.webp', '.gif', '.mp3'];
                for (var i = 0; i < drop_patterns.length; i++) {
                    if (url_lower.indexOf(drop_patterns[i]) !== -1) return;
                }

                var info = { url: url, method: method, ts: Date.now() };
                if (response !== null) {
                    try {
                        info.status = response.code();
                        var body = response.peekBody(65536);  // peek 只读不消费
                        if (body !== null) {
                            info.body = body.string();
                        }
                    } catch (e) {
                        info.body_err = e.toString();
                    }
                }
                send({ t: 'http', data: info });
            } catch (e) {
                send({ t: 'err', e: e.toString() });
            }
        }

        RealCall.execute.implementation = function() {
            var req = this.request();
            try {
                var resp = this.execute.apply(this, arguments);
                logCall(req, resp);
                return resp;
            } catch (e) {
                logCall(req, null);
                throw e;
            }
        };

        RealCall.enqueue.implementation = function(callback) {
            var req = this.request();
            // 不能同步获取 response，只记录 request
            try {
                send({ t: 'http', data: { url: req.url().toString(), method: req.method(), ts: Date.now(), async: true } });
            } catch (e) {}
            return this.enqueue.apply(this, arguments);
        };

        send({ t: 'ready' });
    } catch (e) {
        send({ t: 'err', e: e.toString() });
    }
});
"""


def main():
    out_path = Path('./api_trace.jsonl')
    out_path.unlink(missing_ok=True)

    device = frida.get_usb_device(timeout=10)
    pid = select_running_app_pid(device.enumerate_processes(), APP_PACKAGE)
    if pid is None:
        print(f"{APP_PACKAGE} 未运行。先通过 ADB 打开 App 再跑此脚本")
        return
    print(f"Attach PID={pid}")
    session = device.attach(pid)
    script = session.create_script(HOOK)

    total = 0

    def on_message(msg, data):
        nonlocal total
        if msg.get('type') != 'send':
            return
        p = msg.get('payload', {})
        t = p.get('t')
        if t == 'ready':
            print('[Hook] ready')
        elif t == 'http':
            d = p.get('data', {})
            total += 1
            url = d.get('url', '')
            # 控制台只打印 URL，body 全部写文件
            marker = ' *' if 'book' in url.lower() or 'episode' in url.lower() or 'catalog' in url.lower() else ''
            print(f'[{total}] {d.get("method","?")} {url[:140]}{marker}')
            with out_path.open('a', encoding='utf-8', newline='\n') as f:
                f.write(json.dumps(d, ensure_ascii=False) + '\n')
        elif t == 'err':
            print('[err]', p.get('e'))

    script.on('message', on_message)
    script.load()
    print('Hook loaded, 请操作 App（进入剧详情页等）。Ctrl+C 退出')

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print(f'\n共捕获 {total} 条 HTTP 请求，已保存到 {out_path}')


if __name__ == '__main__':
    main()
