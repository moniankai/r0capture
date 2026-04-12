Java.perform(function() {
    send({s: "Java OK"});

    function bh(arr) {
        var sb = [];
        for (var i = 0; i < arr.length; i++) {
            var b = (arr[i] & 0xFF).toString(16);
            sb.push(b.length === 1 ? "0" + b : b);
        }
        return sb.join("");
    }

    var Cipher = Java.use("javax.crypto.Cipher");
    Cipher.init.overloads.forEach(function(ov) {
        ov.implementation = function() {
            var m = arguments[0];
            var algo = this.getAlgorithm();
            if (algo && algo.indexOf("AES") !== -1) {
                var ms = m === 1 ? "E" : m === 2 ? "D" : String(m);
                var kh = "";
                try { kh = bh(arguments[1].getEncoded()); } catch(e) {}
                var ih = "";
                try {
                    if (arguments.length >= 3 && arguments[2]) {
                        ih = bh(Java.cast(arguments[2], Java.use("javax.crypto.spec.IvParameterSpec")).getIV());
                    }
                } catch(e) {}
                send({t: "c", m: ms, a: algo, k: kh, i: ih});
            }
            return ov.apply(this, arguments);
        };
    });
    send({s: "Cipher hooked"});

    try {
        var MD = Java.use("android.media.MediaDrm");
        MD.provideKeyResponse.implementation = function(scope, resp) {
            send({t: "dr", sz: resp.length, h: bh(resp)});
            try {
                var txt = Java.use("java.lang.String").$new(resp, "UTF-8").toString();
                send({t: "dt", tx: txt.substring(0, 1000)});
            } catch(e) {}
            return this.provideKeyResponse(scope, resp);
        };
        MD.getKeyRequest.overloads.forEach(function(ov) {
            ov.implementation = function() {
                send({t: "gk"});
                return ov.apply(this, arguments);
            };
        });
        send({s: "MediaDrm hooked"});
    } catch(e) {
        send({s: "MDrm err: " + e});
    }

    send({s: "READY - play video"});
});
