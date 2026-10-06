"""Cross-platform background service management for HouseBot (Linux, macOS, Windows).

Provides standard-library-only automated registration so HouseBot can run
as an always-on background daemon on any operating system:
  * Linux: systemd user service (~/.config/systemd/user/housebot.service)
  * macOS: launchd agent (~/Library/LaunchAgents/com.housebot.plist)
  * Windows: Startup folder launcher (shell:startup / housebot.vbs)
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Tuple


def get_service_paths() -> Tuple[str, Path]:
    """Return (os_type, target_service_file_path)."""
    home = Path.home()
    if sys.platform == "linux":
        path = home / ".config" / "systemd" / "user" / "housebot.service"
        return "linux", path
    elif sys.platform == "darwin":
        path = home / "Library" / "LaunchAgents" / "com.housebot.plist"
        return "darwin", path
    elif sys.platform == "win32":
        appdata = Path(os.environ.get("APPDATA", home / "AppData" / "Roaming"))
        path = appdata / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "housebot.vbs"
        return "win32", path
    return "unknown", home / "housebot.service"


def is_service_installed() -> bool:
    """Return True if the background service file exists on this system."""
    _, srv_path = get_service_paths()
    return srv_path.exists()


def install_service(python_exec: str = None, repo_dir: Path = None) -> Tuple[bool, str]:
    """Install and enable HouseBot as a persistent background service."""
    python_bin = python_exec or sys.executable
    repo_path = repo_dir or Path(__file__).resolve().parent.parent
    os_type, srv_path = get_service_paths()

    srv_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        if os_type == "linux":
            content = f"""[Unit]
Description=HouseBot Home Assistant Service
After=network-online.target

[Service]
Type=simple
WorkingDirectory={repo_path}
ExecStart={python_bin} -m housebot
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
"""
            srv_path.write_text(content, encoding="utf-8")
            if shutil.which("systemctl"):
                subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
                subprocess.run(["systemctl", "--user", "enable", "--now", "housebot.service"], check=False)
            return True, f"Systemd user service installed and started at {srv_path}"

        elif os_type == "darwin":
            content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.housebot</string>
    <key>WorkingDirectory</key>
    <string>{repo_path}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python_bin}</string>
        <string>-m</string>
        <string>housebot</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>{Path.home()}/.local/share/housebot/housebot.log</string>
    <key>StandardErrorPath</key>
    <string>{Path.home()}/.local/share/housebot/housebot.err</string>
</dict>
</plist>
"""
            srv_path.write_text(content, encoding="utf-8")
            if shutil.which("launchctl"):
                subprocess.run(["launchctl", "unload", str(srv_path)], check=False)
                subprocess.run(["launchctl", "load", str(srv_path)], check=False)
            return True, f"macOS LaunchAgent registered and loaded at {srv_path}"

        elif os_type == "win32":
            # Silent background launcher on Windows startup
            vbs_script = f'CreateObject("Wscript.Shell").Run "{python_bin} -m housebot", 0, False\n'
            srv_path.write_text(vbs_script, encoding="utf-8")
            return True, f"Windows Startup shortcut created at {srv_path}"

        return False, f"Unsupported operating system: {sys.platform}"
    except Exception as e:
        return False, f"Failed to install service: {e}"


def uninstall_service() -> Tuple[bool, str]:
    """Stop and remove the background service."""
    os_type, srv_path = get_service_paths()
    try:
        if os_type == "linux":
            if shutil.which("systemctl"):
                subprocess.run(["systemctl", "--user", "stop", "housebot.service"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                subprocess.run(["systemctl", "--user", "disable", "housebot.service"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if srv_path.exists():
                srv_path.unlink()
            if shutil.which("systemctl"):
                subprocess.run(["systemctl", "--user", "daemon-reload"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True, "Systemd service stopped and removed."

        elif os_type == "darwin":
            if shutil.which("launchctl") and srv_path.exists():
                subprocess.run(["launchctl", "unload", str(srv_path)], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if srv_path.exists():
                srv_path.unlink()
            return True, "macOS LaunchAgent unloaded and removed."

        elif os_type == "win32":
            if srv_path.exists():
                srv_path.unlink()
            return True, "Windows Startup shortcut removed."

        return False, f"Unsupported operating system: {sys.platform}"
    except Exception as e:
        return False, f"Failed to uninstall service: {e}"
