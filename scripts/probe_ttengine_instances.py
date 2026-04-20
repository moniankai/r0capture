"""枚举 App 内现存的 TTVideoEngine 实例，查看每个实例的关键字段是否具备上下文。

目标：验证能否拿到一个可被主动调用的 TTVideoEngine 实例，用于后续批量主动触发 Hook。
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
    var cls;
    try {
        cls = Java.use('com.ss.ttvideoengine.TTVideoEngine');
    } catch (e) {
        send({t: 'err', msg: 'cannot Java.use TTVideoEngine: ' + e.toString()});
        return;
    }

    // 枚举所有字段名，便于抓取上下文属性
    var fields = cls.class.getDeclaredFields();
    var fieldInfo = [];
    for (var i = 0; i < fields.length; i++) {
        var f = fields[i];
        fieldInfo.push(f.getName() + ':' + f.getType().getName());
    }
    send({t: 'fields', list: fieldInfo});

    // 枚举活跃实例
    Java.choose('com.ss.ttvideoengine.TTVideoEngine', {
        onMatch: function(inst) {
            var info = {t: 'inst'};
            try {
                info.hash = inst.hashCode();
            } catch (e) { info.hashErr = e.toString(); }

            // 尝试读取关键上下文字段
            var keys = ['mVideoID', 'mPlayAuthToken', 'mPlayAPIVersion', 'mVideoModel',
                        'mSource', 'mDataSource', 'mSurface', 'mPlayUrl', 'mPlaybackState',
                        'mIsReleased', 'mIsPaused'];
            keys.forEach(function(k) {
                try {
                    var v = inst[k];
                    if (v !== undefined && v !== null) {
                        var vv = v.value;
                        if (vv !== undefined && vv !== null) {
                            info[k] = String(vv).substring(0, 100);
                        }
                    }
                } catch (e) {
                    // 无此字段，忽略
                }
            });
            try { info.playbackState = inst.getPlaybackState(); } catch (e) {}
            try { info.currentUrl = String(inst.getCurrentPlayUrl() || ''); } catch (e) {}
            try { info.currentPath = String(inst.getCurrentPlayPath() || ''); } catch (e) {}
            try { info.apiVer = inst.getPlayAPIVersion(); } catch (e) {}
            send(info);
        },
        onComplete: function() {
            send({t: 'done'});
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
    print(f"attach pid={pid}")
    session = device.attach(pid)
    script = session.create_script(HOOK)

    insts = []
    fields_sample = []
    done = {"v": False}

    def on_msg(msg, data):
        if msg.get('type') != 'send':
            print("[RAW]", msg)
            return
        p = msg['payload']
        t = p.get('t')
        if t == 'fields':
            fields_sample.extend(p['list'])
        elif t == 'inst':
            insts.append(p)
        elif t == 'done':
            done['v'] = True
        elif t == 'err':
            print("[ERR]", p.get('msg'))

    script.on('message', on_msg)
    script.load()

    deadline = time.time() + 10
    while time.time() < deadline and not done['v']:
        time.sleep(0.2)

    # 打印字段样本（前 30 个，帮助找正确字段名）
    interesting = [f for f in fields_sample if any(k in f.lower() for k in
                  ('videoid', 'playauth', 'apiversion', 'videomodel', 'playurl', 'state', 'source'))]
    print(f"\n--- fields matched ({len(interesting)}) ---")
    for f in interesting[:40]:
        print(f"  {f}")

    print(f"\n--- TTVideoEngine instances: {len(insts)} ---")
    for i, inst in enumerate(insts):
        print(f"[{i}] {inst}")

if __name__ == '__main__':
    main()
