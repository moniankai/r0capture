Java.perform(function() {
    function bh(arr) {
        if (!arr) return "";
        var sb = [];
        for (var i = 0; i < arr.length; i++) {
            var b = (arr[i] & 0xFF).toString(16);
            sb.push(b.length === 1 ? "0" + b : b);
        }
        return sb.join("");
    }

    // Hook native MediaPlayer setStringOptionkey 处理
    var MP = Java.use("com.ss.ttm.player.MediaPlayer");
    MP.setStringOption.overloads.forEach(function(ov) {
        ov.implementation = function() {
            var key = arguments[0];
            var val = String(arguments[1]);
            // 处理 option
            if (val.length > 5 && val.length < 500) {
                send({t: "mp_str", key: key, val: val.substring(0, 300)});
            }
            return ov.apply(this, arguments);
        };
    });

    MP.setIntOption.overloads.forEach(function(ov) {
        ov.implementation = function() {
            var key = arguments[0];
            var val = arguments[1];
            // 处理
            if (key > 900 || val > 100) {
                send({t: "mp_int", key: key, val: val});
            }
            return ov.apply(this, arguments);
        };
    });

    // Hook MediaPlayer.setDataSource处理
    MP.setDataSource.overloads.forEach(function(ov) {
        ov.implementation = function() {
            var args = [];
            for (var i = 0; i < arguments.length; i++) {
                args.push(String(arguments[i]).substring(0, 300));
            }
            send({t: "mp_src", args: args});
            return ov.apply(this, arguments);
        };
    });

    send({s: "MediaPlayer hooked"});

    // Hook TTVideoEngineImpl._initIntertrustDrm
    var Impl = Java.use("com.ss.ttvideoengine.TTVideoEngineImpl");
    Impl._initIntertrustDrm.implementation = function() {
        send({t: "initDrm_called"});

        // 从当前 video model 获取 spadea
        try {
            var modelField = this.getClass().getDeclaredField("mVideoModel");
            modelField.setAccessible(true);
            var model = modelField.get(this);
            if (model) {
                var refField = model.getClass().getDeclaredField("vodVideoRef");
                refField.setAccessible(true);
                var ref = refField.get(model);
                if (ref) {
                    var listField = ref.getClass().getDeclaredField("mVideoList");
                    listField.setAccessible(true);
                    var list = Java.cast(listField.get(ref), Java.use("java.util.List"));
                    if (list.size() > 0) {
                        var info = list.get(0);
                        var spadeaField = info.getClass().getDeclaredField("mSpadea");
                        spadeaField.setAccessible(true);
                        var spadea = spadeaField.get(info);
                        send({t: "drm_spadea", spadea: String(spadea)});
                    }
                }
            }
        } catch(e) {
            send({t: "drm_model_err", err: e.toString()});
        }

        // 处理处理
        var result = this._initIntertrustDrm();
        send({t: "initDrm_result", result: String(result)});
        return result;
    };

    // Hook Impl 上的 setDecryptionKey
    Impl.setDecryptionKey.overloads.forEach(function(ov) {
        ov.implementation = function() {
            var val = "";
            try {
                if (arguments[0] instanceof Array || (arguments[0] && arguments[0].length !== undefined)) {
                    val = bh(arguments[0]);
                } else {
                    val = String(arguments[0]);
                }
            } catch(e) { val = String(arguments[0]); }
            send({t: "setDecKey", val: val.substring(0, 200)});
            return ov.apply(this, arguments);
        };
    });

    Impl.setEncodedKey.overloads.forEach(function(ov) {
        ov.implementation = function() {
            var val = "";
            try {
                if (arguments[0] instanceof Array || (arguments[0] && arguments[0].length !== undefined)) {
                    val = bh(arguments[0]);
                } else {
                    val = String(arguments[0]);
                }
            } catch(e) { val = String(arguments[0]); }
            send({t: "setEncKey", val: val.substring(0, 200)});
            return ov.apply(this, arguments);
        };
    });

    send({s: "ALL READY - play video!"});
});
