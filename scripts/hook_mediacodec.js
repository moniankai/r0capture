// Hook AMediaCodec逻辑 sample
// DRM 逻辑 MediaCodec

var resolver = new ApiResolver("module");

function hookFn(pattern, cb) {
    var m = resolver.enumerateMatches(pattern);
    if (m.length > 0) {
        Interceptor.attach(m[0].address, cb);
        send({s: "hooked " + pattern.split("!").pop()});
        return true;
    }
    send({s: "missed " + pattern.split("!").pop()});
    return false;
}

function hexDump(ptr, len) {
    var h = "";
    for (var i = 0; i < len; i++) {
        var b = (ptr.add(i).readU8() & 0xFF).toString(16);
        h += (b.length === 1 ? "0" : "") + b;
    }
    return h;
}

var sampleCount = 0;

// AMediaCodec_queueInputBuffer(codec, idx, offset, size, time, flags)
// 逻辑 sample 
hookFn("exports:*libmediandk*!AMediaCodec_queueInputBuffer", {
    onEnter: function(args) {
        var size = args[3].toInt32();
        var pts = args[4].toInt32();
        var flags = args[5].toInt32();
        if (size > 0) {
            sampleCount++;
            // 20 sample回退
            if (sampleCount <= 20) {
                send({t: "queue", n: sampleCount, size: size, pts: pts, flags: flags});
            }
        }
    }
});

// AMediaCodec_getInputBuffer(codec, idx, out_size)
// 逻辑
hookFn("exports:*libmediandk*!AMediaCodec_getInputBuffer", {
    onEnter: function(args) {
        this.outSize = args[2];
    },
    onLeave: function(retval) {
        // retval = 处理
        if (!retval.isNull() && this.outSize && !this.outSize.isNull()) {
            this.bufPtr = retval;
        }
    }
});

// AMediaCodec_queueSecureInputBuffer - MediaCrypto 
hookFn("exports:*libmediandk*!AMediaCodec_queueSecureInputBuffer", {
    onEnter: function(args) {
        var size = args[3].toInt32();
        sampleCount++;
        if (sampleCount <= 20) {
            send({t: "secure_queue", n: sampleCount, size: size});
        }
    }
});

// AMediaCrypto_isCryptoSchemeSupported
hookFn("exports:*libmediandk*!AMediaCrypto_isCryptoSchemeSupported", {
    onEnter: function(args) {
        var uuid = hexDump(args[0], 16);
        send({t: "crypto_scheme", uuid: uuid});
    }
});

// AMediaCodec_configure - codec format
hookFn("exports:*libmediandk*!AMediaCodec_configure", {
    onEnter: function(args) {
        send({t: "configure", codec: args[0].toString()});
    }
});

// AMediaCodec_createDecoderByType
hookFn("exports:*libmediandk*!AMediaCodec_createDecoderByType", {
    onEnter: function(args) {
        try {
            var mimeType = args[0].readUtf8String();
            send({t: "create_decoder", mime: mimeType});
        } catch(e) {}
    }
});

send({s: "ready - play video now"});
