"""枚举 body 包装类的方法，找读取字节的入口。"""
from __future__ import annotations
import sys, time
from pathlib import Path
import frida

sys.path.insert(0, str(Path(__file__).parent.parent))
from scripts.download_drama import select_running_app_pid

APP_PACKAGE = "com.phoenix.read"

HOOK = r"""
Java.perform(function() {
    ['com.bytedance.frameworks.baselib.network.http.impl.a$a',
     'com.bytedance.frameworks.baselib.network.http.impl.a',
     'com.bytedance.retrofit2.mime.TypedInput',
     'com.bytedance.retrofit2.mime.TypedByteArray',
     'com.bytedance.retrofit2.mime.TypedString'].forEach(function(n) {
        try {
            var c = Java.use(n);
            var ms = c.class.getDeclaredMethods();
            var lines = [];
            for (var i = 0; i < ms.length; i++) lines.push(ms[i].toGenericString());
            send({t:'m', n:n, ms: lines});
        } catch (e) {
            send({t:'err', n:n, e: e.toString()});
        }
    });
});
"""

def main():
    device = frida.get_usb_device(timeout=10)
    pid = select_running_app_pid(device.enumerate_processes(), APP_PACKAGE)
    if pid is None: return
    s = device.attach(pid)
    sc = s.create_script(HOOK)
    sc.on('message', lambda m, d: print(m.get('payload')) if m.get('type')=='send' else None)
    sc.load()
    time.sleep(6)

if __name__ == '__main__': main()
