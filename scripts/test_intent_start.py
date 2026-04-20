"""测试用 Intent-based startActivity 绕过 NsShortVideoApi.openShortSeriesActivity."""
import sys, os, time, argparse, subprocess
import frida
from loguru import logger

APP_PACKAGE = "com.phoenix.read"

JS = r"""
rpc.exports = {
    startByIntent: function(seriesId, pos) {
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
                        try {
                            var actF = recCls.getDeclaredField('activity');
                            actF.setAccessible(true);
                            var act = actF.get(rec);
                            if (act) { ctx = act; break; }
                        } catch(e){}
                    }
                    if (!ctx) {
                        try { ctx = at.getApplication(); } catch(e){}
                    }
                    if (!ctx) { resolve({ok:false, err:'no_ctx'}); return; }
                    var ctxCls = String(ctx.getClass().getName());

                    var Intent = Java.use('android.content.Intent');
                    var intent = Intent.$new();
                    intent.setClassName(String(ctx.getPackageName()),
                        'com.dragon.read.component.shortvideo.impl.ShortSeriesActivity');
                    intent.putExtra('short_series_id', String(seriesId));
                    intent.putExtra('key_click_video_pos', parseInt(pos) || 0);
                    intent.putExtra('key_player_sub_tag', 'Bash');
                    // FLAG_ACTIVITY_NEW_TASK + CLEAR_TOP 强制新栈 + 清栈顶
                    intent.addFlags(0x10000000);  // NEW_TASK
                    intent.addFlags(0x04000000);  // CLEAR_TOP

                    Java.scheduleOnMainThread(function() {
                        try {
                            ctx.startActivity(intent);
                            resolve({ok:true, ctx: ctxCls});
                        } catch(e) {
                            resolve({ok:false, err:'startActivity: '+String(e), ctx: ctxCls});
                        }
                    });
                } catch(e) {
                    resolve({ok:false, err:String(e)});
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
    args = ap.parse_args()

    logger.remove()
    logger.add(sys.stderr, level='INFO', format='{time:HH:mm:ss} | {message}')

    # 找 pid
    env = {**os.environ, "MSYS_NO_PATHCONV": "1"}
    r = subprocess.run(['adb', 'shell', 'pidof', APP_PACKAGE],
                       capture_output=True, text=True, env=env, timeout=5)
    pids = [int(x) for x in (r.stdout or '').strip().split() if x.isdigit()]
    if not pids:
        logger.error('no App pid')
        return
    pid = min(pids)
    logger.info(f'attach pid={pid}')

    device = frida.get_usb_device(timeout=5)
    session = device.attach(pid)
    script = session.create_script(JS)
    script.load()

    logger.info(f'>>> startByIntent series_id={args.series_id} pos={args.pos}')
    res = script.exports_sync.start_by_intent(args.series_id, args.pos)
    logger.info(f'<<< {res}')

    time.sleep(5)
    # 检查 Activity
    r2 = subprocess.run(['adb', 'shell', 'dumpsys activity activities | grep ResumedActivity'],
                        capture_output=True, text=True, env=env, timeout=5)
    logger.info(f'activity after: {(r2.stdout or "").strip()}')

    script.unload()
    session.detach()


if __name__ == '__main__':
    main()
