# HouseBot Project State & Continuity Summary

**Last Updated:** October 5, 2026 (Evening Session)  
**Active Working Directory:** `/home/archbox/Work/housebot`  
**GitHub Repository:** [popbox99/HouseBotInstaller](https://github.com/popbox99/HouseBotInstaller) (tracking `main`)  
**Active Feature Branch:** `feature/mvp-refactor`  
**Smoke Test Suite:** 40/40 Passing (`python3 tests/smoke.py`)

---

## 1. What We Accomplished in this Session

1. **Multi-Channel Distribution (All 3 Options)**:
   - **Option 1**: Instant 10-Second Test Drive via Telegram link (`--demo` / `demo_mode`).
   - **Option 2**: Standalone double-click portable executables:
     - Built cross-platform via GitHub Actions workflow `.github/workflows/build-executables.yml` (Windows `.exe`, macOS arm64, Linux x86_64).
     - Local standalone builder: `scripts/build_standalone.py` creates `dist/HouseBot`.
   - **Option 3**: 1-line script installers:
     - `install.sh` for Linux & macOS (`~/.local/bin/housebot`).
     - `install.ps1` for Windows PowerShell (`%LOCALAPPDATA%\HouseBot`).

2. **Signal + Google Voice Safe Onboarding**:
   - Added comprehensive warnings and step-by-step instructions in `housebot/wizard.py` and `housebot/webui.py` so Signal testers do not accidentally de-register their primary personal phone number.

3. **Home Assistant Late-Integration**:
   - Added `homeassistant` configuration support to both the Web Dashboard (`/api/test-ha`, `/api/config`, `/api/save-config`) and the terminal CLI wizard (`housebot --setup`).
   - Supports inline Long-Lived Access Tokens.
   - Connects Home Assistant voice satellites / Assist microphones via the OpenAI Conversation integration (`http://<ip>:8082/v1`, token `housebot-local`).

4. **Cross-Platform Uninstaller**:
   - Web UI: Added **🗑️ Uninstall HouseBot** button under System & Hardware tab.
   - CLI: `housebot --uninstall` / `HouseBot.exe --uninstall`.
   - Scripts: `uninstall.sh` (Linux/macOS) and `uninstall.ps1` (Windows PowerShell).
   - Safety: Guarantees user notes and Obsidian vaults are never deleted.

5. **Telegram Pre-Install Pro-Tip Banner**:
   - Placed at the very top of both the Web Setup Wizard and the CLI onboarding wizard, guiding users to install Telegram first for 30-second `@BotFather` token setup.

---

## 2. Key Repository Files

| File | Purpose |
| :--- | :--- |
| `housebot/webui.py` | Standalone Web UI dashboard & setup wizard (`:8082`) with zero external dependencies. |
| `housebot/wizard.py` | Terminal interactive onboarding wizard (`housebot --setup`). |
| `housebot/uninstaller.py` | Clean uninstaller for services, shortcuts, and application binaries. |
| `install.sh` / `install.ps1` | 1-line quick installers. |
| `uninstall.sh` / `uninstall.ps1` | 1-line quick uninstallers. |
| `DISTRIBUTION.md` | Complete playbook for testing and distributing HouseBot. |
| `tests/smoke.py` | Comprehensive smoke test suite covering all 40 checks. |

---

## 3. How to Resume Work

- To run tests: `python3 tests/smoke.py`
- To launch Web Setup: `python3 -m housebot --web-setup`
- To build standalone binary: `python3 scripts/build_standalone.py`
- To push to GitHub: `git push installer feature/mvp-refactor:main`
