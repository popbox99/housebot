"""Watchdog: periodic health checks with change-only ntfy alerts."""

import glob
import json
import os
import time
import urllib.request


class Watchdog:
    def __init__(self, cfg, log=print):
        w = cfg["skills"].get("watchdog") or {}
        self.enabled = bool(w.get("enabled") and w.get("ntfy_topic"))
        self.topic = w.get("ntfy_topic", "")
        self.checks = w.get("checks") or []   # [{"name": "...", "url": "http://..."}]
        self.state_path = os.path.join(cfg.data_dir, "watchdog_state.json")
        self.log = log

    def run_checks(self):
        """Returns [(name, ok, detail)] for each configured check."""
        out = []
        for c in self.checks:
            name, url = c.get("name", c.get("url", "?")), c.get("url", "")
            try:
                with urllib.request.urlopen(url, timeout=10) as r:
                    ok = 200 <= r.status < 400
                out.append((name, ok, f"HTTP {r.status}"))
            except Exception as e:
                out.append((name, False, str(e)[:80]))
        return out

    def tick(self):
        """Run checks; notify ntfy ONLY on state change. Returns alerts sent."""
        if not self.enabled or not self.checks:
            return []
        results = self.run_checks()
        failing = {name for name, ok, _ in results if not ok}
        prev = self._load_state()
        alerts = []
        for name in sorted(failing - set(prev)):
            alerts.append(f"🚨 DOWN: {name}")
        for name in sorted(set(prev) - failing):
            alerts.append(f"✅ RECOVERED: {name}")
        self._save_state(failing)
        for a in alerts:
            self._ntfy(a)
        return alerts

    def _load_state(self):
        try:
            with open(self.state_path, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except Exception:
            return set()

    def _save_state(self, failing):
        tmp = self.state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(sorted(failing), f)
        os.replace(tmp, self.state_path)

    def _ntfy(self, message):
        try:
            req = urllib.request.Request(
                f"https://ntfy.sh/{self.topic}", data=message.encode(),
                headers={"Title": "housebot watchdog", "Tags": "warning"})
            urllib.request.urlopen(req, timeout=15)
            self.log(f"[watchdog] alerted: {message}")
        except Exception as e:
            self.log(f"[watchdog] ntfy failed: {e}")