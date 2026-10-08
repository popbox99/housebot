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
        self.token = s.get("token") or self._read_token(s.get("token_file"))
        self.enabled = bool(s.get("enabled") and s.get("base_url") and self.token)
        self.base = (s.get("base_url") or "").rstrip("/")
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

    def find_entity(self, query: str, domain_filter: str = None):
        """Search HA states for an entity matching query by entity_id or friendly name."""
        if not self.enabled or not self.token:
            return None
        try:
            states = self._get("/api/states")
            if not isinstance(states, list):
                return None
        except Exception:
            return None

        q = query.strip().lower()
        q = re.sub(r"^(?:the|my|a|an)\s+", "", q).strip()

        # 1. Exact entity_id match (e.g. light.living_room or living_room)
        for s in states:
            eid = (s.get("entity_id") or "").lower()
            if domain_filter and not eid.startswith(domain_filter + "."):
                continue
            if eid == q or eid.split(".", 1)[-1] == q:
                return s

        # 2. Exact friendly_name match
        for s in states:
            eid = (s.get("entity_id") or "").lower()
            if domain_filter and not eid.startswith(domain_filter + "."):
                continue
            fn = ((s.get("attributes") or {}).get("friendly_name") or "").lower()
            if fn == q:
                return s

        # 3. Substring match on friendly_name or entity_id
        for s in states:
            eid = (s.get("entity_id") or "").lower()
            if domain_filter and not eid.startswith(domain_filter + "."):
                continue
            fn = ((s.get("attributes") or {}).get("friendly_name") or "").lower()
            if q in fn or q in eid:
                return s
        return None

    def device_control(self, action: str, target: str) -> str:
        """Control a Home Assistant device (turn on, turn off, toggle)."""
        if not self.enabled:
            return "Home Assistant is not configured (skills.homeassistant in config)."
        act = action.lower().strip()
        svc = "turn_on" if act in ("turn on", "on", "start") else \
              "turn_off" if act in ("turn off", "off", "stop") else \
              "toggle" if act in ("toggle", "switch") else None
        if not svc:
            return f"Unknown device action: {action}"

        ent = self.find_entity(target)
        if not ent:
            return f"I couldn't find a Home Assistant device matching {target!r}."

        eid = ent["entity_id"]
        fn = (ent.get("attributes") or {}).get("friendly_name") or eid
        try:
            self.call("homeassistant", svc, {"entity_id": eid})
            verb = "turned on" if svc == "turn_on" else "turned off" if svc == "turn_off" else "toggled"
            return f"⚡ {verb.capitalize()} {fn}."
        except Exception as e:
            return f"Failed to {svc.replace('_', ' ')} {fn}: {e}"

    def device_state(self, target: str) -> str:
        """Query state and attributes of an entity or device in Home Assistant."""
        if not self.enabled:
            return "Home Assistant is not configured (skills.homeassistant in config)."
        ent = self.find_entity(target)
        if not ent:
            return f"I couldn't find a Home Assistant entity matching {target!r}."
        fn = (ent.get("attributes") or {}).get("friendly_name") or ent["entity_id"]
        st = ent.get("state", "unknown")
        unit = (ent.get("attributes") or {}).get("unit_of_measurement", "")
        val = f"{st} {unit}".strip() if unit else st
        return f"📊 {fn} is currently {val}."