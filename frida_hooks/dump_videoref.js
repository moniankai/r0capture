Java.perform(function() {
    function dumpObj(obj, depth, maxDepth) {
        if (!obj || depth > maxDepth) return "...";
        var result = {};
        try {
            var cls = obj.getClass();
            var fields = cls.getDeclaredFields();
            for (var i = 0; i < fields.length; i++) {
                fields[i].setAccessible(true);
                var name = fields[i].getName();
                try {
                    var val = fields[i].get(obj);
                    if (val === null) continue;
                    var s = val.toString();
                    if (s.length < 3000) {
                        result[name] = s;
                    }
                } catch(e) {}
            }
        } catch(e) {}
        return result;
    }

    var Engine = Java.use("com.ss.ttvideoengine.TTVideoEngine");
    Engine.setVideoModel.overloads.forEach(function(ov) {
        ov.implementation = function(model) {
            try {
                // vodVideoRef
                var refField = model.getClass().getDeclaredField("vodVideoRef");
                refField.setAccessible(true);
                var ref = refField.get(model);

                if (ref) {
                    var refData = dumpObj(ref, 0, 0);
                    send({t: "ref", data: refData});

                    // 在 VideoRef 内查找 videoInfoList
                    var refCls = ref.getClass();
                    var refFields = refCls.getDeclaredFields();
                    for (var i = 0; i < refFields.length; i++) {
                        refFields[i].setAccessible(true);
                        var fname = refFields[i].getName();
                        try {
                            var fval = refFields[i].get(ref);
                            if (fval === null) continue;

                            // 处理
                            var fclass = fval.getClass().getName();
                            if (fclass.indexOf("List") !== -1 || fclass.indexOf("ArrayList") !== -1) {
                                var list = Java.cast(fval, Java.use("java.util.List"));
                                var sz = list.size();
                                send({t: "list", name: fname, size: sz});

                                for (var j = 0; j < Math.min(sz, 3); j++) {
                                    var item = list.get(j);
                                    if (item) {
                                        var itemData = dumpObj(item, 0, 0);
                                        send({t: "list_item", list: fname, idx: j, data: itemData});
                                    }
                                }
                            }
                        } catch(e) {}
                    }
                }
            } catch(e) {
                send({t: "err", e: e.toString()});
            }
            return ov.apply(this, arguments);
        };
    });
    send({s: "ready"});
});
