"""Entry point: python -m housebot"""

import os
import sys
import threading
import time

from .config import DEFAULT_CONFIG_PATH, Config
from .engine import Engine
from .jobs import Jobs
from .transports.api import ApiTransport


def main():
    args = sys.argv[1:]

    # CLI subcommands & flags
    if any(arg in args for arg in ("--setup", "setup", "init")):
        from .wizard import run_wizard
        run_wizard()
        return

    if "--web-setup" in args:
        from .webui import start_webui
        print("Starting HouseBot Web Setup Wizard...")
        start_webui(open_browser=True, in_background=False)
        return

    if "--web" in args:
        cfg = Config()
        engine = Engine(cfg)
        from .webui import start_webui
        start_webui(cfg=cfg, engine=engine, open_browser=True, in_background=False)
        return

    if "--hardware" in args:
        from .hardware import inspect_hardware, recommend_model
        hw = inspect_hardware()
        rec = recommend_model(hw)
        print("Hardware Profile:", hw.to_dict())
        print("Model Recommendation:", rec)
        return

    if "--install-service" in args:
        from .service import install_service
        ok, msg = install_service()
        print(msg)
        return

    if "--uninstall-service" in args:
        from .service import uninstall_service
        ok, msg = uninstall_service()
        print(msg)
        return

    # Auto-detect missing configuration
    if not os.path.exists(DEFAULT_CONFIG_PATH) and "--no-wizard" not in args:
        if sys.stdin.isatty():
            print(f"No configuration file found at {DEFAULT_CONFIG_PATH}.")
            print("1. Launch Browser Web Setup (Recommended)")
            print("2. Run Terminal CLI Setup")
            print("3. Skip setup (continue with defaults)")
            choice = input("Select an option [1]: ").strip()
            if choice in ("", "1"):
                from .webui import start_webui
                print("Opening HouseBot Web Setup in your browser...")
                start_webui(open_browser=True, in_background=False)
                if not os.path.exists(DEFAULT_CONFIG_PATH):
                    return
            elif choice == "2":
                from .wizard import run_wizard
                run_wizard()
                if not os.path.exists(DEFAULT_CONFIG_PATH):
                    return
        else:
            # Launched without an interactive TTY (e.g. GUI double-click)
            from .webui import start_webui
            print("[housebot] No config found. Launching Web Setup in browser...")
            start_webui(open_browser=True, in_background=False)
            if not os.path.exists(DEFAULT_CONFIG_PATH):
                return

    cfg = Config()
    engine = Engine(cfg)
    senders = []
    threads = []

    if "--cli" in args:
        from .transports.cli import CliTransport
        cli = CliTransport(cfg, engine)
        cli.serve_forever()
        return

    if cfg["transports"]["signal"].get("enabled"):
        from .transports.signal import SignalTransport
        sig = SignalTransport(cfg, engine)
        senders.append(sig)
        threads.append(("signal", sig.serve_forever))

    if cfg["transports"]["telegram"].get("enabled"):
        from .transports.telegram import TelegramTransport
        tg = TelegramTransport(cfg, engine)
        senders.append(tg)
        threads.append(("telegram", tg.serve_forever))

    if cfg["transports"]["api"].get("enabled"):
        api = ApiTransport(cfg, engine)
        senders.append(api)
        threads.append(("api", api.serve_forever))

    Jobs(cfg, engine, senders).start()

    print(f"housebot starting: {len(threads)} transport(s), "
          f"{len(cfg['llm']['backends'])} LLM backend(s)")

    named = []
    for name, fn in threads:
        t = threading.Thread(target=fn, daemon=True, name=name)
        t.start()
        named.append(t)

    try:
        while any(t.is_alive() for t in named):
            time.sleep(1)
    except KeyboardInterrupt:
        print("bye")


if __name__ == "__main__":
    main()