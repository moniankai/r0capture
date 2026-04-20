// Hook 红果 App 的 ViewHolder 数据绑定 + TTVideoEngine,建立 (ep_index ↔ biz_vid ↔ tt_vid ↔ kid) 映射.
//
// 关键类:
//   com.dragon.read.component.shortvideo.impl.v2.view.holder.a — holder 基类
//   com.dragon.read.component.shortvideo.impl.v2.view.holder.z — 具体 holder
//   j2(SaasVideoData) — 数据绑定入口
//   com.dragon.read.component.shortvideo.data.saas.video.SaasVideoData
//     .getVid() / .getVidIndex() / .getSeriesId() / .getSeriesName() / .getTitle()
//   com.ss.ttvideoengine.TTVideoEngine.setVideoModel(VideoModel)

Java.perform(function() {
    send({t:'hook_start'});

    function tryHook(className, methodName, paramTypes, implFn) {
        try {
            var Cls = Java.use(className);
            var ov = Cls[methodName].overload.apply(Cls[methodName], paramTypes);
            ov.implementation = implFn(ov);
            return true;
        } catch (e) {
            send({t:'hook_err', cls: className, method: methodName, err: String(e)});
            return false;
        }
    }

    function bindImpl(ov) {
        return function(data) {
            if (data) {
                try {
                    var vid = null, idx = -1, seriesId = null, name = null, title = null, total = -1;
                    try { vid = String(data.getVid()); } catch(e) {}
                    try { idx = Number(data.getVidIndex()); } catch(e) {}
                    try { seriesId = String(data.getSeriesId()); } catch(e) {}
                    try { name = String(data.getSeriesName()); } catch(e) {}
                    try { title = String(data.getTitle()); } catch(e) {}
                    try { total = Number(data.getEpisodesCount()); } catch(e) {}
                    var dataHash = data.hashCode ? data.hashCode() : -1;
                    send({t:'bind',
                          vid: vid, idx: idx,
                          series_id: seriesId, name: name, title: title,
                          total_eps: total, data_hash: dataHash});
                } catch (e) {
                    send({t:'bind_err', err: String(e)});
                }
            } else {
                send({t:'bind_null'});
            }
            return ov.call(this, data);
        };
    }

    var params = ['com.dragon.read.component.shortvideo.data.saas.video.SaasVideoData'];
    var okA = tryHook('com.dragon.read.component.shortvideo.impl.v2.view.holder.a', 'j2', params, bindImpl);
    var okZ = tryHook('com.dragon.read.component.shortvideo.impl.v2.view.holder.z', 'j2', params, bindImpl);
    send({t:'bind_hooked', a: okA, z: okZ});

    // 兜底: 直接 hook SaasVideoData 的 setVidIndex/setVid 来捕获每个数据对象
    try {
        var Data = Java.use('com.dragon.read.component.shortvideo.data.saas.video.SaasVideoData');
        var _cache = {};  // hashCode → {vid, idx, name, title, series_id}

        function _dumpData(inst, tag) {
            try {
                var h = inst.hashCode();
                var entry = _cache[h] || {};
                try { entry.vid = String(inst.getVid()); } catch(e) {}
                try { entry.idx = Number(inst.getVidIndex()); } catch(e) {}
                try { entry.series_id = String(inst.getSeriesId()); } catch(e) {}
                try { entry.name = String(inst.getSeriesName()); } catch(e) {}
                try { entry.title = String(inst.getTitle()); } catch(e) {}
                try { entry.total_eps = Number(inst.getEpisodesCount()); } catch(e) {}
                _cache[h] = entry;
                send({t:'data', tag: tag, hash: h, entry: entry});
            } catch (e) {
                send({t:'data_err', tag: tag, err: String(e)});
            }
        }

        Data.setVidIndex.overload('long').implementation = function(v) {
            var r = this.setVidIndex(v);
            _dumpData(this, 'setVidIndex=' + v);
            return r;
        };
        Data.setVid.overload('java.lang.String').implementation = function(v) {
            var r = this.setVid(v);
            _dumpData(this, 'setVid=' + (v || 'null'));
            return r;
        };
        Data.setSeriesName.overload('java.lang.String').implementation = function(v) {
            var r = this.setSeriesName(v);
            _dumpData(this, 'setSeriesName=' + (v || 'null'));
            return r;
        };
        send({t:'data_hooked'});
    } catch (e) {
        send({t:'data_hook_err', err: String(e)});
    }

    // Hook TTVideoEngine.setVideoModel(VideoModel) 以获得 tt_vid + kid
    try {
        var Engine = Java.use('com.ss.ttvideoengine.TTVideoEngine');
        var VM = Java.use('com.ss.ttvideoengine.model.VideoModel');

        var ovs = Engine.setVideoModel.overloads;
        for (var i = 0; i < ovs.length; i++) {
            (function(ov, idx) {
                ov.implementation = function(model) {
                    try {
                        if (model != null) {
                            var tt_vid = null, mediaType = null, url = null;
                            try { tt_vid = String(VM.cast ? model.getVideoRefStr(202) : null); } catch (e) {}
                            // 203: video_id in model
                            try {
                                var m2 = Java.cast(model, VM);
                                tt_vid = String(m2.getVideoRefStr(202)); // video_id
                                url = String(m2.getVideoRefStr(219));    // main_url
                            } catch (e) {}
                            send({t:'set_vm', ov_idx: idx, tt_vid: tt_vid, url: url});
                        } else {
                            send({t:'set_vm', ov_idx: idx, tt_vid: null, url: null, note: 'model null'});
                        }
                    } catch (e) {
                        send({t:'set_vm_err', err: String(e)});
                    }
                    return ov.apply(this, arguments);
                };
            })(ovs[i], i);
        }
        send({t:'engine_hooked', overloads: ovs.length});
    } catch (e) {
        send({t:'engine_hook_err', err: String(e)});
    }

    send({t:'ready'});
});
