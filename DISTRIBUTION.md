# HouseBot Complete Distribution Playbook (All 3 Options)

This guide explains how to distribute HouseBot to your testers using **all three distribution methods**:

| Method | Target Audience | What Tester Does | Setup Time |
| :--- | :--- | :--- | :--- |
| **Option 1: The 10-Second Test Drive** | Non-technical friends & family with a phone | Taps a Telegram link on their phone | **10 seconds** (Zero install) |
| **Option 2: The Double-Click Portable App** | General testers on PC / Mac / Linux | Double-clicks a single downloaded app file | **2 minutes** (No Python/Git) |
| **Option 3: The 1-Line Terminal Command** | Homelabbers, Linux users, developers | Runs 1 terminal command in bash or PowerShell | **1 minute** |

---

## 🚀 Option 1: The "10-Second Test Drive" (Zero Install for Them)

In this mode, you run HouseBot on your machine or a small $5/mo VPS, and invite your friends to talk to your bot directly on Telegram.

### How to Host the Demo:
1. Start HouseBot in **Public Demo Mode**:
   ```bash
   python3 -m housebot --demo
   ```
   *(Or in your `config.json`, set `"bot": { "demo_mode": true }`)*
2. Send your bot's Telegram link (`https://t.me/YourBotUsername`) to your testers.

### What Your Tester Experiences:
1. They tap `https://t.me/YourBotUsername` on their iPhone or Android phone.
2. They tap **START**.
3. HouseBot immediately replies with a welcome message and interactive command suggestions:
   - *"Add milk and coffee to my shopping list"*
   - *"Remind me in 10 minutes to stretch"*
   - *"Remember that the garage code is 4921"*
   - *"What's my garage code?"*
4. **They get immediate feedback in 10 seconds without touching a computer.**

### ✉️ Message to Send:
> *"Hey! I built a private local AI assistant called HouseBot. Tap this link on your phone to test-drive it: https://t.me/YourBotUsername — try texting it 'add sourdough to shopping list' or 'remind me in 5 minutes to call mom'!"*

---

## 📦 Option 2: The "Double-Click Portable App" (Full Local Test Drive)

In this mode, testers download a single standalone executable and run HouseBot locally on their own computer with their own Obsidian vault and local LLM.

### How to Build the Executables:

#### A. Build for All Platforms via GitHub Actions (Recommended)
Push this branch to GitHub, go to the **Actions** tab, select **"Build Standalone Executables"**, and click **"Run workflow"**. GitHub will build:
- 🪟 `HouseBot-Windows-x64.exe` (Windows 10/11)
- 🍏 `HouseBot-macOS-arm64` (Apple Silicon M1/M2/M3/M4 Macs)
- 🐧 `HouseBot-Linux-x86_64` (Linux)

#### B. Build Locally on Current Machine
```bash
python3 scripts/build_standalone.py
```
Output is created in `dist/HouseBot` (or `dist/HouseBot.exe` on Windows).

### What Your Tester Experiences:
1. They download `HouseBot.exe` (Windows) or `HouseBot` (Mac/Linux).
2. They **double-click the file**.
3. It automatically opens **`http://127.0.0.1:8082`** in their default browser:
   - **Hardware Detection**: Scans their system RAM/CPU/GPU and pre-selects the recommended model (`llama3.2:3b`).
   - **Vault Auto-Discovery**: Finds their Obsidian vault automatically.
   - **Live Web Chat Playground**: Lets them test HouseBot right in their browser immediately!
   - **Mobile Setup**: Explains how to link Telegram on their phone.

---

## ⚡ Option 3: The "1-Line Command" (For Technical / Homelab Friends)

For friends who love terminals, Linux, home servers, or Windows PowerShell.

### A. macOS & Linux (1-Line bash)
Testers simply run:
```bash
curl -fsSL https://raw.githubusercontent.com/popbox99/HouseBotInstaller/main/install.sh | bash
```
*What it does:*
- Checks for Python 3.9+.
- Sets up an isolated environment in `~/.local/share/housebot-app`.
- Creates a `housebot` executable command in `~/.local/bin/housebot`.
- Launches the web setup wizard in their browser!

### B. Windows PowerShell (1-Line PowerShell)
In Windows PowerShell, testers run:
```powershell
irm https://raw.githubusercontent.com/popbox99/HouseBotInstaller/main/install.ps1 | iex
```
*What it does:*
- Downloads HouseBot into `%LOCALAPPDATA%\HouseBot`.
- Creates a Start Menu shortcut.
- Automatically launches the Web Setup Wizard in their browser.

### C. Modern Python (`pipx` / `pip`)
```bash
pipx run --spec git+https://github.com/popbox99/HouseBotInstaller.git housebot
```
