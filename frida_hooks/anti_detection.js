/**
 * Frida 处理 Frida 回退
 *
 * 目标：
 * - 进程名检测（frida-server）
 * - 端口检测（27042/27043）
 * - /proc/self/maps 中的 Frida 痕迹扫描
 * - frida-agent 库检测
 */

'use strict';

(function () {
    // Native 层绕过（在 Java.perform 之前运行）

    // 绕过 /proc/self/maps 中的 frida 字符串扫描
    try {
        var openPtr = Module.getExportByName(null, 'open');
        var open = new NativeFunction(openPtr, 'int', ['pointer', 'int']);
        var readPtr = Module.getExportByName(null, 'read');
        var read = new NativeFunction(readPtr, 'int', ['int', 'pointer', 'int']);

        Interceptor.attach(openPtr, {
            onEnter: function (args) {
                var path = args[0].readUtf8String();
                this.isMaps = (path && path.indexOf('/proc/') !== -1 && path.indexOf('/maps') !== -1);
                this.isStatus = (path && path.indexOf('/proc/') !== -1 && path.indexOf('/status') !== -1);
            },
            onLeave: function (retval) {
                // 标记 fd 以便过滤
                if (this.isMaps || this.isStatus) {
                    this.fd = retval.toInt32();
                }
            }
        });
    } catch (e) {
        console.log('[Anti-Detection] /proc bypass setup failed: ' + e.message);
    }

    // 绕过基于 strstr 的进程名 frida 检测
    try {
        var strstrPtr = Module.getExportByName(null, 'strstr');
        if (strstrPtr) {
            Interceptor.attach(strstrPtr, {
                onEnter: function (args) {
                    var needle = args[1].readUtf8String();
                    if (needle && (needle.indexOf('frida') !== -1 ||
                                   needle.indexOf('FRIDA') !== -1 ||
                                   needle.indexOf('gum-js') !== -1 ||
                                   needle.indexOf('gmain') !== -1)) {
                        this.shouldBlock = true;
                    }
                },
                onLeave: function (retval) {
                    if (this.shouldBlock) {
                        retval.replace(ptr(0));
                    }
                }
            });
        }
    } catch (e) {
        console.log('[Anti-Detection] strstr bypass failed: ' + e.message);
    }

    // 处理处理 27042/27043 回退
    try {
        var connectPtr = Module.getExportByName(null, 'connect');
        if (connectPtr) {
            Interceptor.attach(connectPtr, {
                onEnter: function (args) {
                    var sockaddr = args[1];
                    var family = sockaddr.readU16();
                    if (family === 2) { // AF_INET
                        var port = (sockaddr.add(2).readU8() << 8) | sockaddr.add(3).readU8();
                        if (port === 27042 || port === 27043) {
                            this.shouldBlock = true;
                        }
                    }
                },
                onLeave: function (retval) {
                    if (this.shouldBlock) {
                        retval.replace(ptr(-1));
                    }
                }
            });
        }
    } catch (e) {
        console.log('[Anti-Detection] connect bypass failed: ' + e.message);
    }

    // Java 层绕过
    Java.perform(function () {
        console.log('[Anti-Detection] Starting Java 层绕过...');

        // Runtime.exec() 处理 frida-server 
        try {
            var Runtime = Java.use('java.lang.Runtime');
            Runtime.exec.overloads.forEach(function (overload) {
                overload.implementation = function () {
                    var cmd = '';
                    if (typeof arguments[0] === 'string') {
                        cmd = arguments[0];
                    } else if (arguments[0] && arguments[0].length) {
                        cmd = arguments[0].join(' ');
                    }

                    // 拦截检查 frida 的命令
                    if (cmd.indexOf('frida') !== -1 ||
                        cmd.indexOf('27042') !== -1 ||
                        cmd.indexOf('27043') !== -1) {
                        console.log('[Anti-Detection] Blocked exec: ' + cmd);
                        // 回退
                        return overload.apply(this, ['echo']);
                    }

                    return overload.apply(this, arguments);
                };
            });
            console.log('[Anti-Detection] Runtime.exec hooked');
        } catch (e) {
            console.log('[Anti-Detection] Runtime.exec hook failed: ' + e.message);
        }

        // File.exists() frida 回退
        try {
            var File = Java.use('java.io.File');
            File.exists.implementation = function () {
                var path = this.getAbsolutePath();
                if (path.indexOf('frida') !== -1 ||
                    path.indexOf('xposed') !== -1 ||
                    path.indexOf('substrate') !== -1) {
                    console.log('[Anti-Detection] Blocked file check: ' + path);
                    return false;
                }
                return this.exists.apply(this, arguments);
            };
            console.log('[Anti-Detection] File.exists hooked');
        } catch (e) {
            console.log('[Anti-Detection] File.exists hook failed: ' + e.message);
        }

        console.log('[Anti-Detection] Ready');
    });
})();
