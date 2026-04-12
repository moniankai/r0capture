Java.perform(function() {
    send({s: "Java OK"});

    // Hook MediaCodec.configure处理 MediaCrypto
    try {
        var MC = Java.use("android.media.MediaCodec");

        MC.configure.overloads.forEach(function(ov) {
            ov.implementation = function() {
                var format = arguments[0];
                var surface = arguments[1];
                var crypto = arguments[2];
                var flags = arguments[3];

                var hasCrypto = crypto !== null;
                var fmtStr = "";
                try { fmtStr = format.toString().substring(0, 300); } catch(e) {}

                send({t: "mc_configure", hasCrypto: hasCrypto, format: fmtStr, flags: flags});

                if (hasCrypto) {
                    send({t: "mc_crypto_used", info: "MediaCrypto is being used - secure decryption!"});
                }

                return ov.apply(this, arguments);
            };
        });
        send({s: "MediaCodec.configure hooked"});
    } catch(e) { send({s: "MC.configure err: " + e}); }

    // Hook queueSecureInputBuffer
    try {
        var MC = Java.use("android.media.MediaCodec");
        MC.queueSecureInputBuffer.implementation = function(idx, offset, info, pts, flags) {
            send({t: "mc_secure", idx: idx, offset: offset, pts: pts, flags: flags});
            return this.queueSecureInputBuffer(idx, offset, info, pts, flags);
        };
        send({s: "queueSecureInputBuffer hooked"});
    } catch(e) { send({s: "queueSecure err: " + e}); }

    // Hook queueInputBuffer（非 secure）
    try {
        var MC = Java.use("android.media.MediaCodec");
        MC.queueInputBuffer.implementation = function(idx, offset, size, pts, flags) {
            send({t: "mc_input", idx: idx, size: size, pts: pts});
            return this.queueInputBuffer(idx, offset, size, pts, flags);
        };
        send({s: "queueInputBuffer hooked"});
    } catch(e) { send({s: "queueInput err: " + e}); }

    // Hook MediaCrypto 构造函数
    try {
        var MCrypto = Java.use("android.media.MediaCrypto");
        MCrypto.$init.overloads.forEach(function(ov) {
            ov.implementation = function() {
                var uuid = arguments[0];
                var initData = arguments[1];

                var uuidStr = "";
                try {
                    var UUID = Java.use("java.util.UUID");
                    var u = Java.cast(uuid, UUID);
                    uuidStr = u.toString();
                } catch(e) {
                    // uuid 处理
                    try {
                        var sb = [];
                        for (var i = 0; i < uuid.length; i++) {
                            var b = (uuid[i] & 0xFF).toString(16);
                            sb.push(b.length === 1 ? "0" + b : b);
                        }
                        uuidStr = sb.join("");
                    } catch(e2) {}
                }

                var initHex = "";
                try {
                    var sb2 = [];
                    for (var i = 0; i < Math.min(initData.length, 64); i++) {
                        var b = (initData[i] & 0xFF).toString(16);
                        sb2.push(b.length === 1 ? "0" + b : b);
                    }
                    initHex = sb2.join("");
                } catch(e) {}

                send({t: "media_crypto", uuid: uuidStr, initData: initHex, initLen: initData ? initData.length : 0});
                return ov.apply(this, arguments);
            };
        });
        send({s: "MediaCrypto hooked"});
    } catch(e) { send({s: "MCrypto err: " + e}); }

    // Hook 所有 Cipher.init（不限于 AES）
    try {
        var Cipher = Java.use("javax.crypto.Cipher");
        Cipher.init.overloads.forEach(function(ov) {
            ov.implementation = function() {
                var algo = this.getAlgorithm();
                var m = arguments[0] === 1 ? "E" : arguments[0] === 2 ? "D" : String(arguments[0]);
                send({t: "cipher_any", algo: algo, mode: m});
                return ov.apply(this, arguments);
            };
        });
        send({s: "Cipher.init (all) hooked"});
    } catch(e) { send({s: "Cipher err: " + e}); }

    send({s: "ALL HOOKED"});
});
