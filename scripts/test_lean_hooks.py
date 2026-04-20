"""POC: 精简 hook (只挂 ot3.z.B0) + 同会话 Intent RPC 验证.

目标: 证明只挂必要 hook 时 RPC startActivity 不被 Java bridge 阻塞,
且 ot3.z.B0 能同时捕获 idx + biz_vid + series_id + VideoModel 所有字段.

用法:
    python scripts/test_lean_hooks.py --series-id 7622955207885851672 --pos 2
    # App 须已运行 (attach 模式)
"""
import sys, os, time, argparse, subprocess
import frida
from loguru import logger

APP_PACKAGE = "com.phoenix.read"

JS = r"""
'use strict';

var _b0_hooked = false;
var _b0_attempts = 0;

function _longToNum(v) {
    if (v === null || v === undefined) return -1;
    try { if (v.longValue) return v.longValue(); } catch(e){}
    try { return Number(String(v)); } catch(e){}
    return -1;
}

function _tryHookB0() {
    _b0_attempts += 1;
    if (_b0_hooked) return true;
    Java.perform(function() {
        try {
            var Z = Java.use('ot3.z');
            var overloads = Z.B0.overloads;
            overloads.forEach(function(ov) {
                ov.implementation = function() {
                    var args = Array.prototype.slice.call(arguments);
                    try {
                        var svd = null, vm = null;
                        for (var i = 0; i < args.length; i++) {
                            if (args[i] === null) continue;
                            try {
                                var cn = String(args[i].getClass().getName());
                                if (cn.indexOf('saas.video.SaasVideoData') >= 0) svd = args[i];
                                if (cn.indexOf('ttvideoengine.model.VideoModel') >= 0) vm = args[i];
                            } catch(e){}
                        }
                        if (svd) {
                            var idx = -1, biz_vid = '', sid = '';
                            try { idx = _longToNum(svd.getVidIndex()); } catch(e){}
                            try { biz_vid = String(svd.getVid()); } catch(e){}
                            try { sid = String(svd.getSeriesId()); } catch(e){}
                            var tt_vid = '', kid = '', spadea = '', streamCount = 0;
                            if (vm) {
                                try {
                                    var ref = vm.getVideoRef();
                                    if (ref) {
                                        try {
                                            var f = ref.getClass().getDeclaredField('mVideoId');
                                            f.setAccessible(true);
                                            tt_vid = String(f.get(ref) || '');
                                        } catch(e){}
                                        // 读 VideoInfoList 第一个 info 的 mKid/mSpadea
                                        try {
                                            var vl = ref.getVideoInfoList();
                                            if (vl) {
                                                var ArrayList = Java.use('java.util.ArrayList');
                                                var arr = Java.cast(vl, ArrayList);
                                                streamCount = arr.size();
                                                if (streamCount > 0) {
                                                    var info = arr.get(0);
                                                    var ic = info.getClass();
                                                    var kf = ic.getDeclaredField('mKid');
                                                    kf.setAccessible(true);
                                                    kid = String(kf.get(info) || '');
                                                    var sf = ic.getDeclaredField('mSpadea');
                                                    sf.setAccessible(true);
                                                    spadea = String(sf.get(info) || '');
                                                }
                                            }
                                        } catch(e) { send({t:'stream_err', err: String(e)}); }
                                    }
                                } catch(e){}
                            }
                            send({t: 'b0', idx: idx, biz_vid: biz_vid,
                                  sid: sid, tt_vid: tt_vid,
                                  kid: kid, spadea_len: spadea.length,
                                  streams: streamCount, ts: Date.now()});
                        }
                    } catch(e) {
                        send({t: 'b0_err', err: String(e)});
                    }
                    return ov.apply(this, args);
                };
            });
            _b0_hooked = true;
            send({t: 'b0_hooked', overloads: overloads.length, attempts: _b0_attempts});
        } catch(e) {
            send({t: 'b0_defer', err: String(e), attempts: _b0_attempts});
        }
    });
    return _b0_hooked;
}

// attach 时尝试一次, 失败 retry 几次
_tryHookB0();
setTimeout(function(){ if (!_b0_hooked) _tryHookB0(); }, 1000);
setTimeout(function(){ if (!_b0_hooked) _tryHookB0(); }, 3000);

rpc.exports = {
    tryHookB0: function() {
        return _tryHookB0();
    },

    startByIntent: function(seriesId, pos) {
        var t0 = Date.now();
        return new Promise(function(resolve) {
            Java.perform(function() {
                try {
                    var app = Java.use('android.app.ActivityThread')
                                  .currentActivityThread().getApplication();
                    if (!app) { resolve({ok:false, err:'no_app', dt: Date.now()-t0}); return; }

                    var Intent = Java.use('android.content.Intent');
                    var intent = Intent.$new();
                    intent.setClassName(String(app.getPackageName()),
                        'com.dragon.read.component.shortvideo.impl.ShortSeriesActivity');
                    intent.putExtra('short_series_id', String(seriesId));
                    intent.putExtra('key_click_video_pos', parseInt(pos) || 0);
                    intent.putExtra('key_player_sub_tag', 'leanPoc');
                    intent.addFlags(0x10000000);  // NEW_TASK
                    intent.addFlags(0x04000000);  // CLEAR_TOP

                    app.startActivity(intent);
                    resolve({ok: true, dt: Date.now()-t0});
                } catch(e) {
                    resolve({ok: false, err: String(e), dt: Date.now()-t0});
                }
            });
        });
    }
};
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--series-id', required=True)
    ap.add_argument('--pos', type=int, default=2,
                    help='目标集 pos (0-based). ep3 = pos 2')
    ap.add_argument('--observe', type=float, default=12.0)
    args = ap.parse_args()

    logger.remove()
    logger.add(sys.stderr, level='INFO', format='{time:HH:mm:ss.SSS} | {message}')

    env = {**os.environ, "MSYS_NO_PATHCONV": "1"}
    r = subprocess.run(['adb', 'shell', 'pidof', APP_PACKAGE],
                       capture_output=True, text=True, env=env, timeout=5)
    pids = [int(x) for x in (r.stdout or '').strip().split() if x.isdigit()]
    if not pids:
        logger.error('App 没在运行')
        return 1
    pid = min(pids)
    logger.info(f'attach pid={pid}')

    events = []
    t0 = time.time()

    def on_msg(msg, data):
        if msg.get('type') == 'send':
            p = msg['payload']
            p['rel_ms'] = int((time.time() - t0) * 1000)
            events.append(p)
            tag = p.get('t', '?')
            logger.info(f'[{p["rel_ms"]:>6}ms] {tag} {p}')
        elif msg.get('type') == 'error':
            logger.error(f'JS err: {msg.get("description")}')

    device = frida.get_usb_device(timeout=5)
    session = device.attach(pid)
    script = session.create_script(JS)
    script.on('message', on_msg)
    script.load()
    logger.info('script loaded')

    # 给 hook 一点时间安装
    time.sleep(2)
    hooked = script.exports_sync.try_hook_b0()
    logger.info(f'b0 hooked after retry: {hooked}')

    # 关键验证: 调 RPC startByIntent, 测耗时
    logger.info(f'>>> RPC startByIntent series_id={args.series_id} pos={args.pos}')
    rpc_t0 = time.time()
    try:
        res = script.exports_sync.start_by_intent(args.series_id, args.pos)
        rpc_dt = (time.time() - rpc_t0) * 1000
        logger.info(f'<<< {res} (python wall={rpc_dt:.0f}ms)')
    except Exception as e:
        logger.error(f'RPC 异常: {e}')
        session.detach()
        return 2

    # 等 Intent 切集 B0
    time.sleep(5)

    # 主验证: swipe 触发新 B0
    logger.info('=== swipe 验证: 5 次上滑看 B0 idx 变化 ===')
    for i in range(5):
        subprocess.run(['adb', 'shell', 'input swipe 540 1600 540 400 300'],
                       capture_output=True, env=env, timeout=5)
        logger.info(f'swipe #{i+1} sent')
        time.sleep(3)
    logger.info(f'继续观察 {args.observe}s...')
    time.sleep(args.observe)

    # 汇总
    b0s = [e for e in events if e.get('t') == 'b0']
    logger.info(f'=== 结果 ===')
    logger.info(f'  RPC python wall: {rpc_dt:.0f}ms (预期 < 500ms 才是 RPC 未被阻塞)')
    logger.info(f'  b0 事件数: {len(b0s)}')
    for e in b0s[:5]:
        logger.info(f'    b0 idx={e.get("idx")} biz_vid={e.get("biz_vid")} '
                    f'sid={e.get("sid")} tt_vid={e.get("tt_vid", "")[:30]}')

    # 判定
    if rpc_dt > 3000:
        logger.error('RPC 被阻塞 > 3s, 方案 G 失败')
        verdict = 'FAIL: rpc blocked'
    elif not b0s:
        logger.warning('RPC fast 但 b0 未触发 (hook 未生效或切集失败)')
        verdict = 'PARTIAL: rpc_ok, b0_miss'
    else:
        target_idx = args.pos  # 1-based ep = pos+1, 但 B0 的 idx 可能是 0-based 也可能是 1-based
        matched = [e for e in b0s if e.get('idx') == target_idx or e.get('idx') == args.pos + 1]
        if matched:
            logger.info(f'方案 G 验证通过! 切集 idx={matched[0].get("idx")} 匹配')
            verdict = 'PASS'
        else:
            logger.warning(f'b0 有事件但 idx 未匹配 target. 收到 idx: {[e.get("idx") for e in b0s]}')
            verdict = 'PARTIAL: idx_mismatch'

    logger.info(f'VERDICT: {verdict}')

    script.unload()
    session.detach()
    return 0


if __name__ == '__main__':
    sys.exit(main())
