"""
Capture AES decryption key from HongGuo app via Frida.

Spawns the app, monitors dlopen for libttffmpeg.so,
then hooks av_aes_init to capture the 128-bit AES key.


  python scripts/capture_key.py
  python scripts/capture_key.py --duration 120
"""

import frida
import json
import os
import subprocess
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

APP = "com.phoenix.read"


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Capture AES key from HongGuo app")
    parser.add_argument("--duration", "-d", type=int, default=90)
    args = parser.parse_args()

    device = frida.get_usb_device()

    # Kill app
    subprocess.run(["adb", "shell", "su", "-c", f"am force-stop {APP}"],
                   capture_output=True, env={**os.environ, "MSYS_NO_PATHCONV": "1"})
    time.sleep(1)

    pid = device.spawn([APP])
    session = device.attach(pid)

    # 1 dlopen libttffmpeg.so 
    monitor_code = """
    var resolver = new ApiResolver("module");
    var dlopen = resolver.enumerateMatches("exports:*!android_dlopen_ext");
    if (dlopen.length > 0) {
        Interceptor.attach(dlopen[0].address, {
            onEnter: function(args) {
                try {
                    var path = args[0].readUtf8String();
                    if (path && path.indexOf("ttffmpeg") !== -1) {
                        send({t: "loaded", path: path});
                    }
                } catch(e) {}
            }
        });
    }
    send({t: "monitor_ready"});
    """

    lib_loaded = [False]
    keys_found = []

    def on_msg(msg, data):
        if msg["type"] != "send":
            return
        p = msg["payload"]
        t = p.get("t", "")
        if t == "loaded":
            print(f"  [*] libttffmpeg loaded: {p['path']}")
            lib_loaded[0] = True
        elif t == "monitor_ready":
            print("  [*] dlopen monitor ready")
        elif t == "KEY":
            keys_found.append(p["key"])
            print(f"\n  >>> AES KEY: {p['key']} ({p['bits']}bit dec={p['dec']})")
        elif t == "crypt":
            pass  # crypt 
        elif "s" in p:
            print(f"  [{p['s']}]")

    script_m = session.create_script(monitor_code)
    script_m.on("message", on_msg)
    script_m.load()

    device.resume(pid)
    print(f"  PID {pid} resumed, waiting for libttffmpeg.so...")

    # 回退
    for _ in range(30):
        time.sleep(1)
        if lib_loaded[0]:
            time.sleep(2)
            break

    if not lib_loaded[0]:
        print("  [!] libttffmpeg not loaded after 30s, continuing anyway...")
        time.sleep(5)

    # 2Hook av_aes_init
    hook_code = """
    function bh(p,l){var h="";try{for(var i=0;i<l;i++){var b=(p.add(i).readU8()&0xFF).toString(16);h+=(b.length===1?"0":"")+b}}catch(e){}return h}
    var resolver = new ApiResolver("module");
    var m = resolver.enumerateMatches("exports:*libttffmpeg*!av_aes_init");
    send({s: "av_aes_init: " + m.length + " matches"});
    if (m.length > 0) {
        Interceptor.attach(m[0].address, {
            onEnter: function(args) {
                var bits = args[2].toInt32();
                var dec = args[3].toInt32();
                if (bits === 128 || bits === 256) {
                    var key = bh(args[1], bits / 8);
                    send({t: "KEY", bits: bits, dec: dec, key: key});
                }
            }
        });
    }
    send({s: "READY - play a video!"});
    """

    script_h = session.create_script(hook_code)
    script_h.on("message", on_msg)
    script_h.load()

    print(f"\n  Play a video on your phone! Capturing for {args.duration}s...")
    time.sleep(args.duration)

    session.detach()

    if keys_found:
        unique_keys = list(set(keys_found))
        print(f"\n=== Captured {len(unique_keys)} unique key(s) ===")
        for k in unique_keys:
            print(f"  {k}")

        # 
        with open("videos/captured_key.txt", "w") as f:
            for k in unique_keys:
                f.write(k + "\n")
        print("Saved to videos/captured_key.txt")
    else:
        print("\n[!] No keys captured. Make sure you played a video.")


if __name__ == "__main__":
    main()
