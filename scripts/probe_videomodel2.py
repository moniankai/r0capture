"""用 VideoRef.toBashString() 提取 tt_vid。"""
from __future__ import annotations
import sys, time, re
from pathlib import Path
import frida

sys.path.insert(0, str(Path(__file__).parent.parent))
from scripts.download_drama import select_running_app_pid

APP = "com.phoenix.read"

HOOK = r"""
Java.perform(function() {
    var TTE = Java.use('com.ss.ttvideoengine.TTVideoEngine');

    function dump(m) {
        if (!m) return;
        try {
            var ref = m.getVideoRef();
            if (!ref) { send({t:'noref'}); return; }
            var json = '';
            try { json = String(ref.toBashString() || ''); } catch (e) {}
            if (!json) { try { json = String(ref.toBashJsonObject() || ''); } catch (e) {} }
            send({t:'ref_json', len: json.length, preview: json.substring(0, 400)});
        } catch (e) { send({t:'dump_err', e:e.toString()}); }
    }

    // IVideoModel overload
    TTE.setVideoModel.overloads.forEach(function(ov) {
        ov.implementation = function(m) {
            dump(m);
            return ov.call(this, m);
        };
    });
    // AOP static
    try {
        var aop = TTE.com_ss_ttvideoengine_TTVideoEngine_com_dragon_read_aop_TTVideoEngineAop_setVideoModel;
        aop.overloads.forEach(function(ov) {
            ov.implementation = function() {
                var args = Array.prototype.slice.call(arguments);
                if (args.length >= 2) dump(args[1]);
                return ov.apply(this, args);
            };
        });
    } catch (e) {}
    send({t:'ready'});
});
"""

def main():
    device = frida.get_usb_device(timeout=10)
    pid = select_running_app_pid(device.enumerate_processes(), APP)
    if pid is None: print('not running'); return
    s = device.attach(pid)
    sc = s.create_script(HOOK)
    seen_json = set()

    def on_msg(msg, data):
        if msg.get('type') != 'send':
            if msg.get('type') == 'error': print('[JS ERR]', msg.get('description','')[:200])
            return
        p = msg['payload']; t = p.get('t')
        if t == 'ready':
            print('[READY]')
        elif t == 'ref_json':
            pv = p['preview']
            # find video_id
            m = re.search(r'"video_id"\s*:\s*"(v0[^"]+)"', pv)
            key = m.group(1) if m else pv[:60]
            if key in seen_json: return
            seen_json.add(key)
            print(f"\n[REF len={p['len']}] preview: {pv[:300]}")
            if m: print(f"  >>> tt_vid = {m.group(1)}")
        elif t == 'noref': print('[NOREF]')
        elif t == 'dump_err': print(f"[DUMP_ERR] {p['e']}")

    sc.on('message', on_msg)
    sc.load()
    print('上滑切集...')
    time.sleep(40)

if __name__ == '__main__': main()
