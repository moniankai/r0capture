Java.perform(function() {
    // 逻辑
    var tryClasses = [
        "com.ss.ttvideoengine.TTVideoEngine",
        "com.ss.ttvideoengine.VideoEngine",
        "com.ss.ttvideoengine.TTVideoEngineImpl",
        "com.ss.ttm.player.MediaPlayer",
        "com.ss.mediakit.medialoader.AVMDLDataLoader",
        "com.ss.mediakit.medialoader.MediaLoader",
        "com.bytedance.vod.VodPlayer",
        "com.bytedance.playerkit.player.Player",
        "com.bytedance.playerkit.player.PlayerImpl",
        "com.ss.ttvideoengine.DataLoaderHelper",
        "com.ss.ttvideoengine.utils.TTVideoEngineKeys",
        "com.ss.ttvideoengine.strategy.StrategyManager",
    ];

    tryClasses.forEach(function(name) {
        try {
            var cls = Java.use(name);
            send({found: name});

            // 
            var methods = cls.class.getDeclaredMethods();
            var mNames = [];
            for (var i = 0; i < methods.length; i++) {
                var mName = methods[i].getName();
                var lo = mName.toLowerCase();
                if (lo.indexOf("url") !== -1 || lo.indexOf("key") !== -1 ||
                    lo.indexOf("play") !== -1 || lo.indexOf("set") !== -1 ||
                    lo.indexOf("decrypt") !== -1 || lo.indexOf("source") !== -1 ||
                    lo.indexOf("drm") !== -1 || lo.indexOf("cenc") !== -1 ||
                    lo.indexOf("option") !== -1 || lo.indexOf("video") !== -1) {
                    mNames.push(mName);
                }
            }
            if (mNames.length > 0) {
                send({cls: name, methods: mNames.sort()});
            }
        } catch(e) {
            // 处理
        }
    });

    send({s: "scan done"});
});
