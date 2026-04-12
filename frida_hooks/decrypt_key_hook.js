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

    var Engine = Java.use("com.ss.ttvideoengine.TTVideoEngine");

    // Hook setDecryptionKey
    Engine.setDecryptionKey.overloads.forEach(function(ov) {
        ov.implementation = function() {
            var args_info = [];
            for (var i = 0; i < arguments.length; i++) {
                var a = arguments[i];
                if (a === null) {
                    args_info.push("null");
                } else if (typeof a === "string") {
                    args_info.push(a);
                } else if (a.length !== undefined) {
                    args_info.push(bh(a));
                } else {
                    args_info.push(String(a));
                }
            }
            send({t: "decrypt_key", args: args_info});
            console.log("[DECRYPT KEY] " + args_info.join(" | "));
            return ov.apply(this, arguments);
        };
    });

    // Hook setEncodedKey
    Engine.setEncodedKey.overloads.forEach(function(ov) {
        ov.implementation = function() {
            var args_info = [];
            for (var i = 0; i < arguments.length; i++) {
                var a = arguments[i];
                if (a === null) {
                    args_info.push("null");
                } else if (typeof a === "string") {
                    args_info.push(a);
                } else if (a.length !== undefined) {
                    args_info.push(bh(a));
                } else {
                    args_info.push(String(a));
                }
            }
            send({t: "encoded_key", args: args_info});
            console.log("[ENCODED KEY] " + args_info.join(" | "));
            return ov.apply(this, arguments);
        };
    });

    // Hook setDirectURL to capture video URLs
    Engine.setDirectURL.overloads.forEach(function(ov) {
        ov.implementation = function() {
            var url = "";
            for (var i = 0; i < arguments.length; i++) {
                if (typeof arguments[i] === "string" && arguments[i].indexOf("http") === 0) {
                    url = arguments[i];
                    break;
                }
            }
            if (url) {
                send({t: "direct_url", url: url});
                console.log("[URL] " + url.substring(0, 200));
            }
            return ov.apply(this, arguments);
        };
    });

    // Hook setVideoID
    Engine.setVideoID.overloads.forEach(function(ov) {
        ov.implementation = function() {
            send({t: "video_id", id: String(arguments[0])});
            return ov.apply(this, arguments);
        };
    });

    // Hook setTTHlsDrmToken
    Engine.setTTHlsDrmToken.overloads.forEach(function(ov) {
        ov.implementation = function() {
            send({t: "drm_token", token: String(arguments[0])});
            console.log("[DRM TOKEN] " + String(arguments[0]).substring(0, 200));
            return ov.apply(this, arguments);
        };
    });

    // Hook setPlayAuthToken
    Engine.setPlayAuthToken.overloads.forEach(function(ov) {
        ov.implementation = function() {
            send({t: "auth_token", token: String(arguments[0])});
            return ov.apply(this, arguments);
        };
    });

    send({s: "ALL HOOKS READY"});
});
