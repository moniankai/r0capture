"""
mitmproxy-based video URL capture for HongGuo (红果免费短剧).

Steps:
  1. Generate & install mitmproxy CA cert as system cert on rooted device
  2. Start mitmproxy with video URL capture addon
  3. Configure device WiFi proxy
  4. Use Frida to bypass SSL pinning in libttboringssl.so
  5. Capture video URLs from API responses


  python scripts/mitm_capture.py setup    # Install CA cert on device
  python scripts/mitm_capture.py capture  # Start capturing
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from loguru import logger


MITMPROXY_PORT = 8080
CERT_DIR = Path.home() / ".mitmproxy"


def run_adb(args: list[str], check: bool = False) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "MSYS_NO_PATHCONV": "1"}
    return subprocess.run(
        ["adb"] + args, capture_output=True, text=True, timeout=30, check=check, env=env
    )


def get_pc_ip() -> str:
    """Get local IP that the phone can reach."""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    finally:
        s.close()


def install_cert() -> bool:
    """Install mitmproxy CA cert as system cert on rooted device."""
    cert_pem = CERT_DIR / "mitmproxy-ca-cert.pem"

    if not cert_pem.exists():
        logger.info("Generating mitmproxy CA cert (first run)...")
        subprocess.run([sys.executable, "-m", "mitmproxy.tools.main", "--version"],
                       capture_output=True, timeout=30)
        # mitmdump 处理处理
        proc = subprocess.Popen(
            [sys.executable, "-m", "mitmproxy.tools.main", "-p", "0", "--set", "connection_strategy=lazy"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        time.sleep(3)
        proc.terminate()
        proc.wait(timeout=5)

    if not cert_pem.exists():
        logger.error(f"CA cert not found at {cert_pem}")
        return False

    # Android 处理
    import hashlib
    with open(cert_pem, "rb") as f:
        pem_data = f.read()

    # subject hash Android 回退
    result = subprocess.run(
        ["openssl", "x509", "-inform", "PEM", "-subject_hash_old", "-noout"],
        input=pem_data, capture_output=True, timeout=10,
    )

    if result.returncode != 0:
        # 处理 Python hash
        import ssl
        cert_hash = hashlib.md5(pem_data).hexdigest()[:8]
        cert_name = f"{cert_hash}.0"
        logger.warning(f"openssl not found, using fallback hash: {cert_name}")
    else:
        cert_hash = result.stdout.decode().strip()
        cert_name = f"{cert_hash}.0"

    # 处理
    logger.info(f"Installing CA cert as system cert: {cert_name}")

    # system 
    run_adb(["shell", "su", "-c", "mount -o rw,remount /system"])

    # 
    cert_tmp = f"/sdcard/{cert_name}"
    cert_dest = f"/system/etc/security/cacerts/{cert_name}"

    run_adb(["push", str(cert_pem), cert_tmp])
    run_adb(["shell", "su", "-c", f"cp {cert_tmp} {cert_dest}"])
    run_adb(["shell", "su", "-c", f"chmod 644 {cert_dest}"])
    run_adb(["shell", "su", "-c", f"rm {cert_tmp}"])

    # 
    result = run_adb(["shell", f"ls -la {cert_dest}"])
    if cert_name in result.stdout:
        logger.info("CA cert installed successfully!")
        return True

    logger.error("Failed to install CA cert")
    return False


def set_proxy(host: str, port: int) -> None:
    """Set WiFi proxy on device."""
    run_adb(["shell", "settings", "put", "global", "http_proxy", f"{host}:{port}"])
    logger.info(f"Proxy set to {host}:{port}")


def clear_proxy() -> None:
    """Remove WiFi proxy."""
    run_adb(["shell", "settings", "put", "global", "http_proxy", ":0"])
    logger.info("Proxy cleared")


def start_frida_ssl_bypass(pid: int) -> None:
    """Bypass SSL pinning via Frida native hooks."""
    import frida

    device = frida.get_usb_device()
    session = device.attach(pid)

    # BoringSSL SSL 
    bypass_code = '''
    var resolver = new ApiResolver("module");

    // Hook SSL_CTX_set_custom_verify 处理
    var matches = resolver.enumerateMatches("exports:*libttboringssl*!SSL_CTX_set_custom_verify");
    if (matches.length > 0) {
        Interceptor.attach(matches[0].address, {
            onEnter: function(args) {
                // 逻辑
                args[2] = ptr(0);
            }
        });
        send({s: "SSL_CTX_set_custom_verify bypassed"});
    }

    // Hook ssl_verify_peer_cert处理
    matches = resolver.enumerateMatches("exports:*libttboringssl*!ssl_verify_peer_cert");
    if (matches.length > 0) {
        Interceptor.replace(matches[0].address, new NativeCallback(function() {
            return 0; // ssl_verify_ok
        }, "int", []));
        send({s: "ssl_verify_peer_cert bypassed"});
    }

    // Hook X509_verify_cert处理
    var libs = ["libttboringssl.so", "libttcrypto.so", "libcrypto.so"];
    libs.forEach(function(lib) {
        var m = resolver.enumerateMatches("exports:*" + lib + "*!X509_verify_cert");
        if (m.length > 0) {
            Interceptor.replace(m[0].address, new NativeCallback(function() {
                return 1; // 
            }, "int", ["pointer"]));
            send({s: "X509_verify_cert bypassed in " + lib});
        }
    });

    send({s: "SSL bypass ready"});
    '''

    def on_msg(msg, data):
        if msg['type'] == 'send':
            logger.info(f"Frida: {msg['payload'].get('s', msg['payload'])}")

    script = session.create_script(bypass_code)
    script.on('message', on_msg)
    script.load()
    return session


def write_mitm_addon(output_path: str) -> str:
    """Write mitmproxy addon script to capture video URLs."""
    addon_path = os.path.join(os.path.dirname(__file__), "_mitm_addon.py")
    addon_code = f'''
import json
import os
import mitmproxy.http

VIDEO_KEYWORDS = ["play_url", "video_url", "video_list", "video_download",
                  ".m3u8", ".mp4", "play_info", "content_key", "media_url",
                  "drama", "episode", "video_id"]

output_file = r"{output_path}"
captured = []

class VideoCapture:
    def response(self, flow: mitmproxy.http.HTTPFlow):
        url = flow.request.pretty_url
        content_type = flow.response.headers.get("content-type", "")

        # API 
        is_api = "/api/" in url or "/v1/" in url or "/v2/" in url
        is_json = "json" in content_type

        body = ""
        if is_json or is_api:
            try:
                body = flow.response.get_text()
            except:
                pass

        # 处理
        has_video = any(kw in url.lower() or kw in body.lower()
                       for kw in VIDEO_KEYWORDS)

        if has_video:
            entry = {{
                "url": url,
                "method": flow.request.method,
                "status": flow.response.status_code,
                "content_type": content_type,
                "body": body[:5000] if body else "",
            }}
            captured.append(entry)

            # immediately
            with open(output_file, "w") as f:
                json.dump(captured, f, indent=2, ensure_ascii=False)

            print(f"[VIDEO] {{url[:150]}}")
            if body:
                # 处理 URL
                import re
                urls = re.findall(r\\'https?://[^"\\\\s]+(?:\\\\.mp4|\\\\.m3u8)[^"\\\\s]*\\', body)
                for u in urls[:5]:
                    print(f"  >>> {{u[:200]}}")

addons = [VideoCapture()]
'''

    with open(addon_path, "w") as f:
        f.write(addon_code)

    return addon_path


def cmd_setup() -> None:
    """Install CA cert and verify."""
    install_cert()


def cmd_capture(output_dir: str = "./videos/honguo") -> None:
    """Start full capture pipeline."""
    os.makedirs(output_dir, exist_ok=True)
    output_json = os.path.join(output_dir, "mitm_captured.json")

    pc_ip = get_pc_ip()
    logger.info(f"PC IP: {pc_ip}")

    # addon
    addon_path = write_mitm_addon(output_json)

    # 处理
    set_proxy(pc_ip, MITMPROXY_PORT)

    # Frida SSL 
    pid_result = run_adb(["shell", "su", "-c", "pidof com.phoenix.read"])
    pid_str = pid_result.stdout.strip().split()[0] if pid_result.stdout.strip() else ""
    if pid_str:
        try:
            frida_session = start_frida_ssl_bypass(int(pid_str))
            logger.info("SSL pinning bypass active")
        except Exception as e:
            logger.warning(f"Frida bypass failed: {e}")

    # mitmdump
    logger.info(f"Starting mitmproxy on port {MITMPROXY_PORT}...")
    logger.info("Play videos on your phone. Press Ctrl+C to stop.")

    try:
        subprocess.run([
            sys.executable, "-m", "mitmproxy.tools.main",
            "-p", str(MITMPROXY_PORT),
            "--mode", "regular",
            "--set", "ssl_insecure=true",
            "-s", addon_path,
        ])
    except KeyboardInterrupt:
        pass
    finally:
        clear_proxy()
        logger.info(f"Results saved to: {output_json}")
        if os.path.exists(output_json):
            with open(output_json) as f:
                data = json.load(f)
            logger.info(f"Captured {len(data)} video-related responses")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="mitmproxy capture for HongGuo")
    parser.add_argument("command", choices=["setup", "capture"], help="setup=install cert, capture=start")
    parser.add_argument("--output", "-o", default="./videos/honguo")
    args = parser.parse_args()

    if args.command == "setup":
        cmd_setup()
    else:
        cmd_capture(args.output)
