Java.perform(function() {
    var Engine = Java.use("com.ss.ttvideoengine.TTVideoEngine");
    var methods = Engine.class.getDeclaredMethods();

    // Hook 
    var keywords = ["set", "play", "url", "key", "decrypt", "source", "drm", "token", "video", "direct", "local", "encoded", "auth", "model", "info"];
    var hooked = 0;

    for (var i = 0; i < methods.length; i++) {
        var mName = methods[i].getName();
        var lo = mName.toLowerCase();
        var match = false;
        for (var k = 0; k < keywords.length; k++) {
            if (lo.indexOf(keywords[k]) !== -1) { match = true; break; }
        }
        if (!match) continue;

        // Hook 
        try {
            var overloads = Engine[mName].overloads;
            if (!overloads || overloads.length === 0) continue;

            overloads.forEach(function(ov) {
                var name = mName; // 
                ov.implementation = function() {
                    var argStrs = [];
                    for (var j = 0; j < arguments.length; j++) {
                        var a = arguments[j];
                        if (a === null) argStrs.push("null");
                        else if (typeof a === "string") argStrs.push(a.length > 200 ? a.substring(0, 200) + "..." : a);
                        else if (typeof a === "number") argStrs.push(String(a));
                        else argStrs.push(a.getClass ? a.getClass().getName() : typeof a);
                    }
                    send({t: "call", m: name, args: argStrs});
                    return ov.apply(this, arguments);
                };
            });
            hooked++;
        } catch(e) {}
    }

    send({s: "Hooked " + hooked + " TTVideoEngine methods"});
});
