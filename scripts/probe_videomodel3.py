"""直接读 VideoRef.toBashString 完整 JSON，regex 找 tt_vid。"""
from __future__ import annotations
import sys, time, re
from pathlib import Path
import frida

sys.path.insert(0, str(Path(__file__).parent.parent))
from scripts.download_drama import select_running_app_pid

APP = "com.phoenix.read"

HOOK = r"""
Java.perform(function() {
    var TTE = Java.use('com.ss.ttvideoengine.TTVideoEngine');
    function dump(m) {
        if (!m) return;
        try {
            var ref = m.getVideoRef();
            if (!ref) return;
            var json = '';
            try { json = String(ref.toBashString() || ''); } catch (e) {}
            // 分块 send
            var CHUNK = 50000;
            var id = Math.floor(Math.random()*1e9);
            var parts = Math.ceil(json.length / CHUNK);
            for (var k=0;k<parts;k++)
                send({t:'ref', id:id, idx:k, total:parts, len:json.length, body:json.substring(k*CHUNK,(k+1)*CHUNK)});
        } catch (e) { send({t:'e', e:e.toString()}); }
    }
    TTE.setVideoModel.overloads.forEach(function(ov) {
        ov.implementation = function(m) { dump(m); return ov.call(this, m); };
    });
    try {
        var aop = TTE.com_ss_ttvideoengine_TTVideoEngine_com_dragon_read_aop_TTVideoEngineAop_setVideoModel;
        aop.overloads.forEach(function(ov) {
            ov.implementation = function() {
                var args = Array.prototype.slice.call(arguments);
                if (args.length>=2) dump(args[1]);
                return ov.apply(this, args);
            };
        });
    } catch (e) {}
    send({t:'ready'});
});
"""

def main():
    device = frida.get_usb_device(timeout=10)
    pid = select_running_app_pid(device.enumerate_processes(), APP)
    if pid is None: print('not running'); return
    s = device.attach(pid)
    sc = s.create_script(HOOK)
    chunks = {}
    seen_tt = set()

    def on_msg(msg, data):
        if msg.get('type') != 'send':
            if msg.get('type') == 'error': print('[JS ERR]', msg.get('description','')[:200])
            return
        p = msg['payload']; t = p.get('t')
        if t == 'ready': print('[READY]'); return
        if t == 'e': print('[ERR]', p['e']); return
        if t != 'ref': return
        cid = p['id']
        chunks.setdefault(cid, {})[p['idx']] = p['body']
        if len(chunks[cid]) == p['total']:
            full = ''.join(chunks[cid][i] for i in range(p['total']))
            chunks.pop(cid)
            m_vid = re.search(r'"video_id"\s*:\s*"(v0[^"]+)"', full)
            m_urls = re.findall(r'"main_url"\s*:\s*"([^"]+)"', full)
            if m_vid:
                tt = m_vid.group(1)
                if tt not in seen_tt:
                    seen_tt.add(tt)
                    print(f"\n[TT] {tt}  urls={len(m_urls)}  total_len={p['len']}")
                    if m_urls: print(f"  main_url[0]={m_urls[0][:120]}")
            else:
                # no video_id — dump beginning and search all keys
                keys = re.findall(r'"([a-zA-Z_]+)"\s*:', full)
                uniq = sorted(set(keys))
                print(f"\n[NO video_id] len={p['len']}, keys={uniq[:30]}")
                # save for manual inspect
                Path('d:/tmp/ref_dump.json').write_text(full, encoding='utf-8')
                print('  >> saved to d:/tmp/ref_dump.json')

    sc.on('message', on_msg)
    sc.load()
    print('切集...')
    time.sleep(40)

if __name__ == '__main__': main()
