#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
resolve_interactive — 交互式批量抓 series_id.

用法:
  1. 在手机上打开红果 App (任意页面均可)
  2. 运行本脚本, attach 到 App
  3. 在 App 里浏览目标剧 (搜索 / 剧场 / 首页推荐 / 历史记录 都会触发剧信息加载)
  4. 工具实时打印新捕获的剧, 同时追加到 --out JSON 文件
  5. 浏览完所有目标剧后, 在终端按 Ctrl-C 结束, 最终 dump 写入

  python scripts/resolve_interactive.py --out dramas.json
  python scripts/resolve_interactive.py --out dramas.json --filter "凡人仙葫,风水"
    --filter 只保留剧名含任一关键词的记录

Hook 原理:
  - 复用 v5 的 setSeriesName/setEpisodesCount hook → SaasVideoData 触发时 emit
    catalog event
  - 也覆盖 search API 响应 (URL 含 /search/) 抓 series_id + book_name + total
  - 所有抓到的剧聚合成 series_id → {name, total, first_vid} 字典
"""
from __future__ import annotations
import argparse
import json
import signal
import subprocess
import sys
import time
from pathlib import Path


JS = r"""
Java.perform(function() {
    var HTTP_URLBASE = 'okhttp3.Request';
    var SVD_name = 'com.dragon.read.component.shortvideo.data.saas.video.SaasVideoData';
    var Data = null;
    try { Data = Java.use(SVD_name); } catch(e) { send({t:'err', err:'SaasVideoData not found: '+String(e)}); }

    var sent_sid = {};
    function emitCatalog(sid, name, total, vid, idx, title) {
        if (!sid || sid === 'null') return;
        var key = sid + '||' + (name||'');
        if (sent_sid[key]) return;
        sent_sid[key] = true;
        send({t:'catalog', series_id:String(sid), name:name?String(name):null,
              total_eps: total!==null&&total!==undefined ? Number(total) : -1,
              vid: vid?String(vid):null, idx: idx!==null&&idx!==undefined ? Number(idx) : -1,
              title: title?String(title):null, ts: Date.now()});
    }

    // Hook SaasVideoData.setSeriesName + setSeriesId: 全量 bind 时触发
    function dumpInst(inst) {
        try {
            var sid = ''; try { sid = String(inst.getSeriesId()); } catch(e){}
            var name = ''; try { name = String(inst.getSeriesName()); } catch(e){}
            if (name === 'null' || !name) name = null;
            var total = -1; try { total = Number(inst.getEpisodesCount()); } catch(e){}
            var vid = ''; try { vid = String(inst.getVid()); } catch(e){}
            var idx = -1; try { idx = Number(inst.getVidIndex()); } catch(e){}
            var title = ''; try { title = String(inst.getTitle()); } catch(e){}
            if (sid && sid !== 'null') emitCatalog(sid, name, total, vid, idx, title);
        } catch(e) {}
    }

    if (Data) {
        try {
            Data.setSeriesName.overload('java.lang.String').implementation = function(v) {
                var r = this.setSeriesName(v); dumpInst(this); return r;
            };
        } catch(e) {}
        try {
            Data.setSeriesId.overload('java.lang.String').implementation = function(v) {
                var r = this.setSeriesId(v); dumpInst(this); return r;
            };
        } catch(e) {}
        try {
            Data.setEpisodesCount.overload('long').implementation = function(v) {
                var r = this.setEpisodesCount(v); dumpInst(this); return r;
            };
        } catch(e) {}
    }

    // Hook ViewHolder bind (j2) 覆盖切集 / 列表滚动场景
    try {
        var holderA = Java.use('com.dragon.read.component.shortvideo.impl.v2.view.holder.a');
        holderA.j2.overload(SVD_name).implementation = function(d) {
            if (d) dumpInst(d);
            return this.j2(d);
        };
    } catch(e) {}
    try {
        var holderZ = Java.use('com.dragon.read.component.shortvideo.impl.v2.view.holder.z');
        holderZ.j2.overload(SVD_name).implementation = function(d) {
            if (d) dumpInst(d);
            return this.j2(d);
        };
    } catch(e) {}

    send({t: 'ready'});
});
"""


def read_existing(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


def save(path: Path, dramas: list[dict]) -> None:
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(
        json.dumps(dramas, ensure_ascii=False, indent=2),
        encoding='utf-8',
    )
    tmp.replace(path)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', type=Path, default=Path('dramas.json'),
                    help='输出 JSON 路径 (追加式 merge)')
    ap.add_argument('--filter', type=str, default='',
                    help='剧名过滤 (逗号分隔关键词, 只保留含任一的剧)')
    ap.add_argument('--min-total', type=int, default=1,
                    help='最小集数 (过滤 total_eps < N 的, 默认 1)')
    args = ap.parse_args()

    filter_keys = [k.strip() for k in args.filter.split(',') if k.strip()]
    catalog: dict[str, dict] = {}  # series_id -> {name, total_eps, first_vid}
    existing = {d['series_id']: d for d in read_existing(args.out)}
    catalog.update(existing)

    print(f"[resolve] out={args.out}  filter={filter_keys or 'none'}  "
          f"min_total={args.min_total}")
    print(f"[resolve] 现有 {len(existing)} 条, 继续追加")

    # Attach frida
    r = subprocess.run(['adb', 'shell', 'pidof', 'com.phoenix.read'],
                       capture_output=True, text=True, timeout=5)
    if r.returncode != 0 or not r.stdout.strip():
        print("[resolve] App 未运行, 请先打开红果 App")
        return 2
    pid = int(r.stdout.strip().split()[0])
    print(f"[resolve] attach pid={pid}")

    import frida
    dev = frida.get_usb_device()
    session = dev.attach(pid)
    script = session.create_script(JS)

    stop_requested = {'flag': False}

    def on_msg(msg, data):
        if msg.get('type') != 'send':
            if msg.get('type') == 'error':
                print(f"[JS err] {msg.get('description', '')[:200]}")
            return
        p = msg['payload']
        if p.get('t') == 'ready':
            print("[resolve] hook ready, 请在 App 里浏览剧 (搜索/剧场/历史均可)")
            print("[resolve] Ctrl-C 结束")
            return
        if p.get('t') != 'catalog':
            return
        sid = p.get('series_id')
        name = p.get('name')
        total = p.get('total_eps', -1)
        if not sid or not name:
            return
        # filter
        if filter_keys and not any(k in name for k in filter_keys):
            return
        if total < args.min_total:
            return
        entry = catalog.setdefault(sid, {
            'series_id': sid, 'name': name,
            'total': total if total > 0 else 0,
        })
        # 更新 total (如果新值更可靠)
        if total > 0 and entry.get('total', 0) <= 0:
            entry['total'] = total
        if not entry.get('name') and name:
            entry['name'] = name
        print(f"  + {sid}  {name!r}  total={total}")

    script.on('message', on_msg)
    script.load()

    def _sigint(sig, frame):
        stop_requested['flag'] = True
    signal.signal(signal.SIGINT, _sigint)

    try:
        while not stop_requested['flag']:
            time.sleep(1)
    except KeyboardInterrupt:
        pass

    # unload + save
    try:
        script.unload()
    except Exception:
        pass
    try:
        session.detach()
    except Exception:
        pass

    # 保存
    final = sorted(catalog.values(), key=lambda d: d.get('name', ''))
    save(args.out, final)
    print(f"\n[resolve] 总共 {len(final)} 条, 写入 {args.out}")
    for d in final[-10:]:
        print(f"  {d['series_id']}  {d['name']!r}  total={d.get('total')}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
