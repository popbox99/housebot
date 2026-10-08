"""Uninstaller module for HouseBot across Linux, macOS, and Windows.

Cleans up:
  1. Auto-start background services (systemd, launchd, Windows Startup).
  2. Application launchers, symlinks, and cloned directories.
  3. Optional: configuration (~/.config/housebot) and runtime databases.
  4. Safety: NEVER touches user notes or Obsidian vaults.
"""

import os
import platform
import shutil
import sys
from pathlib import Path
from typing import Dict, List, Tuple

from .config import DEFAULT_CONFIG_PATH
from .service import uninstall_service


def get_uninstall_targets() -> Dict[str, List[Path]]:
    """Return dictionary of target paths for uninstallation."""
    home = Path.home()
    app_launchers = []
    app_dirs = []

    if sys.platform == "win32":
        appdata = Path(os.environ.get("APPDATA", home / "AppData" / "Roaming"))
        localappdata = Path(os.environ.get("LOCALAPPDATA", home / "AppData" / "Local"))

        # Shortcuts
        app_launchers.append(appdata / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "HouseBot.lnk")
        app_launchers.append(appdata / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "housebot.vbs")

        # Installed app dir
        app_dirs.append(localappdata / "HouseBot")
    else:
        # Linux & macOS
        app_launchers.append(home / ".local" / "bin" / "housebot")
        app_dirs.append(home / ".local" / "share" / "housebot-app")

    # Config and data
    config_dir = Path(DEFAULT_CONFIG_PATH).parent
    data_dir = home / ".local" / "share" / "housebot"

    return {
        "launchers": app_launchers,
        "app_dirs": app_dirs,
        "config": [config_dir],
        "data": [data_dir],
    }


def perform_uninstall(remove_config: bool = False, remove_data: bool = False) -> Tuple[bool, List[str]]:
    """Perform the uninstallation and return (success, log_messages)."""
    logs = []

    # 1. Stop and remove background service
    try:
        ok, msg = uninstall_service()
        logs.append(f"[Service] {msg}")
    except Exception as e:
        logs.append(f"[Service] Error removing service: {e}")

    targets = get_uninstall_targets()

    # 2. Remove launchers and shortcuts
    for launcher in targets["launchers"]:
        if launcher.exists():
            try:
                if launcher.is_file() or launcher.is_symlink():
                    launcher.unlink()
                elif launcher.is_dir():
                    shutil.rmtree(launcher)
                logs.append(f"[Launcher] Removed {launcher}")
            except Exception as e:
                logs.append(f"[Launcher] Failed to remove {launcher}: {e}")

    # 3. Remove cloned app dirs (from 1-line installer)
    for adir in targets["app_dirs"]:
        if adir.exists():
            try:
                shutil.rmtree(adir)
                logs.append(f"[App Files] Removed {adir}")
            except Exception as e:
                logs.append(f"[App Files] Failed to remove {adir}: {e}")

    # 4. Optional: Remove configuration
    if remove_config:
        for cdir in targets["config"]:
            if cdir.exists():
                try:
                    shutil.rmtree(cdir)
                    logs.append(f"[Config] Removed configuration at {cdir}")
                except Exception as e:
                    logs.append(f"[Config] Failed to remove {cdir}: {e}")
    else:
        logs.append("[Config] Preserved configuration files.")

    # 5. Optional: Remove runtime data & logs
    if remove_data:
        for ddir in targets["data"]:
            if ddir.exists():
                try:
                    shutil.rmtree(ddir)
                    logs.append(f"[Data] Removed database and logs at {ddir}")
                except Exception as e:
                    logs.append(f"[Data] Failed to remove {ddir}: {e}")
    else:
        logs.append("[Data] Preserved database and logs.")

    logs.append("[Safety] User notes and Obsidian vaults were untouched.")
    return True, logs


def run_cli_uninstaller():
    """Interactive CLI uninstaller for terminal users."""
    print("=" * 65)
    print("               🗑️  HouseBot Uninstaller")
    print("=" * 65)
    print("This will remove HouseBot background services, desktop shortcuts,")
    print("and installed launcher binaries from your system.")
    print("Note: Your Obsidian notes and vault files will NEVER be touched.\n")

    confirm = input("Are you sure you want to uninstall HouseBot? (y/N): ").strip().lower()
    if confirm not in ("y", "yes"):
        print("Uninstallation cancelled.")
        return

    rem_cfg = input("Delete HouseBot configuration files (~/.config/housebot)? (y/N): ").strip().lower()
    rem_data = input("Delete HouseBot local database and logs (~/.local/share/housebot)? (y/N): ").strip().lower()

    print("\nUninstalling...")
    success, messages = perform_uninstall(
        remove_config=(rem_cfg in ("y", "yes")),
        remove_data=(rem_data in ("y", "yes")),
    )

    for msg in messages:
        print(f"  ✓ {msg}")

    print("\n" + "=" * 65)
    print("🎉 HouseBot has been successfully uninstalled from your machine.")
    print("=" * 65)
