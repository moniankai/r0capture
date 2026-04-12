"""Environment detection and Frida Server setup for Android devices."""

from __future__ import annotations

import gzip
import os
import platform
import shutil
import subprocess
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from loguru import logger

# Frida 回退Android -> Frida Server 
FRIDA_VERSION_MAP: dict[tuple[int, int], str] = {
    (7, 9): "15.2.2",
    (10, 12): "16.5.2",
    (13, 99): "16.5.2",
}

FRIDA_SERVER_PATH = "/data/local/tmp/frida-server"
FRIDA_DEFAULT_PORT = 27042
FRIDA_ALT_PORT = 27043


@dataclass(frozen=True)
class DeviceInfo:
    android_version: int
    architecture: str
    model: str
    is_rooted: bool


def run_adb(args: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run an ADB command and return the result."""
    cmd = ["adb"] + args
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30, check=check
        )
        return result
    except FileNotFoundError:
        logger.error("ADB not found. Please install Android SDK Platform-Tools.")
        sys.exit(1)
    except subprocess.TimeoutExpired:
        logger.error(f"ADB command timed out: {' '.join(cmd)}")
        sys.exit(1)


def detect_device() -> DeviceInfo:
    """Detect connected Android device information."""
    result = run_adb(["devices"], check=False)
    lines = [l.strip() for l in result.stdout.strip().splitlines() if "\tdevice" in l]
    if not lines:
        logger.error("No device connected. Please connect device via USB and enable USB debugging.")
        sys.exit(1)

    android_ver_str = run_adb(["shell", "getprop", "ro.build.version.release"]).stdout.strip()
    try:
        android_version = int(android_ver_str.split(".")[0])
    except ValueError:
        logger.error(f"Failed to parse Android version: {android_ver_str}")
        sys.exit(1)

    if android_version < 7:
        logger.error(f"Android {android_version} not supported. Minimum version: Android 7")
        sys.exit(1)

    arch = run_adb(["shell", "getprop", "ro.product.cpu.abi"]).stdout.strip()
    model = run_adb(["shell", "getprop", "ro.product.model"]).stdout.strip()

    root_check = run_adb(["shell", "su", "-c", "id"], check=False)
    is_rooted = "uid=0" in root_check.stdout

    info = DeviceInfo(
        android_version=android_version,
        architecture=arch,
        model=model,
        is_rooted=is_rooted,
    )
    logger.info(f"Device: {info.model} | Android {info.android_version} | {info.architecture} | Root: {info.is_rooted}")
    return info


def get_frida_version(android_version: int) -> str:
    """Get the appropriate Frida Server version for the Android version."""
    for (low, high), version in FRIDA_VERSION_MAP.items():
        if low <= android_version <= high:
            return version
    return "16.5.2"


def get_frida_arch(device_arch: str) -> str:
    """Map device architecture to Frida binary architecture name."""
    arch_map = {
        "arm64-v8a": "arm64",
        "armeabi-v7a": "arm",
        "x86_64": "x86_64",
        "x86": "x86",
    }
    return arch_map.get(device_arch, "arm64")


def download_frida_server(version: str, arch: str, output_dir: Optional[str] = None) -> Path:
    """Download Frida Server binary from GitHub releases."""
    filename = f"frida-server-{version}-android-{arch}"
    gz_filename = f"{filename}.xz"
    url = f"https://github.com/frida/frida/releases/download/{version}/{gz_filename}"

    if output_dir is None:
        output_dir = str(Path.home() / ".frida" / "server")
    os.makedirs(output_dir, exist_ok=True)

    output_path = Path(output_dir) / filename
    if output_path.exists():
        logger.info(f"Frida Server already downloaded: {output_path}")
        return output_path

    logger.info(f"Downloading Frida Server {version} for {arch}...")
    logger.info(f"URL: {url}")

    gz_path = Path(output_dir) / gz_filename
    try:
        urllib.request.urlretrieve(url, str(gz_path))
    except Exception as e:
        logger.error(f"Download failed: {e}")
        logger.info("Try manual download and place in: {output_dir}")
        sys.exit(1)

    # xz 
    try:
        import lzma
        with lzma.open(str(gz_path), "rb") as f_in:
            with open(str(output_path), "wb") as f_out:
                shutil.copyfileobj(f_in, f_out)
        gz_path.unlink()
    except Exception as e:
        logger.error(f"Extraction failed: {e}")
        sys.exit(1)

    logger.info(f"Downloaded to: {output_path}")
    return output_path


def install_frida_server(local_path: Path) -> None:
    """Push Frida Server to device and set permissions."""
    logger.info("Pushing Frida Server to device...")
    run_adb(["push", str(local_path), FRIDA_SERVER_PATH])
    run_adb(["shell", "su", "-c", f"chmod 755 {FRIDA_SERVER_PATH}"])
    logger.info(f"Installed to {FRIDA_SERVER_PATH}")


def start_frida_server(port: int = FRIDA_DEFAULT_PORT) -> int:
    """Start Frida Server on device, return the port used."""
    # frida-server
    run_adb(["shell", "su", "-c", "killall frida-server 2>/dev/null"], check=False)

    import time
    time.sleep(1)

    # 处理
    run_adb(
        ["shell", "su", "-c", f"nohup {FRIDA_SERVER_PATH} -l 0.0.0.0:{port} &"],
        check=False,
    )
    time.sleep(2)

    # 
    check = run_adb(["shell", "su", "-c", "ps | grep frida-server"], check=False)
    if "frida-server" in check.stdout:
        logger.info(f"Frida Server started on port {port}")
        return port

    # 处理
    if port == FRIDA_DEFAULT_PORT:
        logger.warning(f"Port {port} failed, trying {FRIDA_ALT_PORT}...")
        return start_frida_server(FRIDA_ALT_PORT)

    logger.error("Failed to start Frida Server")
    sys.exit(1)


def verify_frida_connection() -> bool:
    """ Frida can connect to the device."""
    try:
        result = subprocess.run(
            ["frida-ps", "-U"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            lines = result.stdout.strip().splitlines()
            logger.info(f"Frida connected. {len(lines) - 1} processes found.")
            return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    logger.error("Failed to connect to Frida Server. Please restart the device and try again.")
    return False


def check_python_deps() -> list[str]:
    """Check if all required Python packages are importable."""
    required = ["frida", "loguru", "click", "scapy", "m3u8", "Crypto", "tqdm", "requests"]
    missing = []
    for pkg in required:
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)
    return missing


def setup_environment() -> DeviceInfo:
    """Full environment setup: detect device, download+install Frida, verify."""
    logger.info("=== Environment Setup ===")

    # Python 
    missing = check_python_deps()
    if missing:
        logger.warning(f"Missing Python packages: {', '.join(missing)}")
        logger.info("Run: pip install -r requirements.txt")

    # 
    device = detect_device()

    if not device.is_rooted:
        logger.error("Root access required. Please root your device first.")
        sys.exit(1)

    # 回退 Frida Server
    version = get_frida_version(device.android_version)
    arch = get_frida_arch(device.architecture)
    local_path = download_frida_server(version, arch)
    install_frida_server(local_path)

    # 回退
    start_frida_server()
    if not verify_frida_connection():
        sys.exit(1)

    logger.info("=== Environment Ready ===")
    return device


if __name__ == "__main__":
    setup_environment()
