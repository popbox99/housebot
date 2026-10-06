"""Home Assistant skill: presence, location reminders, battery report, service calls.

Enable with skills.homeassistant {enabled, base_url, token_file}. Location
reminders ("remind me when I get home to unload the car") poll a person entity
and fire when it enters the target zone.
"""

import json
import os
import re
import time
import urllib.request


class HomeAssistant:
    def __init__(self, cfg):
        s = cfg["skills"].get("homeassistant") or {}
        self.enabled = bool(s.get("enabled") and s.get("base_url"))
        self.base = (s.get("base_url") or "").rstrip("/")
        self.token = s.get("token") or self._read_token(s.get("token_file"))
        self.person_entity = s.get("person_entity", "")
        self.vacuum_entity = s.get("vacuum_entity", "vacuum.robot")
        self.zones = s.get("zones") or {"home": "home"}
        self.loc_path = os.path.join(cfg.data_dir, "location_reminders.json")

    def _read_token(self, path):
        if path and os.path.exists(os.path.expanduser(path)):
            with open(os.path.expanduser(path), "r", encoding="utf-8") as f:
                return f.read().strip()
        return ""

    def _get(self, path):
        req = urllib.request.Request(
            self.base + path, headers={"Authorization": "Bearer " + self.token})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())

    def state(self, entity_id):
        return self._get(f"/api/states/{entity_id}")

    def call(self, domain, service, service_data=None):
        data = json.dumps(service_data or {}).encode()
        req = urllib.request.Request(
            f"{self.base}/api/services/{domain}/{service}", data=data,
            headers={"Authorization": "Bearer " + self.token,
                     "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())

    # -- location reminders --------------------------------------------------

    def _load_loc(self):
        try:
            with open(self.loc_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []

    def _save_loc(self, rems):
        tmp = self.loc_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(rems, f, indent=1, ensure_ascii=False)
        os.replace(tmp, self.loc_path)

    def add_location_reminder(self, text):
        """'remind me when i get home to unload the car' -> pending reminder."""
        m = re.search(r"\bwhen\s+(?:i|we)\s+(?:get|arrive|reach|am|are|come)\s+"
                      r"(?:to\s+|home\b|work\b)", text, re.I)
        zone = "home"
        if m and re.search(r"work", m.group(0), re.I):
            zone = "work"
        what = re.sub(r"^(?:remind me|remind|note)\s+(?:to\s+|about\s+|that\s+)?", "",
                      text, flags=re.I)
        what = re.sub(r"\bwhen\s+(?:i|we)\s+(?:get|arrive|reach|am|are|come)\b.*$",
                      "", what, flags=re.I).strip() or text[:60]
        armed_zone = ""
        try:
            if self.enabled and self.person_entity:
                st = self.state(self.person_entity)
                if isinstance(st, dict):
                    armed_zone = (st.get("state") or "").lower()
        except Exception:
            pass
        rems = self._load_loc()
        rems.append({
            "what": what,
            "zone": zone,
            "armed": time.strftime("%Y-%m-%d %H:%M"),
            "armed_zone": armed_zone
        })
        self._save_loc(rems)
        return f"📍 Will remind you to {what!r} when you {'get home' if zone == 'home' else f'arrive at {zone}'}."

    def check_location_reminders(self):
        """Poll HA; fire pending reminders whose zone matches current state.
        Returns list of fired messages."""
        if not (self.enabled and self.person_entity):
            return []
        fired = []
        rems = self._load_loc()
        if not rems:
            return fired
        try:
            state = self.state(self.person_entity).get("state", "")
        except Exception:
            return []
        zone = state.lower()
        remaining = []
        for r in rems:
            if r.get("zone", "home") == zone and zone != r.get("armed_zone", ""):
                fired.append("📍 " + r.get("what", "reminder"))
            else:
                remaining.append(r)
        if fired:
            self._save_loc(remaining)
        return fired

    # -- reports & services ---------------------------------------------------

    def battery_report(self, threshold=30):
        states = self._get("/api/states")
        low = []
        for s in states:
            attrs = s.get("attributes") or {}
            bat = attrs.get("battery_level") or attrs.get("battery")
            if bat is None:
                continue
            try:
                bat = float(bat)
            except (TypeError, ValueError):
                continue
            if bat <= threshold:
                low.append(f"{s['attributes'].get('friendly_name', s['entity_id'])}: {bat:.0f}%")
        return ("🔋 Battery levels:\n" + "\n".join(low)) if low else "All batteries above threshold."

    def vacuum(self, action):
        service = {"start": "start", "dock": "return_to_base", "status": None}.get(action)
        if service is None:
            st = self.state(self.vacuum_entity)
            return f"Vacuum: {st.get('state', 'unknown')}"
        data = {"entity_id": self.vacuum_entity} if self.vacuum_entity else {}
        self.call("vacuum", service, data)
        return f"Vacuum {action} sent."