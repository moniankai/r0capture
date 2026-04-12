/**
 * libttmplayer.so 的 ARM64 AES 硬件指令 Hook
 *
 * 通过 Hook 包含 AESD 硬件指令的函数捕获 AES 解密 key。
 * AESD 是 AES 解密硬件指令。由于 libttmplayer.so
 * 目标：回退 AES 处理 CENC
 * 目标： key 处理
 *
 * 目标：
 * 1. 扫描 libttmplayer.so 中的 AESD 指令模式
 * 2. 将其聚合为 AES 解密函数（10+ 条 AESD = AES-128）
 * 3. Hook 函数入口以捕获 key/IV 参数
 * 4. 同时扫描 AES S-box 常量作为回退（软件 AES 路径）
 *
 * 目标：
 * frida -U com.phoenix.read -l aes_hw_hook.js
 */

'use strict';

(function () {
    var TARGET_MODULE = "libttmplayer.so";

    function hexDump(ptr, len) {
        var h = "";
        try {
            for (var i = 0; i < len; i++) {
                var b = (ptr.add(i).readU8() & 0xFF).toString(16);
                h += (b.length === 1 ? "0" : "") + b;
            }
        } catch (e) {
            h += "<?read_error>";
        }
        return h;
    }

    function hexDumpFormatted(ptr, len) {
        var lines = [];
        try {
            for (var i = 0; i < len; i += 16) {
                var hex = "";
                var ascii = "";
                for (var j = 0; j < 16 && i + j < len; j++) {
                    var b = ptr.add(i + j).readU8();
                    var bh = (b & 0xFF).toString(16);
                    hex += (bh.length === 1 ? "0" : "") + bh + " ";
                    ascii += (b >= 0x20 && b < 0x7f) ? String.fromCharCode(b) : ".";
                }
                lines.push(hex.padEnd(49) + ascii);
            }
        } catch (e) {
            lines.push("<?read_error>");
        }
        return lines.join("\n");
    }

    function findModule() {
        var mod = Process.findModuleByName(TARGET_MODULE);
        if (mod) {
            return mod;
        }
        // 逻辑
        var modules = Process.enumerateModules();
        for (var i = 0; i < modules.length; i++) {
            if (modules[i].name.indexOf("ttmplayer") !== -1) {
                return modules[i];
            }
        }
        return null;
    }

    var mod = findModule();
    if (!mod) {
        console.log("[!] " + TARGET_MODULE + " not loaded yet");
        console.log("[*] Waiting for module load...");

        // 处理
        var dlopen = Module.findExportByName(null, "android_dlopen_ext") ||
                     Module.findExportByName(null, "dlopen");
        if (dlopen) {
            Interceptor.attach(dlopen, {
                onEnter: function (args) {
                    var path = args[0].readCString();
                    if (path && path.indexOf("ttmplayer") !== -1) {
                        console.log("[*] " + TARGET_MODULE + " loading: " + path);
                        this.isTarget = true;
                    }
                },
                onLeave: function (retval) {
                    if (this.isTarget) {
                        console.log("[*] " + TARGET_MODULE + " loaded, starting scan...");
                        setTimeout(function () {
                            var m = findModule();
                            if (m) scanModule(m);
                        }, 1000);
                    }
                }
            });
        }
        return;
    }

    scanModule(mod);

    function scanModule(mod) {
        console.log("[*] " + mod.name + " base=" + mod.base + " size=0x" + mod.size.toString(16));

        var aesdLocations = [];
        var aeseLocations = [];

        // 扫描 AESD 指令
        // AESD Vd.16B, Vn.16B: opcode 0x4E285800 (mask 0xFFFFFC00)
        // Little-endian bytes: 00 58 28 4E (处理 10 )
        // 处理: xx 58 28 4E where xx = 0x00-0xFF
        console.log("[*] Scanning for AESD instructions...");

        // 1MB 处理
        var chunkSize = 1024 * 1024;
        var offset = 0;

        while (offset < mod.size) {
            var scanLen = Math.min(chunkSize, mod.size - offset);
            var scanBase = mod.base.add(offset);

            // 按 32 位指令读取
            for (var i = 0; i < scanLen; i += 4) {
                try {
                    var insn = scanBase.add(i).readU32();

                    // AESD: 0100 1110 0010 1000 0101 1xxx xxxx xxxx
                    if ((insn & 0xFFFFFC00) === 0x4E285800) {
                        aesdLocations.push(scanBase.add(i));
                    }
                    // AESE: 0100 1110 0010 1000 0100 1xxx xxxx xxxx
                    else if ((insn & 0xFFFFFC00) === 0x4E284800) {
                        aeseLocations.push(scanBase.add(i));
                    }
                } catch (e) {
                    break;
                }
            }
            offset += scanLen;
        }

        console.log("[*] Found " + aesdLocations.length + " AESD, " +
                    aeseLocations.length + " AESE instructions");

        if (aesdLocations.length === 0 && aeseLocations.length === 0) {
            console.log("[*] No HW AES instructions found. Trying S-box scan...");
            scanSbox(mod);
            return;
        }

        // AESD AESE
        var locations = aesdLocations.length > 0 ? aesdLocations : aeseLocations;
        var label = aesdLocations.length > 0 ? "AESD" : "AESE";
        var groups = groupConsecutive(locations, 512);

        console.log("[*] Grouped into " + groups.length + " potential AES functions (" + label + ")");

        for (var g = 0; g < groups.length; g++) {
            var group = groups[g];
            console.log("  Group " + g + ": " + group.length + " " + label +
                        " at +" + group[0].sub(mod.base).toString(16) +
                        " to +" + group[group.length - 1].sub(mod.base).toString(16));
        }

        // Hook 处理10+ = AES-128
        var hooked = 0;
        for (var g = 0; g < groups.length && hooked < 5; g++) {
            if (groups[g].length >= 8) {
                hookAesFunction(mod, groups[g], g, label);
                hooked++;
            }
        }

        if (hooked === 0) {
            console.log("[!] No groups with 8+ AES instructions found");
            // 无论如何 Hook 最大分组
            if (groups.length > 0) {
                groups.sort(function (a, b) { return b.length - a.length; });
                hookAesFunction(mod, groups[0], 0, label);
            }
        }

        // S-box 处理
        scanSbox(mod);
    }

    function groupConsecutive(locations, maxGap) {
        if (locations.length === 0) return [];

        var groups = [];
        var current = [locations[0]];

        for (var i = 1; i < locations.length; i++) {
            var gap = locations[i].sub(locations[i - 1]).toInt32();
            if (gap > 0 && gap <= maxGap) {
                current.push(locations[i]);
            } else {
                groups.push(current);
                current = [locations[i]];
            }
        }
        groups.push(current);
        return groups;
    }

    function hookAesFunction(mod, group, groupIdx, label) {
        var firstInsn = group[0];

        // 处理
        var funcStart = findFuncPrologue(firstInsn, 2048);

        if (!funcStart) {
            console.log("[!] Group " + groupIdx + ": no prologue found, using first " +
                        label + " - 64 as entry");
            funcStart = firstInsn.sub(64);
        }

        var offset = funcStart.sub(mod.base);
        console.log("[*] Hooking group " + groupIdx + " at " + funcStart +
                    " (+" + offset.toString(16) + ")");

        var callCount = 0;
        var capturedKeys = {};

        try {
            Interceptor.attach(funcStart, {
                onEnter: function (args) {
                    callCount++;
                    if (callCount > 100) return;

                    // 6 处理 key/iv/data 
                    var findings = [];
                    for (var i = 0; i < 6; i++) {
                        try {
                            var p = args[i];
                            if (p.isNull()) continue;
                            var pVal = p.toInt32 ? p.toInt32() : 0;
                            if (pVal < 0x1000 && pVal >= 0) {
                                // 逻辑
                                findings.push({arg: i, type: 'int', value: pVal});
                                continue;
                            }

                            // 尝试读取 32 字节
                            var bytes16 = hexDump(p, 16);
                            var bytes32 = hexDump(p, 32);

                            // 回退 key处理 0
                            var nonZero = 0;
                            var uniqueBytes = {};
                            for (var j = 0; j < 16; j++) {
                                var b = p.add(j).readU8();
                                if (b !== 0) nonZero++;
                                uniqueBytes[b] = true;
                            }

                            if (nonZero >= 12 && Object.keys(uniqueBytes).length >= 8) {
                                findings.push({
                                    arg: i,
                                    type: 'potential_key',
                                    hex16: bytes16,
                                    hex32: bytes32
                                });

                                // 
                                if (!capturedKeys[bytes16]) {
                                    capturedKeys[bytes16] = true;
                                    console.log("\n[KEY?] Group " + groupIdx + " call #" + callCount);
                                    console.log("  arg[" + i + "] = " + bytes16);
                                    console.log("  full 32B: " + bytes32);
                                    send({
                                        type: 'aes_hw_key_candidate',
                                        group: groupIdx,
                                        call: callCount,
                                        arg: i,
                                        key16: bytes16,
                                        key32: bytes32
                                    });
                                }
                            } else if (nonZero > 0) {
                                findings.push({
                                    arg: i,
                                    type: 'data',
                                    hex16: bytes16
                                });
                            }
                        } catch (e) { /* skip unreadable */ }
                    }

                    if (callCount <= 10 && findings.length > 0) {
                        console.log("[AES] Group " + groupIdx + " call #" + callCount +
                                    " args: " + JSON.stringify(findings.map(function (f) {
                                        return "a" + f.arg + "=" + (f.hex16 || f.value);
                                    })));
                    }
                },
                onLeave: function (retval) {
                    if (callCount <= 10) {
                        // 处理
                        var rv = retval.toInt32();
                        if (rv !== 0) {
                            // 处理回退
                        }
                    }
                }
            });
            console.log("[+] Group " + groupIdx + " hooked fully");
        } catch (e) {
            console.log("[!] Group " + groupIdx + " hook failed: " + e);
        }
    }

    function findFuncPrologue(addr, maxLookback) {
        // ARM64 函数序言：
        // STP X29, X30, [SP, #imm]! => 0xA98x7BFD or 0xA9Bx7BFD
        // SUB SP, SP, #imm => 0xD10xxxFF
        // STP Xn, Xm, [SP, #imm] => 0xA90xxxxx

        for (var off = 4; off <= maxLookback; off += 4) {
            try {
                var candidate = addr.sub(off);
                var insn = candidate.readU32();

                // STP x29, x30, [sp, #imm]! (pre-index)
                if ((insn & 0xFFE07FFF) === 0xA9807BFD) {
                    return candidate;
                }
                // PACIASP (ARMv8.3 pointer auth) - 处理
                if (insn === 0xD503233F) {
                    return candidate;
                }
            } catch (e) {
                break;
            }
        }
        return null;
    }

    function scanSbox(mod) {
        // AES S-box: 63 7c 77 7b f2 6b 6f c5 30 01 67 2b fe d7 ab 76
        console.log("[*] Scanning for AES S-box constant...");

        Memory.scan(mod.base, mod.size, "63 7c 77 7b f2 6b 6f c5 30 01 67 2b fe d7 ab 76", {
            onMatch: function (address, size) {
                var offset = address.sub(mod.base);
                console.log("[*] AES S-box at " + address +
                            " (+" + offset.toString(16) + ")");
                send({
                    type: 'aes_sbox_found',
                    address: address.toString(),
                    offset: '0x' + offset.toString(16)
                });

                // S-box .rodata AES 处理
                // Ghidra/IDA 处理处理 key schedule
            },
            onComplete: function () {
                console.log("[*] S-box scan complete");
            },
            onError: function (e) {
                console.log("[!] S-box scan error: " + e);
            }
        });

        // 回退 S-box处理
        // Inverse S-box: 52 09 6a d5 30 36 a5 38 bf 40 a3 9e 81 f3 d7 fb
        Memory.scan(mod.base, mod.size, "52 09 6a d5 30 36 a5 38 bf 40 a3 9e 81 f3 d7 fb", {
            onMatch: function (address) {
                var offset = address.sub(mod.base);
                console.log("[*] Inverse S-box at " + address +
                            " (+" + offset.toString(16) + ")");
                send({
                    type: 'aes_inv_sbox_found',
                    address: address.toString(),
                    offset: '0x' + offset.toString(16)
                });
            },
            onComplete: function () {},
            onError: function (e) {}
        });

        // AES-CTR 回退 CTR 处理
        // CTR 处理 block 处理
        // 查找 RCON 常量表：01 02 04 08 10 20 40 80 1b 36
        Memory.scan(mod.base, mod.size, "01 00 00 00 02 00 00 00 04 00 00 00 08 00 00 00", {
            onMatch: function (address) {
                var offset = address.sub(mod.base);
                console.log("[*] AES RCON table at " + address +
                            " (+" + offset.toString(16) + ")");
            },
            onComplete: function () {},
            onError: function (e) {}
        });
    }
})();
