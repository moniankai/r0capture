"""红果短剧下载器 v4 — 基于 spadea→key 离线破解的终极版

核心技术突破：
  libttmplayer.so+0xac7f4 是纯函数 F：(spadea_bytes, 37, w4=0) → raw_key hex
  通过 Frida NativeFunction 主动 invoke，不再依赖 av_aes_init Hook。

流程：
  1. 启动 App + Frida attach/spawn
  2. navigate_to_drama_v2 进剧播放器
  3. 预扫描选集面板：遍历三段拿 83 集坐标 -> cells.json
  4. 采集循环：tap 格子 → setVideoModel fire → 同步 F.invoke → 立即拿 raw_key
  5. 并发下载解密 → episode_<N:03>_<kid[:8]>.mp4

用法：
  python scripts/download_v4.py -n "剧名" --total-episodes 83
  python scripts/download_v4.py -n "剧名" --total-episodes 83 --attach-running
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import subprocess
import sys
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

import frida
import requests
from loguru import logger

sys.path.insert(0, str(Path(__file__).parent.parent))
from scripts.decrypt_video import decrypt_mp4, fix_metadata
from scripts.drama_download_common import run_adb, append_jsonl, read_ui_xml_from_device
from scripts.download_drama import select_running_app_pid
from scripts.download_hongguo import verify_playable
from scripts.download_hongguo2 import navigate_to_drama_v2  # 复用 v2 的导航

APP_PACKAGE = "com.phoenix.read"
PLAYER_ACTIVITY = "ShortSeriesActivity"
F_OFFSET = 0xac7f4

HOOK_JS = r"""
Java.perform(function() {
    var F = null;  // NativeFunction，延迟初始化
    var Base64 = Java.use('android.util.Base64');
    var ArrayList = Java.use('java.util.ArrayList');

    function tryInitF() {
        if (F) return true;
        var base = Module.findBaseAddress('libttmplayer.so');
        if (!base) return false;
        F = new NativeFunction(base.add(0xac7f4), 'int', ['pointer','int','pointer','pointer','int']);
        send({t:'log', msg:'F 初始化 @ ' + base.add(0xac7f4)});
        return true;
    }
    tryInitF();
    // 若未加载，等 dlopen
    if (!F) {
        var dl = Module.findExportByName(null, 'dlopen') || Module.findExportByName(null, 'android_dlopen_ext');
        if (dl) Interceptor.attach(dl, {
            onEnter: function(a){ try { this.lib = a[0].readCString(); } catch(e){} },
            onLeave: function(){
                if (this.lib && this.lib.indexOf('libttmplayer') !== -1) setTimeout(tryInitF, 100);
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

    var TTE = Java.use('com.ss.ttvideoengine.TTVideoEngine');
    function handleModel(m) {
        if (!m) return;
        try {
            var ref = m.getVideoRef();
            if (!ref) return;
            var list = ref.getVideoInfoList();
            if (!list) return;
            var arr = Java.cast(list, ArrayList);
            var n = arr.size();
            if (n === 0) return;
            var kid = '', spadea = '';
            var streams = [];
            for (var i = 0; i < n; i++) {
                var info = arr.get(i);
                var cls = info.getClass();
                function fstr(name){ try { var f = cls.getDeclaredField(name); f.setAccessible(true); return String(f.get(info) || ''); } catch(e){ return ''; } }
                function fint(name){ try { var f = cls.getDeclaredField(name); f.setAccessible(true); var v = f.get(info); if (v===null) return 0; try { return v.intValue(); } catch(e){ var n = parseInt(String(v)); return isNaN(n)?0:n; } } catch(e){ return 0; } }
                var cKid = fstr('mKid'), cSp = fstr('mSpadea');
                if (!kid) kid = cKid;
                if (!spadea) spadea = cSp;
                streams.push({
                    main_url: fstr('mMainUrl'),
                    backup_url: fstr('mBackupUrl1'),
                    file_hash: fstr('mFileHash'),
                    bitrate: fint('mBitrate'),
                    vheight: fint('mVHeight'),
                    vwidth: fint('mVWidth'),
                });
            }
            if (!kid || !spadea) return;
            var key = spadeaToKey(spadea);
            send({t:'cap', kid:kid, spadea:spadea, key:key, streams:streams, ts:Date.now()});
        } catch(e) { send({t:'err', msg:e.toString()}); }
    }
    TTE.setVideoModel.overloads.forEach(function(ov){
        ov.implementation = function(mm) { handleModel(mm); return ov.call(this, mm); };
    });
    try {
        var aop = TTE.com_ss_ttvideoengine_TTVideoEngine_com_dragon_read_aop_TTVideoEngineAop_setVideoModel;
        aop.overloads.forEach(function(ov){
            ov.implementation = function() {
                var args = Array.prototype.slice.call(arguments);
                if (args.length >= 2) handleModel(args[1]);
                return ov.apply(this, args);
            };
        });
    } catch(e){}
    send({t:'ready', msg:'v4 hooks ready, F@' + F_addr});
});
"""


@dataclass
class Capture:
    kid: str
    spadea: str
    key: str
    streams: list = field(default_factory=list)
    captured_at: float = 0.0

    def best_stream(self, max_short_side: int = 1080) -> dict | None:
        pool = [s for s in self.streams if s.get('main_url')]
        if not pool:
            return None
        def short(s):
            return min(s['vheight'], s['vwidth']) if s.get('vwidth') else s.get('vheight', 0)
        cand = [s for s in pool if short(s) <= max_short_side]
        if cand:
            pool = cand
        return max(pool, key=lambda s: s.get('bitrate', 0))


class State:
    """采集状态。每次 cap 进来更新 last_new，采集循环 pop 拿最新的。"""

    def __init__(self, cluster: str | None = None):
        self.lock = threading.Lock()
        self.by_kid: dict[str, Capture] = {}
        self.last_new: Capture | None = None
        self.cluster: str | None = cluster
        self.rejected = 0

    def ingest(self, p: dict) -> bool:
        kid = p.get('kid', '')
        if len(kid) != 32 or not p.get('spadea'):
            return False
        cid = kid[8:12]
        if self.cluster is None:
            self.cluster = cid
            logger.info(f"[集群] 锁定 ID={cid} (首 kid={kid[:16]}...)")
        elif cid != self.cluster:
            self.rejected += 1
            if self.rejected <= 3:
                logger.debug(f"[集群] 丢异集群 {kid[:12]} ({cid} vs {self.cluster})")
            return False
        # 规整 streams 里的数值字段（Frida 过来可能是 str）
        streams_raw = p.get('streams') or []
        streams_norm = []
        for s in streams_raw:
            try:
                streams_norm.append({
                    'main_url': s.get('main_url', ''),
                    'backup_url': s.get('backup_url', ''),
                    'file_hash': s.get('file_hash', ''),
                    'bitrate': int(s.get('bitrate', 0) or 0),
                    'vheight': int(s.get('vheight', 0) or 0),
                    'vwidth': int(s.get('vwidth', 0) or 0),
                })
            except (ValueError, TypeError):
                continue
        cap = Capture(
            kid=kid,
            spadea=p['spadea'],
            key=p.get('key') or '',
            streams=streams_norm,
            captured_at=(p.get('ts') or 0) / 1000.0,
        )
        with self.lock:
            self.by_kid[kid] = cap
            self.last_new = cap
        return True

    def wait_new(self, timeout: float = 3.0) -> Capture | None:
        """等下一个 last_new（每次 cap 到来会覆盖）。返回后清空 last_new。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self.lock:
                if self.last_new is not None:
                    c = self.last_new
                    self.last_new = None
                    return c
            time.sleep(0.1)
        return None

    def snapshot(self, path: Path):
        with self.lock:
            data = {
                "cluster": self.cluster,
                "total": len(self.by_kid),
                "by_kid": {k: {
                    'kid': c.kid, 'spadea': c.spadea, 'key': c.key,
                    'streams': c.streams, 'captured_at': c.captured_at,
                } for k, c in self.by_kid.items()},
            }
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def create_on_message(state: State):
    def on_message(msg, _data):
        if msg.get('type') != 'send':
            if msg.get('type') == 'error':
                logger.warning(f"[JS ERR] {msg.get('description','')[:200]}")
            return
        p = msg['payload']
        t = p.get('t')
        if t == 'cap':
            state.ingest(p)
        elif t == 'ready':
            logger.info(f"[Hook] {p.get('msg')}")
        elif t == 'err':
            logger.warning(f"[Hook err] {p['msg']}")
    return on_message


def setup_frida(attach_running: bool, state: State):
    device = frida.get_usb_device(timeout=10)
    env = {**os.environ, "MSYS_NO_PATHCONV": "1"}
    if attach_running:
        pid = select_running_app_pid(device.enumerate_processes(), APP_PACKAGE)
        if pid is None:
            raise RuntimeError(f"{APP_PACKAGE} 未运行")
        session = device.attach(pid)
    else:
        subprocess.run(["adb", "shell", "am", "force-stop", APP_PACKAGE],
                       capture_output=True, check=False, env=env)
        time.sleep(1)
        pid = device.spawn([APP_PACKAGE])
        session = device.attach(pid)
    script = session.create_script(HOOK_JS)
    script.on("message", create_on_message(state))
    script.load()
    if not attach_running:
        device.resume(pid)
        logger.info(f"[Frida] spawned pid={pid}")
        time.sleep(10)
    else:
        logger.info(f"[Frida] attached pid={pid}")
        time.sleep(3)
    return session, script, pid


def _parse_bounds(s: str):
    m = re.match(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]', s or '')
    return tuple(int(x) for x in m.groups()) if m else None


def scan_panel(total_eps: int) -> tuple[dict[int, tuple[int,int]], dict[str, tuple[int,int]]]:
    """预扫描选集面板，返回 (ep_num → 格子中心坐标, 段按钮坐标)。"""
    # 定义三段
    if total_eps <= 30:
        segments = [(f'1-{total_eps}', 1, total_eps)]
    elif total_eps <= 60:
        segments = [('1-30', 1, 30), (f'31-{total_eps}', 31, total_eps)]
    else:
        segments = [('1-30', 1, 30), ('31-60', 31, 60), (f'61-{total_eps}', 61, total_eps)]

    logger.info("[扫描] tap 选集按钮...")
    run_adb(["shell", "input", "tap", "540", "1820"])
    time.sleep(2.0)

    seg_btn: dict[str, tuple[int,int]] = {}
    xml = read_ui_xml_from_device()
    if xml:
        root = ET.fromstring(xml)
        for n in root.iter('node'):
            t = n.get('text', '').strip()
            if t in (s[0] for s in segments):
                b = _parse_bounds(n.get('bounds', ''))
                if b:
                    seg_btn[t] = ((b[0]+b[2])//2, (b[1]+b[3])//2)
    logger.info(f"[扫描] 段按钮: {seg_btn}")

    cells: dict[int, tuple[int, int]] = {}
    for (label, lo, hi) in segments:
        if label in seg_btn:
            run_adb(["shell", "input", "tap", str(seg_btn[label][0]), str(seg_btn[label][1])])
            time.sleep(1.0)
        xml = read_ui_xml_from_device()
        if not xml:
            logger.warning(f"[扫描] {label} dump 失败")
            continue
        try:
            root = ET.fromstring(xml)
        except ET.ParseError:
            continue
        seg_hits = 0
        for n in root.iter('node'):
            txt = (n.get('text') or '').strip()
            if not txt.isdigit():
                continue
            ep = int(txt)
            if lo <= ep <= hi:
                b = _parse_bounds(n.get('bounds', ''))
                if b and ep not in cells:
                    cells[ep] = ((b[0]+b[2])//2, (b[1]+b[3])//2)
                    seg_hits += 1
        logger.info(f"[扫描] 段 {label}: 拿到 {seg_hits} 个格子")

    # 关面板
    run_adb(["shell", "input", "keyevent", "KEYCODE_BACK"])
    time.sleep(1.0)
    return cells, seg_btn


def ep_to_segment(ep: int, total: int) -> str:
    if total <= 30: return f'1-{total}'
    if total <= 60:
        return '1-30' if ep <= 30 else f'31-{total}'
    if ep <= 30: return '1-30'
    if ep <= 60: return '31-60'
    return f'61-{total}'


def collect_episode(state: State, ep: int, total: int, cells: dict,
                    seg_btn: dict, cur_seg: list) -> Capture | None:
    """采集单集：打开面板 → 切段（若需要）→ tap 格子 → 等 Hook cap → 返回。"""
    if ep not in cells:
        logger.error(f"[第{ep}集] cells.json 里无坐标")
        return None

    # 唤起面板
    run_adb(["shell", "input", "tap", "540", "1820"])
    time.sleep(1.2)

    # 段切换
    target_seg = ep_to_segment(ep, total)
    if cur_seg[0] != target_seg and target_seg in seg_btn:
        logger.debug(f"[段切换] {cur_seg[0]} → {target_seg}")
        run_adb(["shell", "input", "tap", str(seg_btn[target_seg][0]), str(seg_btn[target_seg][1])])
        time.sleep(0.8)
        cur_seg[0] = target_seg

    # 清掉 state.last_new（丢弃可能的残留信号）
    with state.lock:
        state.last_new = None

    # tap 格子
    x, y = cells[ep]
    run_adb(["shell", "input", "tap", str(x), str(y)])

    # 等 setVideoModel fire
    cap = state.wait_new(timeout=4.0)
    return cap


def download_and_decrypt(cap: Capture, output_path: str, max_short: int) -> bool:
    stream = cap.best_stream(max_short)
    if not stream:
        logger.error(f"[下载] 无可用 stream")
        return False
    if not cap.key or len(cap.key) != 32:
        logger.error(f"[下载] key 缺失或格式错: {cap.key!r}")
        return False
    urls = [stream['main_url']] + ([stream['backup_url']] if stream.get('backup_url') else [])
    tmp = output_path + ".tmp"
    last_err = None
    for url in urls:
        try:
            resp = requests.get(url, headers={"User-Agent": "AVDML_2.1.230.181-novel_ANDROID"}, timeout=120)
            resp.raise_for_status()
            data = bytearray(resp.content)
            size_mb = len(data) / 1024 / 1024
            n = decrypt_mp4(data, bytes.fromhex(cap.key))
            fix_metadata(data)
            logger.info(f"[下载] {size_mb:.1f}MB h={stream['vheight']} bt={stream['bitrate']} samples={n}")
            os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)
            with open(tmp, 'wb') as f:
                f.write(data)
            os.replace(tmp, output_path)
            return True
        except Exception as e:
            last_err = e
            logger.warning(f"[下载] {url[:60]}... 失败: {e}")
            if os.path.exists(tmp):
                os.remove(tmp)
    logger.error(f"[下载] 全部 URL 失败: {last_err}")
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", "--name", required=True)
    ap.add_argument("--total-episodes", type=int, required=True)
    ap.add_argument("-e", "--start-episode", type=int, default=1)
    ap.add_argument("--output", default="./videos")
    ap.add_argument("--attach-running", action="store_true")
    ap.add_argument("--max-height", type=int, default=1080)
    ap.add_argument("--cluster", type=str, default=None)
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--skip-scan", action="store_true", help="跳过预扫描面板（复用已有 cells.json）")
    ap.add_argument("--max-eps", type=int, default=0, help="最多采集 N 集就停（小规模测试用，0=无限制）")
    args = ap.parse_args()

    output_dir = os.path.join(args.output, args.name)
    os.makedirs(output_dir, exist_ok=True)
    log_file = os.path.join(output_dir, "download_v4.log")
    logger.add(log_file, rotation="10 MB", encoding="utf-8",
               format="{time:YYYY-MM-DD HH:mm:ss} | {level:<8} | {message}", level="INFO")
    logger.info(f"v4 目标: 《{args.name}》 ep{args.start_episode}..{args.total_episodes}")

    state = State(cluster=args.cluster)
    session, script, pid = setup_frida(args.attach_running, state)

    try:
        if not args.attach_running:
            logger.info("[导航] navigate_to_drama_v2...")
            if not navigate_to_drama_v2(args.name):
                logger.error("[导航] 失败")
                return
            time.sleep(3)

        # 预扫描面板
        cells_path = Path(output_dir) / 'cells.json'
        if args.skip_scan and cells_path.exists():
            d = json.loads(cells_path.read_text(encoding='utf-8'))
            cells = {int(k): tuple(v) for k, v in d['cells'].items()}
            seg_btn = {k: tuple(v) for k, v in d['seg_btn'].items()}
            logger.info(f"[扫描] 复用 cells.json: {len(cells)} 集")
        else:
            cells, seg_btn = scan_panel(args.total_episodes)
            cells_path.write_text(json.dumps({
                'cells': {str(k): list(v) for k, v in cells.items()},
                'seg_btn': {k: list(v) for k, v in seg_btn.items()},
            }, ensure_ascii=False, indent=2), encoding='utf-8')
            logger.info(f"[扫描] 拿到 {len(cells)} 集坐标，保存 {cells_path}")

        missing = [i for i in range(args.start_episode, args.total_episodes + 1) if i not in cells]
        if missing:
            logger.warning(f"[扫描] 缺 {len(missing)} 集坐标: {missing[:5]}...")

        # 采集阶段
        plan: dict[int, Capture] = {}
        failed: list[int] = []
        cur_seg = [None]  # mutable holder
        manifest = os.path.join(output_dir, 'session_manifest_v4.jsonl')

        logger.info(f"[采集] 开始遍历 ep{args.start_episode}..{args.total_episodes}")
        for ep in range(args.start_episode, args.total_episodes + 1):
            # 断点续传：已下载就跳过采集
            existing = glob.glob(os.path.join(output_dir, f'episode_{ep:03d}_*.mp4'))
            if existing and os.path.getsize(existing[0]) > 100 * 1024:
                logger.info(f"[第{ep}集] 已存在 {os.path.basename(existing[0])}，跳过")
                continue
            if ep not in cells:
                failed.append(ep)
                continue
            cap = collect_episode(state, ep, args.total_episodes, cells, seg_btn, cur_seg)
            if cap is None or not cap.key:
                logger.warning(f"[第{ep}集] 采集失败 cap={'None' if cap is None else 'no_key'}")
                failed.append(ep)
                continue
            plan[ep] = cap
            logger.info(f"[第{ep}集] kid={cap.kid[:8]} key={cap.key[:8]}... streams={len(cap.streams)}")
            if args.max_eps and len(plan) >= args.max_eps:
                logger.info(f"[采集] 已达 --max-eps={args.max_eps}，提前结束")
                break

        # 关面板
        run_adb(["shell", "input", "keyevent", "KEYCODE_BACK"])
        state.snapshot(Path(output_dir) / 'capture_snapshot.json')

        # 下载阶段（并发）
        logger.info(f"[下载] 开始并发 {args.concurrency}，待下载 {len(plan)} 集")
        success = 0
        def _dl(ep: int, cap: Capture):
            out = os.path.join(output_dir, f'episode_{ep:03d}_{cap.kid[:8]}.mp4')
            ok = download_and_decrypt(cap, out, args.max_height)
            if ok and verify_playable(out):
                stream = cap.best_stream(args.max_height)
                append_jsonl(manifest, {
                    'episode': ep, 'kid': cap.kid, 'key': cap.key,
                    'path': out, 'vheight': stream['vheight'] if stream else 0,
                    'bitrate': stream['bitrate'] if stream else 0,
                    'timestamp': time.time(), 'status': 'ok',
                })
                return ep, True
            else:
                if os.path.exists(out):
                    os.remove(out)
                return ep, False

        with ThreadPoolExecutor(max_workers=args.concurrency) as ex:
            futures = [ex.submit(_dl, ep, cap) for ep, cap in plan.items()]
            for fut in as_completed(futures):
                ep, ok = fut.result()
                if ok:
                    success += 1
                else:
                    failed.append(ep)

        logger.info(f"\n=== v4 完成: 成功 {success}/{len(plan)}, 失败 {len(failed)} ===")
        if failed:
            logger.warning(f"失败集: {sorted(set(failed))}")
    finally:
        try:
            script.unload()
            session.detach()
        except Exception:
            pass


if __name__ == "__main__":
    main()
