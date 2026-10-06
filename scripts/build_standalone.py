#!/usr/bin/env python3
"""Build script for creating standalone, portable HouseBot executables.

Uses PyInstaller to bundle Python and all HouseBot code into a single,
self-contained executable binary for non-technical users and testers.
No Python or Git installation required on the target machine.
"""

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

# Force UTF-8 encoding on Windows to prevent cp1252 charmap encode errors
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def get_binary_name() -> str:
    system = platform.system().lower()
    if system == "windows":
        return "HouseBot.exe"
    return "HouseBot"


def build_executable(dist_dir: Path = None, onefile: bool = True) -> Path:
    repo_root = Path(__file__).resolve().parent.parent
    dist_path = dist_dir or (repo_root / "dist")
    build_path = repo_root / "build"
    entry_point = repo_root / "launcher.py"

    binary_name = get_binary_name()
    target_binary = dist_path / binary_name

    print("=" * 65)
    print("        [+] HouseBot Standalone Executable Builder")
    print("=" * 65)
    print(f"Target OS:       {platform.system()} ({platform.machine()})")
    print(f"Output Binary:   {target_binary}")
    print(f"Entry Point:     {entry_point}")
    print("=" * 65)

    # 1. Verify PyInstaller is installed
    pyinstaller_bin = shutil.which("pyinstaller")
    # Check current python environment or virtualenv
    if not pyinstaller_bin:
        venv_pyinstaller = repo_root / ".venv" / "bin" / "pyinstaller"
        venv_pyinstaller_win = repo_root / ".venv" / "Scripts" / "pyinstaller.exe"
        if venv_pyinstaller.exists():
            pyinstaller_bin = str(venv_pyinstaller)
        elif venv_pyinstaller_win.exists():
            pyinstaller_bin = str(venv_pyinstaller_win)

    if not pyinstaller_bin:
        print("\n[-] PyInstaller is not installed in this environment.")
        print("To install it, run:")
        print("    pip install pyinstaller>=6.0")
        print("or:")
        print("    uv pip install pyinstaller\n")
        sys.exit(1)

    print(f"Using PyInstaller: {pyinstaller_bin}")

    # 2. Prepare PyInstaller command flags
    cmd = [
        pyinstaller_bin,
        "--noconfirm",
        "--clean",
        "--name", "HouseBot",
        "--paths", str(repo_root),
        "--distpath", str(dist_path),
        "--workpath", str(build_path),
        # Hidden imports for dynamic discovery
        "--hidden-import", "housebot",
        "--hidden-import", "housebot.wizard",
        "--hidden-import", "housebot.webui",
        "--hidden-import", "housebot.hardware",
        "--hidden-import", "housebot.service",
        "--hidden-import", "housebot.engine",
        "--hidden-import", "housebot.config",
        "--hidden-import", "housebot.llm",
        "--hidden-import", "housebot.whens",
        "--hidden-import", "housebot.intents",
        "--hidden-import", "housebot.jobs",
        "--hidden-import", "housebot.caldav",
        "--hidden-import", "housebot.wyoming",
        "--hidden-import", "housebot.transports.api",
        "--hidden-import", "housebot.transports.cli",
        "--hidden-import", "housebot.transports.signal",
        "--hidden-import", "housebot.transports.telegram",
        "--collect-all", "housebot",
    ]

    if onefile:
        cmd.append("--onefile")

    cmd.append(str(entry_point))

    print("\nRunning PyInstaller build process...")
    res = subprocess.run(cmd, cwd=str(repo_root))
    if res.returncode != 0:
        print(f"\n[-] Build failed with returncode {res.returncode}")
        sys.exit(res.returncode)

    if not target_binary.exists():
        print(f"\n[-] Expected output binary not found at {target_binary}")
        sys.exit(1)

    size_mb = target_binary.stat().st_size / (1024 * 1024)
    print("\n" + "=" * 65)
    print(f"[*] Build SUCCESSFUL!")
    print(f"Binary created: {target_binary}")
    print(f"Binary size:    {size_mb:.1f} MB")
    print("=" * 65)

    # 3. Quick sanity check on built binary
    print("\nVerifying binary execution (smoke check)...")
    try:
        smoke_res = subprocess.run(
            [str(target_binary), "--hardware"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if smoke_res.returncode == 0 and "Hardware Profile" in smoke_res.stdout:
            print("[+] Binary smoke check passed: --hardware output confirmed!")
        else:
            print(f"[!] Binary executed but output was unexpected:\n{smoke_res.stdout}\n{smoke_res.stderr}")
    except Exception as e:
        print(f"[!] Smoke check skipped/failed: {e}")

    print("\nNext steps to distribute to testers:")
    print(f"1. Copy '{target_binary.name}' to a shared folder or Google Drive/Dropbox.")
    print("2. Send to your tester: they just double-click it to start!")
    print("=" * 65 + "\n")

    return target_binary


if __name__ == "__main__":
    build_executable()
