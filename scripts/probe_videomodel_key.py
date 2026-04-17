"""U5 实验：探查 VideoModel/VideoRef 是否暴露 decryption key/token 字段。

Hook TTVideoEngine.setVideoModel，dump VideoModel + VideoRef 对象的：
  - 所有 field（name + type + value 前 200 字符）
  - 所有 method（name + returnType）
  - dynamic_video_list 每个 Entry 的字段

重点筛选：字段/方法名含 key/decrypt/aes/sec/token/cipher/iv/auth。
"""
import argparse, os, sys, time
from pathlib import Path
import frida
sys.path.insert(0, str(Path(__file__).parent.parent))
from scripts.download_drama import select_running_app_pid

APP_PACKAGE = "com.phoenix.read"

JS = r"""
Java.perform(function() {
    function dumpClass(obj, label) {
        if (!obj) { send({t:'log', msg: label + ' null'}); return; }
        try {
            var cls = obj.getClass();
            send({t:'class', label: label, name: String(cls.getName())});

            // Fields
            var fields = cls.getDeclaredFields();
            for (var i = 0; i < fields.length; i++) {
                var f = fields[i];
                try { f.setAccessible(true); } catch (e) {}
                var name = String(f.getName());
                var type = String(f.getType().getName());
                var val = '';
                try {
                    var v = f.get(obj);
                    if (v === null) val = 'null';
                    else val = String(v).substring(0, 200);
                } catch (e) { val = 'ERR:' + e.message.substring(0,80); }
                send({t:'field', label:label, name:name, type:type, value:val});
            }

            // Methods
            var methods = cls.getDeclaredMethods();
            for (var j = 0; j < methods.length; j++) {
                var m = methods[j];
                var mname = String(m.getName());
                var ret = String(m.getReturnType().getName());
                var params = m.getParameterTypes();
                var parr = [];
                for (var k = 0; k < params.length; k++) parr.push(String(params[k].getName()));
                send({t:'method', label:label, name:mname, ret:ret, params:parr.join(',')});
            }

            // 父类也扫一层
            var parent = cls.getSuperclass();
            if (parent) {
                var pname = String(parent.getName());
                if (pname !== 'java.lang.Object') {
                    send({t:'log', msg: label + ' superclass ' + pname});
                    var pfields = parent.getDeclaredFields();
                    for (var i = 0; i < pfields.length; i++) {
                        var f = pfields[i];
                        try { f.setAccessible(true); } catch (e) {}
                        var name = String(f.getName());
                        var type = String(f.getType().getName());
                        var val = '';
                        try {
                            var v = f.get(obj);
                            if (v === null) val = 'null';
                            else val = String(v).substring(0, 200);
                        } catch (e) { val = 'ERR:' + e.message.substring(0,80); }
                        send({t:'field', label:label+':super', name:name, type:type, value:val});
                    }
                }
            }
        } catch (e) { send({t:'err', label:label, err:e.toString()}); }
    }

    function dumpVideoInfo(info, idx) {
        var label = 'VideoInfo[' + idx + ']';
        dumpClass(info, label);
    }

    try {
        var TTE = Java.use('com.ss.ttvideoengine.TTVideoEngine');
        var ArrayList = Java.use('java.util.ArrayList');

        function handler(model) {
            if (!model) return;
            send({t:'log', msg:'=== setVideoModel fired ==='});
            dumpClass(model, 'VideoModel');
            try {
                var ref = model.getVideoRef();
                if (ref) {
                    dumpClass(ref, 'VideoRef');
                    // dynamic_video_list entries
                    try {
                        var list = ref.getVideoInfoList();
                        if (list) {
                            var arr = Java.cast(list, ArrayList);
                            var n = arr.size();
                            send({t:'log', msg: 'video_list size = ' + n});
                            for (var i = 0; i < Math.min(n, 2); i++) {
                                dumpVideoInfo(arr.get(i), i);
                            }
                        }
                    } catch (e) { send({t:'err', label:'videoList', err:e.toString()}); }
                }
            } catch (e) { send({t:'err', label:'getVideoRef', err:e.toString()}); }
        }

        TTE.setVideoModel.overloads.forEach(function(ov) {
            ov.implementation = function(m) {
                handler(m);
                return ov.call(this, m);
            };
        });
        try {
            var aop = TTE.com_ss_ttvideoengine_TTVideoEngine_com_dragon_read_aop_TTVideoEngineAop_setVideoModel;
            aop.overloads.forEach(function(ov) {
                ov.implementation = function() {
                    var args = Array.prototype.slice.call(arguments);
                    if (args.length >= 2) handler(args[1]);
                    return ov.apply(this, args);
                };
            });
        } catch (e) {}
        send({t:'log', msg:'Hook ready, waiting for setVideoModel...'});
    } catch (e) { send({t:'err', label:'init', err:e.toString()}); }
});
"""

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--duration", type=int, default=15)
    ap.add_argument("--out", default="d:/tmp/videomodel_dump.txt")
    args = ap.parse_args()

    device = frida.get_usb_device(timeout=10)
    pid = select_running_app_pid(device.enumerate_processes(), APP_PACKAGE)
    if pid is None:
        print(f"{APP_PACKAGE} 未运行"); sys.exit(1)
    print(f"attach pid={pid}")
    session = device.attach(pid)
    script = session.create_script(JS)

    fields, methods, logs, errors, classes = [], [], [], [], []
    def on_message(msg, _data):
        if msg.get("type") != "send":
            if msg.get("type") == "error":
                errors.append(f"[JS] {msg.get('description','')}")
            return
        p = msg["payload"]; t = p.get("t")
        if t == "field": fields.append(p)
        elif t == "method": methods.append(p)
        elif t == "log": logs.append(p["msg"])
        elif t == "err": errors.append(f"[{p.get('label','')}] {p.get('err','')}")
        elif t == "class": classes.append(p)
    script.on("message", on_message)
    script.load()
    print(f"Hook loaded. 现在请 tap 一个集号触发 setVideoModel...")
    time.sleep(args.duration)
    script.unload(); session.detach()

    # 输出
    with open(args.out, "w", encoding="utf-8") as f:
        f.write("=== LOGS ===\n")
        for l in logs: f.write(l + "\n")
        f.write("\n=== CLASSES ===\n")
        for c in classes: f.write(f"{c['label']}: {c['name']}\n")
        f.write("\n=== FIELDS ===\n")
        for fld in fields:
            f.write(f"[{fld['label']}] {fld['name']}: {fld['type']} = {fld['value']}\n")
        f.write("\n=== METHODS ===\n")
        for m in methods:
            f.write(f"[{m['label']}] {m['name']}({m['params']}) -> {m['ret']}\n")
        if errors:
            f.write("\n=== ERRORS ===\n")
            for e in errors: f.write(e + "\n")
    print(f"已写入 {args.out}")
    print(f"字段 {len(fields)} 个，方法 {len(methods)} 个，class 标签 {len(classes)} 个")

if __name__ == "__main__":
    main()
