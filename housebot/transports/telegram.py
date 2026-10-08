"""Telegram transport via the Bot API (long polling, stdlib only)."""

import json
import os
import time
import urllib.request


class TelegramTransport:
    push = True   # can proactively message the owner (reminders, alerts)

    def __init__(self, cfg, engine):
        t = cfg["transports"]["telegram"]
        self.engine = engine
        self.token = t.get("token") or ""
        tf = t.get("token_file")
        if tf and os.path.exists(os.path.expanduser(tf)):
            with open(os.path.expanduser(tf), "r", encoding="utf-8") as f:
                self.token = f.read().strip()
        self.base = f"https://api.telegram.org/bot{self.token}"
        self.allowed = set(cfg["bot"].get("allowed_senders") or [])
        self.demo_mode = cfg["bot"].get("demo_mode", False) or ("*" in self.allowed)
        if not self.token:
            print("[telegram] WARNING: No token configured for Telegram transport.")
        elif self.demo_mode:
            print("[telegram] DEMO MODE: accepting messages from all users for test-driving.")
        elif not self.allowed:
            print("[telegram] SECURITY: bot.allowed_senders is empty - ALL incoming "
                  "messages will be rejected. Add your numeric Telegram user id.")
        self.offset = 0

    def send(self, chat_id, text, attachment=None):
        if not self.token:
            return
        req = urllib.request.Request(
            f"{self.base}/sendMessage",
            data=json.dumps({"chat_id": chat_id, "text": text}).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            r.read()

    def serve_forever(self):
        if not self.token:
            print("[telegram] transport inactive (no token).")
            return
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
                    if chat_id is None:
                        continue
                    if not self.demo_mode and (not self.allowed or sender not in self.allowed):
                        continue
                    text = (msg.get("text") or "").strip()
                    if not text:
                        continue
                    if text == "/start":
                        welcome = (
                            "👋 Welcome to HouseBot!\n\n"
                            "I am your local, private AI assistant. Here are a few things you can test:\n"
                            "• 🛒 \"Add sourdough and coffee to shopping list\"\n"
                            "• 📋 \"What's on my shopping list?\"\n"
                            "• ⏰ \"Remind me in 5 minutes to call mom\"\n"
                            "• 🧠 \"Remember that my wifi password is BlueSky123\"\n"
                            "• ❓ \"What is my wifi password?\"\n"
                        )
                        self.send(chat_id, welcome)
                        continue
                    reply, _att = self.engine.handle(sender, text)
                    if reply:
                        self.send(chat_id, reply)
            except Exception as e:
                print(f"[telegram] error: {e}; retrying in 10s")
                time.sleep(10)