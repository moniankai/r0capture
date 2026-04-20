"""RPC 切集深诊脚本 — 追踪 openShortSeriesActivity 全链路时序.

目的: 找到 v5 RPC switchToEp 失败的真实断点.
用法: python scripts/diag_rpc_path.py --series-id 7622955207885851672

流程:
  1. spawn App, 等 splash → Main 稳定
  2. 挂一批 trace hook (只打日志, 不改逻辑):
     - NsShortVideoApi.IMPL.openShortSeriesActivity 入口/出口 + 参数
     - ShortSeriesActivity.onCreate / onNewIntent / onResume
     - Activity.startActivity / startActivityForResult (context 侧)
     - ViewPager.setCurrentItem
     - z.j2 / ot3.z.B0 (v5 已有 BIND hook, 同时保留观察)
  3. 触发一次 RPC pos=0 切集
  4. 30s 内打印所有事件 + 时序, 退出
"""
import sys, os, time, threading, argparse, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import frida
from loguru import logger

APP_PACKAGE = "com.phoenix.read"

DIAG_JS = r"""
'use strict';

function ts() { return Date.now(); }

function send_ev(tag, extra) {
    var p = extra || {};
    p.t = tag;
    p.ts = ts();
    send(p);
}

Java.perform(function() {
    send_ev('diag_loaded', {});

    // === NsShortVideoApi.IMPL.openShortSeriesActivity ===
    try {
        var Api = Java.use('com.dragon.read.component.shortvideo.api.NsShortVideoApi');
        var impl = Api.IMPL.value;
        var implClass = Java.use(String(impl.getClass().getName()));
        var methods = implClass.class.getDeclaredMethods();
        var openM = null;
        for (var i = 0; i < methods.length; i++) {
            if (String(methods[i].getName()) === 'openShortSeriesActivity') {
                openM = methods[i];
                break;
            }
        }
        if (openM) {
            var sig = String(openM.toGenericString());
            send_ev('hook_target', {method: 'openShortSeriesActivity', sig: sig});
            implClass.openShortSeriesActivity.implementation = function(args) {
                send_ev('open_enter', {args_cls: args ? String(args.getClass().getName()) : 'null'});
                // 尝试读关键字段
                try {
                    var sid = String(args.getSeriesId());
                    send_ev('open_arg', {k: 'seriesId', v: sid});
                } catch(e){}
                try {
                    var pos = args.getVideoForcePos();
                    send_ev('open_arg', {k: 'forcePos', v: String(pos)});
                } catch(e){}
                var tStart = ts();
                try {
                    var r = this.openShortSeriesActivity(args);
                    send_ev('open_exit', {dt: ts() - tStart, ok: true});
                    return r;
                } catch(e) {
                    send_ev('open_exit', {dt: ts() - tStart, ok: false, err: String(e)});
                    throw e;
                }
            };
            send_ev('hook_installed', {method: 'openShortSeriesActivity'});
        } else {
            send_ev('hook_missing', {method: 'openShortSeriesActivity'});
        }
    } catch(e) {
        send_ev('hook_err', {target: 'NsShortVideoApi', err: String(e)});
    }

    // === Activity.startActivity (捕获启动意图, 全 intent dump) ===
    function _dumpIntent(intent) {
        var out = {};
        try { out.action = String(intent.getAction() || ''); } catch(e){}
        try {
            var d = intent.getData();
            out.data = d ? String(d) : '';
        } catch(e){}
        try {
            var comp = intent.getComponent();
            out.cls = comp ? String(comp.getClassName()) : '';
            out.pkg = comp ? String(comp.getPackageName()) : '';
        } catch(e){}
        try { out.flags = intent.getFlags(); } catch(e){}
        try {
            var extras = intent.getExtras();
            if (extras) {
                var keys = extras.keySet();
                var it = keys.iterator();
                var kv = {};
                while (it.hasNext()) {
                    var k = String(it.next());
                    try {
                        var v = extras.get(k);
                        if (v === null) { kv[k] = 'null'; }
                        else {
                            var vt = String(v.getClass().getName());
                            kv[k] = {t: vt, v: String(v).substring(0, 200)};
                        }
                    } catch(e) { kv[k] = 'ERR:' + String(e); }
                }
                out.extras = kv;
            }
        } catch(e){}
        return out;
    }
    try {
        var Activity = Java.use('android.app.Activity');
        Activity.startActivity.overload('android.content.Intent').implementation = function(intent) {
            try {
                var info = _dumpIntent(intent);
                if ((info.cls || '').indexOf('ShortSeries') >= 0
                    || (info.cls || '').indexOf('shortvideo') >= 0
                    || (info.data || '').indexOf('series') >= 0
                    || (info.action || '').indexOf('ShortSeries') >= 0) {
                    info.from = String(this.getClass().getName());
                    send_ev('start_activity', info);
                }
            } catch(e){ send_ev('sa_err', {err: String(e)}); }
            return this.startActivity(intent);
        };
        send_ev('hook_installed', {method: 'Activity.startActivity'});
    } catch(e) {
        send_ev('hook_err', {target: 'Activity.startActivity', err: String(e)});
    }

    // === ShortSeriesActivity lifecycle ===
    try {
        var SSA = Java.use('com.dragon.read.component.shortvideo.impl.ShortSeriesActivity');
        SSA.onCreate.overload('android.os.Bundle').implementation = function(b) {
            send_ev('ssa_oncreate', {});
            return this.onCreate(b);
        };
        SSA.onNewIntent.implementation = function(intent) {
            send_ev('ssa_onnewintent', {});
            return this.onNewIntent(intent);
        };
        SSA.onResume.implementation = function() {
            send_ev('ssa_onresume', {});
            return this.onResume();
        };
        send_ev('hook_installed', {method: 'ShortSeriesActivity.lifecycle'});
    } catch(e) {
        send_ev('hook_err', {target: 'ShortSeriesActivity', err: String(e)});
    }

    // === ViewPager.setCurrentItem ===
    try {
        var VP = Java.use('androidx.viewpager.widget.ViewPager');
        VP.setCurrentItem.overload('int').implementation = function(item) {
            send_ev('vp_setcurrent', {item: item, ovl: '1-arg'});
            return this.setCurrentItem(item);
        };
        VP.setCurrentItem.overload('int', 'boolean').implementation = function(item, smooth) {
            send_ev('vp_setcurrent', {item: item, smooth: smooth, ovl: '2-arg'});
            return this.setCurrentItem(item, smooth);
        };
        send_ev('hook_installed', {method: 'ViewPager.setCurrentItem'});
    } catch(e) {
        send_ev('hook_err', {target: 'ViewPager', err: String(e)});
    }

    // === ViewPager2 ===
    try {
        var VP2 = Java.use('androidx.viewpager2.widget.ViewPager2');
        VP2.setCurrentItem.overload('int').implementation = function(item) {
            send_ev('vp2_setcurrent', {item: item, ovl: '1-arg'});
            return this.setCurrentItem(item);
        };
        VP2.setCurrentItem.overload('int', 'boolean').implementation = function(item, smooth) {
            send_ev('vp2_setcurrent', {item: item, smooth: smooth, ovl: '2-arg'});
            return this.setCurrentItem(item, smooth);
        };
        send_ev('hook_installed', {method: 'ViewPager2.setCurrentItem'});
    } catch(e) {
        send_ev('hook_err', {target: 'ViewPager2', err: String(e)});
    }

    // === ot3.z.B0 — 新签名 4 参数 (App 版本升级) ===
    try {
        var B0cls = Java.use('ot3.z');
        // 列出所有 B0 overload 帮诊断
        var B0ms = B0cls.class.getDeclaredMethods();
        for (var i = 0; i < B0ms.length; i++) {
            if (String(B0ms[i].getName()) === 'B0') {
                send_ev('b0_sig', {sig: String(B0ms[i].toGenericString())});
            }
        }
        var _b0_probed = false;
        B0cls.B0.overload(
            'com.ss.ttvideoengine.model.VideoModel',
            'long',
            'java.lang.String',
            'com.dragon.read.component.shortvideo.data.saas.video.SaasVideoData'
        ).implementation = function(model, l, s, data) {
            if (!_b0_probed && data) {
                _b0_probed = true;
                try {
                    var cls = data.getClass();
                    var mm = cls.getDeclaredMethods();
                    // 仅 no-arg 且非 void 且非 private 的 getter/is 方法
                    for (var i = 0; i < mm.length; i++) {
                        var mname = String(mm[i].getName());
                        var ret = String(mm[i].getReturnType().getName());
                        var np = mm[i].getParameterTypes().length;
                        if (np !== 0) continue;
                        if (ret === 'void') continue;
                        if (!(mname.indexOf('get') === 0 || mname.indexOf('is') === 0)) continue;
                        if (ret === 'int' || ret === 'long' || ret === 'java.lang.Integer'
                            || ret === 'java.lang.String') {
                            try {
                                mm[i].setAccessible(true);
                                var v = mm[i].invoke(data, null);
                                send_ev('svd_probe', {m: mname, ret: ret, v: String(v)});
                            } catch(e) {
                                send_ev('svd_probe', {m: mname, ret: ret, err: String(e)});
                            }
                        }
                    }
                    // 再枚举 VideoModel 的 getVideoRef 返回对象
                    try {
                        var ref = model.getVideoRef();
                        if (ref) {
                            send_ev('vref_cls', {cls: String(ref.getClass().getName())});
                            var refCls = ref.getClass();
                            var fields = refCls.getDeclaredFields();
                            for (var j = 0; j < fields.length; j++) {
                                var fname = String(fields[j].getName());
                                if (fname.indexOf('id') >= 0 || fname.indexOf('Id') >= 0
                                    || fname.indexOf('vid') >= 0 || fname.indexOf('Vid') >= 0) {
                                    try {
                                        fields[j].setAccessible(true);
                                        var fv = fields[j].get(ref);
                                        send_ev('vref_field', {n: fname, v: String(fv)});
                                    } catch(e){}
                                }
                            }
                        }
                    } catch(e) { send_ev('vref_err', {err: String(e)}); }
                } catch(e) { send_ev('svd_probe_err', {err: String(e)}); }
            }
            try {
                var vid = null, sid = null, idx = -1;
                try { vid = String(model.getVideoRefStr(202)); } catch(e){}
                try { sid = String(data.getVid()); } catch(e){}
                try { idx = data.getEpisodeIndex(); } catch(e){}
                send_ev('b0_call', {vid: vid, biz_vid: sid, idx: idx, l: String(l), s: s});
            } catch(e){ send_ev('b0_read_err', {err: String(e)}); }
            return this.B0(model, l, s, data);
        };
        send_ev('hook_installed', {method: 'ot3.z.B0(4-arg new)'});
    } catch(e) {
        send_ev('hook_deferred', {target: 'ot3.z.B0', err: String(e)});
    }

    // === SaasVideoData 新路径 setter hook ===
    try {
        var SV = Java.use('com.dragon.read.component.shortvideo.data.saas.video.SaasVideoData');
        if (SV.setSeriesName) {
            SV.setSeriesName.overload('java.lang.String').implementation = function(v) {
                send_ev('svd_setSeriesName', {v: String(v || '')});
                return this.setSeriesName(v);
            };
        }
        if (SV.setSeriesId) {
            SV.setSeriesId.overload('java.lang.String').implementation = function(v) {
                send_ev('svd_setSeriesId', {v: String(v || '')});
                return this.setSeriesId(v);
            };
        }
        send_ev('hook_installed', {method: 'SaasVideoData(new path) setters'});
    } catch(e) {
        send_ev('hook_err', {target: 'SaasVideoData(new)', err: String(e)});
    }

    // === Main Looper idle check — 定时发心跳 ===
    setInterval(function() {
        send_ev('main_alive', {});
    }, 2000);

    send_ev('all_hooks_done', {});
});


// RPC to trigger switch
rpc.exports = {
    triggerSwitch: function(seriesId, pos) {
        return new Promise(function(resolve) {
            Java.perform(function() {
                try {
                    var ActivityThread = Java.use('android.app.ActivityThread');
                    var at = ActivityThread.currentActivityThread();
                    var mActs = at.mActivities.value;
                    var ArrayMap = Java.use('android.util.ArrayMap');
                    var map = Java.cast(mActs, ArrayMap);
                    var vals = map.values();
                    var it = vals.iterator();
                    var ctx = null;
                    while (it.hasNext()) {
                        var rec = it.next();
                        var recCls = rec.getClass();
                        var actF = recCls.getDeclaredField('activity');
                        actF.setAccessible(true);
                        var act = actF.get(rec);
                        if (act) { ctx = act; break; }
                    }
                    if (!ctx) { resolve({ok: false, err: 'no_ctx'}); return; }
                    var ctxCls = String(ctx.getClass().getName());
                    send_ev('rpc_ctx', {ctx: ctxCls});

                    var Args = Java.use('com.dragon.read.component.shortvideo.api.model.ShortSeriesLaunchArgs');
                    var a = Args.$new();
                    a.setContext(ctx);
                    a.setSeriesId(String(seriesId));
                    if (pos >= 0) {
                        try { a.setVideoForcePos(pos); } catch(e){}
                        try { a.setVideoClickPos(pos); } catch(e){}
                    }
                    try { a.setClearTop(true); } catch(e){}
                    send_ev('rpc_args_ready', {sid: String(seriesId), pos: pos});

                    var Api = Java.use('com.dragon.read.component.shortvideo.api.NsShortVideoApi');
                    var impl = Api.IMPL.value;
                    send_ev('rpc_impl_ok', {impl_cls: String(impl.getClass().getName())});

                    Java.scheduleOnMainThread(function() {
                        send_ev('rpc_main_enter', {});
                        try {
                            impl.openShortSeriesActivity(a);
                            send_ev('rpc_main_exit', {ok: true});
                            resolve({ok: true});
                        } catch(e) {
                            send_ev('rpc_main_exit', {ok: false, err: String(e)});
                            resolve({ok: false, err: String(e)});
                        }
                    });
                    send_ev('rpc_scheduled', {});
                } catch(e) {
                    resolve({ok: false, err: String(e)});
                }
            });
        });
    }
};
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--series-id', required=True)
    ap.add_argument('--pos', type=int, default=0)
    ap.add_argument('--observe', type=float, default=30.0,
                    help='触发后继续观察秒数')
    ap.add_argument('--attach', action='store_true',
                    help='attach 到已运行 App (默认 spawn)')
    args = ap.parse_args()

    logger.remove()
    logger.add(sys.stderr, level='INFO',
               format='<green>{time:HH:mm:ss.SSS}</green> | {message}')

    device = frida.get_usb_device(timeout=5)
    if args.attach:
        # frida-ps 可能因 anti-detection 看不到主进程, 用 adb pidof 兜底
        import subprocess as _sp
        env = {**os.environ, "MSYS_NO_PATHCONV": "1"}
        r = _sp.run(['adb', 'shell', 'pidof', APP_PACKAGE],
                    capture_output=True, text=True, env=env, timeout=5)
        pids = [int(x) for x in (r.stdout or '').strip().split() if x.isdigit()]
        if not pids:
            logger.error('no app pid, is App running?')
            return
        pid = min(pids)
        logger.info(f'attach pid={pid} via adb pidof')
        session = device.attach(pid)
    else:
        pid = device.spawn([APP_PACKAGE])
        logger.info(f'spawn pid={pid}')
        session = device.attach(pid)

    events: list[dict] = []
    t0 = time.time()

    def on_msg(msg, data):
        if msg.get('type') == 'send':
            p = msg['payload']
            p['rel_ms'] = int((time.time() - t0) * 1000)
            events.append(p)
            tag = p.get('t', '?')
            logger.info(f'[{p["rel_ms"]:>6}ms] {tag}  {json.dumps({k:v for k,v in p.items() if k not in ("t","ts","rel_ms")}, ensure_ascii=False)}')
        elif msg.get('type') == 'error':
            logger.error(f'JS err: {msg.get("description")}  stack={msg.get("stack")}')

    script = session.create_script(DIAG_JS)
    script.on('message', on_msg)
    script.load()

    if not args.attach:
        device.resume(pid)

    # 等 App 冷启动稳定 (splash → Main → default drama 播放)
    wait_stable = 30 if not args.attach else 3
    logger.info(f'等 App 稳定 {wait_stable}s...')
    time.sleep(wait_stable)

    # 触发 RPC (仅 --series-id 非空且非 'skip' 时)
    if args.series_id and args.series_id != 'skip':
        logger.info(f'>>> RPC openShortSeriesActivity series_id={args.series_id} pos={args.pos}')
        try:
            r = script.exports_sync.trigger_switch(args.series_id, args.pos)
            logger.info(f'<<< RPC returned: {r}')
        except Exception as e:
            logger.error(f'RPC exception: {e}')
    else:
        logger.info('跳过 RPC, 主动 swipe 触发 B0')
        import subprocess as _sp2
        env2 = {**os.environ, "MSYS_NO_PATHCONV": "1"}
        for i in range(3):
            try:
                _sp2.run(['adb', 'shell', 'input swipe 540 1400 540 400 300'],
                         capture_output=True, timeout=3, env=env2)
                logger.info(f'swipe #{i+1} sent')
            except Exception as e:
                logger.warning(f'swipe err: {e}')
            time.sleep(3)

    # 继续观察
    logger.info(f'观察 {args.observe}s ...')
    time.sleep(args.observe)

    # 打印时序汇总
    logger.info('=== TIMELINE ===')
    for e in events:
        logger.info(f'  [{e["rel_ms"]:>6}ms] {e.get("t")}')

    script.unload()
    session.detach()


if __name__ == '__main__':
    main()
