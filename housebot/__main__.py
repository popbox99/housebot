"""Entry point: python -m housebot"""

import threading
import time

from .config import Config
from .engine import Engine
from .jobs import Jobs
from .transports.api import ApiTransport


def main():
    cfg = Config()
    engine = Engine(cfg)
    senders = []

    threads = []
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