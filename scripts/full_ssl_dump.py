"""Spawn app and dump ALL SSL traffic as readable text.

Captures everything from SSL_read via libttboringssl.so + libssl.so,
saves all text-decodable packets for offline analysis.
"""

import frida
import json
import os
import sys
import time

OUTPUT_DIR = "videos/ssl_dump"
APP_PACKAGE = "com.phoenix.read"


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    device = frida.get_usb_device()

    print(f"Spawning {APP_PACKAGE}...")
    pid = device.spawn([APP_PACKAGE])
    session = device.attach(pid)
    device.resume(pid)
    print(f"PID: {pid}, waiting 8s for app to load...")
    time.sleep(8)

    hook = '''
    var resolver = new ApiResolver("module");
    var n = 0;

    function hookSSL(pattern, tag) {
        var readM = resolver.enumerateMatches(pattern + "!SSL_read");
        var writeM = resolver.enumerateMatches(pattern + "!SSL_write");
        if (readM.length > 0) {
            Interceptor.attach(readM[0].address, {
                onEnter: function(a) { this.buf = a[1]; },
                onLeave: function(ret) {
                    var len = ret.toInt32();
                    if (len > 0) {
                        n++;
                        send({f: tag + "_R", n: n, l: len}, this.buf.readByteArray(len));
                    }
                }
            });
        }
        if (writeM.length > 0) {
            Interceptor.attach(writeM[0].address, {
                onEnter: function(a) {
                    var len = a[2].toInt32();
                    if (len > 0) {
                        n++;
                        send({f: tag + "_W", n: n, l: len}, a[1].readByteArray(len));
                    }
                }
            });
        }
        send({s: tag + " hooked (R:" + readM.length + " W:" + writeM.length + ")"});
    }

    hookSSL("exports:*libttboringssl*", "tt");
    hookSSL("exports:*libssl.so*", "sys");
    send({s: "READY - open app and play a video!"});
    '''

    all_packets = []
    text_packets = []
    pkt_count = [0]

    def on_message(msg, data):
        if msg["type"] != "send":
            return
        p = msg["payload"]
        if "s" in p:
            print(f"  [{p['s']}]")
            return
        if data is None:
            return

        pkt_count[0] += 1
        pkt = {"n": p["n"], "f": p["f"], "len": p["l"]}

        # 处理
        try:
            text = data.decode("utf-8", errors="replace")
            # 逻辑
            printable_ratio = sum(1 for c in text if c.isprintable() or c in '\r\n\t') / max(len(text), 1)
            if printable_ratio > 0.5:
                pkt["text"] = text
                text_packets.append(pkt)

                # 处理
                lo = text.lower()
                video_hit = any(kw in lo for kw in [
                    "play_url", "video_url", "video_list", ".m3u8", ".mp4",
                    "content_key", "play_info", "video_id", "drama", "episode",
                    "media_url", "decrypt", "kid", "encrypt_key",
                ])
                if video_hit:
                    print(f"\n>>> VIDEO HIT pkt#{p['n']} ({p['f']}, {p['l']}B)")
                    print(f"    {text[:300]}")
                elif "HTTP" in text[:20]:
                    first_line = text.split("\n")[0][:150]
                    print(f"  [{p['f']}] {first_line}")
        except Exception:
            pass

        all_packets.append(pkt)

    script = session.create_script(hook)
    script.on("message", on_message)
    script.load()

    print("\n=== Open the app and play a NEW video! Capturing for 60s... ===\n")
    time.sleep(60)

    print(f"\n=== Capture complete ===")
    print(f"Total packets: {pkt_count[0]}")
    print(f"Text packets:  {len(text_packets)}")

    # text packets
    output_file = os.path.join(OUTPUT_DIR, "ssl_text_dump.json")
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(text_packets, f, indent=2, ensure_ascii=False)
    print(f"Saved to: {output_file}")

    # 处理 dump回退
    raw_file = os.path.join(OUTPUT_DIR, "ssl_raw_text.txt")
    with open(raw_file, "w", encoding="utf-8") as f:
        for pkt in text_packets:
            f.write(f"\n{'='*60}\n")
            f.write(f"PKT #{pkt['n']} [{pkt['f']}] {pkt['len']}B\n")
            f.write(f"{'='*60}\n")
            f.write(pkt.get("text", "(binary)"))
            f.write("\n")
    print(f"Raw text: {raw_file}")

    session.detach()


if __name__ == "__main__":
    main()
