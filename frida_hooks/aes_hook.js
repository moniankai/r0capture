/**
 * AES 解密 Hook：拦截 javax.crypto.Cipher 以捕获加密 key 和 IV。
 *
 * 目标：
 * - javax.crypto.Cipher.init()
 * - javax.crypto.Cipher.doFinal()
 * - javax.crypto.spec.SecretKeySpec
 */

'use strict';

(function () {
    Java.perform(function () {
        console.log('[AES Hook] Starting...');

        var ByteArray = Java.use('[B');

        function bytesToHex(bytes) {
            if (!bytes) return '';
            var hex = '';
            var arr = Java.array('byte', bytes);
            for (var i = 0; i < arr.length; i++) {
                var b = (arr[i] & 0xFF).toString(16);
                hex += (b.length === 1 ? '0' : '') + b;
            }
            return hex;
        }

        // Hook Cipher.init to capture key and IV
        try {
            var Cipher = Java.use('javax.crypto.Cipher');

            Cipher.init.overloads.forEach(function (overload) {
                overload.implementation = function () {
                    var mode = arguments[0]; // 1=ENCRYPT, 2=DECRYPT
                    var key = arguments[1];
                    var modeStr = mode === 1 ? 'ENCRYPT' : mode === 2 ? 'DECRYPT' : 'UNKNOWN';

                    var keyBytes = null;
                    var ivBytes = null;
                    var algorithm = this.getAlgorithm();

                    // key 
                    try {
                        if (key && key.getEncoded) {
                            keyBytes = bytesToHex(key.getEncoded());
                        }
                    } catch (e) {}

                    // AlgorithmParameterSpec IV
                    try {
                        if (arguments.length >= 3 && arguments[2]) {
                            var spec = arguments[2];
                            var IvParameterSpec = Java.use('javax.crypto.spec.IvParameterSpec');
                            var ivSpec = Java.cast(spec, IvParameterSpec);
                            ivBytes = bytesToHex(ivSpec.getIV());
                        }
                    } catch (e) {}

                    // AES 
                    if (algorithm && algorithm.indexOf('AES') !== -1) {
                        send({
                            type: 'aes_init',
                            mode: modeStr,
                            algorithm: algorithm,
                            key: keyBytes,
                            iv: ivBytes,
                            timestamp: Date.now()
                        });
                        console.log('[AES] Init ' + modeStr + ' | Algo: ' + algorithm);
                        if (keyBytes) console.log('[AES] Key: ' + keyBytes);
                        if (ivBytes) console.log('[AES] IV:  ' + ivBytes);
                    }

                    return overload.apply(this, arguments);
                };
            });
            console.log('[AES Hook] Cipher.init hooked');
        } catch (e) {
            console.log('[AES Hook] Cipher.init failed: ' + e.message);
        }

        // Hook Cipher.doFinal，捕获加密/解密数据大小
        try {
            var Cipher = Java.use('javax.crypto.Cipher');
            Cipher.doFinal.overloads.forEach(function (overload) {
                overload.implementation = function () {
                    var result = overload.apply(this, arguments);
                    var algorithm = this.getAlgorithm();

                    if (algorithm && algorithm.indexOf('AES') !== -1) {
                        var inputLen = 0;
                        var outputLen = 0;

                        if (arguments.length > 0 && arguments[0]) {
                            try { inputLen = arguments[0].length; } catch (e) {}
                        }
                        if (result) {
                            try { outputLen = result.length; } catch (e) {}
                        }

                        send({
                            type: 'aes_doFinal',
                            algorithm: algorithm,
                            input_size: inputLen,
                            output_size: outputLen,
                            timestamp: Date.now()
                        });
                    }

                    return result;
                };
            });
            console.log('[AES Hook] Cipher.doFinal hooked');
        } catch (e) {
            console.log('[AES Hook] Cipher.doFinal failed: ' + e.message);
        }

        // Hook SecretKeySpec 构造函数，捕获所有 AES key 创建
        try {
            var SecretKeySpec = Java.use('javax.crypto.spec.SecretKeySpec');
            SecretKeySpec.$init.overloads.forEach(function (overload) {
                overload.implementation = function () {
                    var result = overload.apply(this, arguments);
                    var algo = this.getAlgorithm();

                    if (algo && algo.indexOf('AES') !== -1) {
                        var keyHex = bytesToHex(this.getEncoded());
                        send({
                            type: 'aes_key_created',
                            algorithm: algo,
                            key: keyHex,
                            key_size: this.getEncoded().length * 8,
                            timestamp: Date.now()
                        });
                        console.log('[AES] Key created: ' + keyHex + ' (' + (this.getEncoded().length * 8) + ' bit)');
                    }

                    return result;
                };
            });
            console.log('[AES Hook] SecretKeySpec hooked');
        } catch (e) {
            console.log('[AES Hook] SecretKeySpec failed: ' + e.message);
        }

        console.log('[AES Hook] Ready');
    });
})();
