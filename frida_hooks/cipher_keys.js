Java.perform(function() {
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
            var m = arguments[0] === 1 ? "E" : arguments[0] === 2 ? "D" : String(arguments[0]);
            var algo = this.getAlgorithm();
            var kh = "";
            try { kh = bh(arguments[1].getEncoded()); } catch(e) {}
            var ih = "";
            try {
                if (arguments.length >= 3 && arguments[2]) {
                    ih = bh(Java.cast(arguments[2], Java.use("javax.crypto.spec.IvParameterSpec")).getIV());
                }
            } catch(e) {}
            send({t: "c", m: m, a: algo, k: kh, i: ih});

            try {
                var stack = Java.use("android.util.Log").getStackTraceString(Java.use("java.lang.Throwable").$new());
                send({t: "st", s: stack.substring(0, 1000)});
            } catch(e) {}

            return ov.apply(this, arguments);
        };
    });
    send({s: "ready"});
});
