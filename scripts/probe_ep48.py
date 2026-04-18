"""Probe: ep48+ 切集失败根因定位.

复用 v5 的 BIND/cap/diag/RPC,额外 hook:
  - 全量 SsHttpCall URL + HTTP code (看 ep47 vs ep48 的 HTTP 差异)
  - ShortSeriesActivity onNewIntent/setIntent/onCreate
  - 广域 ViewPager2.setCurrentItem

用法:
  1. 手动在 App 里打开《凡人仙葫第一季》播放页 (任一集都行)
  2. python scripts/probe_ep48.py
  3. 等 ~2 分钟,脚本自动 RPC 切到 ep46/47/48/50,记录调用差异
  4. 输出到 logs/probe_ep48/<ts>/
"""
from __future__ import annotations
import os, sys, time, json, subprocess
from pathlib import Path
from collections import defaultdict

from loguru import logger
import frida

APP_PACKAGE = "com.phoenix.read"

HOOK_JS = r"""
var currentSwitchSeq = 0;

Java.perform(function() {
    var ArrayList = Java.use('java.util.ArrayList');

    // ===== BIND hook (v5 原版) =====
    function bindImpl(orig) {
        return function(data) {
            if (data) try {
                var vid=null, idx=-1, sid=null, name=null, total=-1;
                try { vid = String(data.getVid()); } catch(e){}
                try { idx = Number(data.getVidIndex()); } catch(e){}
                try { sid = String(data.getSeriesId()); } catch(e){}
                try { name = String(data.getSeriesName()); } catch(e){}
                try { total = Number(data.getEpisodesCount()); } catch(e){}
                send({t:'bind', vid:vid, idx:idx, series_id:sid, name:name,
                      total_eps:total, ts:Date.now(), switch_seq:currentSwitchSeq});
            } catch(e){}
            return orig.call(this, data);
        };
    }
    var bindTypes = ['com.dragon.read.component.shortvideo.data.saas.video.SaasVideoData'];
    ['com.dragon.read.component.shortvideo.impl.v2.view.holder.a',
     'com.dragon.read.component.shortvideo.impl.v2.view.holder.z'].forEach(function(cls){
        try {
            var C = Java.use(cls);
            var ov = C.j2.overload.apply(C.j2, bindTypes);
            ov.implementation = bindImpl(ov);
        } catch(e){}
    });

    // ===== TTVideoEngine.setVideoModel (简化: 只记 kid) =====
    try {
        var TTE = Java.use('com.ss.ttvideoengine.TTVideoEngine');
        TTE.setVideoModel.overloads.forEach(function(ov){
            ov.implementation = function(m) {
                try {
                    if (m) {
                        var ref = m.getVideoRef();
                        if (ref) {
                            var list = ref.getVideoInfoList();
                            if (list) {
                                var arr = Java.cast(list, ArrayList);
                                if (arr.size() > 0) {
                                    var info = arr.get(0);
                                    var cls = info.getClass();
                                    var f = cls.getDeclaredField('mKid');
                                    f.setAccessible(true);
                                    var kid = String(f.get(info) || '');
                                    send({t:'cap', kid:kid, ts:Date.now(),
                                          switch_seq:currentSwitchSeq});
                                }
                            }
                        }
                    }
                } catch(e){}
                return ov.call(this, m);
            };
        });
    } catch(e) { send({t:'hook_err', c:'TTE', err:String(e)}); }

    // ===== 全量 HTTP URL log =====
    try {
        var Call = Java.use('com.bytedance.retrofit2.SsHttpCall');
        var Request = Java.use('com.bytedance.retrofit2.client.Request');
        Call.execute.implementation = function() {
            var url = '';
            try {
                var f = Call.class.getDeclaredField('originalRequest');
                f.setAccessible(true);
                var req = f.get(this);
                if (req) url = String(Request.getUrl.call(req) || '');
            } catch(e){}
            var t0 = Date.now();
            var resp = this.execute();
            var dt = Date.now() - t0;
            var code = -1;
            try { code = Number(resp.code()); } catch(e){}
            // 过滤: 仅 shortvideo/series/chapter/episode 相关
            var lu = url.toLowerCase();
            if (lu.indexOf('shortvideo') >= 0 || lu.indexOf('series') >= 0 ||
                lu.indexOf('chapter') >= 0 || lu.indexOf('episode') >= 0 ||
                lu.indexOf('short_video') >= 0 || lu.indexOf('video/brief') >= 0 ||
                lu.indexOf('reader') >= 0) {
                send({t:'http', url: url.substring(0, 400), code: code, dt: dt,
                      switch_seq: currentSwitchSeq, ts: Date.now()});
            }
            return resp;
        };
        send({t:'log', msg:'http hooked'});
    } catch(e) { send({t:'hook_err', c:'http', err:String(e)}); }

    // ===== ShortSeriesActivity 关键方法 =====
    ['com.dragon.read.component.shortvideo.impl.ShortSeriesActivity'].forEach(function(cls) {
        try {
            var A = Java.use(cls);
            ['onNewIntent', 'setIntent', 'onCreate', 'onResume'].forEach(function(m) {
                try {
                    A[m].overloads.forEach(function(ov) {
                        ov.implementation = function() {
                            send({t:'act_call', cls: cls.split('.').pop(), m: m,
                                  switch_seq: currentSwitchSeq, ts: Date.now()});
                            return ov.apply(this, arguments);
                        };
                    });
                } catch(e){}
            });
        } catch(e) { send({t:'hook_err', c:cls, err:String(e)}); }
    });

    // ===== ShortSeriesLaunchArgs setter hook (看 App 自己/v5 RPC 分别调了哪些) =====
    try {
        var Args = Java.use('com.dragon.read.component.shortvideo.api.model.ShortSeriesLaunchArgs');
        var setters = ['setSeriesId','setTargetVideoId','setFirstVid','setVidForce',
                       'setVideoClickPos','setVideoForcePos','setClearTop','setContext'];
        setters.forEach(function(n) {
            try {
                Args[n].overloads.forEach(function(ov) {
                    ov.implementation = function() {
                        var v = '';
                        try {
                            var a = arguments[0];
                            if (a === null) v = 'null';
                            else if (typeof a === 'string') v = a;
                            else if (typeof a === 'number') v = String(a);
                            else if (typeof a === 'boolean') v = String(a);
                            else v = String(a).substring(0, 40);
                        } catch(e){}
                        send({t:'args_set', m: n, v: v,
                              switch_seq: currentSwitchSeq, ts: Date.now()});
                        return ov.apply(this, arguments);
                    };
                });
            } catch(e){}
        });
    } catch(e) { send({t:'hook_err', c:'LaunchArgs', err:String(e)}); }

    // ===== ViewPager2 setCurrentItem (看切集是否通过它) =====
    ['androidx.viewpager2.widget.ViewPager2',
     'androidx.viewpager.widget.ViewPager'].forEach(function(cls) {
        try {
            var V = Java.use(cls);
            V.setCurrentItem.overloads.forEach(function(ov) {
                ov.implementation = function() {
                    var pos = -1;
                    try { pos = Number(arguments[0]); } catch(e){}
                    send({t:'vp_set', cls: cls.split('.').pop(), pos: pos,
                          switch_seq: currentSwitchSeq, ts: Date.now()});
                    return ov.apply(this, arguments);
                };
            });
        } catch(e){}
    });

    send({t:'ready'});
});

// ===== RPC: switch (与 v5 一致) + scanCurrent(扫 heap 拿 series_id) =====
rpc.exports = {
    scanCurrent: function() {
        return new Promise(function(resolve) {
            Java.perform(function() {
                var found = {};
                try {
                    Java.choose('com.dragon.read.component.shortvideo.data.saas.video.SaasVideoData', {
                        onMatch: function(inst) {
                            try {
                                var sid = String(inst.getSeriesId() || '');
                                var name = String(inst.getSeriesName() || '');
                                var total = Number(inst.getEpisodesCount());
                                var idx = Number(inst.getVidIndex());
                                if (sid && sid !== 'null' && sid !== '' && sid !== '0') {
                                    if (!found[sid]) {
                                        found[sid] = {name: name, total: total, eps: []};
                                    }
                                    if (idx > 0) found[sid].eps.push(idx);
                                }
                            } catch(e){}
                        },
                        onComplete: function() {
                            var best = null, bestN = 0;
                            for (var sid in found) {
                                if (found[sid].eps.length > bestN) {
                                    bestN = found[sid].eps.length;
                                    best = {series_id: sid,
                                            name: found[sid].name,
                                            total: found[sid].total,
                                            eps_count: found[sid].eps.length};
                                }
                            }
                            resolve({found_count: Object.keys(found).length, best: best});
                        }
                    });
                } catch(e) { resolve({err: String(e)}); }
            });
        });
    },

    switchToEp: function(seriesId, targetVid, pos, seq) {
        currentSwitchSeq = seq;
        return new Promise(function(resolve) {
            Java.perform(function() {
                var ctx = null, ctxName = null;
                try {
                    var ActivityThread = Java.use('android.app.ActivityThread');
                    var at = ActivityThread.currentActivityThread();
                    var mActs = at.mActivities.value;
                    var ArrayMap = Java.use('android.util.ArrayMap');
                    var map = Java.cast(mActs, ArrayMap);
                    var vals = map.values();
                    var it = vals.iterator();
                    var preferred = null, resumed = null, anyAct = null;
                    while (it.hasNext()) {
                        var rec = it.next();
                        var recCls = rec.getClass();
                        try {
                            var actF = recCls.getDeclaredField('activity');
                            actF.setAccessible(true);
                            var act = actF.get(rec);
                            if (act === null) continue;
                            var actCls = String(act.getClass().getName());
                            var paused = true;
                            try {
                                var pausedF = recCls.getDeclaredField('paused');
                                pausedF.setAccessible(true);
                                var v = pausedF.get(rec);
                                paused = v && v.booleanValue ? v.booleanValue() : Boolean(v);
                            } catch(e){}
                            if (anyAct === null) anyAct = act;
                            if (actCls.indexOf('ShortSeriesActivity') >= 0 ||
                                actCls.indexOf('.shortvideo.') >= 0) {
                                if (!paused) { preferred = act; break; }
                                if (preferred === null) preferred = act;
                            } else if (!paused && resumed === null) {
                                resumed = act;
                            }
                        } catch(e){}
                    }
                    ctx = preferred || resumed || anyAct;
                    if (ctx === null) { try { ctx = at.getApplication(); } catch(e){} }
                    if (ctx !== null) ctxName = String(ctx.getClass().getName());
                } catch(e) { resolve({ok:false, err:'find_ctx:'+String(e)}); return; }
                if (ctx === null) { resolve({ok:false, err:'no_ctx'}); return; }

                var Args, args;
                try {
                    Args = Java.use('com.dragon.read.component.shortvideo.api.model.ShortSeriesLaunchArgs');
                    args = Args.$new();
                } catch(e) { resolve({ok:false, err:'Args.$new:'+String(e)}); return; }

                try { args.setContext(ctx); } catch(e){}
                try { args.setSeriesId(String(seriesId)); } catch(e){}
                if (targetVid) {
                    try { args.setTargetVideoId(String(targetVid)); } catch(e){}
                    try { args.setFirstVid(String(targetVid)); } catch(e){}
                    try { args.setVidForce(String(targetVid)); } catch(e){}
                }
                if (pos !== null && pos !== undefined && pos >= 0) {
                    try { args.setVideoClickPos(pos); } catch(e){}
                    try { args.setVideoForcePos(pos); } catch(e){}
                }
                try { args.setClearTop(true); } catch(e){}

                var impl;
                try {
                    var Api = Java.use('com.dragon.read.component.shortvideo.api.NsShortVideoApi');
                    impl = Api.IMPL.value;
                } catch(e) { resolve({ok:false, err:'Api.IMPL:'+String(e)}); return; }
                if (impl === null) { resolve({ok:false, err:'IMPL null'}); return; }

                Java.scheduleOnMainThread(function() {
                    try {
                        impl.openShortSeriesActivity(args);
                        resolve({ok:true, ctx:ctxName, seq:seq});
                    } catch(e) {
                        resolve({ok:false, err:'open:'+String(e), seq:seq});
                    }
                });
            });
        });
    }
};
"""


def _adb_pid() -> int | None:
    env = {**os.environ, "MSYS_NO_PATHCONV": "1"}
    r = subprocess.run(["adb", "shell", "pidof", APP_PACKAGE],
                       capture_output=True, text=True, env=env)
    pids = [int(x) for x in (r.stdout or "").strip().split() if x.isdigit()]
    return min(pids) if pids else None


def main():
    pid = _adb_pid()
    if not pid:
        logger.error(f"{APP_PACKAGE} 未运行,请先手动打开 App 并进入《凡人仙葫第一季》播放页")
        return

    device = frida.get_usb_device(timeout=10)
    logger.info(f"attach pid={pid}")
    session = device.attach(pid)
    script = session.create_script(HOOK_JS)

    ts_dir = time.strftime("%Y%m%d_%H%M%S")
    out_dir = Path("logs/probe_ep48") / ts_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    events: list[dict] = []

    # 状态: 从 BIND 被动读
    bound = {"series_id": None, "ep": -1, "name": None, "total": -1}

    def on_msg(msg, _data):
        if msg.get('type') != 'send':
            if msg.get('type') == 'error':
                logger.error(f"[JS ERR] {msg.get('description','')[:200]}")
            return
        p = msg['payload']
        p['_recv_ts'] = time.time()
        events.append(p)
        t = p.get('t')
        if t == 'bind':
            if p.get('series_id') and p.get('idx', -1) > 0:
                bound['series_id'] = p['series_id']
                bound['name'] = p.get('name')
                bound['total'] = p.get('total_eps', -1)
                bound['ep'] = p['idx']
            logger.info(f"[BIND seq={p.get('switch_seq')}] ep={p.get('idx')} "
                        f"name={p.get('name')} total={p.get('total_eps')}")
        elif t == 'cap':
            logger.info(f"[CAP  seq={p.get('switch_seq')}] kid={(p.get('kid','') or '')[:12]}...")
        elif t == 'http':
            logger.debug(f"[HTTP seq={p.get('switch_seq')}] {p.get('code')} "
                         f"{p.get('dt')}ms {(p.get('url','') or '')[:120]}")
        elif t == 'args_set':
            logger.debug(f"[ARGS seq={p.get('switch_seq')}] {p['m']}={p['v']}")
        elif t == 'act_call':
            logger.info(f"[ACT  seq={p.get('switch_seq')}] {p['cls']}.{p['m']}")
        elif t == 'vp_set':
            logger.info(f"[VP   seq={p.get('switch_seq')}] {p['cls']}.setCurrentItem({p['pos']})")
        elif t == 'hook_err':
            logger.warning(f"[HOOK ERR] {p.get('c')}: {p.get('err')}")
        elif t == 'ready':
            logger.info("[hooks ready]")
        elif t == 'log':
            logger.info(f"[JS LOG] {p.get('msg')}")

    script.on('message', on_msg)
    script.load()

    # 先等 3s 看被动 BIND 是否 fire
    logger.info("等 3s 观察被动 BIND...")
    time.sleep(3)

    # 被动没收到,主动 Java.choose 扫 heap
    if not bound['series_id']:
        logger.info("被动 BIND 没 fire,主动扫 heap (SaasVideoData)...")
        try:
            r = script.exports_sync.scan_current()
            logger.info(f"scan 结果: found_count={r.get('found_count')} best={r.get('best')}")
            best = r.get('best')
            if best and best.get('series_id'):
                bound['series_id'] = best['series_id']
                bound['name'] = best.get('name')
                bound['total'] = best.get('total', -1)
                bound['ep'] = -1  # heap 扫不知道当前 ep
        except Exception as e:
            logger.error(f"scan_current 异常: {e}")

    if not bound['series_id']:
        logger.error("还是没拿到 series_id。请确认 App 在 ShortSeriesActivity 播放页后重试。")
        script.unload(); session.detach()
        return
    logger.info(f"检测到剧: 《{bound['name']}》 series_id={bound['series_id']} "
                f"ep={bound['ep']} total={bound['total']}")

    # RPC 切集序列: ep46 (前段对照) → ep47 (边界) → ep48 (目标失败点) → ep50 (确认)
    test_eps = [46, 47, 48, 50]
    seq_counter = 0
    per_ep_events: dict[int, list] = {}

    for target_ep in test_eps:
        seq_counter += 1
        pos = target_ep - 2  # v5 warm RPC 策略
        mark_start = len(events)
        logger.info(f"\n=== 切到 ep{target_ep} pos={pos} seq={seq_counter} ===")
        try:
            r = script.exports_sync.switch_to_ep(
                bound['series_id'], None, pos, seq_counter)
            logger.info(f"RPC ok={r.get('ok')} ctx={(r.get('ctx') or '')[:50]} err={r.get('err')}")
        except Exception as e:
            logger.error(f"RPC 异常: {e}")
            continue
        # 等 8s 收集事件
        time.sleep(8)
        per_ep_events[target_ep] = events[mark_start:]
        logger.info(f"=== ep{target_ep} 收到 {len(per_ep_events[target_ep])} events ===")

    # 再等 3s 收尾
    time.sleep(3)

    # ===== 写出 =====
    all_events_file = out_dir / "all_events.jsonl"
    with all_events_file.open('w', encoding='utf-8') as f:
        for e in events:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    logger.info(f"\n全事件: {all_events_file} ({len(events)} 条)")

    # per-ep 切片
    for ep, evs in per_ep_events.items():
        f = out_dir / f"ep{ep:02d}.jsonl"
        with f.open('w', encoding='utf-8') as fp:
            for e in evs:
                fp.write(json.dumps(e, ensure_ascii=False) + "\n")

    # ===== summary =====
    summary = out_dir / "summary.txt"
    with summary.open('w', encoding='utf-8') as f:
        f.write(f"Probe ep48 根因定位报告\n")
        f.write(f"剧: 《{bound['name']}》 series_id={bound['series_id']} total={bound['total']}\n")
        f.write(f"测试集: {test_eps}\n")
        f.write(f"总事件: {len(events)}\n\n")

        for ep, evs in per_ep_events.items():
            f.write(f"\n===== ep{ep} (pos={ep-2}) =====\n")
            types = defaultdict(int)
            http_urls = []
            bind_eps = []
            cap_count = 0
            for e in evs:
                types[e.get('t', '?')] += 1
                if e.get('t') == 'http':
                    http_urls.append(f"  [{e.get('code')}] {(e.get('url') or '')[:200]}")
                elif e.get('t') == 'bind':
                    bind_eps.append(e.get('idx'))
                elif e.get('t') == 'cap':
                    cap_count += 1
            f.write(f"事件分布: {dict(types)}\n")
            f.write(f"BIND idx序列: {bind_eps}\n")
            f.write(f"CAP 次数: {cap_count}\n")
            f.write(f"HTTP 请求 ({len(http_urls)}):\n")
            for u in http_urls[:30]:
                f.write(u + "\n")
            if len(http_urls) > 30:
                f.write(f"  ... 还有 {len(http_urls)-30} 条\n")

    logger.info(f"summary: {summary}")

    try:
        script.unload(); session.detach()
    except Exception:
        pass


if __name__ == "__main__":
    main()
