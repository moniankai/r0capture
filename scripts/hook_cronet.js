/**
 * Hook ByteDance Cronet (libsscronet.so) to capture video URLs and API responses.
 */

function readCStr(ptr) {
    if (!ptr || ptr.isNull()) return "null";
    try { return ptr.readUtf8String(); } catch(e) { return "?"; }
}

// Hook Cronet_UrlRequestParams_url_set to capture ALL URL requests
var paramsUrlSet = Module.findExportByName("libsscronet.so", "Cronet_UrlRequestParams_url_set");
if (paramsUrlSet) {
    Interceptor.attach(paramsUrlSet, {
        onEnter: function(args) {
            var url = readCStr(args[1]);
            if (url.length > 5) {
                var lower = url.toLowerCase();
                var isVideo = lower.indexOf("video") !== -1 || lower.indexOf(".mp4") !== -1 ||
                    lower.indexOf(".m3u8") !== -1 || lower.indexOf(".ts") !== -1 ||
                    lower.indexOf("play") !== -1 || lower.indexOf("media") !== -1 ||
                    lower.indexOf("stream") !== -1 || lower.indexOf("drama") !== -1 ||
                    lower.indexOf("episode") !== -1 || lower.indexOf("content") !== -1 ||
                    lower.indexOf("key") !== -1 || lower.indexOf("license") !== -1 ||
                    lower.indexOf("encrypt") !== -1 || lower.indexOf("decrypt") !== -1;

                if (isVideo) {
                    send({type: "video_url", url: url});
                    console.log("[VIDEO] " + url.substring(0, 250));
                } else {
                    send({type: "api_url", url: url});
                }
            }
        }
    });
    console.log("[HOOK] Cronet_UrlRequestParams_url_set");
} else {
    console.log("[MISS] Cronet_UrlRequestParams_url_set");
}

// Hook Cronet_UrlRequest_Start
var urlStart = Module.findExportByName("libsscronet.so", "Cronet_UrlRequest_Start");
if (urlStart) {
    Interceptor.attach(urlStart, {
        onEnter: function(args) {
            // Request object in args[0]
        }
    });
    console.log("[HOOK] Cronet_UrlRequest_Start");
}

// Hook OnReadCompleted to see response data
var onReadCompleted = Module.findExportByName("libsscronet.so", "Cronet_UrlRequestCallback_OnReadCompleted");
if (onReadCompleted) {
    Interceptor.attach(onReadCompleted, {
        onEnter: function(args) {
            // args: callback, request, info, buffer, bytesRead
            try {
                var bufferPtr = args[3];
                var bytesRead = args[4].toInt32();
                if (bytesRead > 0 && bytesRead < 100000) {
                    var getData = Module.findExportByName("libsscronet.so", "Cronet_Buffer_GetData");
                    if (getData) {
                        var getDataFn = new NativeFunction(getData, "pointer", ["pointer"]);
                        var dataPtr = getDataFn(bufferPtr);
                        if (dataPtr && !dataPtr.isNull()) {
                            try {
                                var text = dataPtr.readUtf8String(Math.min(bytesRead, 2000));
                                var lower = text.toLowerCase();
                                if (lower.indexOf("play_url") !== -1 || lower.indexOf("video_url") !== -1 ||
                                    lower.indexOf("media_url") !== -1 || lower.indexOf("m3u8") !== -1 ||
                                    lower.indexOf("mp4") !== -1 || lower.indexOf("content_key") !== -1 ||
                                    lower.indexOf("decrypt") !== -1 || lower.indexOf("kid") !== -1) {
                                    send({type: "response_data", size: bytesRead, text: text.substring(0, 2000)});
                                    console.log("[RESP] " + text.substring(0, 200));
                                }
                            } catch(e) {}
                        }
                    }
                }
            } catch(e) {}
        }
    });
    console.log("[HOOK] OnReadCompleted");
}

console.log("[READY] Switch to a new video!");
