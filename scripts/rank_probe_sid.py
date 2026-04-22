#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""对 j30 做全量字段+getter+嵌套对象 dump, 找真 series_id 对应的来源.

Ground truth:
  疯美人 真 series_id = 7624372698860227646
  疯美人 ep1 biz_vid = 7624374611039226905  (j30.J() 返回这个, 错误)

策略:
  1. 挂 c0 hook
  2. 每个 batch 里, 对每个 j30 实例:
     - 取 name (j30.E())
     - 若 name 匹配 TARGETS, dump 全部 40 getter 返回值
     - 还要 dump 所有 non-null 字段值 (reflection)
     - 还要对嵌套对象 (c()/k()/t()/p()/M() 等) cast + 枚举它的字段
  3. 发到 Python 端, Python 按 '等于真 sid' 反推来源
"""
import subprocess, time, threading, json
from pathlib import Path

# Ground truth: 从 videos/疯美人/session_manifest.jsonl 读的
GROUND_TRUTH = {
    '疯美人': '7624372698860227646',
    '开局一条蛇，无限进化': '7622955207885851672',
}

JS = r"""
'use strict';
send({t:'ready'});
setTimeout(function() {
    Java.perform(function() {
        try {
            var VM = Java.use('com.dragon.read.kmp.shortvideo.distribution.page.tab.SeriesRankTabViewModel');
            var tc4e = Java.use('tc4.e');
            var y34c = Java.use('y34.c');
            var j30 = Java.use('com.bytedance.kmp.reading.model.j30');

            var target_names = %TARGETS%;
            var fired = {};

            function dumpObject(obj, depth) {
                if (!obj || depth > 2) return null;
                try {
                    var cls = obj.getClass();
                    var out = { _cls: String(cls.getName()) };
                    var fields = cls.getDeclaredFields();
                    for (var i = 0; i < fields.length; i++) {
                        var f = fields[i];
                        try {
                            f.setAccessible(true);
                            var v = f.get(obj);
                            var fname = String(f.getName());
                            var ftype = String(f.getType().getName());
                            if (v === null) continue;
                            var s = String(v);
                            if (s.length > 300) s = s.substring(0, 300) + '...';
                            out[fname + ':' + ftype] = s;
                        } catch(e) {}
                    }
                    return out;
                } catch(e) { return 'ERR:' + String(e); }
            }

            VM.c0.overload('java.util.List').implementation = function(list) {
                var ret = this.c0(list);
                try {
                    if (list === null) return ret;
                    var size = list.size();
                    for (var i = 0; i < size; i++) {
                        var el = Java.cast(list.get(i), tc4e);
                        var vtm = null;
                        try { vtm = el._a.value; } catch(e) {}
                        if (!vtm) continue;
                        var video = null;
                        try { video = Java.cast(vtm, y34c).g(); } catch(e) {}
                        if (!video) continue;
                        var v = Java.cast(video, j30);

                        var name = null;
                        try { name = String(v.E()); } catch(e) {}
                        if (!name || fired[name]) continue;

                        var hit = false;
                        for (var tn in target_names) {
                            if (name.indexOf(tn) !== -1 || tn.indexOf(name) !== -1) {
                                hit = true; break;
                            }
                        }
                        if (!hit) continue;
                        fired[name] = true;

                        // dump 所有 getter
                        var getter_names = [
                            'A','B','C','D','E','F','G','H','I','J','K','L','M',
                            'b','c','d','e','f','g','h','i','j','k','l','m','n',
                            'o','p','q','r','s','t','u','v','w','x','y','z'
                        ];
                        var getters = {};
                        for (var gi = 0; gi < getter_names.length; gi++) {
                            var gn = getter_names[gi];
                            try {
                                var rv = v[gn]();
                                if (rv === null || rv === undefined) {
                                    getters[gn] = null;
                                } else {
                                    var s = String(rv);
                                    // 对嵌套对象: 不用 toString, 而是 dump 字段
                                    if (s.indexOf('@') !== -1 && s.indexOf('com.') === 0) {
                                        getters[gn] = dumpObject(rv, 1);
                                    } else {
                                        if (s.length > 300) s = s.substring(0,300)+'...';
                                        getters[gn] = s;
                                    }
                                }
                            } catch(e) {
                                getters[gn] = 'ERR:' + String(e).substring(0,80);
                            }
                        }

                        // dump 所有 j30 字段 (120 个)
                        var fields = {};
                        var all_fields = v.getClass().getDeclaredFields();
                        for (var fi = 0; fi < all_fields.length; fi++) {
                            var f = all_fields[fi];
                            try {
                                f.setAccessible(true);
                                var fval = f.get(v);
                                if (fval === null) continue;
                                var fs = String(fval);
                                var ftype = String(f.getType().getName());
                                // 对嵌套对象类: dump 字段
                                if (fs.indexOf('@') !== -1 && ftype.indexOf('com.') === 0) {
                                    fields[String(f.getName()) + ':' + ftype] = dumpObject(fval, 1);
                                } else {
                                    if (fs.length > 300) fs = fs.substring(0,300)+'...';
                                    fields[String(f.getName()) + ':' + ftype] = fs;
                                }
                            } catch(e) {}
                        }

                        send({t:'full_dump', name: name, getters: getters, fields: fields});
                    }
                } catch(e) { send({t:'err', e:String(e)}); }
                return ret;
            };

            send({t:'log', msg:'c0 hooked, targets: ' + JSON.stringify(target_names)});
        } catch(e) { send({t:'err', e:String(e)}); }
    });
}, 200);
"""


def main():
    serial = '4d53df1f'
    targets_dict = {k: v for k, v in GROUND_TRUTH.items()}
    js = JS.replace('%TARGETS%', json.dumps(targets_dict))

    r = subprocess.run(['adb','-s',serial,'shell','pidof','com.phoenix.read'],
                       capture_output=True, text=True, timeout=5)
    pid = int(r.stdout.strip().split()[0])
    print(f'pid={pid}', flush=True)

    import frida
    dev = frida.get_device(serial)
    sess = dev.attach(pid)
    script = sess.create_script(js)

    done = threading.Event()
    dumps = []
    def on_msg(m, d):
        if m.get('type') != 'send':
            if m.get('type') == 'error':
                print('[JS err]', m.get('description','')[:300], flush=True)
            return
        p = m['payload']
        t = p.get('t')
        if t == 'ready':
            print('[ready]', flush=True)
        elif t == 'log':
            print(f'[hook] {p["msg"]}', flush=True)
        elif t == 'err':
            print(f'[err] {p["e"]}', flush=True)
        elif t == 'full_dump':
            print(f'\n=== DUMP: {p["name"]} ===', flush=True)
            dumps.append(p)
            truth_sid = GROUND_TRUTH.get(p['name'])
            for name_candidate, sid in GROUND_TRUTH.items():
                if name_candidate in p['name'] or p['name'] in name_candidate:
                    truth_sid = sid
                    break
            print(f'   ground truth series_id = {truth_sid}', flush=True)
            print('\n   --- getters (non-null) ---', flush=True)
            for k, v in p['getters'].items():
                if v is None: continue
                marker = ' ◄ MATCH' if isinstance(v, str) and truth_sid and truth_sid in v else ''
                vs = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
                if len(vs) > 200: vs = vs[:200] + '...'
                print(f'    {k}() = {vs}{marker}', flush=True)
            print('\n   --- fields (non-null) ---', flush=True)
            for k, v in p['fields'].items():
                marker = ' ◄ MATCH' if isinstance(v, str) and truth_sid and truth_sid in v else ''
                vs = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
                if len(vs) > 200: vs = vs[:200] + '...'
                # 嵌套对象里也要找
                nested_match = ''
                if isinstance(v, dict):
                    for nk, nv in v.items():
                        if isinstance(nv, str) and truth_sid and truth_sid in nv:
                            nested_match += f' ◄◄ nested match: {nk} = {nv[:100]}'
                print(f'    {k} = {vs}{marker}{nested_match}', flush=True)
            if len(dumps) >= 1:
                done.set()

    script.on('message', on_msg)
    try:
        script.load()
        time.sleep(1)

        # 导航: BACK 到主页 → 进排行榜 → tap 热播榜 → swipe 多次找目标剧
        def adb(*a, timeout=8):
            return subprocess.run(['adb','-s',serial]+list(a),
                                  capture_output=True, text=True, timeout=timeout)
        def tap(x,y): adb('shell','input','tap',str(x),str(y))
        def key(k): adb('shell','input','keyevent',k)
        def focus():
            r = adb('shell','dumpsys window windows')
            for ln in r.stdout.splitlines():
                if 'mCurrentFocus' in ln:
                    return ln.strip()
            return ''
        def swipe(x1,y1,x2,y2,dur):
            adb('shell','input','swipe',str(x1),str(y1),str(x2),str(y2),str(dur))

        # BACK 到主页
        for _ in range(8):
            if 'MainFragmentActivity' in focus(): break
            if 'com.phoenix.read' not in focus():
                adb('shell','monkey','-p','com.phoenix.read',
                    '-c','android.intent.category.LAUNCHER','1')
                time.sleep(3)
                continue
            key('KEYCODE_BACK'); time.sleep(0.7)
        print(f'[nav] main: {focus()}', flush=True)

        tap(324, 1820); time.sleep(2.2)
        tap(442, 381); time.sleep(3)
        print(f'[nav] rank: {focus()}', flush=True)

        # 热播榜 (《疯美人》#26, 《开局一条蛇》可能也在)
        tap(528, 516); time.sleep(3)
        print('[nav] tap 热播榜', flush=True)

        # 多次 swipe 扫过 top 30
        for i in range(10):
            if done.is_set(): break
            swipe(540,1550,540,900,700); time.sleep(1.2)
            print(f'[nav] swipe #{i+1}, dumps so far: {len(dumps)}', flush=True)

        done.wait(timeout=5)

    finally:
        try: script.unload()
        except Exception: pass
        try: sess.detach()
        except Exception: pass

if __name__ == '__main__':
    main()
