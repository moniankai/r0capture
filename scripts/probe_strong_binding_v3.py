#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Probe v3: 深挖 holder.a.T2 的 arg[0] 真实类型 + Q6 (SaasVideoDetailModel) 字段."""
from __future__ import annotations
import json
import subprocess
import time
from pathlib import Path

OUT = Path('d:/tmp/probe_v3_events.jsonl')

JS = r"""
Java.perform(function() {
    function tryClassName(o) {
        if (o === null || o === undefined) return 'null';
        try { return String(o.getClass().getName()); } catch(e) {}
        try { return String(o.$className); } catch(e) {}
        return String(typeof o);
    }
    function dumpFields(inst) {
        try {
            var cls = inst.getClass();
            var out = [];
            // 也看父类的 fields
            var chain = [cls];
            var sup = cls.getSuperclass();
            for (var d = 0; d < 3 && sup; d++) {
                chain.push(sup);
                sup = sup.getSuperclass();
            }
            for (var ci = 0; ci < chain.length; ci++) {
                var fs = chain[ci].getDeclaredFields();
                for (var i = 0; i < fs.length; i++) {
                    var f = fs[i];
                    f.setAccessible(true);
                    var v = null;
                    try { v = f.get(inst); } catch(e) {}
                    if (v === null) continue;
                    var s = '';
                    try { s = String(v); } catch(e) { s = '<tostring err>'; }
                    if (s.length > 80) s = s.substring(0, 80) + '...';
                    out.push({
                        n: f.getName(),
                        t: f.getType().getName(),
                        v: s,
                        cls: chain[ci].getName(),
                    });
                }
            }
            return out;
        } catch(e) { return [{err: String(e)}]; }
    }

    var holderA = 'com.dragon.read.component.shortvideo.impl.v2.view.holder.a';
    var A = Java.use(holderA);
    // Hook 所有 T2 overloads, dump 参数和 this
    A.T2.overloads.forEach(function(ov, idx) {
        ov.implementation = function() {
            var args = Array.prototype.slice.call(arguments);
            var argInfo = [];
            for (var i = 0; i < args.length; i++) {
                var clsName = tryClassName(args[i]);
                var extra = null;
                // 若是 VideoModel 试取 tt_vid
                if (clsName.indexOf('VideoModel') >= 0) {
                    try {
                        var ref = args[i].getVideoRef();
                        if (ref) {
                            try {
                                var f = ref.getClass().getDeclaredField('mVideoId');
                                f.setAccessible(true);
                                extra = {tt_vid: String(f.get(ref) || '')};
                            } catch(e) { extra = {err: 'mVideoId:'+String(e)}; }
                        }
                    } catch(e) {}
                }
                if (clsName.indexOf('SaasVideoData') >= 0) {
                    try {
                        extra = {biz_vid: String(args[i].getVid()),
                                 idx: Number(args[i].getVidIndex())};
                    } catch(e) {}
                }
                argInfo.push({cls: clsName, extra: extra, raw: String(args[i]).substring(0,60)});
            }
            // dump this fields
            var thisFields = dumpFields(this);
            // 深挖 Q6 字段 (SaasVideoDetailModel)
            var q6Fields = null;
            try {
                var q6 = this.Q6 ? this.Q6.value : null;
                if (q6) q6Fields = dumpFields(q6);
            } catch(e) {}
            send({t:'T2', ov: idx, args: argInfo,
                  this_fields: thisFields, q6_fields: q6Fields, ts: Date.now()});
            return ov.apply(this, args);
        };
    });

    send({t:'ready'});
});
"""


def main():
    r = subprocess.run(['adb', 'shell', 'pidof', 'com.phoenix.read'],
                       capture_output=True, text=True, timeout=5)
    pid = int(r.stdout.strip().split()[0])
    print(f"[probe v3] attach pid={pid}")
    import frida
    dev = frida.get_usb_device()
    session = dev.attach(pid)
    script = session.create_script(JS)
    events = []
    def on_msg(msg, data):
        if msg.get('type') == 'send':
            events.append(msg['payload'])
    script.on('message', on_msg)
    script.load()
    print("ready, will swipe in 5s...")
    for t in (5, 12):
        time.sleep(5 if t == 5 else 7)
        subprocess.run(['adb', 'shell', 'input swipe 540 1400 540 400 300'],
                       capture_output=True, timeout=3)
        print(f"  [{t}s] swipe")
    time.sleep(5)
    script.unload()
    session.detach()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open('w', encoding='utf-8') as f:
        for e in events:
            f.write(json.dumps(e, ensure_ascii=False) + '\n')
    print(f"[saved] {len(events)} events → {OUT}")


if __name__ == '__main__':
    main()
