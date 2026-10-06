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
            launch = input("Would you like to run the setup wizard now? (Y/n): ").strip().lower()
            if launch in ("", "y", "yes"):
                from .wizard import run_wizard
                run_wizard()
                if not os.path.exists(DEFAULT_CONFIG_PATH):
                    return
        else:
            print(f"[housebot] No config at {DEFAULT_CONFIG_PATH}; continuing with defaults.")

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