"""Session A: spawn App + Intent-based startActivity 进目标剧, detach exit.

设计目的: 绕开 v5 spawn 模式下 20+ 高频 Java hook 卡 Frida RPC 的问题.
本脚本只挂 1 个 RPC export, 无其他 hook, 保证 Application.startActivity 调用
不被高频 hook 阻塞.

用法:
    python scripts/spawn_nav.py --series-id 7622955207885851672 --pos 0
    # 成功后 App 已在目标剧 ShortSeriesActivity, 接着:
    python scripts/hongguo_v5.py --attach --series-id 7622955207885851672 \\
        -n "剧名" -t 83 -e 3

退出码:
    0 = 成功进入 ShortSeriesActivity
    1 = spawn/attach 失败
    2 = RPC startActivity 异常
    3 = startActivity 后未进 ShortSeriesActivity (超时)
"""
import sys, os, time, argparse, subprocess
import frida
from loguru import logger

APP_PACKAGE = "com.phoenix.read"
ACTIVITY = "com.dragon.read.component.shortvideo.impl.ShortSeriesActivity"

# 极简 JS: 仅 RPC export, 零 hook
JS = r"""
rpc.exports = {
    startByIntent: function(seriesId, pos, firstVid) {
        return new Promise(function(resolve) {
            Java.perform(function() {
                try {
                    var app = Java.use('android.app.ActivityThread')
                                  .currentActivityThread().getApplication();
                    if (!app) { resolve({ok:false, err:'no_app'}); return; }
                    var appCls = String(app.getClass().getName());

                    var Intent = Java.use('android.content.Intent');
                    var intent = Intent.$new();
                    intent.setClassName(String(app.getPackageName()),
                        'com.dragon.read.component.shortvideo.impl.ShortSeriesActivity');
                    intent.putExtra('short_series_id', String(seriesId));
                    intent.putExtra('key_click_video_pos', parseInt(pos) || 0);
                    if (firstVid) {
                        intent.putExtra('key_first_vid', String(firstVid));
                        intent.putExtra('key_highlight_vid', String(firstVid));
                    }
                    intent.putExtra('key_player_sub_tag', 'spawnNav');
                    intent.addFlags(0x10000000);  // FLAG_ACTIVITY_NEW_TASK
                    intent.addFlags(0x04000000);  // FLAG_ACTIVITY_CLEAR_TOP

                    app.startActivity(intent);
                    resolve({ok: true, ctx: appCls});
                } catch(e) {
                    resolve({ok: false, err: String(e)});
                }
            });
        });
    }
};
"""


def _adb(cmd: list[str], timeout: float = 5.0) -> str:
    env = {**os.environ, "MSYS_NO_PATHCONV": "1"}
    r = subprocess.run(
        ['adb'] + cmd, capture_output=True, text=True, env=env, timeout=timeout,
    )
    return r.stdout or ''


def _current_activity() -> str:
    try:
        out = _adb(['shell', 'dumpsys activity activities'], timeout=8)
    except subprocess.TimeoutExpired:
        return ''
    for line in out.splitlines():
        if 'ResumedActivity' in line and 'ActivityRecord' in line:
            # 形如: mResumedActivity: ActivityRecord{... com.phoenix.read/.XxxActivity t87}
            for tok in line.split():
                if '/' in tok and '.' in tok:
                    return tok.rstrip('}')
    return ''


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--series-id', required=True, help='目标剧 series_id')
    ap.add_argument('--pos', type=int, default=0, help='集索引 (0-based)')
    ap.add_argument('--first-vid', type=str, default='', help='可选: 首集 vid')
    ap.add_argument('--splash-wait', type=float, default=20.0,
                    help='spawn 后等 App 进 Main 的最大秒数')
    ap.add_argument('--post-wait', type=float, default=6.0,
                    help='startActivity 后等进 ShortSeriesActivity 的秒数')
    args = ap.parse_args()

    logger.remove()
    logger.add(sys.stderr, level='INFO',
               format='<green>{time:HH:mm:ss}</green> | {message}')

    # 先 force-stop 保证 fresh spawn
    logger.info('force-stop App...')
    _adb(['shell', 'am', 'force-stop', APP_PACKAGE])
    time.sleep(2)

    try:
        device = frida.get_usb_device(timeout=5)
        pid = device.spawn([APP_PACKAGE])
        logger.info(f'spawn pid={pid}')
        session = device.attach(pid)
        script = session.create_script(JS)
        script.load()
        device.resume(pid)
    except Exception as e:
        logger.error(f'frida spawn/attach 失败: {e}')
        return 1

    # 等 App 进 MainFragmentActivity (splash → Main)
    logger.info(f'等 App 进 Main... (max {args.splash_wait}s)')
    deadline = time.time() + args.splash_wait
    ready = False
    while time.time() < deadline:
        act = _current_activity()
        if 'MainFragmentActivity' in act or 'ShortSeriesActivity' in act:
            logger.info(f'App ready: {act}')
            ready = True
            break
        time.sleep(1)
    if not ready:
        logger.warning('等 Main 超时, 仍尝试 RPC')

    # Main thread 给点喘息, 避免 Fragment 初始化竞争
    time.sleep(2)

    logger.info(f'>>> startByIntent series_id={args.series_id} pos={args.pos}')
    try:
        r = script.exports_sync.start_by_intent(
            args.series_id, args.pos, args.first_vid or None,
        )
    except Exception as e:
        logger.error(f'RPC 异常: {e}')
        script.unload()
        session.detach()
        return 2
    logger.info(f'<<< {r}')
    if not r.get('ok'):
        logger.error(f'startActivity 失败: {r.get("err")}')
        script.unload()
        session.detach()
        return 2

    # 等 App 真进 ShortSeriesActivity
    deadline = time.time() + args.post_wait
    final_act = ''
    while time.time() < deadline:
        final_act = _current_activity()
        if 'ShortSeriesActivity' in final_act:
            break
        time.sleep(0.5)
    logger.info(f'final activity: {final_act}')

    # detach 让 v5 后续 attach 接管 (Activity 继续存在)
    try:
        script.unload()
        session.detach()
    except Exception as e:
        logger.warning(f'detach 异常 (可忽略): {e}')

    if 'ShortSeriesActivity' not in final_act:
        logger.error('startActivity 调用后 6s 内未进 ShortSeriesActivity')
        return 3
    logger.info('session A 成功, App 已在目标剧 ShortSeriesActivity')
    return 0


if __name__ == '__main__':
    sys.exit(main())
