/**
 * ExoPlayer Hook处理 URL 处理
 *
 * 目标：
 * - com.google.android.exoplayer2.source.MediaSource
 * - com.google.android.exoplayer2.upstream.DataSpec
 * - com.google.android.exoplayer2.Player.Listener
 */

'use strict';

(function () {
    Java.perform(function () {
        console.log('[ExoPlayer Hook] Starting...');

        // Hook DataSpec 处理 URL
        try {
            var DataSpec = Java.use('com.google.android.exoplayer2.upstream.DataSpec');
            DataSpec.$init.overloads.forEach(function (overload) {
                overload.implementation = function () {
                    var result = overload.apply(this, arguments);
                    var uri = this.uri.value;
                    if (uri) {
                        var urlStr = uri.toString();
                        if (urlStr.indexOf('.m3u8') !== -1 ||
                            urlStr.indexOf('.ts') !== -1 ||
                            urlStr.indexOf('.mp4') !== -1 ||
                            urlStr.indexOf('/video/') !== -1 ||
                            urlStr.indexOf('/hls/') !== -1) {
                            send({
                                type: 'exoplayer_url',
                                url: urlStr,
                                timestamp: Date.now()
                            });
                            console.log('[ExoPlayer] URL: ' + urlStr.substring(0, 120));
                        }
                    }
                    return result;
                };
            });
            console.log('[ExoPlayer Hook] DataSpec hooked');
        } catch (e) {
            console.log('[ExoPlayer Hook] DataSpec not found: ' + e.message);
        }

        // Hook MediaItem.Builder 处理
        try {
            var MediaItemBuilder = Java.use('com.google.android.exoplayer2.MediaItem$Builder');
            MediaItemBuilder.setUri.overloads.forEach(function (overload) {
                overload.implementation = function () {
                    var uri = arguments[0];
                    if (uri) {
                        var urlStr = uri.toString();
                        send({
                            type: 'exoplayer_media_item',
                            url: urlStr,
                            timestamp: Date.now()
                        });
                        console.log('[ExoPlayer] MediaItem URI: ' + urlStr.substring(0, 120));
                    }
                    return overload.apply(this, arguments);
                };
            });
            console.log('[ExoPlayer Hook] MediaItem.Builder hooked');
        } catch (e) {
            console.log('[ExoPlayer Hook] MediaItem.Builder not found: ' + e.message);
        }

        // Hook SimpleExoPlayer / ExoPlayer 状态变化
        try {
            var playerClasses = [
                'com.google.android.exoplayer2.SimpleExoPlayer',
                'com.google.android.exoplayer2.ExoPlayerImpl'
            ];
            playerClasses.forEach(function (className) {
                try {
                    var PlayerClass = Java.use(className);
                    if (PlayerClass.setMediaSource) {
                        PlayerClass.setMediaSource.overloads.forEach(function (overload) {
                            overload.implementation = function () {
                                send({
                                    type: 'exoplayer_state',
                                    event: 'setMediaSource',
                                    class: className,
                                    timestamp: Date.now()
                                });
                                return overload.apply(this, arguments);
                            };
                        });
                    }
                    console.log('[ExoPlayer Hook] ' + className + ' hooked');
                } catch (e) {
                    // 处理
                }
            });
        } catch (e) {
            console.log('[ExoPlayer Hook] Player class not found: ' + e.message);
        }

        console.log('[ExoPlayer Hook] Ready');
    });
})();
