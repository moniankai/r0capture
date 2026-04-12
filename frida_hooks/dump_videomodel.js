Java.perform(function() {
    var Engine = Java.use("com.ss.ttvideoengine.TTVideoEngine");

    Engine.setVideoModel.overloads.forEach(function(ov) {
        ov.implementation = function(model) {
            // toString 回退 VideoModel
            try {
                var cls = model.getClass();
                var fields = cls.getDeclaredFields();
                var data = {};

                for (var i = 0; i < fields.length; i++) {
                    var f = fields[i];
                    f.setAccessible(true);
                    var name = f.getName();
                    try {
                        var val = f.get(model);
                        if (val !== null) {
                            var s = val.toString();
                            if (s.length > 0 && s.length < 2000) {
                                data[name] = s;
                            } else if (s.length >= 2000) {
                                data[name] = s.substring(0, 2000) + "...(truncated)";
                            }
                        }
                    } catch(e) {}
                }

                send({t: "video_model", fields: data});
            } catch(e) {
                send({t: "vm_err", err: e.toString()});
            }

            // 处理 getter 
            try {
                var methods = ["getVideoId", "getVideoDuration", "getVideoWidth", "getVideoHeight"];
                var getters = {};
                for (var i = 0; i < methods.length; i++) {
                    try {
                        var result = model[methods[i]]();
                        if (result !== null) getters[methods[i]] = result.toString();
                    } catch(e) {}
                }
                if (Object.keys(getters).length > 0) {
                    send({t: "vm_getters", data: getters});
                }
            } catch(e) {}

            // 逻辑 URL key
            try {
                var videoInfoList = model.mVideoInfoList || model.videoInfoList;
                if (videoInfoList) {
                    var size = videoInfoList.size();
                    send({t: "vm_info_list", size: size});
                    for (var i = 0; i < Math.min(size, 5); i++) {
                        var info = videoInfoList.get(i);
                        var infoFields = info.getClass().getDeclaredFields();
                        var infoData = {};
                        for (var j = 0; j < infoFields.length; j++) {
                            infoFields[j].setAccessible(true);
                            try {
                                var v = infoFields[j].get(info);
                                if (v !== null) {
                                    var vs = v.toString();
                                    if (vs.length > 0 && vs.length < 2000) {
                                        infoData[infoFields[j].getName()] = vs;
                                    }
                                }
                            } catch(e) {}
                        }
                        send({t: "vm_info", index: i, data: infoData});
                    }
                }
            } catch(e) {
                send({t: "vm_list_err", err: e.toString()});
            }

            return ov.apply(this, arguments);
        };
    });

    send({s: "setVideoModel hooked - play a video!"});
});
