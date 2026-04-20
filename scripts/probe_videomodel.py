"""探索 IVideoModel / VideoModel 的方法签名，找到正确的 tt_vid 提取路径。"""
from __future__ import annotations
import sys, time
from pathlib import Path
import frida

sys.path.insert(0, str(Path(__file__).parent.parent))
from scripts.download_drama import select_running_app_pid

APP = "com.phoenix.read"

HOOK = r"""
Java.perform(function() {
    // 1) 列 IVideoModel / VideoModel 的方法
    ['com.ss.ttvideoengine.model.IVideoModel',
     'com.ss.ttvideoengine.model.VideoModel',
     'com.ss.ttvideoengine.model.VideoRef'].forEach(function(n) {
        try {
            var c = Java.use(n);
            var ms = c.class.getDeclaredMethods();
            var out = [];
            for (var i = 0; i < ms.length; i++) {
                var s = ms[i].toGenericString();
                if (s.indexOf('VideoId') !== -1 || s.indexOf('VideoRef') !== -1 ||
                    s.indexOf('getVid') !== -1 || s.indexOf('getId') !== -1 ||
                    s.indexOf('getStr') !== -1 || s.indexOf('getList') !== -1)
                    out.push(s);
            }
            send({t:'m', n:n, ms:out});
        } catch (e) { send({t:'err', n:n, e:e.toString()}); }
    });

    // 2) Hook setVideoModel, 拿到参数对象后 dump 其真实类和 tt_vid 候选
    var TTE = Java.use('com.ss.ttvideoengine.TTVideoEngine');
    function dumpVM(m) {
        if (!m) { send({t:'vm_null'}); return; }
        var clsName = '';
        try { clsName = m.getClass().getName(); } catch (e) {}
        var candidates = {};
        ['getVideoRefStr', 'getVideoId', 'getVid', 'getId'].forEach(function(fn) {
            try {
                if (typeof m[fn] === 'function') {
                    // no-arg try
                    try { candidates[fn+'()'] = String(m[fn]()); } catch (e) { candidates[fn+'()'] = 'ERR:' + e.toString().substring(0,80); }
                }
            } catch (e) {}
        });
        // getVideoRefStr(int)
        try {
            // scan overloads
            var ov = m.getVideoRefStr ? m.getVideoRefStr.overloads : [];
            for (var i = 0; i < (ov ? ov.length : 0); i++) {
                try { candidates['getVideoRefStr.o'+i+'(0)'] = String(ov[i].call(m, 0)); } catch (e) {}
                try { candidates['getVideoRefStr.o'+i+'(202)'] = String(ov[i].call(m, 202)); } catch (e) {}
            }
        } catch (e) {}
        // getVideoRef() -> inner
        try {
            var ref = m.getVideoRef();
            if (ref) {
                try { candidates['ref.class'] = ref.getClass().getName(); } catch (e) {}
                try { candidates['ref.getVideoId()'] = String(ref.getVideoId()); } catch (e) {}
            }
        } catch (e) {}
        send({t:'vm', cls: clsName, cand: candidates});
    }

    try {
        TTE.setVideoModel.overloads.forEach(function(ov, idx) {
            var params = ov.argumentTypes.map(function(a){return a.className;});
            send({t:'ov', idx:idx, params:params});
            ov.implementation = function(m) {
                dumpVM(m);
                return ov.call(this, m);
            };
        });
        send({t:'hook_set'});
    } catch (e) { send({t:'hook_err', e:e.toString()}); }

    // AOP 版本也 Hook
    try {
        var aop = TTE.com_ss_ttvideoengine_TTVideoEngine_com_dragon_read_aop_TTVideoEngineAop_setVideoModel;
        var aop_ov = aop.overloads;
        aop_ov.forEach(function(ov, idx) {
            var params = ov.argumentTypes.map(function(a){return a.className;});
            send({t:'aop_ov', idx:idx, params:params});
            ov.implementation = function() {
                var args = Array.prototype.slice.call(arguments);
                // 第二个参数通常是 VideoModel
                if (args.length >= 2) dumpVM(args[1]);
                else if (args.length >= 1) dumpVM(args[0]);
                return ov.apply(this, args);
            };
        });
        send({t:'aop_hook_set'});
    } catch (e) { send({t:'aop_err', e:e.toString()}); }
});
"""

def main():
    device = frida.get_usb_device(timeout=10)
    pid = select_running_app_pid(device.enumerate_processes(), APP)
    if pid is None: print('App not running'); return
    s = device.attach(pid)
    sc = s.create_script(HOOK)
    seen = set()

    def on_msg(msg, data):
        if msg.get('type') != 'send':
            if msg.get('type') == 'error': print('[JS ERR]', msg.get('description','')[:300])
            return
        p = msg['payload']; t = p.get('t')
        if t == 'm':
            print(f"\n== {p['n']} ==")
            for s in p['ms']: print(' ', s)
        elif t == 'ov':
            print(f"[OV {p['idx']}] params={p['params']}")
        elif t == 'aop_ov':
            print(f"[AOP_OV {p['idx']}] params={p['params']}")
        elif t == 'vm':
            key = p['cls'] + str(p['cand'])
            if key in seen: return
            seen.add(key)
            print(f"\n[VM] cls={p['cls']}")
            for k, v in p['cand'].items():
                print(f"    {k:30s} = {str(v)[:120]}")
        elif t == 'vm_null':
            print('[VM] null')
        elif t in ('err', 'hook_err', 'aop_err'):
            print(f"[ERR {t}]", p)
        else:
            print(p)

    sc.on('message', on_msg)
    sc.load()
    print('Hook loaded. 上滑切集...')
    time.sleep(40)

if __name__ == '__main__': main()
