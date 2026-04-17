"""红果短剧全集下载器 v2 — 基于 kid_map 的 swipe-driven 版本

相比 download_hongguo.py 的主要差异：
  1. 抛弃 UI 选集面板（ivi 按钮、滚动面板），改用上滑切集
  2. Hook 数据源：VideoRef.toBashString() 拿 kid/url/file_hash，av_aes_init 拿 key
  3. 不依赖 tt_vid，用 kid 做主键配对
  4. 初始导航仍用搜索入口（UI 只用这一次）

用法：
  python scripts/download_hongguo2.py -n "剧名" --total-episodes 83
  python scripts/download_hongguo2.py -n "剧名" --attach-running --total-episodes 83 -e 5

前置假设：
  --attach-running 时，App 已经在目标剧的第 e 集（start-episode）播放页。
  默认模式会自动搜索并导航到第 1 集。
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import frida
import requests
from loguru import logger

sys.path.insert(0, str(Path(__file__).parent.parent))
from scripts.decrypt_video import decrypt_mp4, fix_metadata
from scripts.drama_download_common import run_adb, append_jsonl
from scripts.download_drama import select_running_app_pid
from scripts.download_hongguo import navigate_to_drama_via_search, verify_playable

APP_PACKAGE = "com.phoenix.read"

HOOK_JS = r"""
Java.perform(function() {
    // === Hook VideoRef via setVideoModel ===
    try {
        var TTE = Java.use('com.ss.ttvideoengine.TTVideoEngine');
        function dumpRef(m) {
            if (!m) return;
            try {
                var ref = m.getVideoRef();
                if (!ref) return;
                var json = String(ref.toBashString() || '');
                if (!json) return;
                var CHUNK = 50000;
                var id = Math.floor(Math.random()*1e9);
                var parts = Math.ceil(json.length/CHUNK);
                for (var k=0;k<parts;k++)
                    send({t:'ref', id:id, idx:k, total:parts, body:json.substring(k*CHUNK,(k+1)*CHUNK)});
            } catch (e) { send({t:'ref_err', e:e.toString()}); }
        }
        TTE.setVideoModel.overloads.forEach(function(ov) {
            ov.implementation = function(m) { dumpRef(m); return ov.call(this, m); };
        });
        try {
            var aop = TTE.com_ss_ttvideoengine_TTVideoEngine_com_dragon_read_aop_TTVideoEngineAop_setVideoModel;
            aop.overloads.forEach(function(ov) {
                ov.implementation = function() {
                    var args = Array.prototype.slice.call(arguments);
                    if (args.length>=2) dumpRef(args[1]);
                    return ov.apply(this, args);
                };
            });
        } catch (e) {}
        send({t:'ref_ready'});
    } catch (e) { send({t:'ref_init_err', e:e.toString()}); }
});

// === av_aes_init native Hook ===
function hookAes() {
    var fn = Module.findExportByName('libttffmpeg.so', 'av_aes_init');
    if (!fn) { send({t:'aes_err', err:'no av_aes_init'}); return; }
    Interceptor.attach(fn, {
        onEnter: function(args) {
            this.keyPtr = args[1];
            try { this.keyBits = args[2].toInt32(); } catch (e) { this.keyBits = 0; }
        },
        onLeave: function() {
            try {
                var len = this.keyBits >>> 3;
                if (len <= 0 || len > 32) return;
                var bytes = new Uint8Array(this.keyPtr.readByteArray(len));
                var hex = '';
                for (var i=0;i<bytes.length;i++) {
                    var h = bytes[i].toString(16); if (h.length<2) h='0'+h; hex+=h;
                }
                send({t:'aes_key', hex:hex, ts:Date.now()});
            } catch (e) { send({t:'aes_err', err:e.toString()}); }
        }
    });
    send({t:'aes_hooked'});
}
if (Module.findBaseAddress('libttffmpeg.so')) hookAes();
else {
    var dl = Module.findExportByName(null, 'dlopen') || Module.findExportByName(null, 'android_dlopen_ext');
    if (dl) Interceptor.attach(dl, {
        onEnter: function(args) { try { this.lib = args[0].readCString(); } catch (e) {} },
        onLeave: function() { if (this.lib && this.lib.indexOf('libttffmpeg') !== -1) setTimeout(hookAes, 50); }
    });
}
"""


@dataclass
class Stream:
    main_url: str
    backup_url: str
    file_hash: str
    bitrate: int
    vheight: int
    vwidth: int


@dataclass
class Capture:
    kid: str
    streams: list[Stream] = field(default_factory=list)
    captured_at: float = 0.0
    aes_key: str = ""
    aes_ts: float = 0.0

    def best_stream(self, max_short_side: int = 1080) -> Stream | None:
        """按"画质短边 ≤ max_short_side"筛，然后按 bitrate 选最高。

        竖屏短剧：短边 = vwidth（长边在 vheight）。
        横屏：短边 = vheight。
        统一用 min(vheight, vwidth) 抽象。
        """
        if not self.streams:
            return None
        pool = [s for s in self.streams if s.main_url]
        if not pool:
            return None

        def short_side(s: Stream) -> int:
            return min(s.vheight, s.vwidth) if s.vwidth else s.vheight

        candidates = [s for s in pool if short_side(s) <= max_short_side]
        if candidates:
            pool = candidates
        return max(pool, key=lambda s: s.bitrate)


class State:
    """捕获状态：有序 kid 列表 + kid→Capture 映射。"""

    def __init__(self):
        self.lock = threading.Lock()
        self.order: list[str] = []          # 按首次见到时间排序的 kid 列表
        self.by_kid: dict[str, Capture] = {}
        self.unpaired_keys: list[tuple[str, float]] = []  # (hex_key, ts) 尚未匹配到 kid 的预加载 key
        self.chunks: dict[int, dict[int, str]] = {}

    def ingest_ref(self, text: str):
        try:
            obj = json.loads(text)
        except json.JSONDecodeError:
            return
        vl = obj.get("dynamic_video_list", [])
        if not vl:
            return
        ts = time.time()
        new_kids = []
        with self.lock:
            # 同一 ref 可能列出多画质：按 kid 聚合到同一 Capture.streams
            kids_in_ref: dict[str, list[dict]] = {}
            for v in vl:
                kid = v.get("kid")
                if not kid or len(kid) != 32:
                    continue
                kids_in_ref.setdefault(kid, []).append(v)

            for kid, entries in kids_in_ref.items():
                if kid in self.by_kid:
                    cap = self.by_kid[kid]
                    known_urls = {s.main_url for s in cap.streams}
                    for v in entries:
                        url = v.get("main_url") or ""
                        if url and url not in known_urls:
                            cap.streams.append(Stream(
                                main_url=url,
                                backup_url=v.get("backup_url_1") or "",
                                file_hash=v.get("file_hash") or "",
                                bitrate=int(v.get("bitrate") or 0),
                                vheight=int(v.get("vheight") or 0),
                                vwidth=int(v.get("vwidth") or 0),
                            ))
                            known_urls.add(url)
                else:
                    cap = Capture(kid=kid, captured_at=ts)
                    for v in entries:
                        url = v.get("main_url") or ""
                        if not url:
                            continue
                        cap.streams.append(Stream(
                            main_url=url,
                            backup_url=v.get("backup_url_1") or "",
                            file_hash=v.get("file_hash") or "",
                            bitrate=int(v.get("bitrate") or 0),
                            vheight=int(v.get("vheight") or 0),
                            vwidth=int(v.get("vwidth") or 0),
                        ))
                    if cap.streams:
                        self.by_kid[kid] = cap
                        self.order.append(kid)
                        new_kids.append(kid)
        self._try_pair_pending(new_kids, ts)

    def _try_pair_pending(self, new_kids: list[str], ts: float):
        if not new_kids:
            return
        with self.lock:
            # 按出现顺序配对：每个 new_kid 取一个尚未被使用的 pending key
            new_kids_iter = iter(new_kids)
            used_keys = {c.aes_key for c in self.by_kid.values() if c.aes_key}
            remaining = []
            for hex_key, ktime in self.unpaired_keys:
                if hex_key in used_keys:
                    continue  # 已被别的 kid 占用，丢弃
                kid = next(new_kids_iter, None)
                if kid is None:
                    remaining.append((hex_key, ktime))
                else:
                    self.by_kid[kid].aes_key = hex_key
                    self.by_kid[kid].aes_ts = ktime
                    used_keys.add(hex_key)
            self.unpaired_keys = remaining

    def ingest_aes(self, hex_key: str, ts: float):
        """严格去重：每个 AES key 至多使用一次（CENC 保证每集 key 唯一）。"""
        with self.lock:
            # 1) 已被任意 kid 占用 → 忽略重复触发（av_aes_init 每集会 fire 多次相同 key）
            for c in self.by_kid.values():
                if c.aes_key == hex_key:
                    return
            # 2) 已在 pending 队列 → 忽略
            if any(k == hex_key for k, _ in self.unpaired_keys):
                return
            # 3) 限定在 order 末尾最近 3 位，且 kid 捕获不早于 ts-15s
            for kid in self.order[-3:][::-1]:
                c = self.by_kid[kid]
                if c.aes_key:
                    continue
                if ts - c.captured_at < 15.0:
                    c.aes_key = hex_key
                    c.aes_ts = ts
                    return
            # 4) 新 key 无匹配 kid（preload 提前触发），入队
            self.unpaired_keys.append((hex_key, ts))

    def wait_index(self, idx: int, timeout: float, require_key: bool = True) -> Capture | None:
        """等待 order[idx] 存在且 streams+key 齐全。FIFO 消费：不丢新 kid。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self.lock:
                if idx < len(self.order):
                    c = self.by_kid[self.order[idx]]
                    has_url = bool(c.streams and any(s.main_url for s in c.streams))
                    if has_url and (c.aes_key or not require_key):
                        return c
            time.sleep(0.3)
        with self.lock:
            if idx < len(self.order):
                return self.by_kid[self.order[idx]]
        return None

    def order_len(self) -> int:
        with self.lock:
            return len(self.order)

    def snapshot(self, path: Path):
        def _dump_cap(c: Capture) -> dict:
            return {
                "kid": c.kid,
                "captured_at": c.captured_at,
                "aes_key": c.aes_key,
                "aes_ts": c.aes_ts,
                "streams": [s.__dict__ for s in c.streams],
            }
        with self.lock:
            data = {
                "order": list(self.order),
                "captures": {k: _dump_cap(c) for k, c in self.by_kid.items()},
            }
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def create_on_message(state: State):
    def on_message(msg, _data):
        if msg.get("type") != "send":
            if msg.get("type") == "error":
                logger.warning(f"[JS ERR] {msg.get('description','')[:200]}")
            return
        p = msg["payload"]
        t = p.get("t")
        if t == "ref":
            cid = p["id"]
            state.chunks.setdefault(cid, {})[p["idx"]] = p["body"]
            if len(state.chunks[cid]) == p["total"]:
                full = "".join(state.chunks[cid][i] for i in range(p["total"]))
                state.chunks.pop(cid)
                state.ingest_ref(full)
        elif t == "aes_key":
            if len(p.get("hex", "")) == 32:
                state.ingest_aes(p["hex"], p["ts"] / 1000.0)
        elif t == "ref_ready":
            logger.info("[Hook] VideoRef hook ready")
        elif t == "aes_hooked":
            logger.info("[Hook] av_aes_init hooked")
        elif t in ("ref_err", "aes_err", "ref_init_err"):
            logger.warning(f"[Hook {t}] {p.get('err') or p.get('e')}")

    return on_message


def setup_frida(attach_running: bool, state: State):
    device = frida.get_usb_device(timeout=10)
    env = {**os.environ, "MSYS_NO_PATHCONV": "1"}
    if attach_running:
        pid = select_running_app_pid(device.enumerate_processes(), APP_PACKAGE)
        if pid is None:
            raise RuntimeError(f"{APP_PACKAGE} 未运行。请先手动打开并进入剧集播放页")
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


def download_and_decrypt(stream: Stream, key_hex: str, output_path: str) -> bool:
    tmp = output_path + ".tmp"
    urls = [stream.main_url] + ([stream.backup_url] if stream.backup_url else [])
    last_err: Exception | None = None
    for url in urls:
        try:
            resp = requests.get(url, headers={"User-Agent": "AVDML_2.1.230.181-novel_ANDROID"}, timeout=120)
            resp.raise_for_status()
            data = bytearray(resp.content)
            size_mb = len(data) / 1024 / 1024
            logger.info(f"[下载] {size_mb:.1f}MB (h={stream.vheight} bt={stream.bitrate})")
            n = decrypt_mp4(data, bytes.fromhex(key_hex))
            fix_metadata(data)
            logger.info(f"[解密] {n} samples")
            os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
            with open(tmp, "wb") as f:
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


def swipe_next_episode():
    """慢速上滑，避免跨集惯性。"""
    run_adb(["shell", "input", "swipe", "540", "1400", "540", "600", "800"])


def capture_and_download_loop(state: State, total_eps: int, start_ep: int,
                              output_dir: str, manifest_path: str,
                              max_height: int) -> tuple[int, list[int]]:
    """主循环：FIFO 消费 state.order[expected_idx]，每迭代最多 2 次 swipe。

    关键不变式：order[expected_idx] 即本迭代要下载的集，**不因 swipe 一次产生多个
    新 kid 而丢弃**（那些多出的 kid 留给下一迭代消费）。

    Returns: (success_count, failed_eps)
    """
    os.makedirs(output_dir, exist_ok=True)
    success = 0
    failed: list[int] = []

    # 先等初始 kid（App 已在 start_ep 的播放页）
    logger.info(f"[起始] 等待首个 kid（App 应已在第 {start_ep} 集播放页）...")
    first_deadline = time.time() + 30
    nudged = False
    while time.time() < first_deadline and state.order_len() == 0:
        elapsed = first_deadline - time.time()
        if not nudged and elapsed < 20:
            logger.info("[起始] 10s 无 kid，tap 中心尝试唤醒")
            run_adb(["shell", "input", "tap", "540", "960"])
            nudged = True
        elif nudged and elapsed < 10:
            logger.info("[起始] 20s 无 kid，swipe 尝试触发新视频")
            swipe_next_episode()
            nudged = False
        time.sleep(1.0)
    if state.order_len() == 0:
        logger.error("[起始] 30s 内未捕获首个 VideoRef，退出")
        return 0, list(range(start_ep, total_eps + 1))

    for ep_num in range(start_ep, total_eps + 1):
        expected_idx = ep_num - start_ep

        # 非首迭代：若 order 尚未追上 expected_idx，swipe 最多 2 次
        swipes_done = 0
        while state.order_len() <= expected_idx and swipes_done < 2:
            if ep_num == start_ep:
                break  # 首迭代不 swipe
            swipe_next_episode()
            swipes_done += 1
            # 小等 kid 登记（url/key 等 wait_index 再磨）
            for _ in range(10):
                if state.order_len() > expected_idx:
                    break
                time.sleep(0.3)

        # 等这一位 kid 的 url+key 都齐
        cap = state.wait_index(expected_idx, timeout=15.0)
        if cap is None:
            logger.error(f"[第{ep_num}集] order_len={state.order_len()} 未能推进到 idx={expected_idx}")
            failed.append(ep_num)
            continue

        stream = cap.best_stream(max_short_side=max_height)
        if not stream or not cap.aes_key:
            logger.warning(f"[第{ep_num}集] 数据不齐: streams={len(cap.streams)} key={'有' if cap.aes_key else '无'}")
            failed.append(ep_num)
            continue

        # 断点：已存在就跳过实际下载
        output_path = os.path.join(output_dir, f"episode_{ep_num:03d}_{cap.kid[:8]}.mp4")
        existing = glob.glob(os.path.join(output_dir, f"episode_{ep_num:03d}_*.mp4"))
        if existing and os.path.getsize(existing[0]) > 100 * 1024:
            logger.info(f"[第{ep_num}集] 已存在 {existing[0]}，跳过下载")
            success += 1
            continue

        logger.info(f"[第{ep_num}集] kid={cap.kid[:8]} streams={len(cap.streams)} 选 h={stream.vheight} bt={stream.bitrate}")
        ok = download_and_decrypt(stream, cap.aes_key, output_path)
        if ok and verify_playable(output_path):
            success += 1
            append_jsonl(manifest_path, {
                "episode": ep_num,
                "kid": cap.kid,
                "file_hash": stream.file_hash,
                "path": output_path,
                "vheight": stream.vheight,
                "bitrate": stream.bitrate,
                "stream_count": len(cap.streams),
                "timestamp": time.time(),
                "status": "ok",
            })
        else:
            if os.path.exists(output_path):
                os.remove(output_path)
            failed.append(ep_num)

    return success, failed


def main():
    ap = argparse.ArgumentParser(description="红果短剧下载器 v2 (kid-based, swipe-driven)")
    ap.add_argument("-n", "--name", required=True)
    ap.add_argument("-e", "--start-episode", type=int, default=1)
    ap.add_argument("--total-episodes", type=int, required=True)
    ap.add_argument("--output", default="./videos")
    ap.add_argument("--attach-running", action="store_true",
                    help="attach 到已运行 App，跳过搜索导航。需确保 App 已在第 start-episode 集全屏播放")
    ap.add_argument("--max-height", type=int, default=1080,
                    help="画质短边上限（默认 1080）。短边 = min(vheight,vwidth)，兼容横竖屏。符合条件档位中选 bitrate 最高。")
    args = ap.parse_args()

    output_dir = os.path.join(args.output, args.name)
    os.makedirs(output_dir, exist_ok=True)
    log_file = os.path.join(output_dir, "download_v2.log")
    logger.add(log_file, rotation="10 MB", encoding="utf-8",
               format="{time:YYYY-MM-DD HH:mm:ss} | {level:<8} | {message}", level="INFO")
    logger.info(f"目标: 《{args.name}》 ep{args.start_episode}..{args.total_episodes}")

    state = State()
    session, script, pid = setup_frida(args.attach_running, state)

    # 只有非 attach 模式才自动搜索导航
    if not args.attach_running:
        logger.info("[导航] 搜索并进入目标剧...")
        navigate_to_drama_via_search(args.name)
        time.sleep(5)

    manifest = os.path.join(output_dir, "session_manifest_v2.jsonl")
    success, failed = capture_and_download_loop(
        state, args.total_episodes, args.start_episode, output_dir, manifest,
        max_height=args.max_height,
    )

    # 保存映射快照
    state.snapshot(Path(output_dir) / "kid_map_snapshot.json")

    logger.info(f"\n=== 完成: 成功 {success}/{args.total_episodes - args.start_episode + 1} ===")
    if failed:
        logger.warning(f"失败: {failed}")

    try:
        script.unload()
        session.detach()
    except Exception:
        pass


if __name__ == "__main__":
    main()
