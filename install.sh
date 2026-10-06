#!/usr/bin/env bash
# ==============================================================================
# HouseBot 1-Line Installer for Linux and macOS (Option 3)
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/popbox99/housebot/main/install.sh | bash
# ==============================================================================
set -e

REPO_URL="https://github.com/popbox99/housebot.git"
INSTALL_DIR="${HOME}/.local/share/housebot-app"
BIN_DIR="${HOME}/.local/bin"

echo "================================================================="
echo "            🏠 HouseBot 1-Line Quick Installer"
echo "================================================================="

mkdir -p "${BIN_DIR}"

# Check for Python 3.9+
if command -v python3 >/dev/null 2>&1; then
    PY_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
    echo "✓ Found Python ${PY_VER}"
else
    echo "❌ Python 3 is not installed. Please install Python 3 or use the standalone executable."
    exit 1
fi

# Clone or update repository
if [ -d "${INSTALL_DIR}" ]; then
    echo "Updating existing installation in ${INSTALL_DIR}..."
    git -C "${INSTALL_DIR}" pull --quiet || true
else
    echo "Cloning HouseBot repository into ${INSTALL_DIR}..."
    if command -v git >/dev/null 2>&1; then
        git clone --depth 1 "${REPO_URL}" "${INSTALL_DIR}" --quiet
    else
        echo "Creating directory..."
        mkdir -p "${INSTALL_DIR}"
        curl -fsSL "https://github.com/popbox99/housebot/archive/refs/heads/main.tar.gz" | tar -xz --strip-components=1 -C "${INSTALL_DIR}"
    fi
fi

# Setup isolated virtual environment
echo "Setting up isolated virtual environment..."
VENV_DIR="${INSTALL_DIR}/.venv"
if [ ! -d "${VENV_DIR}" ]; then
    python3 -m venv "${VENV_DIR}"
fi

# Create symlink or launcher script in ~/.local/bin
cat << 'EOF' > "${BIN_DIR}/housebot"
#!/usr/bin/env bash
INSTALL_DIR="${HOME}/.local/share/housebot-app"
exec "${INSTALL_DIR}/.venv/bin/python" -m housebot "$@"
EOF
chmod +x "${BIN_DIR}/housebot"

echo "================================================================="
echo "🎉 Installation complete! 'housebot' is installed in ${BIN_DIR}/housebot"
echo "================================================================="

# Launch Setup Wizard
if [ -t 0 ]; then
    read -p "Would you like to launch the HouseBot Web Setup Wizard now? (Y/n): " launch_choice
    launch_choice=${launch_choice:-Y}
    if [[ "$launch_choice" =~ ^[Yy]$ ]]; then
        exec "${BIN_DIR}/housebot" --web-setup
    fi
fi
