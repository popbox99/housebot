"""Reminder skill: timed reminders queued in reminders.json, dispatched by jobs.py."""

import json
import os
import time
import uuid

from .. import whens


class Reminders:
    def __init__(self, cfg, ask_llm_fn):
        self.cfg = cfg
        self.ask_llm_fn = ask_llm_fn
        self.path = os.path.join(cfg.data_dir, "reminders.json")

    def _load(self):
        try:
            return json.load(open(self.path))
        except Exception:
            return []

    def _save(self, rems):
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(rems, f, indent=1)
        os.replace(tmp, self.path)

    def add(self, text):
        """Returns a human confirmation, or raises ValueError on parse failure."""
        fast = whens.fast_parse_when(text)
        if fast:
            when, what = fast
        else:
            when, what = whens.llm_extract_when(self.ask_llm_fn, text)
            if when is None:
                raise ValueError(
                    "Could not parse the reminder time - try 'remind me in 2 hours to ...'")
        rems = self._load()
        rems.append({"id": uuid.uuid4().hex[:10], "when": when.strftime("%Y-%m-%d %H:%M"),
                     "what": what, "created": time.strftime("%Y-%m-%d %H:%M")})
        self._save(rems)
        return f"Reminder set: {what!r} at {when.strftime('%a %b %d %H:%M')}"

    def list(self, k=10):
        rems = sorted(self._load(), key=lambda r: r["when"])[:k]
        if not rems:
            return "No pending reminders."
        return "Pending reminders:\n" + "\n".join(
            f"  {r['when']} - {r['what']}" for r in rems)

    def due(self, now=None):
        """Pop and return due reminders: [(id, what, when_str)]."""
        now = now or time.strftime("%Y-%m-%d %H:%M")
        rems = self._load()
        due = [r for r in rems if r["when"] <= now]
        if due:
            self._save([r for r in rems if r["when"] > now])
        return [(r["id"], r["what"], r["when"]) for r in due]