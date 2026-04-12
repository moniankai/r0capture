"""
Auto-capture video metadata from HongGuo app.

Spawns the app, hooks TTVideoEngine.setVideoModel to extract:
- Video download URLs (mMainUrl, mBackupUrl1)
- Encryption info (mKid, mSpadea, mEncrypt)
- Video metadata (resolution, codec, size)


  python scripts/auto_capture.py
  python scripts/auto_capture.py --duration 120
"""

import frida
import json
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

HOOK_JS = r"""
Java.perform(function() {
    function bh(arr) {
        if (!arr) return "";
        var sb = [];
        for (var i = 0; i < arr.length; i++) {
            var b = (arr[i] & 0xFF).toString(16);
            sb.push(b.length === 1  "0" + b : b);
        }
        return sb.join("");
    }

    function dumpObj(obj) {
        var result = {};
        try {
            var fields = obj.getClass().getDeclaredFields();
            for (var i = 0; i < fields.length; i++) {
                fields[i].setAccessible(true);
                try {
                    var val = fields[i].get(obj);
                    if (val !== null) {
                        var s = val.toString();
                        if (s.length > 0 && s.length < 3000)
                            result[fields[i].getName()] = s;
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
                var refField = model.getClass().getDeclaredField("vodVideoRef");
                refField.setAccessible(true);
                var ref = refField.get(model);
                if (!ref) return ov.apply(this, arguments);

                var refData = dumpObj(ref);
                send({t: "ref", data: refData});

                // 处理
                var listField = ref.getClass().getDeclaredField("mVideoList");
                listField.setAccessible(true);
                var list = Java.cast(listField.get(ref), Java.use("java.util.List"));

                for (var i = 0; i < list.size(); i++) {
                    var info = list.get(i);
                    var infoData = dumpObj(info);
                    send({t: "info", idx: i, data: infoData});
                }
            } catch(e) {
                send({t: "err", e: e.toString()});
            }

            return ov.apply(this, arguments);
        };
    });

    send({t: "ready"});
});
"""


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", "-d", type=int, default=90, help="Capture duration (seconds)")
    parser.add_argument("--output", "-o", default="videos/captured_videos.json")
    args = parser.parse_args()

    device = frida.get_usb_device()

    # App
    import subprocess
    subprocess.run(["adb", "shell", "su", "-c", "am force-stop com.phoenix.read"],
                   capture_output=True, env={**os.environ, "MSYS_NO_PATHCONV": "1"})
    time.sleep(1)

    pid = device.spawn(["com.phoenix.read"])
    session = device.attach(pid)

    events = []
    def on_msg(msg, data):
        if msg["type"] == "send":
            p = msg["payload"]
            events.append(p)
            t = p.get("t", "")
            if t == "ref":
                vid = p["data"].get("mVideoId", "?")
                dur = p["data"].get("mVideoDuration", "?")
                print(f"\n>>> Video: {vid} ({dur}s)")
            elif t == "info":
                d = p["data"]
                res = d.get("mResolution", "?")
                enc = d.get("mEncrypt", "?")
                url = d.get("mMainUrl", "")[:80]
                spadea = d.get("mSpadea", "")
                kid = d.get("mKid", "")
                print(f"  [{p['idx']}] {res} enc={enc} kid={kid[:16]}... spadea={spadea[:20]}...")
                print(f"      URL: {url}...")
            elif t == "ready":
                print("[READY] Open the app and play a drama!")

    script = session.create_script(HOOK_JS)
    script.on("message", on_msg)
    script.load()
    device.resume(pid)

    print(f"\nCapturing for {args.duration}s. Play videos in the app!")
    time.sleep(args.duration)

    session.detach()

    # 处理
    videos = []
    current_ref = None
    for e in events:
        if e.get("t") == "ref":
            current_ref = e["data"]
        elif e.get("t") == "info" and current_ref:
            d = e["data"]
            videos.append({
                "video_id": current_ref.get("mVideoId", ""),
                "duration": current_ref.get("mVideoDuration", ""),
                "resolution": d.get("mResolution", ""),
                "codec": d.get("mCodecType", ""),
                "size": d.get("mSize", ""),
                "encrypt": d.get("mEncrypt", ""),
                "kid": d.get("mKid", ""),
                "spadea": d.get("mSpadea", ""),
                "main_url": d.get("mMainUrl", ""),
                "backup_url": d.get("mBackupUrl1", ""),
                "file_hash": d.get("mFileHash", ""),
                "check_info": d.get("mCheckInfo", ""),
            })

    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(videos, f, indent=2, ensure_ascii=False)

    print(f"\n=== Captured {len(videos)} video entries ===")
    print(f"Saved to {args.output}")

    # 
    seen_vids = set()
    for v in videos:
        vid = v["video_id"]
        if vid not in seen_vids:
            seen_vids.add(vid)
            print(f"\n  Video {vid} ({v['duration']}s)")
        print(f"    {v['resolution']} {v['codec']} {int(v.get('size',0))/1024/1024:.1f}MB enc={v['encrypt']}")
        print(f"    KID: {v['kid']}")
        print(f"    Spadea: {v['spadea']}")


if __name__ == "__main__":
    main()
