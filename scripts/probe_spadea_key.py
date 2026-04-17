"""U5b: 同时 Hook setVideoModel(VideoInfo.mSpadea) + av_aes_init(key)，验证关系。"""
import argparse, os, sys, time
from pathlib import Path
import frida
sys.path.insert(0, str(Path(__file__).parent.parent))
from scripts.download_drama import select_running_app_pid

APP_PACKAGE = "com.phoenix.read"

JS = r"""
Java.perform(function() {
    var ArrayList = Java.use('java.util.ArrayList');

    function handleModel(m) {
        if (!m) return;
        try {
            var ref = m.getVideoRef();
            if (!ref) return;
            var list = ref.getVideoInfoList();
            if (!list) return;
            var arr = Java.cast(list, ArrayList);
            var n = arr.size();
            for (var i = 0; i < n; i++) {
                var info = arr.get(i);
                var cls = info.getClass();
                var kid = '', spadea = '', def = '';
                try {
                    var fKid = cls.getDeclaredField('mKid'); fKid.setAccessible(true);
                    kid = String(fKid.get(info) || '');
                } catch(e){}
                try {
                    var fSp = cls.getDeclaredField('mSpadea'); fSp.setAccessible(true);
                    spadea = String(fSp.get(info) || '');
                } catch(e){}
                try {
                    var fD = cls.getDeclaredField('mDefinition'); fD.setAccessible(true);
                    def = String(fD.get(info) || '');
                } catch(e){}
                send({t:'info', kid:kid, spadea:spadea, def:def, idx:i, total:n, ts:Date.now()});
            }
        } catch(e){ send({t:'err', msg:e.toString()}); }
    }
    try {
        var TTE = Java.use('com.ss.ttvideoengine.TTVideoEngine');
        TTE.setVideoModel.overloads.forEach(function(ov){
            ov.implementation = function(m){ handleModel(m); return ov.call(this, m); };
        });
        try {
            var aop = TTE.com_ss_ttvideoengine_TTVideoEngine_com_dragon_read_aop_TTVideoEngineAop_setVideoModel;
            aop.overloads.forEach(function(ov){
                ov.implementation = function(){
                    var args = Array.prototype.slice.call(arguments);
                    if (args.length>=2) handleModel(args[1]);
                    return ov.apply(this, args);
                };
            });
        } catch(e){}
        send({t:'log', msg:'setVideoModel hook OK'});
    } catch(e){ send({t:'err', msg:'setVideoModel init: '+e.toString()}); }
});

// av_aes_init
function hookAes() {
    var fn = Module.findExportByName('libttffmpeg.so', 'av_aes_init');
    if (!fn){ send({t:'err', msg:'no av_aes_init'}); return; }
    Interceptor.attach(fn, {
        onEnter: function(args){
            this.keyPtr = args[1];
            try { this.keyBits = args[2].toInt32(); } catch(e){ this.keyBits = 0; }
        },
        onLeave: function(){
            try {
                var len = this.keyBits >>> 3;
                if (len<=0 || len>32) return;
                var bytes = new Uint8Array(this.keyPtr.readByteArray(len));
                var hex = '';
                for (var i=0;i<bytes.length;i++){ var h=bytes[i].toString(16); if(h.length<2)h='0'+h; hex+=h; }
                send({t:'key', hex:hex, ts:Date.now()});
            } catch(e){}
        }
    });
    send({t:'log', msg:'av_aes_init hook OK'});
}
if (Module.findBaseAddress('libttffmpeg.so')) hookAes();
else {
    var dl = Module.findExportByName(null,'dlopen') || Module.findExportByName(null,'android_dlopen_ext');
    if (dl) Interceptor.attach(dl, {
        onEnter: function(a){ try{ this.lib=a[0].readCString(); }catch(e){} },
        onLeave: function(){ if (this.lib && this.lib.indexOf('libttffmpeg')!==-1) setTimeout(hookAes,50); }
    });
}
"""

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--duration", type=int, default=20)
    args = ap.parse_args()
    device = frida.get_usb_device(timeout=10)
    pid = select_running_app_pid(device.enumerate_processes(), APP_PACKAGE)
    print(f"attach pid={pid}")
    session = device.attach(pid)
    script = session.create_script(JS)
    events = []
    def on_message(msg,_d):
        if msg.get('type')!='send':
            if msg.get('type')=='error': print('[JS err]', msg.get('description','')[:200])
            return
        p = msg['payload']
        t = p.get('t')
        if t == 'info': events.append(('info', p['ts'], p))
        elif t == 'key': events.append(('key', p['ts'], p))
        elif t == 'log': print('[JS]', p['msg'])
        elif t == 'err': print('[err]', p['msg'])
    script.on('message', on_message)
    script.load()
    time.sleep(args.duration)
    script.unload(); session.detach()

    events.sort(key=lambda e: e[1])
    print(f"\n=== 时间线 ({len(events)} events) ===")
    for kind, ts, p in events:
        if kind == 'info':
            print(f"  [model] +{ts%100000} kid={p['kid'][:16]} def={p['def']} spadea_len={len(p['spadea'])} spadea[:24]={p['spadea'][:24]}")
        else:
            print(f"  [aes]   +{ts%100000} key={p['hex']}")

if __name__ == "__main__":
    main()
