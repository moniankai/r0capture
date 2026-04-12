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

    // Hook MediaPlayer.setStringOptionkey 处理 native
    var MP = Java.use("com.ss.ttm.player.MediaPlayer");
    MP.setStringOption.overloads.forEach(function(ov) {
        ov.implementation = function() {
            var optKey = arguments[0];
            var optVal = String(arguments[1]).substring(0, 300);
            send({t: "mp_opt", key: optKey, val: optVal});
            return ov.apply(this, arguments);
        };
    });

    // Hook MediaPlayer.setDataSource
    MP.setDataSource.overloads.forEach(function(ov) {
        ov.implementation = function() {
            for (var i = 0; i < arguments.length; i++) {
                send({t: "mp_src", idx: i, val: String(arguments[i]).substring(0, 300)});
            }
            return ov.apply(this, arguments);
        };
    });

    // Hook setIntOption处理 DRM option code
    MP.setIntOption.overloads.forEach(function(ov) {
        ov.implementation = function() {
            var optKey = arguments[0];
            var optVal = arguments[1];
            // 记录所有 option 以查找 DRM 相关项
            if (optKey > 7000 || (optVal > 100 && optKey > 100)) {
                send({t: "mp_int", key: optKey, val: optVal});
            }
            return ov.apply(this, arguments);
        };
    });

    send({s: "MediaPlayer hooked"});

    // Hook TTVideoEngineImpl._initIntertrustDrm
    var Impl = Java.use("com.ss.ttvideoengine.TTVideoEngineImpl");
    Impl._initIntertrustDrm.implementation = function() {
        send({t: "drm_start"});
        var result = this._initIntertrustDrm();
        send({t: "drm_end"});
        return result;
    };

    // Hook 会调用 _initIntertrustDrm 的 _playInternal
    try {
        Impl._playInternal.overloads.forEach(function(ov) {
            ov.implementation = function() {
                send({t: "play_internal"});
                return ov.apply(this, arguments);
            };
        });
    } catch(e) {}

    // Hook setDecryptionKey
    try {
        Impl.setDecryptionKey.overloads.forEach(function(ov) {
            ov.implementation = function() {
                var val = String(arguments[0]).substring(0, 200);
                send({t: "set_dec_key", val: val});
                return ov.apply(this, arguments);
            };
        });
    } catch(e) {}

    // Hook getMediaFileKey
    try {
        Impl.getMediaFileKey.overloads.forEach(function(ov) {
            ov.implementation = function() {
                var result = ov.apply(this, arguments);
                send({t: "get_media_key", val: result ? String(result).substring(0, 200) : "null"});
                return result;
            };
        });
    } catch(e) {}

    send({s: "ALL READY"});
});
