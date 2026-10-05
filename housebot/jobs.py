"""Scheduled brain: reminder dispatch, location reminders, watchdog."""

import threading
import time


class Jobs:
    def __init__(self, cfg, engine, senders):
        """senders: transports with .send(recipient, text). Reminders go to
        config bot.owner."""
        self.cfg = cfg
        self.engine = engine
        self.owner = cfg["bot"].get("owner", "")
        # only transports that can proactively message get alerts/reminders
        self.senders = [s for s in senders if getattr(s, "push", False)]
        self.watchdog = None
        try:
            from .skills.watchdog import Watchdog
            self.watchdog = Watchdog(cfg)
        except Exception:
            pass

    def _alert_all(self, text):
        for s in self.senders:
            try:
                s.send(self.owner, text)
            except Exception as e:
                print("[jobs] send failed:", e)

    def _tick(self):
        last_watchdog = 0.0
        while True:
            try:
                for _id, what, when in self.engine.reminders.due():
                    self._alert_all("⏰ Reminder: " + what)
            except Exception as e:
                print("[jobs] reminder error:", e)
            try:
                ha = self.engine._ha
                if ha:
                    for msg in ha.check_location_reminders():
                        self._alert_all(msg)
            except Exception as e:
                print("[jobs] location error:", e)
            try:
                if self.watchdog and time.time() - last_watchdog > 900:
                    last_watchdog = time.time()
                    self.watchdog.tick()
            except Exception as e:
                print("[jobs] watchdog error:", e)
            time.sleep(30)

    def start(self):
        threading.Thread(target=self._tick, daemon=True).start()