#!/usr/bin/env bash
# ==============================================================================
# HouseBot Uninstaller for Linux and macOS
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/popbox99/housebot/master/uninstall.sh | bash
# ==============================================================================
set -e

INSTALL_DIR="${HOME}/.local/share/housebot-app"
BIN_DIR="${HOME}/.local/bin"
DATA_DIR="${HOME}/.local/share/housebot"
CONFIG_DIR="${HOME}/.config/housebot"
SYSTEMD_SERVICE="${HOME}/.config/systemd/user/housebot.service"
LAUNCHD_PLIST="${HOME}/Library/LaunchAgents/com.housebot.plist"

echo "================================================================="
echo "               🗑️  HouseBot Quick Uninstaller"
echo "================================================================="
echo "This will stop background services and remove HouseBot binaries."
echo "Your Obsidian vault and personal notes will NOT be touched."
echo ""

# 1. Stop and remove Linux systemd service if present
if [ -f "${SYSTEMD_SERVICE}" ]; then
    echo "Stopping and disabling systemd service..."
    if command -v systemctl >/dev/null 2>&1; then
        systemctl --user stop housebot.service 2>/dev/null || true
        systemctl --user disable housebot.service 2>/dev/null || true
    fi
    rm -f "${SYSTEMD_SERVICE}"
    if command -v systemctl >/dev/null 2>&1; then
        systemctl --user daemon-reload 2>/dev/null || true
    fi
    echo "✓ Removed systemd service."
fi

# 2. Stop and remove macOS launchd service if present
if [ -f "${LAUNCHD_PLIST}" ]; then
    echo "Unloading macOS LaunchAgent..."
    if command -v launchctl >/dev/null 2>&1; then
        launchctl unload "${LAUNCHD_PLIST}" 2>/dev/null || true
    fi
    rm -f "${LAUNCHD_PLIST}"
    echo "✓ Removed launchd agent."
fi

# 3. Remove launcher binary
if [ -f "${BIN_DIR}/housebot" ]; then
    rm -f "${BIN_DIR}/housebot"
    echo "✓ Removed ${BIN_DIR}/housebot"
fi

# 4. Remove installed application directory
if [ -d "${INSTALL_DIR}" ]; then
    rm -rf "${INSTALL_DIR}"
    echo "✓ Removed application directory: ${INSTALL_DIR}"
fi

# 5. Interactive prompt to remove config and data
if [ -t 0 ]; then
    read -p "Also delete configuration and database files? (y/N): " purge_choice
    purge_choice=${purge_choice:-N}
    if [[ "$purge_choice" =~ ^[Yy]$ ]]; then
        rm -rf "${CONFIG_DIR}"
        rm -rf "${DATA_DIR}"
        echo "✓ Removed configuration (${CONFIG_DIR}) and data (${DATA_DIR})."
    else
        echo "ℹ️ Kept configuration and database files."
    fi
else
    echo "ℹ️ Preserved configuration and data folders."
fi

echo "================================================================="
echo "🎉 HouseBot has been successfully uninstalled from your machine."
echo "================================================================="
