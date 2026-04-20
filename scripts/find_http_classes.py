"""探测红果 App 实际使用的 HTTP 类。"""
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
    // 枚举所有含 Http 的类
    Java.enumerateLoadedClasses({
        onMatch: function(name) {
            if (name.indexOf('okhttp3') !== -1 && name.indexOf('$') === -1) {
                send({t: 'cls', name: name});
            }
            if (name.toLowerCase().indexOf('httpclient') !== -1 && name.indexOf('$') === -1) {
                send({t: 'cls', name: name});
            }
        },
        onComplete: function() { send({t: 'done'}); }
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

    classes = []
    done = False
    def on_msg(msg, data):
        nonlocal done
        if msg.get('type') != 'send':
            return
        p = msg['payload']
        if p['t'] == 'cls':
            classes.append(p['name'])
        elif p['t'] == 'done':
            done = True

    script.on('message', on_msg)
    script.load()

    for _ in range(30):
        if done: break
        time.sleep(0.5)

    print(f'共找到 {len(classes)} 个相关类')
    for name in sorted(classes)[:80]:
        print(f'  {name}')

if __name__ == '__main__':
    main()
