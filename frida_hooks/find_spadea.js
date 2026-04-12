Java.perform(function() {
    // 搜索处理 spadea 的类/方法
    // Hook VideoInfo.setSpadea 回退
    var VideoInfo = Java.use("com.ss.ttvideoengine.model.VideoInfo");
    var fields = VideoInfo.class.getDeclaredFields();
    var methods = VideoInfo.class.getDeclaredMethods();

    send({s: "VideoInfo fields: " + fields.length + ", methods: " + methods.length});

    // 查找 spadea 相关方法
    var spadeaMethods = [];
    for (var i = 0; i < methods.length; i++) {
        var name = methods[i].getName();
        if (name.toLowerCase().indexOf("spadea") !== -1 ||
            name.toLowerCase().indexOf("key") !== -1 ||
            name.toLowerCase().indexOf("encrypt") !== -1 ||
            name.toLowerCase().indexOf("decrypt") !== -1) {
            spadeaMethods.push(name);
        }
    }
    send({s: "Spadea/Key methods: " + JSON.stringify(spadeaMethods)});

    // TTVideoEngineImpl 查找 spadea 相关方法
    try {
        var Impl = Java.use("com.ss.ttvideoengine.TTVideoEngineImpl");
        var implMethods = Impl.class.getDeclaredMethods();
        var implSpadea = [];
        for (var i = 0; i < implMethods.length; i++) {
            var name = implMethods[i].getName();
            if (name.toLowerCase().indexOf("spadea") !== -1 ||
                name.toLowerCase().indexOf("intertrustdrm") !== -1 ||
                name.toLowerCase().indexOf("decryptionkey") !== -1 ||
                name.toLowerCase().indexOf("encodedkey") !== -1) {
                implSpadea.push(name + "(" + implMethods[i].getParameterTypes().length + " args)");
            }
        }
        send({s: "TTVideoEngineImpl spadea methods: " + JSON.stringify(implSpadea)});

        // Hook _initIntertrustDrm处理 spadea
        if (Impl._initIntertrustDrm) {
            Impl._initIntertrustDrm.overloads.forEach(function(ov) {
                ov.implementation = function() {
                    send({t: "initDrm", args: arguments.length});
                    for (var i = 0; i < arguments.length; i++) {
                        var a = arguments[i];
                        if (a !== null) {
                            send({t: "initDrm_arg", idx: i, val: String(a).substring(0, 300)});
                        }
                    }
                    return ov.apply(this, arguments);
                };
            });
            send({s: "_initIntertrustDrm hooked"});
        }
    } catch(e) {
        send({s: "Impl search err: " + e});
    }

    // 在 DataLoaderHelper 中搜索 spadea
    try {
        var DLH = Java.use("com.ss.ttvideoengine.DataLoaderHelper");
        var dlhMethods = DLH.class.getDeclaredMethods();
        var dlhSpadea = [];
        for (var i = 0; i < dlhMethods.length; i++) {
            var name = dlhMethods[i].getName();
            if (name.toLowerCase().indexOf("spadea") !== -1 ||
                name.toLowerCase().indexOf("key") !== -1 ||
                name.toLowerCase().indexOf("encrypt") !== -1 ||
                name.toLowerCase().indexOf("proxy") !== -1) {
                dlhSpadea.push(name);
            }
        }
        send({s: "DataLoaderHelper key methods: " + JSON.stringify(dlhSpadea)});
    } catch(e) {}

    // 在 AVMDLDataLoader 中搜索 spadea
    try {
        var MDL = Java.use("com.ss.mediakit.medialoader.AVMDLDataLoader");
        var mdlMethods = MDL.class.getDeclaredMethods();
        var mdlKey = [];
        for (var i = 0; i < mdlMethods.length; i++) {
            var name = mdlMethods[i].getName();
            if (name.toLowerCase().indexOf("key") !== -1 ||
                name.toLowerCase().indexOf("encrypt") !== -1 ||
                name.toLowerCase().indexOf("spadea") !== -1 ||
                name.toLowerCase().indexOf("drm") !== -1) {
                mdlKey.push(name);
            }
        }
        send({s: "AVMDLDataLoader key methods: " + JSON.stringify(mdlKey)});
    } catch(e) {}

    send({s: "DONE"});
});
