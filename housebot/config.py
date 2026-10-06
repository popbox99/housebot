"""Configuration: a single JSON file (default ~/.config/housebot/config.json)."""

import json
import os

DEFAULT_CONFIG_PATH = os.environ.get(
    "HOUSEBOT_CONFIG", os.path.expanduser("~/.config/housebot/config.json")
)

DEFAULTS = {
    "bot": {
        "name": "house",
        "data_dir": "~/.local/share/housebot",
        "allowed_senders": [],          # empty = reject every Signal and Telegram sender
    },
    "llm": {
        # Fallback chain: first reachable backend wins. api: "ollama" | "openai"
        "backends": [
            {"name": "local", "base_url": "http://127.0.0.1:11434",
             "model": "qwen3:4b-instruct", "api": "ollama"}
        ],
        "extract_model": None,          # defaults to backends[0].model
    },
    "caldav": {
        "base_url": "",                 # e.g. http://127.0.0.1:5232
        "user": "",                     # collection owner, e.g. "me"
        "password": "",
        "password_file": "",            # alternative to inline password
        "calendar": "calendar",         # VEVENT collection name
        "tasks": "tasks",               # VTODO collection name
        "tz": "America/New_York",
    },
    "transports": {
        "signal": {"enabled": False, "socket": "~/.local/run/signal-cli/socket",
                    "account": ""},
        "telegram": {"enabled": False, "token": "", "token_file": ""},
        # Off until a real token is set. Enabled + "change-me" refuses to start.
        "api": {"enabled": False, "port": 8082, "token": ""}
    },
    "skills": {
        "contacts_json": "",            # enables contacts skill (house-contacts)
        "contact_ledger_vcf": "",        # bot-added contacts (.vcf registered as a source)
        "notes_dir": "~/Documents/Notes",
        "search_dirs": [],
        "default_place": "",            # fallback for weather
        "searxng": {"base_url": ""},    # self-hosted search, e.g. http://127.0.0.1:8888
        "wyoming_stt": {"host": "127.0.0.1", "port": 10300},   # voice-note transcription
        "immich": {"enabled": False, "base_url": "http://127.0.0.1:2283",
                   "api_key_file": "", "fallback_url": ""},
        "paperless": {"enabled": False, "base_url": "http://127.0.0.1:8010",
                      "token_file": ""},
        "homeassistant": {"enabled": False, "base_url": "", "token": "", "token_file": "",
                          "person_entity": "", "zones": {"home": "home"},
                          "vacuum_entity": "vacuum.robot"},
        "watchdog": {"enabled": False, "ntfy_topic": "",
                     "checks": []}   # [{"name": "webui", "url": "http://..."}]
    }
}


def _merge(base, override):
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


class Config:
    def __init__(self, path=None):
        self.path = path or DEFAULT_CONFIG_PATH
        raw = {}
        if os.path.exists(self.path):
            with open(self.path, encoding="utf-8") as f:
                raw = json.load(f)
        self._data = _merge(DEFAULTS, raw)
        self._expand(self._data)

    def _expand(self, node):
        if isinstance(node, dict):
            for k, v in node.items():
                if isinstance(v, str) and "~" in v:
                    node[k] = os.path.expanduser(v)
                elif isinstance(v, (dict, list)):
                    self._expand(v)
        elif isinstance(node, list):
            for i, v in enumerate(node):
                if isinstance(v, str) and "~" in v:
                    node[i] = os.path.expanduser(v)
                elif isinstance(v, (dict, list)):
                    self._expand(v)

    def __getitem__(self, key):
        return self._data[key]

    def get(self, key, default=None):
        return self._data.get(key, default)

    def to_dict(self):
        return dict(self._data)

    @property
    def data_dir(self):
        d = self._data["bot"]["data_dir"]
        os.makedirs(d, exist_ok=True)
        return d

    def caldav_password(self):
        cal = self._data["caldav"]
        if cal.get("password"):
            return cal["password"]
        if cal.get("password_file") and os.path.exists(cal["password_file"]):
            with open(cal["password_file"], encoding="utf-8") as f:
                return f.read().strip()
        return ""