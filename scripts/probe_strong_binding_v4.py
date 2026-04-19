#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Probe v4: hook ot3.z (控制器类),在 B0 / setVideoModel caller 处 dump 字段找 biz_vid."""
from __future__ import annotations
import json
import subprocess
import time
from pathlib import Path

OUT = Path('d:/tmp/probe_v4_events.jsonl')

JS = r"""
Java.perform(function() {
    function classOf(o) {
        if (o === null || o === undefined) return 'null';
        try { return String(o.getClass().getName()); } catch(e) { return 'unknown'; }
    }
    function deepField(inst, limit) {
        if (!inst) return null;
        try {
            var cls = inst.getClass();
            var chain = [cls];
            var s = cls.getSuperclass();
            for (var d = 0; d < 2 && s; d++) { chain.push(s); s = s.getSuperclass(); }
            var out = [];
            for (var ci = 0; ci < chain.length; ci++) {
                var fs = chain[ci].getDeclaredFields();
                for (var i = 0; i < fs.length; i++) {
                    var f = fs[i];
                    f.setAccessible(true);
                    var v = null;
                    try { v = f.get(inst); } catch(e) {}
                    if (v === null) continue;
                    var txt = '';
                    try { txt = String(v); } catch(e) { txt = '<err>'; }
                    if (txt.length > 120) txt = txt.substring(0,120)+'...';
                    out.push({n: f.getName(), t: f.getType().getName(), v: txt});
                    if (out.length >= (limit || 40)) return out;
                }
            }
            return out;
        } catch(e) { return [{err: String(e)}]; }
    }

    // Hook ot3.z 的所有 public 方法, 感兴趣的 dump this
    try {
        var Z = Java.use('ot3.z');
        var ms = Z.class.getDeclaredMethods();
        var hooked = 0;
        for (var i = 0; i < ms.length; i++) {
            var m = ms[i];
            var mname = m.getName();
            // 只 hook 这些我们在 stack 里看到的
            if (['B0','t1','O','O0','s0','r1','s1','w','G','b1'].indexOf(mname) < 0) continue;
            (function(methodName) {
                try {
                    var ovs = Z[methodName].overloads;
                    ovs.forEach(function(ov, idx) {
                        ov.implementation = function() {
                            var args = Array.prototype.slice.call(arguments);
                            var argInfo = args.map(function(a, i){
                                var c = classOf(a);
                                var extra = null;
                                if (c.indexOf('VideoModel') >= 0) {
                                    try {
                                        var ref = a.getVideoRef();
                                        if (ref) {
                                            try {
                                                var f = ref.getClass().getDeclaredField('mVideoId');
                                                f.setAccessible(true);
                                                extra = {tt_vid: String(f.get(ref)||'')};
                                            } catch(e){}
                                        }
                                    } catch(e){}
                                }
                                if (c.indexOf('SaasVideoData') >= 0) {
                                    try {
                                        extra = {biz_vid: String(a.getVid()),
                                                 idx: Number(a.getVidIndex())};
                                    } catch(e){}
                                }
                                return {cls: c, extra: extra};
                            });
                            // dump this (ot3.z 实例)
                            var fields = deepField(this, 50);
                            send({t:'z.'+methodName, args: argInfo,
                                  this_fields: fields, ts: Date.now()});
                            return ov.apply(this, args);
                        };
                    });
                    hooked += ovs.length;
                } catch(e) { send({t:'hook_err', m:methodName, err:String(e)}); }
            })(mname);
        }
        send({t:'z_hooked', count: hooked});
        // 也列 ot3.z 的所有方法名备用
        var names = [];
        for (var i = 0; i < ms.length; i++) names.push(ms[i].getName());
        send({t:'z_methods', all: names});
    } catch(e) { send({t:'z_err', err:String(e)}); }

    send({t:'ready'});
});
"""


def main():
    r = subprocess.run(['adb', 'shell', 'pidof', 'com.phoenix.read'],
                       capture_output=True, text=True, timeout=5)
    pid = int(r.stdout.strip().split()[0])
    print(f"[probe v4] attach pid={pid}")
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
    print("ready, swipe in 5s...")
    time.sleep(5)
    subprocess.run(['adb', 'shell', 'input swipe 540 1400 540 400 300'],
                   capture_output=True, timeout=3)
    print("swiped")
    time.sleep(10)
    script.unload()
    session.detach()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open('w', encoding='utf-8') as f:
        for e in events:
            f.write(json.dumps(e, ensure_ascii=False) + '\n')
    print(f"[saved] {len(events)} events → {OUT}")


if __name__ == '__main__':
    main()
