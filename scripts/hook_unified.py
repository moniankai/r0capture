"""一体化 Hook：VideoRef(kid+url+file_hash) + av_aes_init(AES key) + (可选) multi_video_model API(biz→kid)。

主索引用 kid（32-hex KID）作为主键，因为每集独立且在 VideoRef.toBashString() 和 av_aes_init 时序附近都可获得。
  - d:/tmp/kid_map.json   kid → {aes_key, main_url, backup_url, file_hash, bitrate, vheight, captured_at, seq}
  - d:/tmp/biz_kid_map.json   biz_vid → {kid, episode_idx, title, duration} (需要 --sniff-api 时填充)

Ctrl+C 退出。
"""
from __future__ import annotations
import sys, time, json, re, argparse
from pathlib import Path

import frida
sys.path.insert(0, str(Path(__file__).parent.parent))
from scripts.download_drama import select_running_app_pid

APP = "com.phoenix.read"
KID_MAP = Path('d:/tmp/kid_map.json')
BIZ_KID_MAP = Path('d:/tmp/biz_kid_map.json')
LOG = Path('d:/tmp/unified_hook.log')

HOOK_TEMPLATE = r"""
var __SNIFF_API__ = __SNIFF_API_FLAG__;

Java.perform(function() {
    // === VideoRef Hook: 取每集 kid + url + file_hash ===
    try {
        var TTE = Java.use('com.ss.ttvideoengine.TTVideoEngine');
        function dumpRef(m) {
            if (!m) return;
            try {
                var ref = m.getVideoRef();
                if (!ref) return;
                var json = '';
                try { json = String(ref.toBashString() || ''); } catch (e) {}
                if (!json) return;
                // 分块
                var CHUNK = 50000;
                var id = Math.floor(Math.random()*1e9);
                var parts = Math.ceil(json.length / CHUNK);
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

    // === (可选) API 响应 Hook: biz_vid -> kid/url ===
    if (!__SNIFF_API__) {
        send({t:'api_disabled'});
    } else try {
        var Call = Java.use('com.bytedance.retrofit2.SsHttpCall');
        var Request = Java.use('com.bytedance.retrofit2.client.Request');
        var BodyCls = Java.use('com.bytedance.frameworks.baselib.network.http.impl.a$a');
        var API_KEYS = ['multi_video_model', 'video_detail'];
        Call.execute.implementation = function() {
            var url = '';
            try {
                var f = Call.class.getDeclaredField('originalRequest');
                f.setAccessible(true);
                var req = f.get(this);
                if (req) url = String(Request.getUrl.call(req) || '');
            } catch (e) {}
            var resp = this.execute();
            var matched = false;
            for (var i=0;i<API_KEYS.length;i++) if (url.indexOf(API_KEYS[i]) !== -1) matched = true;
            if (!matched) return resp;
            try {
                var bodyObj = resp.body();
                if (!bodyObj) return resp;
                var body = Java.cast(bodyObj, BodyCls);
                var is = body.in();
                var BAOS = Java.use('java.io.ByteArrayOutputStream');
                var buf = BAOS.$new();
                var ba = Java.array('byte', new Array(8192).fill(0));
                var total = 0;
                while (true) {
                    var n = is.read(ba, 0, 8192);
                    if (n <= 0) break;
                    buf.write(ba, 0, n);
                    total += n;
                    if (total > 4*1024*1024) break;
                }
                var bytes = buf.toByteArray();
                var Str = Java.use('java.lang.String');
                var full = String(Str.$new(bytes, 'UTF-8'));
                var CHUNK = 50000;
                var id = Math.floor(Math.random()*1e9);
                var parts = Math.ceil(full.length / CHUNK);
                for (var k=0;k<parts;k++)
                    send({t:'api', id:id, idx:k, total:parts, url:url.substring(0,300), body:full.substring(k*CHUNK,(k+1)*CHUNK)});
            } catch (e) { send({t:'api_err', err:e.toString()}); }
            return resp;
        };
        send({t:'api_ready'});
    } catch (e) { send({t:'api_init_err', e:e.toString()}); }
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
        onLeave: function(retval) {
            try {
                var len = this.keyBits >>> 3;
                if (len <= 0 || len > 32) return;
                var bytes = new Uint8Array(this.keyPtr.readByteArray(len));
                var hex = '';
                for (var i=0;i<bytes.length;i++) {
                    var h = bytes[i].toString(16); if (h.length<2) h='0'+h; hex+=h;
                }
                send({t:'aes_key', hex:hex, bits:this.keyBits, ts:Date.now()});
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

class State:
    def __init__(self):
        self.kid_map = {}          # kid -> {url, key, file_hash, bitrate, vheight, captured_at, seq}
        self.biz_kid_map = {}      # biz_vid -> {kid, episode_idx, title, duration, url}
        self.recent_kids = []      # (kid, ts) 按时间排序，最近几集的 kid 候选
        self.chunks = {}
        self.api_chunks = {}
        self.seq = 0
        if KID_MAP.exists():
            try: self.kid_map = json.loads(KID_MAP.read_text(encoding='utf-8'))
            except: pass
        if BIZ_KID_MAP.exists():
            try: self.biz_kid_map = json.loads(BIZ_KID_MAP.read_text(encoding='utf-8'))
            except: pass
        self.seq = len(self.kid_map)

    def ingest_ref(self, js_text):
        try: obj = json.loads(js_text)
        except: return
        vl = obj.get('dynamic_video_list', [])
        if not vl: return
        ts = int(time.time() * 1000)
        new = 0
        for v in vl:
            kid = v.get('kid')
            if not kid or len(kid) != 32: continue
            entry = self.kid_map.setdefault(kid, {'seq': self.seq})
            if 'captured_at' not in entry:
                entry['captured_at'] = ts
                self.seq += 1
                entry['seq'] = self.seq
                new += 1
            # 合并字段
            entry['main_url'] = v.get('main_url') or entry.get('main_url')
            entry['backup_url_1'] = v.get('backup_url_1') or entry.get('backup_url_1')
            entry['file_hash'] = v.get('file_hash') or entry.get('file_hash')
            entry['bitrate'] = v.get('bitrate') or entry.get('bitrate')
            entry['vheight'] = v.get('vheight') or entry.get('vheight')
            entry['vwidth'] = v.get('vwidth') or entry.get('vwidth')
        # 记录最近的 kid 候选（按首次捕获时间）
        # 只把本次 ref 的 kids 标一个时间戳（用于 aes_key 时间关联）
        kids_now = [v.get('kid') for v in vl if v.get('kid')]
        for k in kids_now:
            self.recent_kids.append((k, ts))
        # 只保留最近 30 个
        self.recent_kids = self.recent_kids[-30:]
        if new:
            print(f'  [ref] +{new} new kids (total={len(self.kid_map)})')

    def ingest_aes(self, hex_key, ts):
        # 策略：找最近 15s 内且还没 key 的 kid
        cand = [k for k, t in reversed(self.recent_kids) if (ts - t) < 15000 and not self.kid_map.get(k, {}).get('aes_key')]
        if cand:
            kid = cand[0]  # 最近的优先
            self.kid_map.setdefault(kid, {})['aes_key'] = hex_key
            self.kid_map[kid]['aes_ts'] = ts
            paired = sum(1 for k, v in self.kid_map.items() if k != '_UNKNOWN_KEYS' and isinstance(v, dict) and v.get('aes_key'))
            total_kids = sum(1 for k in self.kid_map if k != '_UNKNOWN_KEYS')
            print(f'  [aes] kid={kid[:12]}... key={hex_key[:8]}... (pair {paired}/{total_kids})')
        else:
            # 无关联 kid，存 UNKNOWN
            self.kid_map.setdefault('_UNKNOWN_KEYS', []).append({'key': hex_key, 'ts': ts})

    def ingest_api(self, url, body):
        try: obj = json.loads(body)
        except: return
        if 'video_detail' in url and 'multi_video_detail' not in url:
            vl = obj.get('data', {}).get('video_data', {}).get('video_list', [])
            for it in vl:
                biz = it.get('vid')
                if not biz: continue
                e = self.biz_kid_map.setdefault(biz, {})
                e.update({'episode_idx': it.get('vid_index'), 'duration': it.get('duration'), 'title': it.get('title')})
            if vl: print(f'  [video_detail] +{len(vl)} eps biz entries')
        elif 'multi_video_model' in url:
            data = obj.get('data', {})
            new = 0
            for biz, info in data.items():
                vm = info.get('video_model', '')
                if not vm: continue
                try: vmj = json.loads(vm)
                except: continue
                # mvm 的 video_model 里有 video_list
                vl = vmj.get('video_list', [])
                if not vl: continue
                v0 = vl[0]
                kid = v0.get('kid')
                main = v0.get('main_url')
                tt = vmj.get('video_id')
                e = self.biz_kid_map.setdefault(biz, {})
                if kid and 'kid' not in e: new += 1
                if kid: e['kid'] = kid
                if main: e['main_url'] = main
                if tt: e['tt_vid'] = tt
            print(f'  [mvm] +{new} biz with kid (total biz={len(self.biz_kid_map)})')

    def snapshot(self):
        KID_MAP.write_text(json.dumps(self.kid_map, ensure_ascii=False, indent=2), encoding='utf-8')
        BIZ_KID_MAP.write_text(json.dumps(self.biz_kid_map, ensure_ascii=False, indent=2), encoding='utf-8')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sniff-api', action='store_true', help='额外破坏性地读 API body 拿 biz→kid 映射')
    ap.add_argument('--duration', type=int, default=0, help='运行秒数（0=无限）')
    args = ap.parse_args()
    state = State()
    device = frida.get_usb_device(timeout=10)
    pid = select_running_app_pid(device.enumerate_processes(), APP)
    if pid is None: print('App not running'); return
    print(f'attach pid={pid} sniff_api={args.sniff_api}')
    session = device.attach(pid)
    code = HOOK_TEMPLATE.replace('__SNIFF_API_FLAG__', 'true' if args.sniff_api else 'false')
    script = session.create_script(code)
    LOG.write_text('', encoding='utf-8')

    def on_msg(msg, data):
        if msg.get('type') != 'send':
            if msg.get('type') == 'error': print('[JSERR]', msg.get('description','')[:200])
            return
        p = msg['payload']; t = p.get('t')
        try:
            with LOG.open('a', encoding='utf-8') as fl:
                rec = {k:(v[:200] if isinstance(v,str) else v) for k,v in p.items()}
                fl.write(json.dumps(rec, ensure_ascii=False)+'\n')
        except: pass
        if t == 'ref':
            cid = p['id']
            state.chunks.setdefault(cid, {})[p['idx']] = p['body']
            if len(state.chunks[cid]) == p['total']:
                full = ''.join(state.chunks[cid][i] for i in range(p['total']))
                state.chunks.pop(cid)
                state.ingest_ref(full)
                state.snapshot()
        elif t == 'aes_key':
            if len(p['hex']) == 32:
                state.ingest_aes(p['hex'], p['ts'])
                state.snapshot()
        elif t == 'api':
            cid = p['id']
            state.api_chunks.setdefault(cid, {})[p['idx']] = p['body']
            if len(state.api_chunks[cid]) == p['total']:
                full = ''.join(state.api_chunks[cid][i] for i in range(p['total']))
                state.api_chunks.pop(cid)
                state.ingest_api(p['url'], full)
                state.snapshot()
        elif t == 'ref_ready':   print('[ref] hook OK')
        elif t == 'api_ready':   print('[api] hook OK (destructive)')
        elif t == 'api_disabled':print('[api] sniff OFF')
        elif t == 'aes_hooked':  print('[aes] hook OK')
        elif t in ('ref_err','api_err','aes_err','ref_init_err','api_init_err'):
            print(f'[ERR {t}] {p.get("err") or p.get("e")}')

    script.on('message', on_msg)
    script.load()
    print('=== 运行中，让 App 播放。Ctrl+C 或到时间后退出 ===')
    t0 = time.time()
    try:
        while True:
            time.sleep(10)
            paired = sum(1 for k,v in state.kid_map.items() if k != '_UNKNOWN_KEYS' and isinstance(v, dict) and v.get('aes_key'))
            biz_pair = sum(1 for v in state.biz_kid_map.values() if v.get('kid'))
            print(f'  [stat] kids={len(state.kid_map)-(1 if "_UNKNOWN_KEYS" in state.kid_map else 0)} with_key={paired} biz_with_kid={biz_pair}')
            if args.duration and (time.time() - t0) > args.duration: break
    except KeyboardInterrupt:
        pass
    state.snapshot()
    print(f'已保存: {KID_MAP}, {BIZ_KID_MAP}')

if __name__ == '__main__': main()
