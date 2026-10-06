# HouseBot Tester Distribution Guide (Option 2: Standalone Portable App)

This guide explains how to package HouseBot into a standalone executable (`HouseBot.exe` on Windows, or `HouseBot` on macOS and Linux) and send it to your testers so they can test-drive HouseBot with **zero setup, zero Git, and zero Python knowledge**.

---

## 🌟 How It Works for Your Testers

1. **They download a single file** (e.g. `HouseBot.exe` or `HouseBot-Windows-x64.zip`).
2. **They double-click it**:
   - A friendly window launches and automatically opens **`http://127.0.0.1:8082`** in their default web browser (Chrome, Edge, Safari, Firefox).
   - **Step 1 (Hardware & Brain)**: HouseBot probes their RAM, CPU cores, and GPU, detects if Ollama/LM Studio is running, and automatically selects the recommended model (e.g. `llama3.2:3b`).
   - **Step 2 (Storage)**: Auto-detects their existing Obsidian vaults in a dropdown, or lets them pick any folder.
   - **Step 3 (Messenger)**: They can choose **Instant Web Chat** (requires no accounts), **Telegram**, or **Signal**.
   - **Step 4 (Launch)**: Clicking "Save & Launch" starts HouseBot immediately.
3. **They can chat right in their browser**, or follow the step-by-step **Mobile Companion Guide** tab to talk to HouseBot from their iPhone or Android phone!

---

## 🛠️ How to Build Standalone Executables

### Method A: Build Multi-Platform Binaries Free via GitHub Actions (Recommended)
You don't need a Windows PC or a Mac to compile Windows and Mac binaries. The repository includes an automated GitHub Actions workflow: `.github/workflows/build-executables.yml`.

1. Push your code to your GitHub repository.
2. On GitHub, go to the **Actions** tab.
3. Select **"Build Standalone Executables"** on the left and click **"Run workflow"**.
4. Within 2–3 minutes, GitHub compiles:
   - `HouseBot-Windows-x64.exe` (For Windows 10/11 testers)
   - `HouseBot-macOS-arm64` (For Apple Silicon M1/M2/M3/M4 Macs)
   - `HouseBot-Linux-x86_64` (For Linux testers)
5. Download the zip artifact directly from the GitHub run or attach it to a GitHub Release.

---

### Method B: Build Locally on Your Current Machine
To compile a binary on your local machine using the included build pipeline:

```bash
# 1. Install PyInstaller
pip install pyinstaller>=6.0

# 2. Run the build script
python3 scripts/build_standalone.py
```

The compiled binary will be placed in:
- `dist/HouseBot` (Linux / macOS)
- `dist/HouseBot.exe` (Windows)

The build script automatically performs an execution smoke test on the generated binary before finishing.

---

## 📦 How to Send HouseBot to Testers

1. **Upload the file**:
   Upload `HouseBot.exe` (or zip) to Google Drive, Dropbox, iCloud Drive, or a GitHub Release.
2. **Copy the message template below** and send it to your testers via email, Discord, or WhatsApp:

---

### ✉️ Tester Invitation Template

> **Subject:** Want to test-drive HouseBot? (Private AI assistant for notes & reminders)
>
> Hey [Name],
>
> I've been working on **HouseBot**, a private AI assistant that runs entirely on your computer and helps you manage reminders, shopping lists, and notes without monthly subscriptions or cloud lock-in.
>
> I'd love to get your feedback on it! You don't need to install Python or know any programming to try it out.
>
> **How to try it in 2 minutes:**
> 1. Download the app from this link: [Insert your Google Drive / Dropbox link here]
> 2. Double-click **HouseBot.exe** (or **HouseBot** on Mac).
> 3. Your web browser will open automatically with a quick setup guide:
>    - It scans your computer to recommend the best local AI model.
>    - If you use Obsidian, it finds your vault automatically.
>    - You can chat with it right in your browser, or connect it to Telegram on your phone.
>
> **Things you can try asking it:**
> - *"Add eggs, sourdough bread, and coffee to my shopping list"*
> - *"Remind me at 5pm to call mom"*
> - *"Remember that the garage code is 4921"*
> - *"What was that garage code again?"*
>
> Let me know how the setup felt and if anything was confusing!

---

## 🔍 Troubleshooting for Testers

- **Windows SmartScreen warning ("Windows protected your PC")**:
  - *Why*: The executable is newly built and not code-signed with an expensive Microsoft certificate yet.
  - *Fix*: Testers just click **"More info"** -> **"Run anyway"**.
- **macOS Gatekeeper warning ("HouseBot cannot be opened because it is from an unidentified developer")**:
  - *Fix*: Right-click (or Control-click) `HouseBot` -> Click **Open** -> Click **Open** again.
- **Browser didn't open automatically**:
  - *Fix*: Manually open any browser and visit: `http://127.0.0.1:8082`.
