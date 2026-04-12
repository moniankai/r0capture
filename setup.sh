#!/bin/bash
#
# honguo_capture - 一键环境安装脚本
#
# 功能：
#   1. 检查 Python 版本
#   2. 安装 Python 依赖
#   3. 检查 ADB 和 FFmpeg
#   4. 检测设备并安装 Frida Server
#
# 使用：
#   chmod +x setup.sh && ./setup.sh
#

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC} $1"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; }

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "========================================"
echo " honguo_capture - Environment Setup"
echo "========================================"
echo ""

# 1. Check Python
info "Checking Python..."
if command -v python3 &>/dev/null; then
    PYTHON=python3
elif command -v python &>/dev/null; then
    PYTHON=python
else
    error "Python not found. Please install Python 3.6+"
    exit 1
fi

PY_VER=$($PYTHON -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
info "Python version: $PY_VER"

PY_MAJOR=$($PYTHON -c "import sys; print(sys.version_info.major)")
PY_MINOR=$($PYTHON -c "import sys; print(sys.version_info.minor)")

if [ "$PY_MAJOR" -lt 3 ] || ([ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 6 ]); then
    error "Python 3.6+ required, got $PY_VER"
    exit 1
fi

# 2. Install Python dependencies
info "Installing Python dependencies..."
$PYTHON -m pip install --upgrade pip
$PYTHON -m pip install -r "$SCRIPT_DIR/requirements.txt"
info "Python dependencies installed"

# 3. Check ADB
info "Checking ADB..."
if command -v adb &>/dev/null; then
    ADB_VER=$(adb version | head -1)
    info "ADB: $ADB_VER"
else
    warn "ADB not found. Install Android SDK Platform-Tools:"
    warn "  https://developer.android.com/tools/releases/platform-tools"
fi

# 4. Check FFmpeg
info "Checking FFmpeg..."
if command -v ffmpeg &>/dev/null; then
    FF_VER=$(ffmpeg -version | head -1)
    info "FFmpeg: $FF_VER"
else
    warn "FFmpeg not found. Required for merging video segments."
    warn "  Linux: sudo apt install ffmpeg"
    warn "  macOS: brew install ffmpeg"
    warn "  Windows: https://ffmpeg.org/download.html"
fi

# 5. Device setup (optional)
echo ""
read -p "Setup Frida on connected device? (y/N) " -n 1 -r
echo ""

if [[ $REPLY =~ ^[Yy]$ ]]; then
    if ! command -v adb &>/dev/null; then
        error "ADB required for device setup"
        exit 1
    fi

    DEVICE_COUNT=$(adb devices | grep -c "device$" || true)
    if [ "$DEVICE_COUNT" -eq 0 ]; then
        error "No device connected. Connect via USB and enable USB debugging."
        exit 1
    fi

    info "Running device setup..."
    $PYTHON "$SCRIPT_DIR/scripts/check_environment.py"
fi

echo ""
echo "========================================"
info "Setup complete!"
echo ""
echo "Quick start:"
echo "  # Setup device (if not done above)"
echo "  $PYTHON honguo_capture.py setup"
echo ""
echo "  # Live capture mode"
echo "  $PYTHON honguo_capture.py live --app com.hongguo.duanju"
echo ""
echo "  # Analyze existing PCAP"
echo "  $PYTHON honguo_capture.py offline --pcap capture.pcap --download"
echo ""
echo "  # Hook mode (direct interception)"
echo "  $PYTHON honguo_capture.py hook --app com.hongguo.duanju --spawn --download"
echo "========================================"
