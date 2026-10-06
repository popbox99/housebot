"""Reminder skill: timed reminders queued in reminders.json, dispatched by jobs.py."""

import json
import os
import threading
import time
import uuid

from .. import whens


class Reminders:
    def __init__(self, cfg, ask_llm_fn):
        self.cfg = cfg
        self.ask_llm_fn = ask_llm_fn
        self.path = os.path.join(cfg.data_dir, "reminders.json")
        self._lock = threading.Lock()

    def _load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []

    def _save(self, rems):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(rems, f, indent=1, ensure_ascii=False)
        os.replace(tmp, self.path)

    def add(self, text, sender=None):
        """Returns a human confirmation, or raises ValueError on parse failure."""
        fast = whens.fast_parse_when(text)
        if fast:
            when, what = fast
        else:
            when, what = whens.llm_extract_when(self.ask_llm_fn, text)
            if when is None:
                raise ValueError(
                    "Could not parse the reminder time - try 'remind me in 2 hours to ...'")
        with self._lock:
            rems = self._load()
            item = {
                "id": uuid.uuid4().hex[:10],
                "when": when.strftime("%Y-%m-%d %H:%M"),
                "what": what,
                "created": time.strftime("%Y-%m-%d %H:%M"),
            }
            if sender:
                item["sender"] = sender
            rems.append(item)
            self._save(rems)
        return f"Reminder set: {what!r} at {when.strftime('%a %b %d %H:%M')}"

    def list(self, k=10):
        with self._lock:
            rems = sorted(self._load(), key=lambda r: r["when"])[:k]
        if not rems:
            return "No pending reminders."
        return "Pending reminders:\n" + "\n".join(
            f"  {r['when']} - {r['what']}" for r in rems)

    def due(self, now=None, pop=False):
        """Return due reminders: [(id, what, when_str)]. Pop immediately if pop=True."""
        now = now or time.strftime("%Y-%m-%d %H:%M")
        with self._lock:
            rems = self._load()
            due = [r for r in rems if r["when"] <= now]
            if pop and due:
                self._save([r for r in rems if r["when"] > now])
        return [(r["id"], r["what"], r["when"]) for r in due]

    def mark_sent(self, reminder_id):
        """Remove a reminder after successful delivery to prevent data loss."""
        with self._lock:
            rems = self._load()
            new_rems = [r for r in rems if r["id"] != reminder_id]
            if len(new_rems) != len(rems):
                self._save(new_rems)