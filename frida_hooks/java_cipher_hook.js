// Hook javax.crypto.Cipher 和 android.media.MediaDrm 以捕获 CENC 解密 key
Java.perform(function() {
    send({s: "Java.perform OK, Android " + Java.androidVersion});

    function bytesToHex(arr) {
        var sb = [];
        for (var i = 0; i < arr.length; i++) {
            var b = (arr[i] & 0xFF).toString(16);
            sb.push(b.length === 1 ? "0" + b : b);
        }
        return sb.join("");
    }

    // 1. Hook AES Cipher.init
    try {
        var Cipher = Java.use("javax.crypto.Cipher");
        Cipher.init.overloads.forEach(function(overload) {
            overload.implementation = function() {
                var mode = arguments[0];
                var key = arguments[1];
                var algo = this.getAlgorithm();

                if (algo && algo.indexOf("AES") !== -1) {
                    var modeStr = mode === 1 ? "ENC" : mode === 2 ? "DEC" : String(mode);
                    var keyHex = "";
                    try { keyHex = bytesToHex(key.getEncoded()); } catch(e) {}

                    var ivHex = "";
                    try {
                        if (arguments.length >= 3 && arguments[2]) {
                            var IvPS = Java.use("javax.crypto.spec.IvParameterSpec");
                            var ivSpec = Java.cast(arguments[2], IvPS);
                            ivHex = bytesToHex(ivSpec.getIV());
                        }
                    } catch(e) {}

                    send({type: "cipher", mode: modeStr, algo: algo, key: keyHex, iv: ivHex});
                    console.log("[CIPHER] " + modeStr + " " + algo + " key=" + keyHex);
                }
                return overload.apply(this, arguments);
            };
        });
        send({s: "Cipher.init hooked"});
    } catch(e) { send({s: "Cipher err: " + e}); }

    // 2. Hook MediaDrm
    try {
        var MediaDrm = Java.use("android.media.MediaDrm");

        MediaDrm.provideKeyResponse.implementation = function(scope, response) {
            send({type: "drm_response", size: response.length, hex: bytesToHex(response)});
            try {
                var str = Java.use("java.lang.String").$new(response, "UTF-8");
                send({type: "drm_text", text: str.toString().substring(0, 1000)});
            } catch(e) {}
            return this.provideKeyResponse(scope, response);
        };

        MediaDrm.getKeyRequest.overloads.forEach(function(overload) {
            overload.implementation = function() {
                send({type: "drm_key_request"});
                return overload.apply(this, arguments);
            };
        });

        send({s: "MediaDrm hooked"});
    } catch(e) { send({s: "MediaDrm err: " + e}); }

    // 3. DRM / 回退
    var interesting = [];
    Java.enumerateLoadedClasses({
        onMatch: function(name) {
            var lo = name.toLowerCase();
            if (lo.indexOf("mediadrm") !== -1 || lo.indexOf("mediacrypto") !== -1 ||
                lo.indexOf("ttvideoengine") !== -1 || lo.indexOf("videoengine") !== -1 ||
                lo.indexOf("cenc") !== -1 || lo.indexOf("drmsession") !== -1 ||
                lo.indexOf("clearkey") !== -1 || lo.indexOf("contentkey") !== -1 ||
                (lo.indexOf("decrypt") !== -1 && lo.indexOf("video") !== -1)) {
                interesting.push(name);
            }
        },
        onComplete: function() {
            send({type: "classes", list: interesting});
        }
    });
});
