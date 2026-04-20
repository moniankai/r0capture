"""Spawn 红果 + 加载 episode_bind.js,观察 SaasVideoData 绑定和 setVideoModel 的时序."""
from __future__ import annotations
import sys, time, subprocess, os, json
from pathlib import Path
from datetime import datetime
import frida

APP_PACKAGE = "com.phoenix.read"
HOOK_PATH = Path("d:/dev/cursor/r0capture/frida_hooks/episode_bind.js")
OUT_PATH = Path("d:/tmp/episode_bind_log.jsonl")


def main():
    device = frida.get_usb_device(timeout=10)
    env = {**os.environ, "MSYS_NO_PATHCONV": "1"}
    subprocess.run(["adb", "shell", "am", "force-stop", APP_PACKAGE],
                   capture_output=True, check=False, env=env)
    time.sleep(1)
    pid = device.spawn([APP_PACKAGE])
    print(f"spawned pid={pid}", flush=True)
    s = device.attach(pid)
    sc = s.create_script(HOOK_PATH.read_text(encoding="utf-8"))

    OUT_PATH.write_text("", encoding="utf-8")
    log = OUT_PATH.open("a", encoding="utf-8")

    binds: list[dict] = []
    set_vms: list[dict] = []

    def on_msg(msg, data):
        if msg.get("type") != "send":
            if msg.get("type") == "error":
                print(f"[JS ERR] {msg.get('description','')[:300]}", flush=True)
            return
        p = msg["payload"]
        t = p.get("t")
        p["_ts"] = time.time()
        log.write(json.dumps(p, ensure_ascii=False) + "\n")
        log.flush()
        if t == "hook_start":
            print("[hook_start]", flush=True)
        elif t == "bind_hooked":
            print(f"[bind_hooked] a={p['a']} z={p['z']}", flush=True)
        elif t == "engine_hooked":
            print(f"[engine_hooked] overloads={p['overloads']}", flush=True)
        elif t == "ready":
            print("[ready]", flush=True)
        elif t == "bind":
            binds.append(p)
            print(f"[BIND] ep={p['idx']} vid={p['vid']} name={p['name']} "
                  f"title={(p['title'] or '')[:30]}", flush=True)
        elif t == "set_vm":
            set_vms.append(p)
            print(f"[VM] tt_vid={p['tt_vid']} url={(p.get('url') or '')[:80]}", flush=True)
        elif t == "data":
            e = p['entry']
            print(f"[DATA {p['tag'][:30]}] hash={p['hash']} ep={e.get('idx')} "
                  f"vid={e.get('vid')} name={e.get('name')} title={(e.get('title') or '')[:25]}",
                  flush=True)
        elif t == "data_hooked":
            print("[data_hooked]", flush=True)
        elif t in ("hook_err", "bind_err", "engine_hook_err", "set_vm_err",
                   "data_err", "data_hook_err"):
            print(f"[ERR] {p}", flush=True)

    sc.on("message", on_msg)
    sc.load()
    device.resume(pid)
    print("已 resume, 请操作 App: 搜索《乡下御厨》→ 进详情页 → swipe 切集", flush=True)
    print("观察 240s", flush=True)

    t0 = time.time()
    try:
        while time.time() - t0 < 240:
            time.sleep(2)
    except KeyboardInterrupt:
        pass
    log.close()

    print(f"\n共捕获 binds={len(binds)} setVMs={len(set_vms)}", flush=True)
    print(f"日志 {OUT_PATH}", flush=True)


if __name__ == "__main__":
    main()
