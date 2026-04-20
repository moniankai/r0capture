#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""从 App 内存扫 SaasVideoDetailModel 实例拿当前剧的 series_id + total.

前置: App 已在目标剧的 ShortSeriesActivity (播放页).
"""
import json
import subprocess
import sys
import time


JS = r"""
Java.perform(function() {
    var DetailCls = 'com.dragon.read.component.shortvideo.data.saas.video.SaasVideoDetailModel';
    var SvdCls    = 'com.dragon.read.component.shortvideo.data.saas.video.SaasVideoData';
    var found = [];
    var seen_series = {};

    function dumpFields(inst) {
        var out = {};
        try {
            var c = inst.getClass();
            var fs = c.getDeclaredFields();
            for (var i = 0; i < fs.length; i++) {
                fs[i].setAccessible(true);
                var v = null;
                try { v = fs[i].get(inst); } catch(e) {}
                if (v !== null) {
                    var s = '';
                    try { s = String(v); } catch(e) {}
                    if (s.length > 80) s = s.substring(0, 80) + '...';
                    out[fs[i].getName()] = s;
                }
            }
        } catch(e) {}
        return out;
    }

    try {
        Java.choose(DetailCls, {
            onMatch: function(inst) {
                var fields = dumpFields(inst);
                var sid = fields.episodesId || fields.seriesId || '';
                if (sid && !seen_series[sid]) {
                    seen_series[sid] = true;
                    found.push({cls: 'SaasVideoDetailModel', fields: fields});
                }
            },
            onComplete: function() {}
        });
    } catch(e) { send({t:'detail_err', err: String(e)}); }

    try {
        Java.choose(SvdCls, {
            onMatch: function(inst) {
                try {
                    var sid = String(inst.getSeriesId());
                    if (!sid || seen_series[sid]) return;
                    var total = -1;
                    try { total = Number(inst.getEpisodesCount()); } catch(e) {}
                    var name = '';
                    try { name = String(inst.getSeriesName()); } catch(e) {}
                    found.push({
                        cls: 'SaasVideoData',
                        seriesId: sid, total: total, name: name,
                        vid: String(inst.getVid()), idx: Number(inst.getVidIndex()),
                        title: String(inst.getTitle()).substring(0, 40),
                    });
                    seen_series[sid] = true;
                } catch(e) {}
            },
            onComplete: function() {
                send({t: 'result', found: found});
            }
        });
    } catch(e) {
        send({t: 'result', found: found, err: String(e)});
    }
});
"""


def main():
    r = subprocess.run(['adb', 'shell', 'pidof', 'com.phoenix.read'],
                       capture_output=True, text=True, timeout=5)
    pid = int(r.stdout.strip().split()[0])
    print(f"[probe] attach pid={pid}", file=sys.stderr)
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
    # Java.choose 是同步的, 但 attach 后需要等 ready
    for _ in range(30):
        if any(e.get('t') == 'result' for e in events):
            break
        time.sleep(0.2)
    script.unload()
    session.detach()

    # 解析结果
    result = next((e for e in events if e.get('t') == 'result'), None)
    if not result:
        print(json.dumps({'error': 'no result'}, ensure_ascii=False))
        return 1
    # 输出最完整的一条
    found = result.get('found', [])
    if not found:
        print(json.dumps({'error': 'no matching instance', 'err': result.get('err')},
                          ensure_ascii=False))
        return 1
    # 优先 SaasVideoData (更完整)
    svd = next((f for f in found if f.get('cls') == 'SaasVideoData'), None)
    if svd:
        out = {
            'series_id': svd['seriesId'],
            'name': svd.get('name', ''),
            'total': svd.get('total', -1),
            'current_ep': svd.get('idx'),
            'current_title': svd.get('title'),
        }
    else:
        dm = found[0]['fields']
        out = {
            'series_id': dm.get('episodesId') or dm.get('seriesId'),
            'name': dm.get('bookName') or dm.get('seriesName') or '',
            'total': int(dm.get('episodesCount', -1) or -1),
            'raw_fields': dm,
        }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
