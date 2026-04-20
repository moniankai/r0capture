"""v5-lean: 精简 hook 版本 — 只挂 ot3.z.B0 + dlopen(init F), 绕过 Frida RPC 阻塞.

架构:
  1. 外部先跑 spawn_nav.py 让 App 进目标剧 ShortSeriesActivity
  2. 本脚本 attach + 挂极简 hook + swipe 驱动 + 下载

优势:
  - 单 Frida session, hook 负担小 → Java bridge 不阻塞
  - B0 同时提供 idx + biz_vid + tt_vid + kid + spadea → 单事件足够下载
  - swipe 100% 触发 B0 (已验证) → 可靠推进

用法:
    python scripts/spawn_nav.py --series-id 7622955207885851672 --pos 0
    python scripts/v5_lean.py -n "剧名" --series-id 7622955207885851672 -t 83 -s 1 -e 3
"""
import sys, os, time, json, argparse, subprocess, threading
from pathlib import Path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import frida
from loguru import logger

# 复用 v5 的下载/解密/manifest 工具
from hongguo_v5 import (
    Capture, download_and_decrypt, append_manifest,
    read_committed_eps, DEFAULT_OUT_DIR,
)

APP_PACKAGE = "com.phoenix.read"


LEAN_JS = r"""
'use strict';

var F = null;

Java.perform(function() {
    var Base64 = Java.use('android.util.Base64');
    var ArrayList = Java.use('java.util.ArrayList');

    function tryInitF() {
        if (F) return true;
        var base = Module.findBaseAddress('libttmplayer.so');
        if (!base) return false;
        F = new NativeFunction(base.add(0xac7f4), 'int',
                               ['pointer','int','pointer','pointer','int']);
        send({t:'log', msg:'F init @ ' + base.add(0xac7f4)});
        return true;
    }
    tryInitF();
    if (!F) {
        var dl = Module.findExportByName(null, 'dlopen') ||
                 Module.findExportByName(null, 'android_dlopen_ext');
        if (dl) Interceptor.attach(dl, {
            onEnter: function(a) { try { this.lib = a[0].readCString(); } catch(e){} },
            onLeave: function() {
                if (this.lib && this.lib.indexOf('libttmplayer') !== -1) {
                    setTimeout(tryInitF, 100);
                }
            }
        });
    }

    function spadeaToKey(spadea_b64) {
        if (!F && !tryInitF()) return null;
        try {
            var bytes = Base64.decode(spadea_b64, 0);
            var len = bytes.length;
            if (len < 20 || len > 128) return null;
            var mem = Memory.alloc(len);
            for (var i = 0; i < len; i++) mem.add(i).writeU8(bytes[i] & 0xff);
            var outPP = Memory.alloc(8); outPP.writePointer(ptr(0));
            var auxPP = Memory.alloc(8); auxPP.writePointer(ptr(0));
            var ret = F(mem, len, outPP, auxPP, 0);
            if (ret !== 8) return null;
            var p = outPP.readPointer();
            if (p.isNull()) return null;
            return p.readCString();
        } catch(e) { return null; }
    }

    function _longToNum(v) {
        if (v === null || v === undefined) return -1;
        try { if (v.longValue) return v.longValue(); } catch(e){}
        try { return Number(String(v)); } catch(e){}
        return -1;
    }

    function _hookB0() {
        try {
            var Z = Java.use('ot3.z');
            Z.B0.overloads.forEach(function(ov) {
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
                        if (svd && vm) {
                            var idx = _longToNum(svd.getVidIndex());
                            var biz_vid = String(svd.getVid() || '');
                            var sid = String(svd.getSeriesId() || '');
                            var tt_vid = '', kid = '', spadea = '';
                            var streams = [];
                            try {
                                var ref = vm.getVideoRef();
                                if (ref) {
                                    try {
                                        var f0 = ref.getClass().getDeclaredField('mVideoId');
                                        f0.setAccessible(true);
                                        tt_vid = String(f0.get(ref) || '');
                                    } catch(e){}
                                    try {
                                        var vl = ref.getVideoInfoList();
                                        if (vl) {
                                            var arr = Java.cast(vl, ArrayList);
                                            var n = arr.size();
                                            for (var j = 0; j < n; j++) {
                                                var info = arr.get(j);
                                                var ic = info.getClass();
                                                var fstr = function(name) {
                                                    try { var f = ic.getDeclaredField(name);
                                                          f.setAccessible(true);
                                                          return String(f.get(info) || ''); }
                                                    catch(e) { return ''; }
                                                };
                                                var fint = function(name) {
                                                    try { var f = ic.getDeclaredField(name);
                                                          f.setAccessible(true);
                                                          var v = f.get(info);
                                                          if (v === null) return 0;
                                                          try { return v.intValue(); }
                                                          catch(e) { return parseInt(String(v)) || 0; }
                                                    } catch(e) { return 0; }
                                                };
                                                var ck = fstr('mKid'), cs = fstr('mSpadea');
                                                if (!kid && ck) kid = ck;
                                                if (!spadea && cs) spadea = cs;
                                                streams.push({
                                                    main_url: fstr('mMainUrl'),
                                                    backup_url: fstr('mBackupUrl1'),
                                                    file_hash: fstr('mFileHash'),
                                                    bitrate: fint('mBitrate'),
                                                    vheight: fint('mVHeight'),
                                                    vwidth: fint('mVWidth'),
                                                });
                                            }
                                        }
                                    } catch(e) { send({t:'stream_err', err: String(e)}); }
                                }
                            } catch(e){}
                            var key = (kid && spadea) ? spadeaToKey(spadea) : '';
                            send({t: 'b0', idx: idx, biz_vid: biz_vid, sid: sid,
                                  tt_vid: tt_vid, kid: kid, spadea: spadea,
                                  key: key || '', streams: streams, ts: Date.now()});
                        }
                    } catch(e) {
                        send({t: 'b0_err', err: String(e)});
                    }
                    return ov.apply(this, args);
                };
            });
            send({t: 'b0_hooked', overloads: Z.B0.overloads.length});
            return true;
        } catch(e) {
            send({t: 'b0_defer', err: String(e)});
            return false;
        }
    }
    _hookB0() || setTimeout(_hookB0, 1500);

    rpc.exports = {
        retryHookB0: function() { return _hookB0(); }
    };
});
"""


class B0Event:
    """B0 事件的 Python 数据容器."""
    __slots__ = ('idx', 'biz_vid', 'sid', 'tt_vid', 'kid', 'spadea', 'key', 'streams', 'ts')

    def __init__(self, p: dict):
        self.idx = int(p.get('idx', -1))
        self.biz_vid = p.get('biz_vid', '')
        self.sid = p.get('sid', '')
        self.tt_vid = p.get('tt_vid', '')
        self.kid = p.get('kid', '')
        self.spadea = p.get('spadea', '')
        self.key = p.get('key', '')
        self.streams = p.get('streams', [])
        self.ts = float(p.get('ts', 0)) / 1000.0 if p.get('ts', 0) > 1e12 else float(p.get('ts', 0))


class LeanState:
    """收集 B0 事件并按 ep 索引."""
    def __init__(self):
        self.lock = threading.Lock()
        self.by_ep: dict[int, B0Event] = {}
        self.all: list[B0Event] = []
        self.latest_idx = -1
        # 目标 series_id, 下载前强校验防串剧 (空串表示不校验, 用于 debug)
        self.target_sid: str = ''
        self.rejected_sids: set[str] = set()  # 被拒绝的 sid, 汇报用

    def ingest(self, p: dict):
        e = B0Event(p)
        if e.idx < 1:
            return
        with self.lock:
            # 串剧保护: 非目标 sid 不入 by_ep 但仍入 all (便于调试 dump)
            if self.target_sid and e.sid and e.sid != self.target_sid:
                self.rejected_sids.add(e.sid)
                self.all.append(e)
                return
            self.by_ep[e.idx] = e
            self.all.append(e)
            self.latest_idx = e.idx

    def wait_ep(self, target_ep: int, timeout: float = 10.0) -> B0Event | None:
        """等 target_ep 对应的 B0 事件 (带 key)."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self.lock:
                e = self.by_ep.get(target_ep)
                if e and e.key and e.streams:
                    return e
            time.sleep(0.2)
        return None


def adb_shell(cmd: str, timeout: float = 5.0) -> None:
    env = {**os.environ, "MSYS_NO_PATHCONV": "1"}
    subprocess.run(['adb', 'shell', cmd], capture_output=True, env=env, timeout=timeout)


def swipe_next() -> None:
    """下一集 (上滑). 距离缩短 + duration 加长避免 ViewPager fling 跨多集."""
    adb_shell('input swipe 540 1200 540 700 700')


def swipe_prev() -> None:
    """上一集 (下滑). 同上."""
    adb_shell('input swipe 540 700 540 1200 700')


def attach_lean(state: LeanState) -> tuple:
    env = {**os.environ, "MSYS_NO_PATHCONV": "1"}
    r = subprocess.run(['adb', 'shell', 'pidof', APP_PACKAGE],
                       capture_output=True, text=True, env=env, timeout=5)
    pids = [int(x) for x in (r.stdout or '').strip().split() if x.isdigit()]
    if not pids:
        raise RuntimeError('App 未运行, 先跑 spawn_nav.py')
    pid = min(pids)
    logger.info(f'attach pid={pid}')
    device = frida.get_usb_device(timeout=5)
    session = device.attach(pid)
    script = session.create_script(LEAN_JS)

    def on_msg(msg, data):
        if msg.get('type') == 'send':
            p = msg['payload']
            t = p.get('t')
            if t == 'b0':
                state.ingest(p)
                logger.info(f'[B0] ep={p.get("idx")} biz_vid={p.get("biz_vid","")[:16]}... '
                            f'kid={p.get("kid","")[:16]}... key_len={len(p.get("key") or "")}')
            elif t == 'b0_hooked':
                logger.success(f'B0 hook 就位 (overloads={p.get("overloads")})')
            elif t == 'b0_defer':
                logger.warning(f'B0 hook 延后: {p.get("err")}')
            elif t == 'b0_err':
                logger.warning(f'B0 read err: {p.get("err")}')
            elif t == 'log':
                logger.info(f'[JS] {p.get("msg")}')
            elif t == 'stream_err':
                logger.warning(f'stream_err: {p.get("err")}')
        elif msg.get('type') == 'error':
            logger.error(f'JS err: {msg.get("description")}')

    script.on('message', on_msg)
    script.load()
    return session, script


def make_capture(e: B0Event) -> Capture:
    """B0 事件 → v5 的 Capture 对象, 复用 v5 download_and_decrypt."""
    return Capture(
        kid=e.kid,
        spadea=e.spadea,
        key=e.key,
        vid=e.tt_vid,
        streams=e.streams,
        ts=e.ts,
        switch_seq=0,
    )


def try_download_current(state: LeanState, ep: int,
                          out_dir: Path, drama: str, max_short: int) -> bool:
    """当前 B0 idx == ep 时下载 + manifest. 返回是否成功."""
    e = state.wait_ep(ep, timeout=6.0)
    if not e:
        return False
    # 串剧校验: 下载前 sid 必须等于目标 (防御 App 意外切剧, 推荐侧栏 ingest 等)
    if state.target_sid and e.sid != state.target_sid:
        logger.warning(f'ep{ep}: sid 不符 ({e.sid} != target {state.target_sid}), 拒绝下载')
        return False
    if not e.key or len(e.key) != 32 or not e.streams:
        logger.warning(f'ep{ep}: B0 缺 key/streams '
                       f'(key_len={len(e.key or "")}, streams={len(e.streams)})')
        return False
    cap = make_capture(e)
    final_path = download_and_decrypt(cap, ep, out_dir, drama, max_short_side=max_short)
    if not final_path:
        return False
    rec = {
        'ep': ep, 'vid': e.tt_vid, 'biz_vid': e.biz_vid,
        'kid': e.kid, 'series_id': e.sid,
        'kid_prefix8': e.kid[:8],
        'file': final_path.name,
        'ts': time.time(),
    }
    append_manifest(out_dir, rec)
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('-n', '--name', required=True)
    ap.add_argument('--series-id', required=True)
    ap.add_argument('-t', '--total', type=int, required=True)
    ap.add_argument('-s', '--start', type=int, default=1)
    ap.add_argument('-e', '--end', type=int, default=0,
                    help='0=total')
    ap.add_argument('--out', type=Path, default=DEFAULT_OUT_DIR)
    ap.add_argument('--max-short', type=int, default=1080)
    args = ap.parse_args()

    logger.remove()
    logger.add(sys.stderr, level='INFO',
               format='<green>{time:HH:mm:ss}</green> | <level>{level:<8}</level> | {message}')

    end = args.end if args.end > 0 else args.total
    out_dir = args.out / args.name
    out_dir.mkdir(parents=True, exist_ok=True)

    committed = read_committed_eps(out_dir)
    logger.info(f'[lean] 剧=《{args.name}》 series_id={args.series_id} '
                f'range={args.start}..{end} 已 committed={len(committed)}')

    state = LeanState()
    state.target_sid = args.series_id  # 串剧保护: 非目标 sid 的 B0 事件不入 by_ep
    session, script = attach_lean(state)

    try:
        # 等初始 B0 (App 恢复进度). 同时允许"被拒绝"的 sid 事件作为 spawn_nav
        # 失败的信号 — App 不在目标剧里.
        logger.info(f'等初始 B0 target_sid={args.series_id}...')
        deadline = time.time() + 15
        while time.time() < deadline and state.latest_idx < 1:
            time.sleep(0.5)
        if state.latest_idx < 1:
            # 检查是否所有 B0 都被拒绝 (App 在别的剧)
            if state.rejected_sids:
                logger.error(f'初始 B0 全被拒绝, 当前 App 在非目标剧: {state.rejected_sids}')
                logger.error('spawn_nav.py 失败? 请重跑 spawn_nav 或检查 App 状态')
                return 3
            logger.warning('初始 B0 未到, swipe 触发')
            swipe_next()
            time.sleep(4)
            # swipe 后再检查一次
            if state.latest_idx < 1 and state.rejected_sids:
                logger.error(f'swipe 后仍全被拒绝: {state.rejected_sids}')
                return 3

        logger.info(f'初始 ep = {state.latest_idx} (rejected_sids={state.rejected_sids or "none"})')

        # 扫描式下载: swipe 过冲严重, 所以不强制精确 target,
        # 每次 swipe 后看当前 ep 是否 pending, 是就下载.
        # 双向扫描保证每集都扫过, 过冲只是跳过某集由下轮补.
        targets = set(range(args.start, end + 1))
        downloaded = set(k for k in committed.keys() if args.start <= k <= end)
        pending = targets - downloaded
        logger.info(f'targets={len(targets)} already={len(downloaded)} '
                    f'pending={len(pending)}')

        ok = 0
        direction = 'next'
        max_iters = len(pending) * 6 + 20
        iters = 0
        no_progress = 0
        last_pending_size = len(pending)

        while pending and iters < max_iters:
            iters += 1
            current = state.latest_idx

            if current in pending:
                logger.info(f'=== 命中: ep{current} 待下 (pending={len(pending)}) ===')
                if try_download_current(state, current, out_dir, args.name, args.max_short):
                    downloaded.add(current)
                    pending.discard(current)
                    ok += 1
                    logger.success(f'ep{current} 成功, done={len(downloaded)}/{len(targets)}')
                else:
                    logger.warning(f'ep{current} 下载失败')

            if not pending:
                break

            # 确定 swipe 方向: 根据当前位置与 pending 边界关系
            lo, hi = min(pending), max(pending)
            if direction == 'next':
                if current >= hi:
                    direction = 'prev'
                    swipe_prev()
                    logger.info(f'[scan] cur={current} 至顶 hi={hi}, 转向 prev')
                else:
                    swipe_next()
                    logger.info(f'[scan] cur={current} swipe next (pending lo={lo} hi={hi})')
            else:
                if current <= lo:
                    direction = 'next'
                    swipe_next()
                    logger.info(f'[scan] cur={current} 至底 lo={lo}, 转向 next')
                else:
                    swipe_prev()
                    logger.info(f'[scan] cur={current} swipe prev (pending lo={lo} hi={hi})')
            time.sleep(3)

            # 进度停滞检测
            if len(pending) == last_pending_size:
                no_progress += 1
            else:
                no_progress = 0
                last_pending_size = len(pending)
            if no_progress > len(targets) * 2 + 20:
                logger.error(f'进度停滞 {no_progress} 轮, 退出')
                break

        logger.info(f'=== 完成 ok={ok} pending={len(pending)} ===')
        if pending:
            logger.warning(f'未下集: {sorted(pending)[:20]}...')

    finally:
        try: script.unload()
        except Exception: pass
        try: session.detach()
        except Exception: pass


if __name__ == '__main__':
    main()
