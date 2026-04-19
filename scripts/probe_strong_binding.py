#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Probe: 找 biz_vid (SaasVideoData) 和 tt_vid (TTVideoEngine.VideoModel) 的强绑定 caller.

原理:
- 切一次集会触发一系列 Java 调用:
    某 Controller.onPlayEpisode(SaasVideoData)
      → SaasVideoData.setVid/setVidIndex (biz 层数据初始化)
      → 某 Mapper.resolveVideoModel(biz_vid) → TTVideoEngine.setVideoModel(tt_model)
- 如果这个 Controller 方法同时持有 biz_vid 和能构造出 tt_vid, 就能做"一次性强绑定"
- Hook 它就能 emit 单条 {ep, biz_vid, tt_vid, kid, url} 事件, 从根上杜绝串集

方法: Hook SaasVideoData.setVid / TTE.setVideoModel, 每次调用打 Thread 调用栈 (20 层),
      用户手动切 1 次集, 我对比两次 stack 找公共祖先类/方法.

用法:
  1. App 已在《X 剧》ShortSeriesActivity, frida-server running
  2. python scripts/probe_strong_binding.py
  3. 脚本提示 "切一集" 后, 在手机上**向下滑一下** (切到下一集)
  4. 30s 后脚本自动退出, 事件写入 d:/tmp/probe_events.jsonl
  5. python scripts/probe_strong_binding.py --analyze
"""
from __future__ import annotations
import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

OUT = Path('d:/tmp/probe_events.jsonl')

JS = r"""
Java.perform(function() {
    var TTE = Java.use('com.ss.ttvideoengine.TTVideoEngine');
    var VM = Java.use('com.ss.ttvideoengine.model.VideoModel');
    var SVD = null;
    try { SVD = Java.use('com.dragon.read.component.shortvideo.data.saas.video.SaasVideoData'); } catch(e) {}
    var ThreadCls = Java.use('java.lang.Thread');

    function dumpStack(maxDepth) {
        var bt = ThreadCls.currentThread().getStackTrace();
        var stack = [];
        var limit = Math.min(bt.length, maxDepth || 25);
        for (var i = 0; i < limit; i++) {
            var e = bt[i];
            stack.push(e.getClassName() + '.' + e.getMethodName());
        }
        return stack;
    }

    // Hook setVideoModel, 抓 tt_vid + backtrace
    TTE.setVideoModel.overloads.forEach(function(ov){
        ov.implementation = function(m) {
            var tt_vid = '';
            try {
                var cast = Java.cast(m, VM);
                tt_vid = String(cast.getVideoRefStr(202) || '');
            } catch(e) {}
            if (!tt_vid) {
                try {
                    var ref = m.getVideoRef();
                    if (ref) {
                        try {
                            var f = ref.getClass().getDeclaredField('mVideoId');
                            f.setAccessible(true);
                            tt_vid = String(f.get(ref) || '');
                        } catch(e2) {}
                    }
                } catch(e) {}
            }
            send({t:'setVideoModel', tt_vid: tt_vid, stack: dumpStack(25), ts: Date.now()});
            return ov.call(this, m);
        };
    });

    // Hook SaasVideoData.setVid / setVidIndex / setSeriesId
    if (SVD) {
        try {
            SVD.setVid.overload('java.lang.String').implementation = function(v) {
                var r = this.setVid(v);
                send({t:'setVid', biz_vid: v,
                      hash: this.hashCode(), stack: dumpStack(25), ts: Date.now()});
                return r;
            };
        } catch(e) { send({t:'hook_err', where:'setVid', err:String(e)}); }
        try {
            SVD.setVidIndex.overload('long').implementation = function(v) {
                var r = this.setVidIndex(v);
                send({t:'setVidIndex', idx: v,
                      hash: this.hashCode(), stack: dumpStack(15), ts: Date.now()});
                return r;
            };
        } catch(e) {}
    }

    send({t:'ready'});
});
"""


def run_capture():
    import frida
    # 查 App PID
    import subprocess
    r = subprocess.run(['adb', 'shell', 'pidof', 'com.phoenix.read'],
                       capture_output=True, text=True, timeout=5)
    pid = int(r.stdout.strip().split()[0])
    print(f"[probe] attach pid={pid}")
    dev = frida.get_usb_device()
    session = dev.attach(pid)
    script = session.create_script(JS)

    events = []

    def on_msg(msg, data):
        if msg.get('type') == 'send':
            events.append(msg['payload'])
        elif msg.get('type') == 'error':
            print(f"[frida err] {msg.get('description')}")

    script.on('message', on_msg)
    script.load()

    print("=" * 60)
    print("脚本已挂. 请在手机上:")
    print("  1. 确认在某剧的播放页 (ShortSeriesActivity)")
    print("  2. **向下滑动屏幕一次** 切到下一集")
    print("  3. 等 10s 让所有事件 fire 完")
    print("=" * 60)

    start = time.time()
    last_report = 0
    try:
        while time.time() - start < 60:
            time.sleep(1)
            el = int(time.time() - start)
            if el != last_report and el % 5 == 0:
                print(f"  [{el}s] 已收 {len(events)} 条事件")
                last_report = el
    except KeyboardInterrupt:
        pass

    script.unload()
    session.detach()

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open('w', encoding='utf-8') as f:
        for e in events:
            f.write(json.dumps(e, ensure_ascii=False) + '\n')
    print(f"[probe] 保存 {len(events)} 条事件 → {OUT}")


def analyze():
    if not OUT.exists():
        print(f"没有 {OUT}")
        return
    events = []
    for line in OUT.read_text(encoding='utf-8').splitlines():
        if line.strip():
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                pass

    svm = [e for e in events if e.get('t') == 'setVideoModel']
    svid = [e for e in events if e.get('t') == 'setVid']
    sidx = [e for e in events if e.get('t') == 'setVidIndex']

    print(f"\n=== 事件统计 ===")
    print(f"setVideoModel: {len(svm)}")
    print(f"setVid:        {len(svid)}")
    print(f"setVidIndex:   {len(sidx)}")

    if not svm or not svid:
        print("缺事件, 无法分析")
        return

    # 分析公共 caller: svm 的 stack 和最近的 svid 的 stack 有哪些公共类
    print(f"\n=== setVideoModel 事件示例 ===")
    for e in svm[:3]:
        print(f"\n[setVideoModel tt_vid={e.get('tt_vid','')[:20]} ts={e['ts']}]")
        for s in e['stack'][:15]:
            if 'com.ss.ttvideoengine' in s or 'java.lang' in s:
                continue
            print(f"  {s}")

    print(f"\n=== setVid 事件示例 (biz_vid 设置) ===")
    for e in svid[:3]:
        print(f"\n[setVid biz_vid={e.get('biz_vid','')} hash={e.get('hash')} ts={e['ts']}]")
        for s in e['stack'][:15]:
            if 'java.lang' in s:
                continue
            print(f"  {s}")

    # 找共同类
    svm_classes = set()
    for e in svm:
        for s in e['stack']:
            cls = s.rsplit('.', 1)[0]
            if 'com.dragon' in cls:
                svm_classes.add(cls)

    svid_classes = set()
    for e in svid:
        for s in e['stack']:
            cls = s.rsplit('.', 1)[0]
            if 'com.dragon' in cls:
                svid_classes.add(cls)

    common = svm_classes & svid_classes
    print(f"\n=== 调用栈里同时出现在 setVideoModel 和 setVid 的类 ({len(common)}) ===")
    for c in sorted(common):
        print(f"  {c}")

    # setVideoModel 调用栈里 com.dragon.* 的方法频率
    print(f"\n=== setVideoModel 调用栈 com.dragon.* 方法频次 ===")
    cnt = Counter()
    for e in svm:
        for s in e['stack']:
            if 'com.dragon' in s:
                cnt[s] += 1
    for s, c in cnt.most_common(20):
        print(f"  {c}×  {s}")


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--analyze', action='store_true', help='分析已抓的事件')
    args = ap.parse_args()
    if args.analyze:
        analyze()
    else:
        run_capture()
