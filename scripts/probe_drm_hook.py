"""U5c: Hook _initIntertrustDrm / setDecryptionKey / setEncodedKey 看能否在 Java 层直接拿 raw AES key。"""
import argparse, sys, time
from pathlib import Path
import frida
sys.path.insert(0, str(Path(__file__).parent.parent))
from scripts.download_drama import select_running_app_pid

APP_PACKAGE = "com.phoenix.read"

JS = r"""
Java.perform(function() {
    function bh(arr) {
        if (!arr) return '';
        var r = '';
        for (var i = 0; i < arr.length; i++) {
            var b = (arr[i] & 0xff).toString(16);
            if (b.length < 2) b = '0' + b;
            r += b;
        }
        return r;
    }

    // Hook TTVideoEngineImpl._initIntertrustDrm
    try {
        var Impl = Java.use('com.ss.ttvideoengine.TTVideoEngineImpl');
        if (Impl._initIntertrustDrm) {
            Impl._initIntertrustDrm.overloads.forEach(function(ov) {
                ov.implementation = function() {
                    send({t:'drm_enter', ts:Date.now(), args:arguments.length});
                    for (var i = 0; i < arguments.length; i++) {
                        var a = arguments[i];
                        var v = (a === null) ? 'null' : String(a).substring(0,200);
                        send({t:'drm_arg', idx:i, val:v});
                    }
                    var ret = ov.apply(this, arguments);
                    send({t:'drm_ret', ts:Date.now(), val: ret===null ? 'null' : String(ret).substring(0,200)});
                    return ret;
                };
            });
            send({t:'log', msg:'_initIntertrustDrm hooked ('+Impl._initIntertrustDrm.overloads.length+' overloads)'});
        } else {
            send({t:'log', msg:'_initIntertrustDrm NOT FOUND on Impl'});
        }
    } catch(e) { send({t:'err', msg:'Impl init: '+e.toString()}); }

    // Hook setDecryptionKey / setEncodedKey (on TTVideoEngine + Impl)
    var hookKeyMethod = function(clsName, methodName) {
        try {
            var C = Java.use(clsName);
            if (C[methodName]) {
                C[methodName].overloads.forEach(function(ov) {
                    ov.implementation = function() {
                        var vals = [];
                        for (var i = 0; i < arguments.length; i++) {
                            var a = arguments[i];
                            if (a === null) { vals.push('null'); continue; }
                            try {
                                // byte[] case
                                if (typeof a === 'object' && a.length !== undefined) {
                                    vals.push('bytes['+a.length+']:' + bh(a));
                                } else {
                                    vals.push(String(a).substring(0,200));
                                }
                            } catch(e) { vals.push('ERR:'+e.toString().substring(0,40)); }
                        }
                        send({t:'key_call', cls:clsName, method:methodName, ts:Date.now(), args:vals});
                        return ov.apply(this, arguments);
                    };
                });
                send({t:'log', msg:clsName+'.'+methodName+' hooked ('+C[methodName].overloads.length+' overloads)'});
            }
        } catch(e) { send({t:'err', msg:clsName+'.'+methodName+': '+e.toString().substring(0,80)}); }
    };
    hookKeyMethod('com.ss.ttvideoengine.TTVideoEngine', 'setDecryptionKey');
    hookKeyMethod('com.ss.ttvideoengine.TTVideoEngine', 'setEncodedKey');
    hookKeyMethod('com.ss.ttvideoengine.TTVideoEngineImpl', 'setDecryptionKey');
    hookKeyMethod('com.ss.ttvideoengine.TTVideoEngineImpl', 'setEncodedKey');

    // 也 Hook setVideoModel + av_aes_init 用于时序对照
    var ArrayList = Java.use('java.util.ArrayList');
    try {
        var TTE = Java.use('com.ss.ttvideoengine.TTVideoEngine');
        TTE.setVideoModel.overloads.forEach(function(ov) {
            ov.implementation = function(m) {
                try {
                    var ref = m.getVideoRef();
                    if (ref) {
                        var list = ref.getVideoInfoList();
                        if (list) {
                            var arr = Java.cast(list, ArrayList);
                            if (arr.size() > 0) {
                                var info = arr.get(0);
                                var cls = info.getClass();
                                var kid = '', spadea = '';
                                try { var f = cls.getDeclaredField('mKid'); f.setAccessible(true); kid = String(f.get(info)||''); } catch(e){}
                                try { var f = cls.getDeclaredField('mSpadea'); f.setAccessible(true); spadea = String(f.get(info)||''); } catch(e){}
                                send({t:'model', ts:Date.now(), kid:kid.substring(0,32), spadea:spadea});
                            }
                        }
                    }
                } catch(e){}
                return ov.call(this, m);
            };
        });
        try {
            var aop = TTE.com_ss_ttvideoengine_TTVideoEngine_com_dragon_read_aop_TTVideoEngineAop_setVideoModel;
            aop.overloads.forEach(function(ov) {
                ov.implementation = function() {
                    var args = Array.prototype.slice.call(arguments);
                    if (args.length >= 2 && args[1]) {
                        try {
                            var m = args[1];
                            var ref = m.getVideoRef();
                            if (ref) {
                                var list = ref.getVideoInfoList();
                                if (list) {
                                    var arr = Java.cast(list, ArrayList);
                                    if (arr.size() > 0) {
                                        var info = arr.get(0);
                                        var cls = info.getClass();
                                        var kid = '', spadea = '';
                                        try { var f = cls.getDeclaredField('mKid'); f.setAccessible(true); kid = String(f.get(info)||''); } catch(e){}
                                        try { var f = cls.getDeclaredField('mSpadea'); f.setAccessible(true); spadea = String(f.get(info)||''); } catch(e){}
                                        send({t:'model_aop', ts:Date.now(), kid:kid.substring(0,32), spadea:spadea});
                                    }
                                }
                            }
                        } catch(e){}
                    }
                    return ov.apply(this, args);
                };
            });
        } catch(e){}
        send({t:'log', msg:'setVideoModel hook OK'});
    } catch(e) { send({t:'err', msg:'setVideoModel: '+e.toString()}); }
});

// av_aes_init
function hookAes() {
    var fn = Module.findExportByName('libttffmpeg.so', 'av_aes_init');
    if (!fn) return;
    Interceptor.attach(fn, {
        onEnter: function(args) {
            this.keyPtr = args[1];
            try { this.keyBits = args[2].toInt32(); } catch(e){ this.keyBits = 0; }
        },
        onLeave: function() {
            try {
                var len = this.keyBits >>> 3;
                if (len <= 0 || len > 32) return;
                var bytes = new Uint8Array(this.keyPtr.readByteArray(len));
                var hex = '';
                for (var i=0;i<bytes.length;i++){ var h=bytes[i].toString(16); if(h.length<2)h='0'+h; hex+=h; }
                send({t:'aes_key', hex:hex, ts:Date.now()});
            } catch(e){}
        }
    });
    send({t:'log', msg:'av_aes_init hooked'});
}
if (Module.findBaseAddress('libttffmpeg.so')) hookAes();
else {
    var dl = Module.findExportByName(null, 'dlopen') || Module.findExportByName(null, 'android_dlopen_ext');
    if (dl) Interceptor.attach(dl, {
        onEnter: function(a){ try{ this.lib = a[0].readCString(); }catch(e){} },
        onLeave: function(){ if (this.lib && this.lib.indexOf('libttffmpeg') !== -1) setTimeout(hookAes, 50); }
    });
}
"""

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--duration", type=int, default=25)
    args = ap.parse_args()
    device = frida.get_usb_device(timeout=10)
    pid = select_running_app_pid(device.enumerate_processes(), APP_PACKAGE)
    print(f"attach pid={pid}")
    session = device.attach(pid)
    script = session.create_script(JS)

    events = []
    def on_message(m, _d):
        if m.get("type") != "send":
            if m.get("type") == "error": print("[JS err]", m.get("description","")[:200])
            return
        p = m["payload"]
        events.append(p)
        if p.get("t") == "log": print("[JS]", p["msg"])
        elif p.get("t") == "err": print("[err]", p["msg"])
    script.on("message", on_message)
    script.load()
    time.sleep(args.duration)
    script.unload(); session.detach()

    # 按时序打印所有关键事件
    print(f"\n=== 事件时序 ({len(events)}) ===")
    for p in events:
        t = p.get("t")
        ts = p.get("ts", 0) % 100000
        if t == "model" or t == "model_aop":
            print(f"  +{ts:>6} [{t}] kid={p['kid'][:12]} spadea_len={len(p['spadea'])} spadea[:20]={p['spadea'][:20]}")
        elif t == "drm_enter":
            print(f"  +{ts:>6} [DRM_ENTER] args={p['args']}")
        elif t == "drm_arg":
            print(f"         [DRM_ARG {p['idx']}] {p['val']}")
        elif t == "drm_ret":
            print(f"  +{ts:>6} [DRM_RET] {p['val']}")
        elif t == "key_call":
            print(f"  +{ts:>6} [KEY_CALL] {p['cls'].split('.')[-1]}.{p['method']} args={p['args']}")
        elif t == "aes_key":
            print(f"  +{ts:>6} [AES_INIT] key={p['hex']}")

if __name__ == "__main__":
    main()
