"""Interactive Onboarding & Setup Wizard for HouseBot (zero dependencies).

Guides users through:
  1. Storage selection: Auto-detecting Obsidian Vaults vs. standard notes folder
  2. Messaging platform selection with live token verification (Telegram, Signal, CLI)
  3. AI Brain configuration: Hardware detection, model sizing recommendations,
     and local LLM discovery (Ollama, LM Studio)
  4. Auto-starting background service registration (Linux, macOS, Windows)
"""

import json
import os
import platform
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .hardware import detect_local_llm_services, inspect_hardware, recommend_model
from .service import install_service


def prompt_choice(question: str, options: List[str], default_idx: int = 0) -> int:
    """Prompt user to select an option from a numbered list."""
    print(f"\n{question}")
    for idx, opt in enumerate(options, 1):
        marker = " (Default)" if (idx - 1) == default_idx else ""
        print(f"  [{idx}] {opt}{marker}")
    while True:
        choice = input(f"Select (1-{len(options)}) [{default_idx + 1}]: ").strip()
        if not choice:
            return default_idx
        if choice.isdigit() and 1 <= int(choice) <= len(options):
            return int(choice) - 1
        print("Invalid choice. Please enter a valid number.")


def detect_obsidian_vaults() -> List[Path]:
    """Auto-detect existing Obsidian vaults on the host machine."""
    vaults = []
    home = Path.home()

    # 1. Inspect Obsidian's internal config file
    config_paths = [
        home / ".config" / "obsidian" / "obsidian.json",
        home / "Library" / "Application Support" / "obsidian" / "obsidian.json",
        Path(os.environ.get("APPDATA", "")) / "obsidian" / "obsidian.json",
    ]
    for cp in config_paths:
        if cp.exists():
            try:
                with open(cp, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for _vid, vinfo in data.get("vaults", {}).items():
                        vpath = Path(vinfo.get("path", ""))
                        if vpath.is_dir() and vpath not in vaults:
                            vaults.append(vpath)
            except Exception:
                pass

    # 2. Check standard common vault directories
    common_paths = [
        home / "Documents" / "Obsidian Vault",
        home / "Documents" / "Vault",
        home / "Vault",
    ]
    for cp in common_paths:
        if cp.is_dir() and cp not in vaults:
            vaults.append(cp)

    return vaults


def run_storage_setup() -> str:
    """Guide the user through picking an Obsidian vault or standard notes folder."""
    print("\n--- [Step 1/4] Storage & Notes Directory ---")
    detected_vaults = detect_obsidian_vaults()

    options = [
        "Use an Obsidian Vault (recommended for Obsidian users)",
        "Use a standard notes folder (e.g. Documents/HouseBot)",
    ]
    st_idx = prompt_choice("Where should HouseBot keep your notes, shopping lists, and logs?", options)

    home = Path.home()
    if st_idx == 0:
        if detected_vaults:
            print("\n🔍 Found existing Obsidian Vault(s) on your system:")
            v_options = [str(v) for v in detected_vaults] + ["Enter a custom vault path"]
            v_idx = prompt_choice("Select your Obsidian Vault:", v_options)
            if v_idx < len(detected_vaults):
                chosen_dir = str(detected_vaults[v_idx])
            else:
                custom = input("Enter the absolute path to your Obsidian Vault: ").strip()
                chosen_dir = str(Path(custom).expanduser()) if custom else str(detected_vaults[0])
        else:
            default_v = home / "Documents" / "Obsidian Vault"
            custom = input(f"Enter path to your Obsidian Vault [{default_v}]: ").strip()
            chosen_dir = str(Path(custom).expanduser()) if custom else str(default_v)
    else:
        default_dir = home / "Documents" / "HouseBot"
        custom = input(f"Enter folder for HouseBot notes [{default_dir}]: ").strip()
        chosen_dir = str(Path(custom).expanduser()) if custom else str(default_dir)

    target_path = Path(chosen_dir)
    target_path.mkdir(parents=True, exist_ok=True)
    print(f"✓ Notes directory set to: {target_path}")
    return str(target_path), (st_idx == 0)


def run_transport_setup() -> Tuple[dict, List[str], str, dict]:
    """Guide the user through choosing and verifying their messaging platform."""
    print("\n--- [Step 2/4] Messaging Platform ---")
    options = [
        "Telegram (Recommended: 60-second setup, mobile notifications, zero daemons)",
        "Signal (Privacy-focused, connects via signal-cli)",
        "Interactive Terminal / CLI Mode (Instant test mode, no chat account required)",
    ]
    t_idx = prompt_choice("Which messaging platform would you like to use with HouseBot?", options)

    transports = {
        "signal": {"enabled": False},
        "telegram": {"enabled": False},
        "api": {"enabled": True, "port": 8082, "token": os.urandom(16).hex()},
    }
    allowed_senders = []
    owner = ""

    if t_idx == 0:  # Telegram
        print("\nSetting up Telegram Bot:")
        print("  1. Open Telegram and message @BotFather (https://t.me/BotFather)")
        print("  2. Send '/newbot' and follow the prompts to create your bot.")
        token = input("  3. Paste your Bot Token: ").strip()

        print("Testing Telegram Bot Token...")
        bot_username = ""
        try:
            req = urllib.request.Request(f"https://api.telegram.org/bot{token}/getMe")
            with urllib.request.urlopen(req, timeout=10) as r:
                res = json.loads(r.read())
                if res.get("ok"):
                    bot_username = res["result"].get("username", "your bot")
                    print(f"✓ Connected successfully to @{bot_username}!")
        except Exception as e:
            print(f"⚠️ Could not verify token online ({e}). Continuing with entered token.")

        print(f"\nNow send any message (e.g. 'hello') to @{bot_username or 'your bot'} on Telegram.")
        input("Press Enter once you have sent the message to auto-detect your User ID... ")

        detected_user_id = ""
        try:
            req = urllib.request.Request(f"https://api.telegram.org/bot{token}/getUpdates")
            with urllib.request.urlopen(req, timeout=10) as r:
                res = json.loads(r.read())
                for upd in reversed(res.get("result", [])):
                    msg = upd.get("message") or {}
                    sender_id = str(msg.get("from", {}).get("id", ""))
                    sender_name = msg.get("from", {}).get("first_name", "User")
                    if sender_id:
                        detected_user_id = sender_id
                        print(f"✓ Identified you: {sender_name} (User ID: {sender_id})")
                        break
        except Exception:
            pass

        if not detected_user_id:
            detected_user_id = input("Could not auto-detect ID. Enter your numeric Telegram User ID: ").strip()

        if detected_user_id:
            allowed_senders.append(detected_user_id)
            owner = detected_user_id

        transports["telegram"] = {"enabled": True, "token": token}
        meta = {"platform": "telegram", "bot_username": bot_username}

    elif t_idx == 1:  # Signal
        account = input("Enter the Signal phone number registered with signal-cli (e.g. +15555550100): ").strip()
        user_num = input("Enter your personal phone number or UUID for allowlist: ").strip()
        socket_path = input("Enter signal-cli socket path [~/.local/run/signal-cli/socket]: ").strip()
        if not socket_path:
            socket_path = "~/.local/run/signal-cli/socket"
        transports["signal"] = {"enabled": True, "account": account, "socket": socket_path}
        if user_num:
            allowed_senders.append(user_num)
            owner = user_num
        meta = {"platform": "signal", "account": account}

    elif t_idx == 2:  # CLI
        print("✓ Interactive CLI mode configured. You can chat with HouseBot directly in your terminal.")
        meta = {"platform": "cli"}

    return transports, allowed_senders, owner, meta


def run_llm_setup() -> List[dict]:
    """Inspect hardware and configure local or cloud LLM backends."""
    print("\n--- [Step 3/4] AI Brain (LLM) Configuration ---")
    hw = inspect_hardware()
    rec = recommend_model(hw)

    print(f"🔍 System Hardware Detected:")
    print(f"  • Operating System: {hw.os_name.capitalize()} ({hw.arch})")
    print(f"  • CPU Cores: {hw.cpu_count}")
    print(f"  • Physical Memory: {hw.total_ram_gb} GB" + (" (Apple Silicon Unified Memory)" if hw.is_apple_silicon else ""))
    if hw.gpu_name:
        print(f"  • GPU: {hw.gpu_name} ({hw.gpu_vram_gb} GB VRAM)")

    print(f"\n💡 Hardware Recommendation:")
    print(f"  Tier: {rec['tier']} ({rec['size']})")
    print(f"  Suggested Model: '{rec['recommended_model']}' (requires {rec['ram_needed']} memory)")
    print(f"  {rec['description']}")

    # Check for running services
    discovered = detect_local_llm_services()
    backends = []

    if discovered.get("ollama", {}).get("active"):
        models = discovered["ollama"].get("models", [])
        print(f"\n✓ Found active Ollama server at http://127.0.0.1:11434 with {len(models)} model(s).")
        if models:
            m_options = models + [f"Download recommended '{rec['recommended_model']}'"]
            m_idx = prompt_choice("Select model to use:", m_options)
            if m_idx < len(models):
                chosen_m = models[m_idx]
            else:
                chosen_m = rec["recommended_model"]
                print(f"Pulling '{chosen_m}' via Ollama...")
                subprocess.run(["ollama", "pull", chosen_m], check=False)
            backends.append({
                "name": "local-ollama",
                "base_url": "http://127.0.0.1:11434",
                "model": chosen_m,
                "api": "ollama",
            })
            return backends

    elif discovered.get("lmstudio", {}).get("active"):
        models = discovered["lmstudio"].get("models", [])
        print(f"\n✓ Found active LM Studio server at http://127.0.0.1:1234.")
        chosen_m = models[0] if models else rec["recommended_model"]
        backends.append({
            "name": "local-lmstudio",
            "base_url": "http://127.0.0.1:1234",
            "model": chosen_m,
            "api": "openai",
        })
        return backends

    # No service running — prompt user
    llm_options = [
        f"Install Ollama & pull '{rec['recommended_model']}' automatically",
        "Use a Cloud API (OpenAI / Groq / DeepSeek / OpenRouter)",
        "I already have a server running on another port or host",
        "Skip AI configuration for now (keyword & regex skills only)",
    ]
    llm_idx = prompt_choice("No active local AI server was found. How would you like to proceed?", llm_options)

    if llm_idx == 0:  # Install Ollama
        if not shutil.which("ollama"):
            print("\nInstalling Ollama...")
            if sys.platform == "linux":
                subprocess.run("curl -fsSL https://ollama.com/install.sh | sh", shell=True, check=False)
            elif sys.platform == "darwin":
                if shutil.which("brew"):
                    subprocess.run(["brew", "install", "ollama"], check=False)
                else:
                    print("Please install Ollama from https://ollama.com/download")
            elif sys.platform == "win32":
                subprocess.run(["winget", "install", "Ollama.Ollama"], check=False)

        if shutil.which("ollama"):
            print(f"Downloading recommended model '{rec['recommended_model']}'...")
            subprocess.run(["ollama", "pull", rec["recommended_model"]], check=False)

        backends.append({
            "name": "local-ollama",
            "base_url": "http://127.0.0.1:11434",
            "model": rec["recommended_model"],
            "api": "ollama",
        })

    elif llm_idx == 1:  # Cloud API
        key = input("Enter your API Key: ").strip()
        provider_options = [
            ("OpenAI", "https://api.openai.com/v1", "gpt-4o-mini"),
            ("Groq (Ultra-fast)", "https://api.groq.com/openai/v1", "llama-3.1-8b-instant"),
            ("DeepSeek", "https://api.deepseek.com/v1", "deepseek-chat"),
            ("OpenRouter", "https://openrouter.ai/api/v1", "meta-llama/llama-3.1-8b-instruct"),
            ("Custom Base URL", "", ""),
        ]
        p_idx = prompt_choice("Select provider:", [p[0] for p in provider_options])
        name, base_url, default_model = provider_options[p_idx]
        if not base_url:
            base_url = input("Enter custom base URL: ").strip()
            model = input("Enter model name: ").strip()
        else:
            model = input(f"Enter model name [{default_model}]: ").strip() or default_model

        backends.append({
            "name": name.lower().split()[0],
            "base_url": base_url,
            "api_key": key,
            "model": model,
            "api": "openai",
        })

    elif llm_idx == 2:  # Custom server
        url = input("Enter server base URL [http://127.0.0.1:11434]: ").strip() or "http://127.0.0.1:11434"
        model = input(f"Enter model name [{rec['recommended_model']}]: ").strip() or rec["recommended_model"]
        api_type = "openai" if "/v1" in url else "ollama"
        backends.append({
            "name": "custom",
            "base_url": url,
            "model": model,
            "api": api_type,
        })

    return backends


def run_phone_guide(transport_meta: dict, is_obsidian: bool):
    """Walk the user through the exact mobile apps they need on their phone."""
    print("\n--- [Step 5/5] Mobile Phone Companion Apps ---")
    p_options = [
        "iPhone / iPad (iOS)",
        "Android",
        "Skip mobile guide (Desktop only)",
    ]
    p_idx = prompt_choice("What mobile device will you use to talk with HouseBot?", p_options)
    if p_idx == 2:
        return

    is_ios = (p_idx == 0)
    device_label = "iPhone" if is_ios else "Android"
    print(f"\n📱 Recommended Apps for your {device_label}:")
    print("=" * 60)

    platform_name = transport_meta.get("platform", "telegram")
    if platform_name == "telegram":
        bot_user = transport_meta.get("bot_username")
        print("1. Primary Chat App: Telegram Messenger")
        if is_ios:
            print("   • Download Telegram from the App Store.")
        else:
            print("   • Download Telegram from Google Play Store or F-Droid.")
        if bot_user:
            print(f"   • Tap here to message your bot: https://t.me/{bot_user}")
        print("   • Tap 'START' and send 'whats on my shopping list' or 'remind me in 10 minutes' to test!")

    elif platform_name == "signal":
        print("1. Primary Chat App: Signal Private Messenger")
        print("   • Download Signal from the App Store / Google Play.")
        print(f"   • Send a message to your bot's registered phone number ({transport_meta.get('account', '')}).")

    elif platform_name == "cli":
        print("1. Chat Interface:")
        print("   • You chose Terminal CLI mode. You can interact directly from your computer.")

    # Notes & Lists
    if is_obsidian:
        print("\n2. Notes & Lists (Obsidian Vault):")
        print("   • Download the Obsidian Mobile App (App Store / Google Play).")
        if is_ios:
            print("   • If using iCloud, place your vault in iCloud Drive to view live edits on your iPhone.")
        else:
            print("   • You can sync your vault folder to Android using Syncthing or Obsidian Sync.")
        print("   • Note: You don't even need the mobile app open—texting the bot updates your files automatically!")
    else:
        print("\n2. Notes & Shopping Lists:")
        print("   • No mobile app required! Just text 'add milk to shopping list' and HouseBot")
        print("     will update your desktop notes automatically.")

    # Calendar & Reminders (CalDAV)
    print("\n3. Calendar & Tasks Sync (Optional CalDAV):")
    if is_ios:
        print("   • Calendar: Syncs natively! Go to Settings -> Calendar -> Accounts -> Add Account -> Other -> Add CalDAV Account.")
        print("   • Tasks: Apple Reminders ignores CalDAV, but you can use any CalDAV task app from the App Store.")
    else:
        print("   • Sync Engine: Install 'DAVx⁵' (available on F-Droid and Google Play).")
        print("   • Tasks: Install 'Tasks.org' (open source, connects directly to DAVx⁵).")
        print("   • Calendar: Use native Google Calendar or 'Fossify Calendar'.")
    print("=" * 60)


def run_wizard(config_path: Path = None):
    """Run the complete onboarding wizard and write the configuration file."""
    print("=" * 65)
    print("           🏠 Welcome to HouseBot Setup Wizard")
    print("=" * 65)
    print("This wizard will get HouseBot configured and running on your system.")

    target_cfg = config_path or Path(os.environ.get(
        "HOUSEBOT_CONFIG", Path.home() / ".config" / "housebot" / "config.json"
    ))

    # Run setup steps
    notes_dir, is_obsidian = run_storage_setup()
    transports, allowed_senders, owner, meta = run_transport_setup()
    backends = run_llm_setup()

    # Build finalized config dict
    data_dir = str(Path.home() / ".local" / "share" / "housebot")
    final_config = {
        "bot": {
            "name": "HouseBot",
            "data_dir": data_dir,
            "owner": owner,
            "allowed_senders": allowed_senders,
        },
        "llm": {
            "backends": backends or [{
                "name": "fallback",
                "base_url": "http://127.0.0.1:11434",
                "model": "llama3.1:8b",
                "api": "ollama",
            }],
        },
        "skills": {
            "notes_dir": notes_dir,
            "search_dirs": [notes_dir],
        },
        "transports": transports,
    }

    # Save configuration file
    target_cfg.parent.mkdir(parents=True, exist_ok=True)
    with open(target_cfg, "w", encoding="utf-8") as f:
        json.dump(final_config, f, indent=2, ensure_ascii=False)

    print("\n" + "=" * 65)
    print(f"✓ Configuration successfully saved to: {target_cfg}")

    # Step 4: Background Service Installation
    print("\n--- [Step 4/5] Always-On Background Service ---")
    srv_choice = input("Would you like HouseBot to start automatically in the background on system boot? (Y/n): ").strip().lower()
    if srv_choice in ("", "y", "yes"):
        ok, msg = install_service()
        if ok:
            print(f"✓ {msg}")
        else:
            print(f"⚠️ {msg}")

    # Step 5: Mobile Phone Companion Guide
    run_phone_guide(meta, is_obsidian)

    print("\n🎉 Setup complete! You can start HouseBot at any time with:")
    print("     python3 -m housebot\n")
