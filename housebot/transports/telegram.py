"""Telegram transport via the Bot API (long polling, stdlib only)."""

import json
import os
import time
import urllib.request


class TelegramTransport:
    def __init__(self, cfg, engine):
        t = cfg["transports"]["telegram"]
        self.engine = engine
        self.token = t.get("token") or ""
        tf = t.get("token_file")
        if tf and os.path.exists(os.path.expanduser(tf)):
            self.token = open(os.path.expanduser(tf)).read().strip()
        self.base = f"https://api.telegram.org/bot{self.token}"
        self.allowed = set(cfg["bot"].get("allowed_senders") or [])
        self.offset = 0

    def send(self, chat_id, text):
        req = urllib.request.Request(
            f"{self.base}/sendMessage",
            data=json.dumps({"chat_id": chat_id, "text": text}).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            r.read()

    def serve_forever(self):
        while True:
            try:
                url = f"{self.base}/getUpdates?timeout=50&offset={self.offset}"
                with urllib.request.urlopen(url, timeout=60) as r:
                    data = json.loads(r.read())
                for upd in data.get("result", []):
                    self.offset = max(self.offset, upd["update_id"] + 1)
                    msg = upd.get("message") or {}
                    chat_id = (msg.get("chat") or {}).get("id")
                    sender = str(msg.get("from", {}).get("id", chat_id))
                    if chat_id is None or (self.allowed and sender not in self.allowed):
                        continue
                    text = msg.get("text") or ""
                    if not text:
                        continue
                    reply, _att = self.engine.handle(sender, text)
                    if reply:
                        self.send(chat_id, reply)
            except Exception as e:
                print(f"[telegram] error: {e}; retrying in 10s")
                time.sleep(10)