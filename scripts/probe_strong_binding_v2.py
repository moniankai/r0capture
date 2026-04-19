#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Probe v2: 聚焦 holder.a.T2 (setVideoModel 的直接 caller 之一),
看它的参数 + holder 实例的字段, 找强绑定点.
"""
from __future__ import annotations
import json
import subprocess
import sys
import time
from pathlib import Path

OUT = Path('d:/tmp/probe_v2_events.jsonl')

JS = r"""
Java.perform(function() {
    function dumpFields(inst, maxDepth) {
        try {
            var cls = inst.getClass();
            var fs = cls.getDeclaredFields();
            var out = [];
            for (var i = 0; i < fs.length; i++) {
                var f = fs[i];
                f.setAccessible(true);
                var fname = f.getName();
                var ftype = f.getType().getName();
                var val = null;
                try { val = f.get(inst); } catch(e) {}
                if (val === null) continue;
                var s = '';
                try { s = String(val); } catch(e) { s = '<err>'; }
                if (s.length > 60) s = s.substring(0,60) + '...';
                out.push({n: fname, t: ftype, v: s});
            }
            return out;
        } catch(e) { return [{err: String(e)}]; }
    }

    function hookClsMethod(clsName, methodName, tag) {
        try {
            var C = Java.use(clsName);
            var ovs = C[methodName].overloads;
            ovs.forEach(function(ov, idx) {
                ov.implementation = function() {
                    var args = Array.prototype.slice.call(arguments);
                    var types = [];
                    var snaps = [];
                    for (var i = 0; i < args.length; i++) {
                        if (args[i] === null) { types.push('null'); snaps.push(null); continue; }
                        var t = '';
                        try { t = args[i].getClass().getName(); } catch(e) { t = typeof args[i]; }
                        types.push(t);
                        // 如果参数类型是 SaasVideoData, 抓 vid/idx
                        if (t.indexOf('SaasVideoData') >= 0) {
                            try {
                                snaps.push({
                                    vid: String(args[i].getVid()),
                                    idx: Number(args[i].getVidIndex()),
                                    title: String(args[i].getTitle()),
                                    seriesId: String(args[i].getSeriesId())
                                });
                            } catch(e) { snaps.push({err: String(e)}); }
                        } else if (t.indexOf('VideoModel') >= 0) {
                            try {
                                snaps.push({tt_vid: String(args[i].getVideoRefStr(202))});
                            } catch(e) { snaps.push({err: String(e)}); }
                        } else {
                            snaps.push(null);
                        }
                    }
                    // 抓 this 实例的字段快照
                    var fields = dumpFields(this, 5);
                    send({t: tag, ov: idx, types: types, args: snaps,
                          this_fields: fields, ts: Date.now()});
                    return ov.apply(this, args);
                };
            });
            send({t:'hook_ok', cls: clsName, method: methodName, count: ovs.length});
        } catch(e) {
            send({t:'hook_err', cls: clsName, method: methodName, err: String(e)});
        }
    }

    // holder.a 的 j2, T2, 以及枚举所有方法
    var holderA = 'com.dragon.read.component.shortvideo.impl.v2.view.holder.a';
    hookClsMethod(holderA, 'T2', 'a.T2');
    hookClsMethod(holderA, 'j2', 'a.j2');

    // 列 holder.a 上所有方法名 (便于看还有哪些感兴趣的)
    try {
        var A = Java.use(holderA);
        var ms = A.class.getDeclaredMethods();
        var names = [];
        for (var i = 0; i < ms.length; i++) {
            names.push(ms[i].getName() + '(' + ms[i].getParameterTypes().length + ')');
        }
        send({t:'a_methods', all: names});
    } catch(e) {}

    // Hook setVideoModel 继续作为标杆事件
    var TTE = Java.use('com.ss.ttvideoengine.TTVideoEngine');
    var VM = Java.use('com.ss.ttvideoengine.model.VideoModel');
    TTE.setVideoModel.overloads.forEach(function(ov) {
        ov.implementation = function(m) {
            var vid = '';
            try {
                var cast = Java.cast(m, VM);
                vid = String(cast.getVideoRefStr(202) || '');
            } catch(e) {}
            send({t:'setVM', tt_vid: vid, ts: Date.now()});
            return ov.call(this, m);
        };
    });

    send({t:'ready'});
});
"""


def main():
    r = subprocess.run(['adb', 'shell', 'pidof', 'com.phoenix.read'],
                       capture_output=True, text=True, timeout=5)
    pid = int(r.stdout.strip().split()[0])
    print(f"[probe v2] attach pid={pid}")
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
    print("ready. waiting 45s...")

    start = time.time()
    while time.time() - start < 45:
        time.sleep(1)
        el = int(time.time() - start)
        if el % 5 == 0 and el > 0:
            print(f"  [{el}s] 收 {len(events)} 条")
        # 第 8s / 18s / 28s 各触发一次 swipe
        if el in (8, 18, 28):
            subprocess.run(['adb', 'shell', 'input swipe 540 1400 540 400 300'],
                           capture_output=True, timeout=3)
            print(f"  [{el}s] swipe")

    script.unload()
    session.detach()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open('w', encoding='utf-8') as f:
        for e in events:
            f.write(json.dumps(e, ensure_ascii=False) + '\n')
    print(f"[saved] {len(events)} events → {OUT}")


if __name__ == '__main__':
    main()
