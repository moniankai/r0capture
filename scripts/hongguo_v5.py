"""红果短剧 v5 下载器: BIND 驱动的集数精准下载.

相对 v4 的升级:
  - 合并 episode_bind hook, 用 SaasVideoData.vidIndex 作为集数真值
  - 放弃面板 tap/错位补偿, 改用 swipe + BIND 观察的自然节奏
  - 每集 (ep, biz_vid, kid, spadea, key, url) 通过 BIND→cap 时序关联

工作流:
  1. spawn 红果 App, 加载合并的 Hook
  2. 搜索剧名 → tap 结果进 ShortSeriesActivity
  3. 首个 BIND 锁定剧名 + total_eps
  4. 循环: 判定当前 ep → 等 cap → 下载 → 上滑切下一集
"""
from __future__ import annotations
import os, sys, time, json, re, threading, subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger
import frida

sys.path.insert(0, str(Path(__file__).parent.parent))
from scripts.download_drama import run_adb, read_ui_xml_from_device
from scripts.decrypt_video import decrypt_mp4, fix_metadata

APP_PACKAGE = "com.phoenix.read"
DEFAULT_OUT_DIR = Path("videos")


# =============== Agent 契约: 退出码 + 事件流 (design doc v4 §3.3/§3.4) ===============

# 退出码 (Agent 根据码做 FSM 决策, 见 §3.1.1)
EXIT_OK = 0                # 全 ok
EXIT_PARTIAL = 1           # 有 fail 但未致命
EXIT_ANR_SUSPECTED = 2     # frida/transport/state 疑似 ANR, Agent → RECOVERING
EXIT_FATAL = 3             # 配置错 / manifest 损坏 / context 不一致, Agent → ABORTED
EXIT_USER_ABORT = 4        # SIGINT/SIGTERM
EXIT_PRECOND_FAIL = 5      # attach-resume 前置条件不满足, Agent → NAVIGATING


def emit(event_type: str, **fields) -> None:
    """输出一行 JSON 机读事件到 stdout (Agent 用 line-by-line 解析).
    Line-buffered stdout + 每次 flush, 避免 Windows 块缓冲导致 watchdog 误判.
    """
    rec = {'type': event_type, 'ts': time.time(), **fields}
    try:
        sys.stdout.write(json.dumps(rec, ensure_ascii=False) + '\n')
        sys.stdout.flush()
    except Exception:
        pass  # stdout 写失败不影响主流程


class CrossDramaError(RuntimeError):
    """BIND 携带的 series_id 与 state 锁定值不一致. 串剧防护, 不可恢复."""


def safe_unload_session(script, session, timeout: float = 3.0) -> None:
    """script.unload() + session.detach() 带超时包装.
    frida 16.5.9 在 App 主线程忙时 unload 会卡 (见 pitfalls 坑 12).
    超时后 emit cleanup_timeout 事件供 Agent 升级处理 (Codex S4).
    """
    done = threading.Event()
    errors = {'unload': None, 'detach': None}

    def _run():
        try:
            script.unload()
        except Exception as e:
            errors['unload'] = repr(e)
        try:
            session.detach()
        except Exception as e:
            errors['detach'] = repr(e)
        done.set()

    t = threading.Thread(target=_run, daemon=True, name='frida-cleanup')
    t.start()
    if not done.wait(timeout):
        logger.warning(f"script.unload/detach 超时 {timeout}s, 放弃")
        # Codex S4: emit 机器可读事件, Agent 侧可升级到强力 cleanup
        emit('cleanup_timeout',
             timeout=timeout,
             detail='script.unload/detach blocked, potential frida session leak')


class Heartbeat:
    """后台心跳线程, 每 10s emit phase_alive, Agent watchdog 用.
    线程安全, stop() 幂等."""

    def __init__(self, phase: str, interval: float = 10.0):
        self.phase = phase
        self.interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True,
                                         name=f"hb-{self.phase}")
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            emit('phase_alive', phase=self.phase)

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *a):
        self.stop()

# =============== HOOK JS ===============
# 合并: v4 的 kid/spadea/key capture + SaasVideoData BIND
HOOK_JS = r"""
var currentSwitchSeq = 0;  // 单调递增,每次 RPC switch_to_ep +1,BIND/cap 消息携带此 seq

Java.perform(function() {
    var F = null;
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
                if (this.lib && this.lib.indexOf('libttmplayer') !== -1)
                    setTimeout(tryInitF, 100);
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
                function fstr(name){
                    try { var f = cls.getDeclaredField(name); f.setAccessible(true);
                          return String(f.get(info) || ''); } catch(e) { return ''; }
                }
                function fint(name){
                    try { var f = cls.getDeclaredField(name); f.setAccessible(true);
                          var v = f.get(info); if (v === null) return 0;
                          try { return v.intValue(); } catch(e) {
                              var nn = parseInt(String(v)); return isNaN(nn)?0:nn; }
                    } catch(e) { return 0; }
                }
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
            if (!kid || !spadea) {
                send({t:'cap_skip', kid:kid, spadea_len: spadea ? spadea.length : 0,
                      n_streams: n, switch_seq: currentSwitchSeq});
                return;
            }
            var key = spadeaToKey(spadea);
            send({t:'cap', kid:kid, spadea:spadea, key:key, streams:streams,
                  ts:Date.now(), switch_seq: currentSwitchSeq});
        } catch(e) { send({t:'err', msg:e.toString()}); }
    }
    TTE.setVideoModel.overloads.forEach(function(ov){
        ov.implementation = function(mm) { handleModel(mm); return ov.call(this, mm); };
    });

    // 诊断 hook: 其他 set*/play*/prepare* 方法, 日志其调用(寻找异常剧的播放路径)
    try {
        var allMethods = TTE.class.getDeclaredMethods();
        var diagKeys = ['setDataSource', 'setDirectURL', 'setDirectUrl',
                        'setLocalURL', 'setVideoID', 'setDirectMediaModelURL',
                        'setSurfaceHolder', '_setVideoModel', 'updateVideoModel'];
        allMethods.forEach(function(jm) {
            var name = String(jm.getName());
            if (diagKeys.indexOf(name) < 0) return;
            try {
                var ovs = TTE[name].overloads;
                ovs.forEach(function(ov) {
                    ov.implementation = function() {
                        var argDesc = [];
                        for (var i = 0; i < arguments.length; i++) {
                            var a = arguments[i];
                            if (a === null) argDesc.push('null');
                            else if (typeof a === 'string') argDesc.push('"'+a.substring(0,60)+'"');
                            else if (typeof a === 'number') argDesc.push(String(a));
                            else {
                                try { argDesc.push(a.getClass ? String(a.getClass().getName()) : typeof a); }
                                catch(e) { argDesc.push('?'); }
                            }
                        }
                        send({t:'diag_call', m: name, args: argDesc,
                              switch_seq: currentSwitchSeq});
                        return ov.apply(this, arguments);
                    };
                });
            } catch(e) {}
        });
        send({t:'diag_hooked'});
    } catch(e) { send({t:'diag_err', err: String(e)}); }

    // 8 秒后枚举已加载的 TTVideoEngine 类及其子类(目标剧加载后再跑)
    setTimeout(function() {
        Java.perform(function() {
            var found = [];
            Java.enumerateLoadedClasses({
                onMatch: function(n) {
                    if (n.indexOf('ttvideoengine') >= 0 && n.indexOf('$') < 0 &&
                        (n.toLowerCase().indexOf('engine') >= 0 || n.toLowerCase().indexOf('player') >= 0)) {
                        found.push(n);
                    }
                },
                onComplete: function() {
                    // dedupe
                    var seen = {};
                    var uniq = [];
                    for (var i = 0; i < found.length; i++) {
                        if (!seen[found[i]]) { seen[found[i]] = 1; uniq.push(found[i]); }
                    }
                    send({t:'engine_classes', classes: uniq});
                }
            });
        });
    }, 8000);
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

    // ========= BIND hook =========
    function bindImpl(orig) {
        return function(data) {
            if (data) try {
                var vid=null, idx=-1, sid=null, name=null, title=null, total=-1;
                try { vid = String(data.getVid()); } catch(e){}
                try { idx = Number(data.getVidIndex()); } catch(e){}
                try { sid = String(data.getSeriesId()); } catch(e){}
                try { name = String(data.getSeriesName()); } catch(e){}
                try { title = String(data.getTitle()); } catch(e){}
                try { total = Number(data.getEpisodesCount()); } catch(e){}
                send({t:'bind', vid:vid, idx:idx, series_id:sid,
                      name:name, title:title, total_eps:total,
                      ts:Date.now(), switch_seq: currentSwitchSeq});
            } catch(e) { send({t:'bind_err', err:String(e)}); }
            return orig.call(this, data);
        };
    }
    var paramTypes = ['com.dragon.read.component.shortvideo.data.saas.video.SaasVideoData'];
    function tryHookBind(cls) {
        try {
            var C = Java.use(cls);
            var ov = C.j2.overload.apply(C.j2, paramTypes);
            ov.implementation = bindImpl(ov);
            return true;
        } catch(e) { return false; }
    }
    var okA = tryHookBind('com.dragon.read.component.shortvideo.impl.v2.view.holder.a');
    var okZ = tryHookBind('com.dragon.read.component.shortvideo.impl.v2.view.holder.z');
    send({t:'bind_hooked', a:okA, z:okZ});

    // SaasVideoData setter hooks — 除了 setSeriesName,也抓 setSeriesId/setEpisodesCount,
    // 用于搜索阶段构建 (series_id, name, total) 目录
    try {
        var Data = Java.use('com.dragon.read.component.shortvideo.data.saas.video.SaasVideoData');
        function sendCatalog(inst, source) {
            try {
                var sid = null, name = null, total = -1, vid = null, idx = -1;
                try { sid = String(inst.getSeriesId()); } catch(e){}
                try { name = String(inst.getSeriesName()); } catch(e){}
                try { total = Number(inst.getEpisodesCount()); } catch(e){}
                try { vid = String(inst.getVid()); } catch(e){}
                try { idx = Number(inst.getVidIndex()); } catch(e){}
                send({t:'catalog', src: source,
                      series_id: sid, name: name, total_eps: total,
                      vid: vid, idx: idx, ts: Date.now()});
            } catch(e) {}
        }
        Data.setSeriesName.overload('java.lang.String').implementation = function(v) {
            var r = this.setSeriesName(v);
            sendCatalog(this, 'setSeriesName=' + (v || ''));
            return r;
        };
        try {
            Data.setSeriesId.overload('java.lang.String').implementation = function(v) {
                var r = this.setSeriesId(v);
                sendCatalog(this, 'setSeriesId=' + (v || ''));
                return r;
            };
        } catch(e) { send({t:'hook_err', cls:'SaasVideoData', method:'setSeriesId', err:String(e)}); }
        try {
            Data.setEpisodesCount.overload('long').implementation = function(v) {
                var r = this.setEpisodesCount(v);
                sendCatalog(this, 'setEpisodesCount=' + v);
                return r;
            };
        } catch(e) { send({t:'hook_err', cls:'SaasVideoData', method:'setEpisodesCount', err:String(e)}); }
    } catch(e) { send({t:'hook_err', cls:'SaasVideoData', method:'__outer', err:String(e)}); }

    // ========= 搜索 API 拦截 (SsHttpCall tee) =========
    try {
        var Call = Java.use('com.bytedance.retrofit2.SsHttpCall');
        var Request = Java.use('com.bytedance.retrofit2.client.Request');
        var BodyCls = Java.use('com.bytedance.frameworks.baselib.network.http.impl.a$a');
        Call.execute.implementation = function() {
            var req = null, url = '';
            try {
                var f = Call.class.getDeclaredField('originalRequest');
                f.setAccessible(true);
                req = f.get(this);
                if (req) url = String(Request.getUrl.call(req) || '');
            } catch(e){}
            var resp = this.execute();
            // 宽松匹配: URL 含 /search/ 或 /search? 即可
            var lu = url.toLowerCase();
            if (lu.indexOf('/search/') < 0 && lu.indexOf('/search?') < 0) {
                return resp;
            }
            try {
                var bodyObj = resp.body();
                if (!bodyObj) return resp;
                // 仅处理原始字节 body; 非 a$a 类型静默跳过
                var bodyClsName = bodyObj.getClass().getName();
                if (bodyClsName.indexOf('http.impl.a$a') < 0) {
                    return resp;
                }
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
                    if (total > 4 * 1024 * 1024) break;
                }
                var bytes = buf.toByteArray();
                var Str = Java.use('java.lang.String');
                var text = String(Str.$new(bytes, 'UTF-8'));
                var CHUNK = 60000;
                var id = Math.floor(Math.random() * 1e9);
                var parts = Math.ceil(text.length / CHUNK);
                for (var k = 0; k < parts; k++) {
                    send({t:'search_body', id:id, idx:k, total:parts,
                          url: url.substring(0, 300), len: total,
                          body: text.substring(k*CHUNK, (k+1)*CHUNK)});
                }
            } catch(e) {
                send({t:'search_read_err', url: url.substring(0,120), err: String(e)});
            }
            return resp;
        };
        send({t:'search_hooked'});
    } catch(e) {
        send({t:'search_hook_err', err: String(e)});
    }

    send({t:'ready', msg:'v5 hooks ready'});
});

// ===== RPC: switchToEpisode — 直接 Java invoke (同步等待 Main thread 完成) =====
rpc.exports = {
    getDeviceTime: function() {
        // 给 Python 校准时间基准,但当前主要靠 switch_seq 不是 ts
        return Date.now();
    },

    switchToEp: function(seriesId, targetVid, pos, seq) {
        currentSwitchSeq = seq;  // 后续 BIND/cap 会带上这个 seq
        return new Promise(function(resolve) {
            Java.perform(function() {
                var required = [];

                // 1) 拿 Context - 优先 ShortSeriesActivity, 否则未 paused, 否则第一个
                var ctx = null;
                var ctxName = null;
                var PREFERRED = 'com.dragon.read.component.shortvideo.impl.ShortSeriesActivity';
                try {
                    var ActivityThread = Java.use('android.app.ActivityThread');
                    var at = ActivityThread.currentActivityThread();
                    var mActs = at.mActivities.value;
                    var ArrayMap = Java.use('android.util.ArrayMap');
                    var map = Java.cast(mActs, ArrayMap);
                    var vals = map.values();
                    var it = vals.iterator();
                    var preferred = null, resumed = null, anyAct = null;
                    while (it.hasNext()) {
                        var rec = it.next();
                        var recCls = rec.getClass();
                        try {
                            var actF = recCls.getDeclaredField('activity');
                            actF.setAccessible(true);
                            var act = actF.get(rec);
                            if (act === null) continue;
                            var actCls = String(act.getClass().getName());
                            var paused = true;
                            try {
                                var pausedF = recCls.getDeclaredField('paused');
                                pausedF.setAccessible(true);
                                var v = pausedF.get(rec);
                                paused = v && v.booleanValue ? v.booleanValue() : Boolean(v);
                            } catch(e){}
                            if (anyAct === null) anyAct = act;
                            // 宽匹配: ShortSeriesActivity 或其子类
                            if (actCls.indexOf('ShortSeriesActivity') >= 0 ||
                                actCls.indexOf('.shortvideo.') >= 0) {
                                if (!paused) { preferred = act; break; }
                                if (preferred === null) preferred = act;
                            } else if (!paused && resumed === null) {
                                resumed = act;
                            }
                        } catch(e){}
                    }
                    ctx = preferred || resumed || anyAct;
                    if (ctx === null) { try { ctx = at.getApplication(); } catch(e){} }
                    if (ctx !== null) ctxName = String(ctx.getClass().getName());
                } catch(e) {
                    resolve({ok:false, err:'find_ctx: ' + String(e), seq: seq});
                    return;
                }
                if (ctx === null) { resolve({ok:false, err:'no_ctx', seq: seq}); return; }

                // 2) 构造 ShortSeriesLaunchArgs,关键 setter 校验
                var Args, args;
                try {
                    Args = Java.use('com.dragon.read.component.shortvideo.api.model.ShortSeriesLaunchArgs');
                    args = Args.$new();
                } catch(e) {
                    resolve({ok:false, err:'Args.$new: ' + String(e), seq: seq});
                    return;
                }

                function mustSet(name, fn) {
                    try { fn(); } catch(e) { required.push(name + ':' + String(e)); }
                }
                function softSet(fn) { try { fn(); } catch(e){} }

                mustSet('setContext',  function(){ args.setContext(ctx); });
                mustSet('setSeriesId', function(){ args.setSeriesId(String(seriesId)); });
                if (targetVid) {
                    softSet(function(){ args.setTargetVideoId(String(targetVid)); });
                    softSet(function(){ args.setFirstVid(String(targetVid)); });
                    softSet(function(){ args.setVidForce(String(targetVid)); });
                }
                if (pos !== null && pos !== undefined && pos >= 0) {
                    softSet(function(){ args.setVideoClickPos(pos); });
                    softSet(function(){ args.setVideoForcePos(pos); });
                }
                softSet(function(){ args.setClearTop(true); });
                softSet(function(){ args.setStartActivityWithoutAnyAnim(true); });
                softSet(function(){ args.setEnableEnterAlphaAnimation(false); });
                softSet(function(){ args.setEnableStartAnimation(false); });

                if (required.length > 0) {
                    resolve({ok:false, err:'missing setter:' + required.join(';'), seq: seq});
                    return;
                }

                // 3) 拿 IMPL
                var impl;
                try {
                    var Api = Java.use('com.dragon.read.component.shortvideo.api.NsShortVideoApi');
                    impl = Api.IMPL.value;
                } catch(e) {
                    resolve({ok:false, err:'Api.IMPL: ' + String(e), seq: seq});
                    return;
                }
                if (impl === null) { resolve({ok:false, err:'IMPL null', seq: seq}); return; }

                // 4) 在 main thread 执行 + resolve 放 callback 内部.
                //    这样 Python exports_sync 会等主线程真正完成才返回,天然节流
                //    (防 scheduleOnMainThread callback 队列堆积 → ANR).
                //    Python 侧用 rpc_switch (threading + join timeout=15s) 兜底异常卡死.
                Java.scheduleOnMainThread(function() {
                    try {
                        impl.openShortSeriesActivity(args);
                        resolve({ok: true, ctx: ctxName, seq: seq});
                    } catch(e) {
                        resolve({ok: false, err: 'open: ' + String(e), ctx: ctxName, seq: seq});
                    }
                });
            });
        });
    }
};
"""


# =============== RPC timeout wrapper ===============
def rpc_switch(script, series_id, target_vid, pos, seq, timeout: float = 15.0) -> dict:
    """调用 switch_to_ep 带硬超时.
    frida exports_sync 在 transport/main-thread 卡住时可能永久阻塞.
    这里用后台线程跑,超时后假设已 fire(JS 是 fire-and-forget),继续去等 BIND/CAP.
    """
    result = {'_done': False, 'r': None, 'err': None}
    def _call():
        try:
            result['r'] = script.exports_sync.switch_to_ep(series_id, target_vid, pos, seq)
        except Exception as e:
            result['err'] = str(e)
        finally:
            result['_done'] = True
    t = threading.Thread(target=_call, daemon=True)
    t.start()
    t.join(timeout)
    if not result['_done']:
        return {'ok': True, 'scheduled': True, 'timeout': True, 'ctx': 'unknown'}
    if result['err'] is not None:
        return {'ok': False, 'err': result['err']}
    return result['r']


# =============== State ===============
@dataclass
class Capture:
    kid: str
    spadea: str
    key: str
    streams: list = field(default_factory=list)
    ts: float = 0.0
    switch_seq: int = 0   # 事件携带的切集序号(JS 侧 currentSwitchSeq 值)

    def best_stream(self, max_short_side: int = 1080) -> dict | None:
        pool = [s for s in self.streams if s.get('main_url')]
        if not pool:
            return None
        def short(s): return min(s['vheight'], s['vwidth']) if s.get('vwidth') else s.get('vheight', 0)
        cand = [s for s in pool if short(s) <= max_short_side]
        if cand:
            pool = cand
        return max(pool, key=lambda s: s.get('bitrate', 0))


@dataclass
class Bind:
    idx: int
    vid: str | None
    series_id: str | None
    name: str | None
    title: str | None
    total_eps: int
    ts: float
    switch_seq: int = 0


class State:
    def __init__(self):
        self.lock = threading.Lock()
        self.cluster: str | None = None
        self.by_kid: dict[str, Capture] = {}
        # 队列:按到达顺序保留所有 cap/bind,按 switch_seq + idx 校验取
        self.cap_queue: list[Capture] = []
        self.bind_queue: list[Bind] = []
        # series_name 由 setSeriesName 异步回填,按 (vid → name) 缓存
        self.name_by_vid: dict[str, str] = {}
        self.target_series_id: str | None = None
        self.target_series_name: str | None = None
        self.total_eps: int = 0
        # 单调递增的切集序号, Python 侧权威 (跨 JS/Python 时钟)
        self._switch_seq_counter: int = 0
        # 搜索阶段建的 {series_id → {name, total_eps, first_vid}} 目录
        self.drama_catalog: dict[str, dict] = {}

    def next_switch_seq(self) -> int:
        """切集前调用, 分配一个 seq.
        Python 持有权威序号,不依赖 JS Date.now()."""
        with self.lock:
            self._switch_seq_counter += 1
            return self._switch_seq_counter

    def ingest_cap(self, p: dict) -> bool:
        kid = p.get('kid') or ''
        if len(kid) != 32 or not p.get('spadea'):
            return False
        # cluster 记录但不过滤: switch_seq 已足够区分"本次切集的 cap";
        # 不同剧有不同 cluster,早期锁定会漏掉目标剧 cap
        cid = kid[8:12]
        if self.cluster is None:
            self.cluster = cid
            logger.info(f"[集群] 记录首 ID={cid} (不过滤, 仅供参考)")
        cap = Capture(
            kid=kid, spadea=p['spadea'], key=p.get('key') or '',
            streams=p.get('streams') or [],
            ts=(p.get('ts') or 0) / 1000.0,
            switch_seq=int(p.get('switch_seq') or 0),
        )
        with self.lock:
            self.by_kid[kid] = cap
            self.cap_queue.append(cap)
            # 防爆: 保留最近 200 条
            if len(self.cap_queue) > 200:
                self.cap_queue = self.cap_queue[-200:]
        return True

    def ingest_bind(self, p: dict) -> Bind | None:
        idx = p.get('idx', -1)
        vid = p.get('vid')
        if not vid or idx < 1:
            return None
        name = p.get('name')
        if name in ('null', 'None', ''):
            name = None
        b = Bind(
            idx=idx, vid=vid, series_id=p.get('series_id'),
            name=name, title=p.get('title'),
            total_eps=p.get('total_eps', -1) or -1,
            ts=(p.get('ts') or 0) / 1000.0,
            switch_seq=int(p.get('switch_seq') or 0),
        )
        with self.lock:
            # 串剧防护 (design doc v4 §6): 锁定 series_id 后, 若 BIND 携带
            # 不同 series_id 且 idx > 1, 说明 App 跳到了别的剧 (账号推荐/错点等).
            # 立即 emit cross_drama 事件 + raise CrossDramaError, Agent 转 ABORTED.
            if (self.target_series_id
                    and b.series_id
                    and b.series_id != self.target_series_id
                    and b.idx > 1):
                emit('cross_drama',
                     expected=self.target_series_id,
                     actual=b.series_id,
                     ep=b.idx, vid=vid)
                raise CrossDramaError(
                    f"locked={self.target_series_id} but BIND has "
                    f"series_id={b.series_id} (idx={b.idx})"
                )
            if not b.name and vid in self.name_by_vid:
                b.name = self.name_by_vid[vid]
            self.bind_queue.append(b)
            if len(self.bind_queue) > 500:
                self.bind_queue = self.bind_queue[-500:]
            if self.target_series_id and b.series_id == self.target_series_id:
                if b.total_eps > 0:
                    self.total_eps = max(self.total_eps, b.total_eps)
        return b

    def ingest_name(self, p: dict):
        vid = p.get('vid')
        name = p.get('name')
        if vid and name:
            with self.lock:
                self.name_by_vid[vid] = str(name)

    def ingest_search_body(self, url: str, text: str):
        """从搜索 API 响应 JSON 里结构化抽取 book_id/series_id/book_name/serial_count."""
        import json as _json
        try:
            doc = _json.loads(text)
        except Exception as e:
            # dump 错误点附近 200 字节便于 debug
            err_col = getattr(e, 'colno', 0) or getattr(e, 'pos', 0)
            ctx_start = max(0, err_col - 100)
            ctx_end = min(len(text), err_col + 100)
            logger.warning(f"[ingest_search_body] JSON parse failed: {e}, "
                           f"len={len(text)}, near err[{ctx_start}:{ctx_end}]={text[ctx_start:ctx_end]!r}")
            # 保存完整 body 到 tmp
            try:
                import pathlib
                pathlib.Path('d:/tmp/search_body_bad.json').write_text(text, encoding='utf-8')
                logger.info("  dumped to d:/tmp/search_body_bad.json")
            except Exception:
                pass
            return
        NAME_KEYS = {'book_name', 'series_name', 'title'}  # 不含 'name' 避免误匹配 tab
        TOTAL_KEYS = {'episode_cnt', 'episodes_count', 'serial_count',
                      'tomato_book_serial_count'}

        def visit(node):
            if isinstance(node, dict):
                sid = node.get('series_id')
                if isinstance(sid, str) and sid.isdigit():
                    # 在同一 dict 内找 name + total
                    name = None
                    for k in ('book_name', 'series_name', 'title'):
                        v = node.get(k)
                        if isinstance(v, str) and v.strip():
                            name = v.strip()
                            break
                    total = -1
                    for k in TOTAL_KEYS:
                        v = node.get(k)
                        if isinstance(v, (int, str)):
                            try:
                                total = int(v); break
                            except ValueError:
                                pass
                    if name:
                        with self.lock:
                            fresh = sid not in self.drama_catalog
                            entry = self.drama_catalog.setdefault(sid, {
                                'name': None, 'total_eps': -1,
                                'first_vid': None, 'vids': set()
                            })
                            if entry.get('name') is None:
                                entry['name'] = name
                            if total > 0 and entry.get('total_eps', -1) <= 0:
                                entry['total_eps'] = total
                        if fresh:
                            logger.info(f"  +catalog  {sid}  name={name!r}  total={total}")
                for v in node.values():
                    visit(v)
            elif isinstance(node, list):
                for it in node:
                    visit(it)

        visit(doc)

    def ingest_catalog(self, p: dict):
        """聚合 catalog 消息到 drama_catalog: series_id → {name, total_eps, ...}."""
        sid = p.get('series_id')
        if not sid or sid in ('null', 'None'):
            return
        with self.lock:
            entry = self.drama_catalog.setdefault(sid, {
                'name': None, 'total_eps': -1, 'first_vid': None, 'vids': set()
            })
            name = p.get('name')
            if name and name not in ('null', 'None'):
                entry['name'] = name
            total = p.get('total_eps', -1) or -1
            if total > 0:
                entry['total_eps'] = total
            vid = p.get('vid')
            if vid and vid not in ('null', 'None'):
                entry['vids'].add(vid)
                if entry['first_vid'] is None:
                    entry['first_vid'] = vid

    def find_drama_by_name(self, drama: str,
                           expected_total: int | None = None) -> list[tuple[str, dict]]:
        """在 drama_catalog 中找剧名匹配 + (可选)总集数匹配的. 返回 [(series_id, info)]."""
        out = []
        with self.lock:
            for sid, info in self.drama_catalog.items():
                nm = info.get('name') or ''
                if not nm:
                    continue  # 无 name 的剧跳过,不能判断是否匹配
                # 严格: drama 是 nm 的子串, 或反之(兼容截断/后缀)
                if drama not in nm and nm not in drama:
                    continue
                if expected_total is not None and info.get('total_eps', -1) != expected_total:
                    continue
                out.append((sid, {**info, 'vids': list(info.get('vids', []))[:5]}))
        return out

    def wait_bind_for_series_seq(self, series_id: str, target_idx: int,
                                  min_seq: int, timeout: float = 6.0) -> Bind | None:
        """等第一个 (series_id, idx, switch_seq >= min_seq) 的 BIND."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self.lock:
                for b in reversed(self.bind_queue):
                    if (b.series_id == series_id and b.idx == target_idx
                            and b.switch_seq >= min_seq):
                        return b
            time.sleep(0.1)
        return None

    def wait_first_valid_bind(self, min_total_eps: int = 3,
                              timeout: float = 15.0,
                              min_seq: int = 0) -> Bind | None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self.lock:
                for b in reversed(self.bind_queue):
                    if (b.series_id and b.total_eps >= min_total_eps
                            and b.idx >= 1 and b.switch_seq >= min_seq):
                        return b
            time.sleep(0.2)
        return None

    def wait_cap_for_seq(self, min_seq: int, timeout: float = 8.0,
                         settle: float = 0.0,
                         exclude_kids: set[str] | None = None) -> Capture | None:
        """等 switch_seq >= min_seq 的 cap. 返回 **首个** 匹配项.
        RPC 模式下,切集后 setVideoModel fire 顺序是:
          fire 1 = 当前请求集 (N) → 这就是我们要的
          fire 2+ = App 预加载的下一集 (N+1, N+2) ← 要忽略,否则 off-by-one
        若 timeout 内没等到匹配 seq,fallback 到最新未使用 cap.
        """
        exclude_kids = exclude_kids or set()
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self.lock:
                for c in self.cap_queue:  # 顺序(首个优先)
                    if c.switch_seq >= min_seq and c.kid not in exclude_kids:
                        return c
            time.sleep(0.05)
        # Fallback: seq=0 是启动 feed(可能是任意其他剧),必排除.
        # 允许 seq < min_seq 但 >= 1 的 cap (可能是目标剧 preload 早于本次 RPC)
        with self.lock:
            for c in reversed(self.cap_queue):
                if c.switch_seq >= 1 and c.kid not in exclude_kids:
                    return c
        return None


# =============== frida setup ===============
def _adb_pidof() -> int | None:
    env = {**os.environ, "MSYS_NO_PATHCONV": "1"}
    r = subprocess.run(["adb", "shell", "pidof", APP_PACKAGE],
                       capture_output=True, text=True, env=env)
    pids = [int(x) for x in (r.stdout or "").strip().split() if x.isdigit()]
    return min(pids) if pids else None


def setup_frida(state: State, attach_running: bool = False):
    device = frida.get_usb_device(timeout=10)
    env = {**os.environ, "MSYS_NO_PATHCONV": "1"}
    if attach_running:
        pid = _adb_pidof()
        if not pid:
            raise RuntimeError("App 未运行")
        session = device.attach(pid)
    else:
        subprocess.run(["adb", "shell", "am", "force-stop", APP_PACKAGE],
                       capture_output=True, check=False, env=env)
        time.sleep(1)
        pid = device.spawn([APP_PACKAGE])
        session = device.attach(pid)

    script = session.create_script(HOOK_JS)

    # 搜索响应 body 分片缓冲 {id → {idx: body_str, total: N}}
    search_buf: dict[int, dict] = {}

    def on_msg(msg, _data):
        if msg.get('type') != 'send':
            if msg.get('type') == 'error':
                logger.warning(f"[JS ERR] {msg.get('description','')[:300]}")
            return
        p = msg['payload']
        t = p.get('t')
        if t == 'cap':
            ok = state.ingest_cap(p)
            if ok:
                c = state.cap_queue[-1]
                logger.info(f"[CAP] kid={c.kid[:12]}... seq={c.switch_seq} ts={c.ts:.3f}")
        elif t == 'cap_skip':
            logger.info(f"[CAP_SKIP] kid={(p.get('kid') or '')[:12]} "
                        f"spadea_len={p.get('spadea_len')} streams={p.get('n_streams')} "
                        f"seq={p.get('switch_seq')}")
        elif t == 'diag_call':
            logger.info(f"[DIAG] {p['m']}({', '.join(p['args'][:3])}) seq={p.get('switch_seq')}")
        elif t == 'diag_hooked':
            logger.info("[Hook] diag player methods hooked")
        elif t == 'diag_err':
            logger.warning(f"[Hook] diag err: {p.get('err')}")
        elif t == 'engine_classes':
            logger.info(f"[engine_classes] {len(p['classes'])} found:")
            for c in p['classes']:
                logger.info(f"    {c}")
        elif t == 'bind':
            b = state.ingest_bind(p)
            if b:
                logger.info(f"[BIND] ep={b.idx} vid={b.vid[:14]}... seq={b.switch_seq} "
                             f"total={b.total_eps} name={b.name or '?'}")
                state.ingest_catalog(p)
        elif t == 'catalog':
            state.ingest_catalog(p)
        elif t == 'name_set':
            state.ingest_name(p)
        elif t == 'ready':
            logger.info(f"[Hook] {p.get('msg', '')}")
        elif t == 'bind_hooked':
            logger.info(f"[Hook] bind a={p['a']} z={p['z']}")
        elif t == 'log':
            logger.info(f"[Hook] {p.get('msg')}")
        elif t == 'err':
            logger.warning(f"[Hook err] {p.get('msg')}")
        elif t == 'hook_err':
            logger.debug(f"[hook_err] {p}")
        elif t == 'search_hooked':
            logger.info("[Hook] search API intercepted")
        elif t == 'search_hook_err':
            logger.warning(f"[Hook] search hook err: {p.get('err')}")
        elif t == 'search_body':
            # 分片缓冲合并
            sid = int(p['id'])
            buf = search_buf.setdefault(sid, {'parts': {}, 'total': p['total'], 'url': p['url']})
            buf['parts'][int(p['idx'])] = p['body']
            if len(buf['parts']) == buf['total']:
                full = ''.join(buf['parts'][i] for i in sorted(buf['parts'].keys()))
                del search_buf[sid]
                logger.info(f"[search body] url={buf['url'][:80]} len={len(full)}")
                state.ingest_search_body(buf['url'], full)
                logger.info(f"[catalog] 现有 {len(state.drama_catalog)} 条")
        elif t == 'search_read_err':
            logger.warning(f"[Hook] search body read err: {p.get('err')}")

    script.on('message', on_msg)
    script.load()

    if not attach_running:
        device.resume(pid)
        logger.info(f"[Frida] spawned pid={pid}")
        time.sleep(10)
    else:
        logger.info(f"[Frida] attached pid={pid}")
        time.sleep(2)
    return session, script, pid


# =============== UI nav ===============
def _parse_bounds(s: str):
    m = re.match(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]', s or '')
    return tuple(int(x) for x in m.groups()) if m else None


def navigate_to_drama(drama: str, state: State, script, timeout: float = 30.0,
                       expected_total: int | None = None,
                       max_candidates: int = 6) -> Bind | None:
    """通过搜索进入目标剧,返回首个有效 BIND.
    expected_total: 若非 None,校验 b0.total_eps 必须匹配,否则 press_back + 试下一个候选.
    max_candidates: 最多尝试几个同名候选.
    """
    env = {**os.environ, "MSYS_NO_PATHCONV": "1"}

    # deeplink 到搜索页 (deeplink 的 query 参数不可靠,只作用于打开页面;之后用 ADBKeyboard 重填)
    url = f"dragon8662://search"
    subprocess.run(["adb", "shell", "am", "start", "-p", APP_PACKAGE,
                    "-a", "android.intent.action.VIEW", "-d", url],
                   capture_output=True, env=env)
    logger.info(f"[nav] deeplink {url}")
    time.sleep(3)

    # 找 EditText, tap focus, 清空, 用 ADBKeyboard 输入
    def _find_search_edit() -> tuple[int, int] | None:
        xml = read_ui_xml_from_device()
        if not xml:
            return None
        try:
            root = ET.fromstring(xml)
        except ET.ParseError:
            return None
        for n in root.iter('node'):
            if (n.get('class') or '').endswith('EditText'):
                b = _parse_bounds(n.get('bounds') or '')
                if b and b[1] < 300:
                    return ((b[0]+b[2])//2, (b[1]+b[3])//2)
        return None

    edit_xy = None
    for _ in range(5):
        edit_xy = _find_search_edit()
        if edit_xy:
            break
        time.sleep(1)
    if not edit_xy:
        logger.warning("[nav] 未找到搜索 EditText")
        return None
    logger.info(f"[nav] tap EditText @ {edit_xy}")
    run_adb(["shell", "input", "tap", str(edit_xy[0]), str(edit_xy[1])])
    time.sleep(0.8)
    # 全选 + 删除
    run_adb(["shell", "input", "keyevent", "KEYCODE_MOVE_END"])
    for _ in range(30):
        run_adb(["shell", "input", "keyevent", "KEYCODE_DEL"])
    time.sleep(0.5)
    # ADBKeyboard 输入
    logger.info(f"[nav] ADBKeyboard input: {drama}")
    subprocess.run(
        ["adb", "shell", "am", "broadcast", "-a", "ADB_INPUT_TEXT",
         "--es", "msg", drama],
        capture_output=True, env=env, check=False,
    )
    time.sleep(1.5)
    # 收起键盘 (ADBKeyboard 可能留键盘覆盖"搜索"按钮)
    run_adb(["shell", "input", "keyevent", "KEYCODE_BACK"])
    time.sleep(0.8)

    # 点"搜索"按钮触发搜索(找 text="搜索" 的 TextView)
    def _tap_search_button() -> bool:
        xml = read_ui_xml_from_device()
        if not xml:
            return False
        try:
            root = ET.fromstring(xml)
        except ET.ParseError:
            return False
        for n in root.iter('node'):
            if (n.get('text') or '').strip() == '搜索':
                b = _parse_bounds(n.get('bounds') or '')
                # 搜索按钮在顶部(y<300)
                if b and b[1] < 300:
                    cx, cy = (b[0]+b[2])//2, (b[1]+b[3])//2
                    run_adb(["shell", "input", "tap", str(cx), str(cy)])
                    logger.info(f"[nav] tap '搜索' button @ ({cx},{cy})")
                    return True
        return False

    if not _tap_search_button():
        logger.info("[nav] 未找到搜索按钮, 用 Enter 触发")
        run_adb(["shell", "input", "keyevent", "KEYCODE_ENTER"])

    # 等搜索结果渲染 + 滚动触发更多结果的 SaasVideoData 创建
    # 每个结果渲染时会 setSeriesId/setSeriesName/setEpisodesCount,被我们的 catalog hook 捕获
    logger.info("[nav] 等搜索结果渲染 + catalog 积累...")
    time.sleep(5)
    for _ in range(4):
        run_adb(["shell", "input", "swipe", "540", "1600", "540", "800", "400"])
        time.sleep(1.5)

    # 从 drama_catalog 挑目标剧
    matches = state.find_drama_by_name(drama, expected_total=expected_total)
    logger.info(f"[nav] drama_catalog 共 {len(state.drama_catalog)} 条, "
                f"匹配 '{drama}' + total={expected_total}: {len(matches)}")
    if not matches:
        # 放宽: 只按名字
        if expected_total is not None:
            matches = state.find_drama_by_name(drama)
            logger.info(f"[nav] 放宽不查 total, 匹配 '{drama}': {len(matches)}")
    if not matches:
        logger.warning(f"[nav] catalog 里无 '{drama}'. catalog 前 10 条:")
        for sid, info in list(state.drama_catalog.items())[:10]:
            logger.info(f"  {sid[:20]}  name={info.get('name')!r}  total={info.get('total_eps')}")
        return None

    # 打印所有匹配
    for sid, info in matches:
        logger.info(f"[nav match] series_id={sid}  name={info['name']!r}  "
                    f"total={info['total_eps']}  first_vid={info.get('first_vid')}")

    # 若有多个,优先 total 匹配; 否则取第一个
    picks = [m for m in matches if expected_total is None or m[1].get('total_eps') == expected_total]
    if not picks:
        picks = matches
    chosen_sid, chosen_info = picks[0]
    state.target_series_id = chosen_sid
    state.target_series_name = chosen_info.get('name') or drama
    state.total_eps = chosen_info.get('total_eps', 0)
    logger.info(f"[nav] 选定 series_id={chosen_sid} name={chosen_info['name']!r}")

    # 通过 RPC 直接进目标剧 ep1
    nav_seq = state.next_switch_seq()
    try:
        r = script.exports_sync.switch_to_ep(chosen_sid, chosen_info.get('first_vid'), 0, nav_seq)
        logger.info(f"[nav] rpc ok={r.get('ok')} ctx={r.get('ctx')} seq={nav_seq}")
        if not r.get('ok'):
            logger.warning(f"[nav] rpc err: {r.get('err')}")
            return None
    except Exception as e:
        logger.warning(f"[nav] RPC err: {e}")
        return None

    # 等 ep=1 (或最小 idx) BIND 确认
    deadline = time.time() + timeout
    while time.time() < deadline:
        b = state.wait_first_valid_bind(min_total_eps=1, timeout=1.0, min_seq=nav_seq)
        if b and b.series_id == chosen_sid:
            logger.info(f"[nav] confirm BIND: ep={b.idx} series_id={b.series_id} "
                        f"total={b.total_eps}")
            return b
    logger.warning("[nav] 切目标剧后 BIND 未到")
    return None


def swipe_to_next() -> None:
    run_adb(["shell", "input", "swipe", "540", "1400", "540", "400", "280"])


def swipe_to_prev() -> None:
    run_adb(["shell", "input", "swipe", "540", "400", "540", "1400", "280"])


# =============== Download ===============
def download_and_decrypt(cap: Capture, ep: int, out_dir: Path,
                         drama: str, max_short_side: int = 1080) -> Path | None:
    """下载 + 解密 + 原子落地.
    返回 final_path (Path) 成功 / None 失败.

    design doc v4 §3.5 严格提交顺序 (此函数负责 1-5 步):
      1. 下载 CDN → 内存 bytearray
      2. AES-CTR 解密 + fix_metadata
      3. 写 videos/<drama>/.tmp/ep_NN.decrypted
      4. fsync 解密文件 fd
      5. 原子 rename → videos/<drama>/episode_NNN_<kid8>.mp4
    manifest append + fsync (步 6-7) 由调用方处理, emit ep_ok (步 8) 最后发.
    """
    import requests
    stream = cap.best_stream(max_short_side)
    if not stream:
        logger.warning(f"[ep{ep}] 无可用 stream")
        return None
    if not cap.key or len(cap.key) != 32:
        logger.warning(f"[ep{ep}] key 缺失/格式错: {cap.key!r}")
        return None
    urls = [stream['main_url']] + ([stream['backup_url']] if stream.get('backup_url') else [])
    ep_out = out_dir / f"episode_{ep:03d}_{cap.kid[:8]}.mp4"
    tmp_dir = out_dir / ".tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp = tmp_dir / f"ep_{ep:03d}.decrypted"
    last_err = None
    for url in urls:
        try:
            logger.info(f"[ep{ep}] 下载 {url[:80]}")
            r = requests.get(url, headers={"User-Agent": "AVDML_2.1.230.181-novel_ANDROID"},
                             timeout=120)
            r.raise_for_status()
            data = bytearray(r.content)
            size_mb = len(data) / (1 << 20)
            n = decrypt_mp4(data, bytes.fromhex(cap.key))
            fix_metadata(data)
            logger.info(f"[ep{ep}] {size_mb:.1f}MB h={stream.get('vheight', 0)} "
                        f"bt={stream.get('bitrate', 0)} samples={n}")
            # 严格提交顺序: 写 .tmp → fsync → atomic rename
            with open(tmp, 'wb') as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(str(tmp), str(ep_out))
            logger.success(f"[ep{ep}] ✓ {ep_out.name}")
            return ep_out
        except Exception as e:
            last_err = e
            logger.warning(f"[ep{ep}] {url[:60]}... 失败: {e}")
            if tmp.exists():
                try: tmp.unlink()
                except OSError: pass
    logger.error(f"[ep{ep}] 全部 URL 失败: {last_err}")
    return None


def append_manifest(out_dir: Path, rec: dict) -> bool:
    """Append manifest 一行 + flush + fsync. 返回是否成功.
    design doc v4 §3.5 步 6-7: committed source of truth.
    """
    mfile = out_dir / 'session_manifest.jsonl'
    try:
        with mfile.open('a', encoding='utf-8') as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
        return True
    except OSError as e:
        logger.error(f"manifest append 失败: {e}")
        return False


def read_committed_eps(out_dir: Path) -> dict[int, str]:
    """读 session_manifest.jsonl 返回 {ep -> kid_prefix8}. 末行半写跳过.
    design doc v4 §3.5: manifest 是 committed source of truth.
    """
    mfile = out_dir / 'session_manifest.jsonl'
    if not mfile.exists():
        return {}
    result: dict[int, str] = {}
    try:
        with mfile.open('r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue  # 末行半写等等不完整行
                ep = rec.get('ep')
                kid = rec.get('kid', '') or ''
                if isinstance(ep, int) and ep > 0 and kid:
                    result[ep] = kid[:8]
    except OSError:
        pass
    return result


def cleanup_final_dir_orphans(out_dir: Path, committed: dict[int, str]) -> int:
    """删除 final-dir 里不在 manifest 的孤儿 mp4
    (rename 成功但 manifest 未落盘的崩溃场景, design doc v4 §3.5).
    返回清理的文件数."""
    if not out_dir.exists():
        return 0
    n = 0
    pat = re.compile(r"episode_(\d+)_([0-9a-fA-F]{8})\.mp4$")
    for f in out_dir.glob("episode_*.mp4"):
        m = pat.match(f.name)
        if not m:
            continue
        ep = int(m.group(1))
        kid8 = m.group(2).lower()
        if committed.get(ep) != kid8:
            try:
                f.unlink()
                emit('orphan_removed', file=f.name, ep=ep)
                logger.warning(f"orphan mp4 removed: {f.name} (not in manifest)")
                n += 1
            except OSError:
                pass
    return n


def cleanup_tmp_dir(out_dir: Path) -> int:
    """清 .tmp/ 下所有残留 (crashed 进程留下的半写文件). 返回清理文件数."""
    tmp_dir = out_dir / ".tmp"
    if not tmp_dir.exists():
        return 0
    n = 0
    for f in tmp_dir.glob("*"):
        try:
            f.unlink()
            n += 1
        except OSError:
            pass
    return n


def resolve_start_ep(out_dir: Path, total: int, cli_start: str) -> int:
    """解析 --start 参数.
    - 数字: 原样返回 int
    - 'auto': 扫 manifest + (有 Agent token 时) orphan 清理, 返回最小缺失集 (1..total)
    design doc v4 §3.6.

    Codex S5 single-writer 保护: orphan 清理会删 final-dir 里"无 manifest 记录"的 mp4,
    在并发 writer 场景下有误删风险. 只有 `HONGGUO_AGENT_TOKEN` 环境变量存在时
    (说明被 Agent 编排, Agent 已做 stale-detection 保证单 writer) 才跑 cleanup.
    独立用户手动跑 v5 attach-resume --start auto 时跳过 cleanup + 警告.
    """
    if cli_start != 'auto':
        try:
            return int(cli_start)
        except ValueError:
            logger.warning(f"--start 无效值 {cli_start!r}, 回退到 1")
            return 1

    committed = read_committed_eps(out_dir)
    if os.environ.get('HONGGUO_AGENT_TOKEN'):
        # Agent 编排保证 single-writer, orphan cleanup 安全
        cleanup_final_dir_orphans(out_dir, committed)
        cleanup_tmp_dir(out_dir)
    else:
        logger.warning("[resolve_start] 未检测到 HONGGUO_AGENT_TOKEN, 跳过 orphan cleanup "
                       "(防并发 writer 误删). 如需清理请用 Agent 编排启动.")

    # 校验每个 committed ep 的 mp4 是否存在且 > 1MB, 不存在则视为 missing
    valid: set[int] = set()
    for ep, kid8 in committed.items():
        f = out_dir / f"episode_{ep:03d}_{kid8}.mp4"
        if f.exists() and f.stat().st_size > 1 * 1024 * 1024:
            valid.add(ep)

    if total <= 0:
        # 未知 total, 取 committed 最大集 +1 作为起点 (保守)
        return max(valid) + 1 if valid else 1

    for ep in range(1, total + 1):
        if ep not in valid:
            return ep
    return total + 1  # 全部已下


# =============== Mode 骨架 (design doc v4 §3.1) ===============

def run_spawn_resolve(args):
    """模式 spawn-resolve: 冷启动 + 搜索拿 series_id/total, 立即退出.
    Agent 编排专用 (RESOLVING 阶段). 不下载不进主循环.
    stdout 输出 resolved 事件后 exit 0."""
    raise NotImplementedError("spawn-resolve mode 待 Day 1-2/1-3 实现")


def _adb_pidof_app() -> int | None:
    """轻量版 pidof, 独立于 _adb_pidof. timeout/error 视为不存在."""
    env = {**os.environ, "MSYS_NO_PATHCONV": "1"}
    try:
        r = subprocess.run(["adb", "shell", "pidof", APP_PACKAGE],
                           capture_output=True, text=True, env=env, timeout=5)
    except (subprocess.TimeoutExpired, OSError):
        return None
    pids = [int(x) for x in (r.stdout or "").strip().split() if x.isdigit()]
    return min(pids) if pids else None


def _adb_foreground_activity() -> str:
    """返回当前前台 Activity 全名 (形如 'com.phoenix.read/.impl.ShortSeriesActivity').
    timeout/error 返回空串."""
    env = {**os.environ, "MSYS_NO_PATHCONV": "1"}
    try:
        r = subprocess.run(
            ["adb", "shell", "dumpsys activity activities"],
            capture_output=True, text=True, env=env, timeout=8,
        )
    except (subprocess.TimeoutExpired, OSError):
        return ''
    for line in (r.stdout or "").splitlines():
        # 兼容 mResumedActivity (AOSP) / ResumedActivity (MIUI)
        if 'ResumedActivity' in line and 'ActivityRecord' in line:
            m = re.search(r'\S+/\S+', line)
            if m:
                return m.group(0).rstrip('}')
    return ''


def _read_manifest_first_record(drama_dir: Path) -> dict | None:
    """读 session_manifest.jsonl 第一条有效记录 (用于续跑上下文校验).
    末行半写被忽略."""
    mfile = drama_dir / 'session_manifest.jsonl'
    if not mfile.exists():
        return None
    try:
        with mfile.open('r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    return json.loads(line)
                except json.JSONDecodeError:
                    continue  # 不完整行跳过
    except OSError:
        return None
    return None


def _precheck_attach_resume(args) -> tuple[bool, str, int]:
    """attach-resume 启动前置自检 (design doc v4 §3.1.1 阶段 A).
    返回 (ok, reason, exit_code). ok=True 时 reason='', exit_code=0.
    失败时发 precond_fail 事件并返回对应 exit code.
    """
    # 1. App 进程存活
    app_pid = _adb_pidof_app()
    if not app_pid:
        emit('precond_fail', reason='no_app')
        return False, 'no_app', EXIT_PRECOND_FAIL

    # 2. 前台 Activity 是 ShortSeries*
    fg = _adb_foreground_activity()
    if 'ShortSeries' not in fg:
        emit('precond_fail', reason='wrong_foreground', actual=fg)
        return False, f'wrong_foreground:{fg}', EXIT_PRECOND_FAIL

    # 3. manifest 可读 + 续跑上下文一致
    drama_dir = args.out / args.name
    first_rec = _read_manifest_first_record(drama_dir)
    # manifest 不存在不算错 (首次跑可能没有), 但要求续跑时 --series-id 和 manifest 一致
    if first_rec is not None:
        manifest_sid = str(first_rec.get('series_id') or '')
        if args.series_id and manifest_sid and args.series_id != manifest_sid:
            emit('precond_fail', reason='context_mismatch',
                 expected=args.series_id, actual=manifest_sid)
            return False, 'context_mismatch', EXIT_FATAL
        # 若 args 没传 series_id, 从 manifest 补
        if not args.series_id and manifest_sid:
            args.series_id = manifest_sid
    elif drama_dir.exists():
        # 目录存在但 manifest 无有效记录 (可能损坏)
        mfile = drama_dir / 'session_manifest.jsonl'
        if mfile.exists() and mfile.stat().st_size > 0:
            emit('precond_fail', reason='manifest_corrupt')
            return False, 'manifest_corrupt', EXIT_FATAL

    # 4/5. frida attach + HOOK_JS load 在 setup_frida 里做, 失败由上层捕获
    return True, '', EXIT_OK


def run_attach_resume(args) -> int:
    """模式 attach-resume: attach 到运行中的 App (假设已在 ShortSeries*).
    按 manifest + --start/--end 下载缺口集. Agent 编排专用 (DOWNLOADING 主路径).

    design doc v4 §3.1.1 阶段 A: 启动前置自检. 失败即退出.
    阶段 B (8s 首次 BIND) 由 _download_main 主循环里 wait_first_valid_bind 兜底.
    """
    emit('phase', phase='attach_resume_start')
    ok, reason, code = _precheck_attach_resume(args)
    if not ok:
        emit('phase', phase='attach_resume_abort', reason=reason)
        return code

    # precheck 通过 → 强制 attach + 走 legacy 下载主体
    args.attach = True
    # attach-resume 要求有 series_id (precheck 已从 manifest 补过, 仍空则 fatal)
    if not args.series_id:
        emit('precond_fail', reason='no_series_id_after_manifest_lookup')
        return EXIT_FATAL

    with Heartbeat(phase='downloading'):
        try:
            return _download_main(args)
        except CrossDramaError as e:
            # 已在 ingest_bind 里 emit cross_drama. 此处只返回 fatal.
            logger.error(f"CrossDramaError: {e}")
            return EXIT_FATAL
        except frida.TransportError as e:
            emit('anr_suspected', detail=f'transport_error:{e}')
            return EXIT_ANR_SUSPECTED
        except frida.InvalidOperationError as e:
            emit('anr_suspected', detail=f'invalid_op:{e}')
            return EXIT_ANR_SUSPECTED
        except Exception as e:
            emit('fatal', detail=repr(e))
            return EXIT_FATAL


def run_probe_bind(args) -> int:
    """模式 probe-bind: attach + 对 --eps 列表逐个 RPC, 只收 BIND 抓 vid.
    不下载, 不写 manifest. Agent 编排专用 (VERIFYING 阶段).
    输出 probe_result 事件后 exit 0/2.

    前置: App 已在 ShortSeries*, 目标剧 series_id 已知 (--series-id 必需).
    不做串剧 assert (probe 是只读行为, 允许偶发 mismatch 由调用方判断).
    """
    emit('phase', phase='probe_bind_start')
    if not args.series_id:
        emit('precond_fail', reason='no_series_id')
        return EXIT_FATAL
    if not args.eps:
        emit('precond_fail', reason='no_eps')
        return EXIT_FATAL
    try:
        sample_eps = [int(x) for x in args.eps.split(',') if x.strip()]
    except ValueError as e:
        emit('precond_fail', reason=f'bad_eps:{e}')
        return EXIT_FATAL
    if not sample_eps:
        emit('precond_fail', reason='empty_eps')
        return EXIT_FATAL

    # 复用 attach-resume 的前置自检 (前台 Activity / App 存活)
    ok, reason, code = _precheck_attach_resume(args)
    if not ok:
        emit('phase', phase='probe_bind_abort', reason=reason)
        return code

    args.attach = True
    state = State()
    state.target_series_id = args.series_id
    state.target_series_name = args.name

    try:
        session, script, pid = setup_frida(state, attach_running=True)
    except frida.TransportError as e:
        emit('precond_fail', reason=f'frida_attach_err:{e}')
        return EXIT_ANR_SUSPECTED
    except Exception as e:
        emit('precond_fail', reason=f'script_load_err:{e}')
        return EXIT_ANR_SUSPECTED

    expected: dict[int, str] = {}  # {ep -> vid}

    try:
        with Heartbeat(phase='verifying'):
            for ep in sample_eps:
                pos = ep - 1  # center page = vidIndex = pos+1 (design v4 §3.1.1 坑 11)
                seq = state.next_switch_seq()
                emit('probe_ep_start', ep=ep, seq=seq)
                r = rpc_switch(script, args.series_id, None, pos, seq, timeout=15.0)
                if not r.get('ok'):
                    emit('probe_ep_fail', ep=ep,
                         reason=f'rpc_err:{r.get("err")}')
                    continue

                # 取首个 BIND idx=ep (center page), 含降级复用 preload
                b = state.wait_bind_for_series_seq(
                    args.series_id, ep, seq, timeout=8.0)
                # Codex M2: probe-bind **禁止降级复用旧 BIND**.
                # 下载路径用降级是为 "preload 已 bind, 无须重 bind" 的容错; 但 probe 是
                # 独立验证流程, 必须用本次 RPC 真实产生的 BIND 才算对齐证据. 旧 BIND 可能
                # 是上次 session 的残留, 复用会让 misaligned 情形从 VERIFYING 溜过.
                if b and b.switch_seq == seq:
                    expected[ep] = b.vid
                    emit('probe_ep_ok', ep=ep, vid=b.vid)
                else:
                    emit('probe_ep_fail', ep=ep,
                         reason='bind_timeout_no_reuse',
                         note='probe 严判: 不复用旧 BIND')
    except CrossDramaError as e:
        emit('probe_fatal', detail=str(e))
        return EXIT_FATAL
    except frida.TransportError as e:
        emit('anr_suspected', detail=f'transport_error:{e}')
        return EXIT_ANR_SUSPECTED
    except Exception as e:
        emit('fatal', detail=repr(e))
        return EXIT_FATAL
    finally:
        safe_unload_session(script, session, timeout=3.0)

    emit('probe_result',
         series_id=args.series_id,
         expected_count=len(expected),
         total_requested=len(sample_eps),
         expected=expected)
    emit('done', ok=len(expected), fail=len(sample_eps) - len(expected),
         last_ep=max(expected) if expected else 0,
         series_id=args.series_id)
    return EXIT_OK if len(expected) == len(sample_eps) else EXIT_PARTIAL


# =============== Main ===============
def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('-n', '--name', required=True, help='剧名')
    ap.add_argument('-s', '--start', type=str, default='1',
                    help='起始集 (int), 或 "auto" (扫 manifest 找最小缺失集)')
    ap.add_argument('-e', '--end', type=int, default=0, help='0=all')
    ap.add_argument('-t', '--total', type=int, default=0,
                    help='已知总集数, 用于在搜索结果中过滤同名剧; 0=不校验')
    ap.add_argument('--series-id', type=str, default='',
                    help='已知 series_id 时跳过搜索')
    ap.add_argument('--max-short', type=int, default=1080)
    ap.add_argument('--out', type=Path, default=DEFAULT_OUT_DIR)
    ap.add_argument('--attach', action='store_true',
                    help='(legacy mode) attach 到运行中的 App')
    # v4 新增: Agent 编排专用 mode
    ap.add_argument('--mode', choices=['legacy', 'spawn-resolve', 'attach-resume', 'probe-bind'],
                    default='legacy',
                    help='启动模式: legacy=默认全流程 / spawn-resolve=仅 resolve / '
                         'attach-resume=续跑下载 / probe-bind=对指定 eps 抓 BIND 验证')
    ap.add_argument('--eps', type=str, default='',
                    help='(probe-bind 专用) 逗号分隔的 ep 列表, 如 "1,15,30,45,60"')
    args = ap.parse_args()

    logger.remove()
    logger.add(sys.stderr, level='INFO',
               format='<green>{time:HH:mm:ss}</green> | <level>{level:<8}</level> | {message}')

    # Mode 分发 (legacy 保持原行为, 新 mode 走独立函数)
    if args.mode == 'spawn-resolve':
        return run_spawn_resolve(args)
    if args.mode == 'attach-resume':
        return run_attach_resume(args)
    if args.mode == 'probe-bind':
        return run_probe_bind(args)
    # mode == 'legacy' 走 _download_main
    return _download_main(args)


def _download_main(args) -> int:
    """Legacy 下载主流程, 同时供 attach-resume 复用.
    返回 exit code (0=ok, 1=partial, 2=anr, 3=fatal)."""
    out_dir = args.out / args.name
    out_dir.mkdir(parents=True, exist_ok=True)

    state = State()
    session, script, pid = setup_frida(state, attach_running=args.attach)
    logger.info(f"[v5] Frida ready. 目标: 《{args.name}》")

    try:
        expected_total = args.total if args.total > 0 else None
        if args.series_id:
            # 跳过搜索,直接用给定 series_id RPC 进剧
            logger.info(f"[v5] 跳过搜索,直接进 series_id={args.series_id}")
            state.target_series_id = args.series_id
            state.target_series_name = args.name
            state.total_eps = args.total or 0
            nav_seq = state.next_switch_seq()
            r = rpc_switch(script, args.series_id, None, 0, nav_seq, timeout=15.0)
            logger.info(f"[nav direct] rpc ok={r.get('ok')} ctx={r.get('ctx')} "
                        f"err={r.get('err')} timeout={r.get('timeout')}")
            if not r.get('ok'):
                logger.error(f"RPC failed: {r.get('err')}")
                emit('fatal', detail=f'nav_rpc_err:{r.get("err")}')
                return EXIT_ANR_SUSPECTED if r.get('timeout') else EXIT_FATAL
            # 等 BIND 确认
            deadline = time.time() + 30
            b0 = None
            while time.time() < deadline:
                b0 = state.wait_first_valid_bind(min_total_eps=1, timeout=1.0, min_seq=nav_seq)
                if b0 and b0.series_id == args.series_id:
                    logger.info(f"[nav direct] BIND ep={b0.idx} total={b0.total_eps}")
                    break
                b0 = None
            if not b0:
                logger.error("BIND 未到")
                emit('fatal', detail='nav_bind_timeout')
                return EXIT_ANR_SUSPECTED
            state.total_eps = b0.total_eps
        else:
            b0 = navigate_to_drama(args.name, state, script, timeout=30,
                                    expected_total=expected_total)
            if not b0:
                logger.error("进入剧失败")
                emit('fatal', detail='navigate_to_drama_failed')
                return EXIT_FATAL
        total = state.total_eps or b0.total_eps
        if total <= 0:
            logger.error("未拿到总集数")
            emit('fatal', detail='total_eps_unknown')
            return EXIT_FATAL
        end = args.end if args.end > 0 else total

        # 解析 --start (支持 'auto': 扫 manifest + orphan 清理 + 找最小缺失集)
        start_ep = resolve_start_ep(out_dir, total, str(args.start))
        if start_ep > end:
            logger.info(f"[v5] 全部已下 (committed {len(read_committed_eps(out_dir))} eps), 退出")
            emit('done', ok=0, fail=0, last_ep=end,
                 series_id=state.target_series_id,
                 note='all_committed')
            return EXIT_OK

        logger.info(f"[v5] 剧=《{state.target_series_name}》 "
                    f"series_id={state.target_series_id} total={total}  下载 {start_ep}..{end}")
        emit('resolved', series_id=state.target_series_id, total=total,
             name=state.target_series_name, start=start_ep, end=end)

        ok = 0
        fail = 0
        seen_ep_vids: set[tuple[int, str]] = set()  # (ep, vid) 联合去重
        used_kids: set[str] = set()   # 已用过的 cap.kid, 防止同一 cap 多次下载
        current_ep = b0.idx

        # 循环: 每集分配一个 switch_seq → RPC → 等匹配 BIND → 等匹配 cap → 下载
        for target_ep in range(start_ep, end + 1):
            # 首集特判: 已经通过 navigate 进入, b0 就是我们要的
            use_b0 = (current_ep == b0.idx and target_ep == b0.idx
                      and target_ep == start_ep)
            if use_b0:
                tgt_bind = b0
                target_seq = b0.switch_seq  # 0, 意味着首集用 nav 阶段的 cap
                logger.info(f"[ep{target_ep}] use nav b0 idx={b0.idx} "
                            f"vid={b0.vid[:14]}... seq={target_seq}")
            else:
                # 实证 (probe_ep48.py 2026-04-18): pos=N 切集后,ViewPager center page
                # 对应 vidIndex=N+1 (0-based pos vs 1-based vidIndex)。
                # 事件顺序: CAP[center=vid N+1] → BIND[center] → BIND[right preload N+2]
                #          → BIND[left N] → CAP[right preload=vid N+2]。
                # 取首个 CAP = center = target,故 pos = target_ep - 1。
                # (历史 pos=target_ep-2 靠 used_kids 过滤掉 preload 的 ep N-1,
                #  在中途跳段时暴露 off-by-one; 见 probe summary.txt)
                pos = target_ep - 1
                target_seq = state.next_switch_seq()
                logger.info(f"[ep{target_ep}] RPC switchToEp pos={pos} seq={target_seq}")
                r = rpc_switch(script, state.target_series_id, None, pos, target_seq, timeout=15.0)
                logger.info(f"[ep{target_ep}] rpc ok={r.get('ok')} ctx={r.get('ctx')} "
                            f"seq={r.get('seq')} timeout={r.get('timeout')}")
                if not r.get('ok'):
                    logger.warning(f"[ep{target_ep}] rpc err: {r.get('err')}")
                    fail += 1
                    continue

                # 等匹配 BIND(idx=target, switch_seq >= target_seq)
                tgt_bind = state.wait_bind_for_series_seq(
                    state.target_series_id, target_ep, target_seq, timeout=10.0)
                if not tgt_bind:
                    # 降级: 上一集下载时,ViewPager 已把 target_ep 作为 preload right
                    # bind 过了 (idx=target_ep, seq=target_seq-1). Holder 缓存,本次
                    # setCurrentItem 不再 rebind. 复用旧 BIND 仍然指向正确 biz_vid.
                    with state.lock:
                        reuse = [b for b in state.bind_queue
                                 if b.series_id == state.target_series_id
                                 and b.idx == target_ep and b.switch_seq >= 1]
                    if reuse:
                        tgt_bind = max(reuse, key=lambda b: b.ts)
                        logger.info(f"[ep{target_ep}] 降级复用 preload BIND "
                                    f"(seq={tgt_bind.switch_seq}, vid={tgt_bind.vid[:14]}...)")
                    else:
                        logger.warning(f"[ep{target_ep}] 切集后新 BIND 未到 (seq={target_seq})")
                        fail += 1
                        continue

            ep_vid_key = (target_ep, tgt_bind.vid)
            if ep_vid_key in seen_ep_vids:
                logger.info(f"[ep{target_ep}] (ep,vid) 已下,跳过")
                continue
            seen_ep_vids.add(ep_vid_key)
            current_ep = target_ep

            # 等匹配 cap(switch_seq >= target_seq), settle 内消费后续
            # fallback: 若预加载 cap 早到, exclude used_kids 后取最新
            cap_timeout = 15.0 if use_b0 else 8.0  # ep1 nav 后首次 Activity 启动慢
            cap = state.wait_cap_for_seq(target_seq, timeout=cap_timeout, settle=1.5,
                                          exclude_kids=used_kids)
            if not cap:
                logger.warning(f"[ep{target_ep}] 无匹配 cap (seq={target_seq})")
                fail += 1
                continue
            used_kids.add(cap.kid)
            logger.info(f"[ep{target_ep}] cap kid={cap.kid[:12]}... seq={cap.switch_seq}")

            # design doc v4 §3.5 严格提交顺序:
            #   步 1-5 download_and_decrypt (内存解密 → .tmp/ fsync → rename → final)
            #   步 6-7 append_manifest (write + fsync = committed)
            #   步 8 emit ep_ok (manifest commit 后才算真正 ok)
            ep_path = download_and_decrypt(cap, target_ep, out_dir, args.name, args.max_short)
            if ep_path is None:
                emit('ep_fail', ep=target_ep, reason='download_or_decrypt_err')
                fail += 1
                continue

            rec = {
                'ep': target_ep, 'vid': tgt_bind.vid,
                'kid': cap.kid, 'ts': time.time(),
                'series_id': state.target_series_id,
                'bytes': ep_path.stat().st_size if ep_path.exists() else 0,
            }
            if not append_manifest(out_dir, rec):
                # mp4 已 rename 但 manifest 写失败 → orphan.
                # 记 fail, 下次 Agent --start auto 会通过 final-dir orphan 清理 (Day 2-2) 处理.
                emit('ep_fail', ep=target_ep, reason='manifest_append_err')
                fail += 1
                continue

            ok += 1
            emit('ep_ok', ep=target_ep, vid=tgt_bind.vid, kid=cap.kid,
                 bytes=rec['bytes'], series_id=state.target_series_id)

        logger.info(f"[v5 完成] ok={ok} fail={fail} / 目标 {end - start_ep + 1}")
        emit('done', ok=ok, fail=fail, last_ep=current_ep,
             series_id=state.target_series_id)
        return EXIT_OK if fail == 0 else EXIT_PARTIAL
    finally:
        safe_unload_session(script, session, timeout=3.0)


if __name__ == '__main__':
    rc = main()
    # Codex M1: 非 int 返回值不能兜底成 0 (会把真实失败隐藏成成功).
    # 只有 rc 是明确 int 才信任; None 或其他对象一律视为 fatal.
    if isinstance(rc, int):
        sys.exit(rc)
    emit('fatal', detail=f'main_returned_non_int:{type(rc).__name__}')
    sys.exit(EXIT_FATAL)
