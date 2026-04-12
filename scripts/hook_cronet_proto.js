/**
 * Enhanced Cronet Hook - Capture raw binary protobuf responses.
 *
 * Previous version only captured text-decodable responses.
 * This version captures ALL responses as raw binary for offline
 * protobuf decoding, and also attempts inline varint parsing
 * to detect key-like binary fields.
 *
 * 目标：
 * frida -U com.phoenix.read -l hook_cronet_proto.js
 *
 * Companion script: scripts/decode_protobuf.py
 */

'use strict';

function readCStr(ptr) {
    if (!ptr || ptr.isNull()) return "";
    try { return ptr.readUtf8String(); } catch (e) { return ""; }
}

function hexDump(ptr, len) {
    var h = "";
    for (var i = 0; i < len; i++) {
        var b = (ptr.add(i).readU8() & 0xFF).toString(16);
        h += (b.length === 1 ? "0" : "") + b;
    }
    return h;
}

// URL 
var currentUrls = [];
var responseBuffers = {};
var respId = 0;

// Cronet API 
var getUrl = Module.findExportByName("libsscronet.so", "Cronet_UrlResponseInfo_url_get");
var getStatusCode = Module.findExportByName("libsscronet.so",
    "Cronet_UrlResponseInfo_http_status_code_get");
var getData = Module.findExportByName("libsscronet.so", "Cronet_Buffer_GetData");

var getUrlFn = getUrl ? new NativeFunction(getUrl, "pointer", ["pointer"]) : null;
var getStatusCodeFn = getStatusCode ? new NativeFunction(getStatusCode, "int", ["pointer"]) : null;
var getDataFn = getData ? new NativeFunction(getData, "pointer", ["pointer"]) : null;

// Hook URL 处理 URL
var paramsUrlSet = Module.findExportByName("libsscronet.so",
    "Cronet_UrlRequestParams_url_set");
if (paramsUrlSet) {
    Interceptor.attach(paramsUrlSet, {
        onEnter: function (args) {
            var url = readCStr(args[1]);
            if (url.length > 5) {
                currentUrls.push(url);
                // 回退 50 
                if (currentUrls.length > 50) currentUrls.shift();

                var lo = url.toLowerCase();
                // 处理/key API
                if (lo.indexOf("api") !== -1 || lo.indexOf("drama") !== -1 ||
                    lo.indexOf("video") !== -1 || lo.indexOf("play") !== -1 ||
                    lo.indexOf("episode") !== -1 || lo.indexOf("key") !== -1 ||
                    lo.indexOf("license") !== -1 || lo.indexOf("content") !== -1) {
                    console.log("[URL] " + url.substring(0, 300));
                }
            }
        }
    });
    console.log("[OK] URL capture active");
}

// Hook OnResponseStarted
var onRespStarted = Module.findExportByName("libsscronet.so",
    "Cronet_UrlRequestCallback_OnResponseStarted");
if (onRespStarted) {
    Interceptor.attach(onRespStarted, {
        onEnter: function (args) {
            respId++;
            var url = "";
            var statusCode = 0;

            if (getUrlFn) {
                try {
                    var urlPtr = getUrlFn(args[2]);
                    url = readCStr(urlPtr);
                } catch (e) {}
            }
            if (getStatusCodeFn) {
                try { statusCode = getStatusCodeFn(args[2]); } catch (e) {}
            }

            responseBuffers[respId] = {
                url: url,
                status: statusCode,
                chunks: [],
                totalSize: 0
            };
        }
    });
    console.log("[OK] Response tracking active");
}

// Hook OnReadCompleted处理处理
var onReadCompleted = Module.findExportByName("libsscronet.so",
    "Cronet_UrlRequestCallback_OnReadCompleted");
if (onReadCompleted) {
    Interceptor.attach(onReadCompleted, {
        onEnter: function (args) {
            try {
                var bufferPtr = args[3];
                var bytesRead = args[4].toInt32();

                if (bytesRead <= 0 || bytesRead > 2000000) return;
                if (!getDataFn) return;

                var dataPtr = getDataFn(bufferPtr);
                if (!dataPtr || dataPtr.isNull()) return;

                // 处理
                var resp = responseBuffers[respId] || {url: "?", chunks: [], totalSize: 0};
                resp.totalSize += bytesRead;
                resp.chunks.push(bytesRead);
                responseBuffers[respId] = resp;

                // 处理 500KB 处理
                if (resp.totalSize < 500000) {
                    var rawData = dataPtr.readByteArray(bytesRead);

                    send({
                        type: "cronet_raw",
                        id: respId,
                        url: resp.url.substring(0, 500),
                        chunkIdx: resp.chunks.length - 1,
                        chunkSize: bytesRead,
                        totalSize: resp.totalSize
                    }, rawData);

                    // 处理
                    analyzeResponseChunk(dataPtr, bytesRead, resp.url, respId);
                }
            } catch (e) {}
        }
    });
    console.log("[OK] Raw binary capture active");
}

// Hook OnSucceeded处理
var onSucceeded = Module.findExportByName("libsscronet.so",
    "Cronet_UrlRequestCallback_OnSucceeded");
if (onSucceeded) {
    Interceptor.attach(onSucceeded, {
        onEnter: function (args) {
            var resp = responseBuffers[respId];
            if (resp && resp.totalSize > 0) {
                send({
                    type: "cronet_complete",
                    id: respId,
                    url: resp.url.substring(0, 500),
                    totalSize: resp.totalSize,
                    chunks: resp.chunks.length
                });
            }
        }
    });
}

function analyzeResponseChunk(dataPtr, size, url, id) {
    // 逻辑 key 

    // 1. UTF-8 回退JSON 
    try {
        var text = dataPtr.readUtf8String(Math.min(size, 4000));
        if (text) {
            var lo = text.toLowerCase();
            // JSON key 
            if (lo.indexOf("play_url") !== -1 || lo.indexOf("video_url") !== -1 ||
                lo.indexOf("content_key") !== -1 || lo.indexOf("decrypt") !== -1 ||
                lo.indexOf("media_key") !== -1 || lo.indexOf("kid") !== -1 ||
                lo.indexOf("key_id") !== -1 || lo.indexOf("video_key") !== -1) {
                console.log("\n>>> KEY FIELDS IN RESPONSE #" + id);
                console.log("    URL: " + url.substring(0, 200));
                console.log("    Body: " + text.substring(0, 500));
                send({
                    type: "key_field_found",
                    id: id,
                    url: url,
                    preview: text.substring(0, 2000)
                });
            }
        }
    } catch (e) { /*  UTF-8 protobuf */ }

    // 2. protobuf 
    // Protobuf 处理 tag varint 
    try {
        var first = dataPtr.readU8();
        // protobuf tag
        // 0x08 = field 1, varint
        // 0x0a = field 1, length-delimited
        // 0x10 = field 2, varint
        // 0x12 = field 2, length-delimited
        // 0x1a = field 3, length-delimited
        if (first === 0x08 || first === 0x0a || first === 0x10 ||
            first === 0x12 || first === 0x1a || first === 0x22) {
            // 回退 AES key 16 
            // protobuf length-delimited 处理
            scanForKeyBytes(dataPtr, size, id);
        }
    } catch (e) {}
}

function scanForKeyBytes(dataPtr, size, respId) {
    // protobuf length-delimited 回退
    // [field_tag] [length_varint] [bytes...]
    // AES-128 key 16 AES-256 32 

    try {
        for (var i = 0; i < size - 17; i++) {
            var tag = dataPtr.add(i).readU8();
            // Length-delimited wire type = tag & 0x07 === 2
            if ((tag & 0x07) !== 2) continue;

            var lenByte = dataPtr.add(i + 1).readU8();

            // 16 payloadAES-128 key
            if (lenByte === 16 && i + 2 + 16 <= size) {
                var keyCandidate = dataPtr.add(i + 2);
                // 
                var nonZero = 0;
                var unique = {};
                for (var j = 0; j < 16; j++) {
                    var b = keyCandidate.add(j).readU8();
                    if (b !== 0) nonZero++;
                    unique[b] = true;
                }

                if (nonZero >= 12 && Object.keys(unique).length >= 10) {
                    var keyHex = hexDump(keyCandidate, 16);
                    console.log("\n!!! POTENTIAL AES-128 KEY IN PROTO RESPONSE #" + respId);
                    console.log("    Offset: " + i + " field_tag: 0x" + tag.toString(16));
                    console.log("    Key: " + keyHex);
                    send({
                        type: "proto_key_candidate",
                        respId: respId,
                        offset: i,
                        fieldTag: tag,
                        keyHex: keyHex,
                        keySize: 128
                    });
                }
            }

            // 32 payloadAES-256 key
            if (lenByte === 32 && i + 2 + 32 <= size) {
                var keyCandidate32 = dataPtr.add(i + 2);
                var nonZero32 = 0;
                var unique32 = {};
                for (var j = 0; j < 32; j++) {
                    var b = keyCandidate32.add(j).readU8();
                    if (b !== 0) nonZero32++;
                    unique32[b] = true;
                }

                if (nonZero32 >= 24 && Object.keys(unique32).length >= 16) {
                    var keyHex32 = hexDump(keyCandidate32, 32);
                    console.log("\n!!! POTENTIAL AES-256 KEY IN PROTO RESPONSE #" + respId);
                    console.log("    Offset: " + i + " field_tag: 0x" + tag.toString(16));
                    console.log("    Key: " + keyHex32);
                    send({
                        type: "proto_key_candidate",
                        respId: respId,
                        offset: i,
                        fieldTag: tag,
                        keyHex: keyHex32,
                        keySize: 256
                    });
                }
            }
        }
    } catch (e) {}
}

console.log("[READY] Play a new video to capture responses!");
