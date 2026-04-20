"""探索 TTVideoEngine 的方法签名，看能否主动调用 setVideoID + play 触发 Hook。"""
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
    var candidates = [
        'com.ss.ttvideoengine.TTVideoEngine',
        'com.ss.ttvideoengine.TTVideoEngineInterface',
    ];
    candidates.forEach(function(name) {
        try {
            var cls = Java.use(name);
            send({t: 'cls_ok', name: name});
            // 枚举方法
            var methods = cls.class.getDeclaredMethods();
            var lines = [];
            for (var i = 0; i < methods.length; i++) {
                lines.push(methods[i].toGenericString());
            }
            send({t: 'methods', name: name, methods: lines});
        } catch (e) {
            send({t: 'cls_err', name: name, err: e.toString()});
        }
    });
});
"""

def main():
    device = frida.get_usb_device(timeout=10)
    pid = select_running_app_pid(device.enumerate_processes(), APP_PACKAGE)
    if pid is None:
        print("App 未运行")
        return
    session = device.attach(pid)
    script = session.create_script(HOOK)

    done = False
    def on_msg(msg, data):
        nonlocal done
        if msg.get('type') != 'send':
            return
        p = msg['payload']
        t = p['t']
        if t == 'cls_ok':
            print(f'[OK] {p["name"]}')
        elif t == 'cls_err':
            print(f'[ERR] {p["name"]}: {p["err"]}')
        elif t == 'methods':
            print(f'--- {p["name"]} methods ---')
            for m in p['methods']:
                if any(k in m.lower() for k in ('setvideoid', 'setvideomodel', 'play', 'configresolution', 'seek', 'setdatasource', 'setsurface')):
                    print(f'  {m}')

    script.on('message', on_msg)
    script.load()

    time.sleep(5)

if __name__ == '__main__':
    main()
