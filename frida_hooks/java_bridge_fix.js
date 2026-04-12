/**
 * Frida Java Bridge 修复 + TTVideoEngine Hook
 *
 * Android 9 Frida 16.x/17.x Java bridge 处理
 * Java bridge 可用后，Hook 字节系 TTVideoEngine 类
 * 目标：回退key 处理
 *
 * 目标：
 * frida -U -f com.phoenix.read -l java_bridge_fix.js --no-pause
 * frida -U com.phoenix.read -l java_bridge_fix.js (attach mode, preferred)
 */

'use strict';

function hexBytes(arr) {
    if (!arr) return '';
    var hex = '';
    for (var i = 0; i < arr.length; i++) {
        var b = (arr[i] & 0xFF).toString(16);
        hex += (b.length === 1 ? '0' : '') + b;
    }
    return hex;
}

function waitForJava(callback, maxRetries) {
    maxRetries = maxRetries || 60;
    var attempt = 0;
    var timer = setInterval(function () {
        attempt++;
        if (typeof Java !== 'undefined' && Java.available) {
            clearInterval(timer);
            console.log('[+] Java bridge ready after ' + attempt + ' x 500ms');
            callback();
            return;
        }
        if (attempt >= maxRetries) {
            clearInterval(timer);
            console.log('[!] Java bridge not available after ' + (maxRetries * 0.5) + 's');
            console.log('[!] typeof Java = ' + typeof Java);
            if (typeof Java !== 'undefined') {
                console.log('[!] Java.available = ' + Java.available);
            }
            console.log('[!] Try: frida -U com.phoenix.read (attach, not spawn)');
            console.log('[!] Or downgrade: pip install frida==16.2.1 frida-tools==16.2.1');
        }
    }, 500);
}

function hookTTVideoEngine() {
    Java.perform(function () {
        console.log('[+] Java.perform OK, Android ' + Java.androidVersion);

        // ====== Phase 1: 回退 ======
        var interestingClasses = [];
        Java.enumerateLoadedClasses({
            onMatch: function (name) {
                var lo = name.toLowerCase();
                if (lo.indexOf('decrypt') !== -1 || lo.indexOf('cenc') !== -1 ||
                    lo.indexOf('videokey') !== -1 || lo.indexOf('contentkey') !== -1 ||
                    lo.indexOf('mediakey') !== -1 || lo.indexOf('drm') !== -1 ||
                    lo.indexOf('ttvideoengine') !== -1 || lo.indexOf('medialoader') !== -1 ||
                    lo.indexOf('mdlapi') !== -1 || lo.indexOf('mdlloader') !== -1 ||
                    lo.indexOf('videoengine') !== -1 || lo.indexOf('ttmplayer') !== -1 ||
                    lo.indexOf('mediaplayer') !== -1 || lo.indexOf('ttmediaplayer') !== -1 ||
                    lo.indexOf('encryptor') !== -1) {
                    interestingClasses.push(name);
                }
            },
            onComplete: function () {
                console.log('[+] Found ' + interestingClasses.length + ' interesting classes:');
                interestingClasses.forEach(function (c) {
                    console.log('    ' + c);
                });
            }
        });

        // ====== Phase 2: Hook TTVideoEngine ======
        var ttveClasses = [
            'com.ss.ttvideoengine.TTVideoEngine',
            'com.ss.ttvideoengine.TTVideoEngineInterface',
        ];

        ttveClasses.forEach(function (className) {
            try {
                var clz = Java.use(className);
                console.log('[+] ' + className + ' found');

                // 枚举并 Hook 所有方法
                var methods = clz.class.getDeclaredMethods();
                methods.forEach(function (m) {
                    var name = m.getName();
                    var lo = name.toLowerCase();
                    // Hook 回退key处理回退
                    if (lo.indexOf('datasource') !== -1 || lo.indexOf('key') !== -1 ||
                        lo.indexOf('decrypt') !== -1 || lo.indexOf('play') !== -1 ||
                        lo.indexOf('prepare') !== -1 || lo.indexOf('cenc') !== -1 ||
                        lo.indexOf('drm') !== -1 || lo.indexOf('encrypt') !== -1 ||
                        lo.indexOf('setoptionstr') !== -1 || lo.indexOf('setoptionint') !== -1) {
                        try {
                            clz[name].overloads.forEach(function (overload) {
                                overload.implementation = function () {
                                    var argStr = [];
                                    for (var i = 0; i < arguments.length; i++) {
                                        var a = arguments[i];
                                        argStr.push(a !== null ? String(a).substring(0, 300) : 'null');
                                    }
                                    console.log('[TTVE] ' + name + '(' + argStr.join(', ') + ')');
                                    send({
                                        type: 'ttve_call',
                                        method: name,
                                        args: argStr
                                    });
                                    return overload.apply(this, arguments);
                                };
                            });
                        } catch (e) { /* skip un-hookable */ }
                    }
                });
            } catch (e) {
                console.log('[-] ' + className + ': ' + e.message);
            }
        });

        // ====== Phase 3: Hook MediaLoader / MDL ======
        var mdlClasses = [
            'com.ss.mediakit.medialoader.common.MediaLoaderApi',
            'com.ss.ttvideoengine.medialoader.TTMDLApi',
            'com.ss.mediakit.medialoader.AVMDLApi',
            'com.ss.mediakit.medialoader.model.AVMDLDataConfig',
        ];

        mdlClasses.forEach(function (className) {
            try {
                var clz = Java.use(className);
                console.log('[+] ' + className + ' found');
                // 处理
                var methods = clz.class.getDeclaredMethods();
                methods.forEach(function (m) {
                    console.log('    method: ' + m.getName());
                });
            } catch (e) {
                console.log('[-] ' + className + ': ' + e.message);
            }
        });

        // ====== Phase 4: Hook javax.crypto (with Java bridge fixed) ======
        try {
            var Cipher = Java.use('javax.crypto.Cipher');
            Cipher.init.overloads.forEach(function (overload) {
                overload.implementation = function () {
                    var mode = arguments[0];
                    var algo = this.getAlgorithm();
                    if (algo && algo.indexOf('AES') !== -1) {
                        var keyHex = '';
                        var ivHex = '';
                        try {
                            keyHex = hexBytes(arguments[1].getEncoded());
                        } catch (e) {}
                        try {
                            var IvSpec = Java.use('javax.crypto.spec.IvParameterSpec');
                            var iv = Java.cast(arguments[2], IvSpec);
                            ivHex = hexBytes(iv.getIV());
                        } catch (e) {}
                        console.log('[AES] ' + (mode === 2 ? 'DECRYPT' : 'ENCRYPT') +
                                    ' algo=' + algo + ' key=' + keyHex + ' iv=' + ivHex);
                        send({
                            type: 'aes_cipher_init',
                            mode: mode === 2 ? 'DECRYPT' : 'ENCRYPT',
                            algorithm: algo,
                            key: keyHex,
                            iv: ivHex
                        });
                    }
                    return overload.apply(this, arguments);
                };
            });
            console.log('[+] Cipher.init hooked');
        } catch (e) {
            console.log('[-] Cipher.init: ' + e.message);
        }

        // ====== Phase 5: Hook MediaCodec Java API ======
        try {
            var MediaCodec = Java.use('android.media.MediaCodec');
            MediaCodec.configure.overloads.forEach(function (overload) {
                overload.implementation = function () {
                    // 参数：format、surface、crypto、flags
                    var format = arguments[0];
                    var crypto = arguments[2];
                    console.log('[MediaCodec] configure format=' +
                                (format ? format.toString().substring(0, 300) : 'null') +
                                ' crypto=' + (crypto ? crypto.toString() : 'null'));
                    if (crypto) {
                        send({type: 'mediacodec_configure_crypto', crypto: crypto.toString()});
                    }
                    return overload.apply(this, arguments);
                };
            });
            console.log('[+] MediaCodec.configure hooked');
        } catch (e) {
            console.log('[-] MediaCodec: ' + e.message);
        }

        // ====== Phase 6: Hook MediaCrypto ======
        try {
            var MediaCrypto = Java.use('android.media.MediaCrypto');
            MediaCrypto.$init.overloads.forEach(function (overload) {
                overload.implementation = function () {
                    // 参数：uuid(UUID)、initData(byte[])
                    console.log('[MediaCrypto] init called');
                    if (arguments.length >= 2) {
                        console.log('[MediaCrypto] UUID=' + arguments[0]);
                        if (arguments[1]) {
                            console.log('[MediaCrypto] initData=' + hexBytes(arguments[1]));
                        }
                    }
                    send({type: 'mediacrypto_init'});
                    return overload.apply(this, arguments);
                };
            });
            console.log('[+] MediaCrypto hooked');
        } catch (e) {
            console.log('[-] MediaCrypto: ' + e.message);
        }

        console.log('[+] All hooks installed');
    });
}

// 主入口
waitForJava(hookTTVideoEngine);
