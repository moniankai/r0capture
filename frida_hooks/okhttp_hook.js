/**
 * OkHttp Hook HTTP 处理
 *
 * 目标：
 * - okhttp3.OkHttpClient
 * - okhttp3.Request
 * - okhttp3.Response
 * - okhttp3.Interceptor.Chain
 */

'use strict';

(function () {
    Java.perform(function () {
        console.log('[OkHttp Hook] Starting...');

        // Hook OkHttpClient.newCall 处理
        try {
            var OkHttpClient = Java.use('okhttp3.OkHttpClient');
            var RealCall = Java.use('okhttp3.RealCall');

            RealCall.execute.implementation = function () {
                var request = this.request();
                logRequest(request);
                var response = this.execute.apply(this, arguments);
                logResponse(request, response);
                return response;
            };

            RealCall.enqueue.implementation = function (callback) {
                var request = this.request();
                logRequest(request);
                return this.enqueue.apply(this, arguments);
            };

            console.log('[OkHttp Hook] RealCall hooked');
        } catch (e) {
            console.log('[OkHttp Hook] RealCall not found: ' + e.message);
        }

        // Hook Interceptor.Chain.proceed 处理
        try {
            var interceptorClasses = [
                'okhttp3.internal.http.RealInterceptorChain',
                'okhttp3.internal.http.BridgeInterceptor',
            ];

            interceptorClasses.forEach(function (className) {
                try {
                    var ChainClass = Java.use(className);
                    if (ChainClass.proceed) {
                        ChainClass.proceed.overloads.forEach(function (overload) {
                            overload.implementation = function () {
                                var request = arguments[0] || this.request();
                                try {
                                    var url = request.url().toString();
                                    if (isVideoUrl(url)) {
                                        logRequest(request);
                                    }
                                } catch (e) {}
                                return overload.apply(this, arguments);
                            };
                        });
                    }
                } catch (e) {}
            });
        } catch (e) {
            console.log('[OkHttp Hook] Interceptor chain not found: ' + e.message);
        }

        function isVideoUrl(url) {
            return url.indexOf('.m3u8') !== -1 ||
                   url.indexOf('.ts') !== -1 ||
                   url.indexOf('.mp4') !== -1 ||
                   url.indexOf('/video/') !== -1 ||
                   url.indexOf('/hls/') !== -1 ||
                   url.indexOf('/stream/') !== -1;
        }

        function logRequest(request) {
            try {
                var url = request.url().toString();
                var method = request.method();
                var headers = {};

                var headerNames = request.headers();
                for (var i = 0; i < headerNames.size(); i++) {
                    var name = headerNames.name(i);
                    var value = headerNames.value(i);
                    headers[name] = value;
                }

                send({
                    type: 'okhttp_request',
                    url: url,
                    method: method,
                    headers: headers,
                    is_video: isVideoUrl(url),
                    timestamp: Date.now()
                });

                if (isVideoUrl(url)) {
                    console.log('[OkHttp] ' + method + ' ' + url.substring(0, 150));
                }
            } catch (e) {
                console.log('[OkHttp] logRequest error: ' + e.message);
            }
        }

        function logResponse(request, response) {
            try {
                var url = request.url().toString();
                var code = response.code();

                if (isVideoUrl(url)) {
                    var contentType = response.header('Content-Type') || '';
                    var contentLength = response.header('Content-Length') || '0';

                    send({
                        type: 'okhttp_response',
                        url: url,
                        status_code: code,
                        content_type: contentType,
                        content_length: parseInt(contentLength),
                        timestamp: Date.now()
                    });
                    console.log('[OkHttp] Response ' + code + ' ' + contentType + ' ' + url.substring(0, 100));
                }
            } catch (e) {}
        }

        console.log('[OkHttp Hook] Ready');
    });
})();
