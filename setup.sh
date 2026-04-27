#!/bin/bash
# ─────────────────────────────────────────────────────────────
#  NW Booking Bot — setup script
#  Supports: Ubuntu/Debian, GitHub Codespaces, UserLand (Android)
# ─────────────────────────────────────────────────────────────
set -e

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Colour

info()    { echo -e "${GREEN}✔${NC}  $1"; }
warn()    { echo -e "${YELLOW}⚠${NC}  $1"; }
section() { echo -e "\n${GREEN}━━━  $1${NC}"; }

echo -e "${GREEN}"
echo "  ██╗  ██╗██╗    ██╗    ██████╗  ██████╗ ████████╗"
echo "  ███╗ ██║██║    ██║    ██╔══██╗██╔═══██╗╚══██╔══╝"
echo "  ████╗██║██║ █╗ ██║    ██████╔╝██║   ██║   ██║   "
echo "  ██╔████║██║███╗██║    ██╔══██╗██║   ██║   ██║   "
echo "  ██║╚███║╚███╔███╔╝    ██████╔╝╚██████╔╝   ██║   "
echo "  ╚═╝ ╚══╝ ╚══╝╚══╝     ╚═════╝  ╚═════╝    ╚═╝   "
echo -e "${NC}"
echo "  Nordic Wellness Booking Automator — setup"
echo ""

# ── Detect environment ────────────────────────────────────────
section "Detecting environment"

HAS_SUDO=false
if command -v sudo &>/dev/null && sudo -n true 2>/dev/null; then
    HAS_SUDO=true
    info "sudo available"
else
    warn "sudo not available — skipping system package install (UserLand / restricted env)"
fi

IS_ANDROID=false
if uname -r | grep -qi android || [ -d /data/data ]; then
    IS_ANDROID=true
    warn "Android/UserLand detected — some system libs may already be bundled"
fi

# ── System dependencies ───────────────────────────────────────
if [ "$HAS_SUDO" = true ] && [ "$IS_ANDROID" = false ]; then
    section "Installing system dependencies"
    sudo apt-get update -qq

    PACKAGES=(
        ca-certificates
        fonts-liberation
        libasound2t64
        libatk-bridge2.0-0
        libatk1.0-0
        libatspi2.0-0
        libcups2
        libdbus-1-3
        libdrm2
        libgdk-pixbuf2.0-0
        libgtk-3-0
        libnspr4
        libnss3
        libx11-xcb1
        libxcomposite1
        libxdamage1
        libxfixes3
        libxrandr2
        libxss1
        libxtst6
        xdg-utils
    )

    # libappindicator3-1 may not exist on newer distros — skip if missing
    if apt-cache show libappindicator3-1 &>/dev/null; then
        PACKAGES+=(libappindicator3-1)
    fi

    sudo apt-get install -y --no-install-recommends "${PACKAGES[@]}" \
        2>&1 | grep -E "^(Get|Setting|Unpacking|Selecting)" || true

    info "System dependencies installed"
else
    warn "Skipping system packages — run 'playwright install-deps' manually if Chromium fails to launch"
fi

# ── Python dependencies ───────────────────────────────────────
section "Installing Python packages"

# Prefer pip3, fall back to pip
PIP=pip3
command -v pip3 &>/dev/null || PIP=pip

# Check Python version
PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
info "Python $PYTHON_VERSION detected"

if python3 -c 'import sys; assert sys.version_info >= (3,10)' 2>/dev/null; then
    info "Python version OK (≥ 3.10)"
else
    echo -e "${RED}✘  Python 3.10+ required. Found $PYTHON_VERSION${NC}"
    exit 1
fi

# Install into venv if available, otherwise system/user
if [ -d ".venv" ]; then
    info "Existing .venv detected — installing into it"
    source .venv/bin/activate
elif command -v python3 -m venv --help &>/dev/null; then
    info "Creating virtual environment (.venv)..."
    python3 -m venv .venv
    source .venv/bin/activate
    info "Virtual environment activated"
fi

$PIP install --quiet --upgrade pip
$PIP install --quiet flask flask-cors playwright requests aiohttp

info "Python packages installed: flask, flask-cors, playwright, requests, aiohttp"

# ── Playwright browser ────────────────────────────────────────
section "Installing Playwright browser"

# Only install Chromium — we don't need Firefox or WebKit
playwright install chromium

info "Chromium installed"

# ── Verify ────────────────────────────────────────────────────
section "Verifying installation"

python3 -c "import flask, flask_cors, playwright, requests, aiohttp; print('All imports OK')" && \
    info "All Python packages verified" || \
    { echo -e "${RED}✘  Import check failed${NC}"; exit 1; }

# ── Done ──────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}  ✅  Setup complete!${NC}"
echo ""
echo "  Start the server:"
echo "      python3 server.py"
echo ""
echo "  Then open in your browser:"
echo "      http://localhost:5000"
echo ""
if [ "$IS_ANDROID" = true ]; then
    IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "YOUR_IP")
    echo "  On Android, use your local IP instead:"
    echo "      http://$IP:5000"
    echo ""
fi
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"