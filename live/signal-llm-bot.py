#!/usr/bin/env python3
"""
DEPLOYED COPY - the Signal bot that actually runs on the home server
(homebrain). This is NOT the packaged housebot app in ../housebot/; it is the
single-file script as deployed, with secrets and personal details scrubbed.
All credentials, phone numbers, Signal ids and private-network addresses come
from environment variables (see live/README.md and live/.env.example).

Signal <-> LLM assistant on Debby (the always-on server).
Chat backend: omarchybox gpt-oss-32k when ~/.local/share/house/omarchy_llm_up says up, else thinkbox qwen3:4b-instruct. com.house.omarchy-llm refreshes that file; chat does not probe the laptop. There is no /big model switch.

Commands:
  /find <text>   - search filenames + contents in /tank/data
  /send <text>   - find a file and send it to you as a Signal attachment
  /organize      - dry-run: show how /tank/data would be sorted by type
  /organize go   - actually do it
  /status /help /syncthing /photos <query>
  /alerts /ask /package /net /vault /voice /whisper /calendar

Natural language: plain messages are intent-classified by the LLM
(find / send / organize / chat), so "where is my resume?" just works.
"""
import json, os, re, shutil, socket, subprocess, sys, threading, time, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

def _env_list(name):
    """Comma-separated environment variable -> list of non-empty strings."""
    return [x.strip() for x in os.environ.get(name, "").split(",") if x.strip()]

# Signal account the signal-cli daemon is registered as (E.164 format).
ACCOUNT = os.environ.get("SIGNAL_ACCOUNT", "")
# Who may talk to the bot: E.164 numbers and/or Signal UUIDs, comma-separated.
_ALLOWED_LIST = _env_list("SIGNAL_ALLOWED_NUMBERS")
ALLOWED_SENDERS = set(_ALLOWED_LIST)

SEARCH_DIRS = ["/tank/data"]
SEARCH_SKIP = [".stfolder", ".git", "node_modules", "__pycache__", ".cache"]
MAX_RESULTS = 8
MAX_ATTACHMENT_BYTES = 90 * 1024 * 1024   # Signal limit ~100MB, stay safe
SOCKET = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/run/user/1000"),
                      "signal-cli", "socket")

SERVICE = "http://127.0.0.1:8000"   # house file-tools API on Debby
# Remote transcribers tried in order, e.g. http://thinkbox:8766/transcribe
WHISPER_SERVICES = _env_list("WHISPER_URLS")
RADICALE = "http://127.0.0.1:5232"  # house calendar/tasks (CalDAV)
CAL_USER = os.environ.get("CALDAV_USER", "")

# LLM backend chain: tried in order. Configured in
# ~/.local/share/house/llm_backends.conf:
#   BACKEND_1_NAME=loq
#   BACKEND_1_URL=http://my-llm-box:11434/v1     (any OpenAI-compatible server:
#                                                 Ollama, llama.cpp, LM Studio, vLLM)
#   BACKEND_1_MODEL=gpt-oss-32k
#   BACKEND_1_PRESENCE=~/.local/share/house/omarchy_llm_up   (optional: backend is
#                                                 considered up only if this file exists -
#                                                 for machines that are sometimes off)
# Missing file -> built-in defaults below.

def _small_model():
    if not LLM_BACKENDS:
        return "house"
    return (LLM_BACKENDS[1]["model"] if len(LLM_BACKENDS) > 1
            else LLM_BACKENDS[0]["model"])

HTTP_API_PORT = 8082          # OpenAI-compatible API for Home Assistant
HTTP_API_TOKEN = os.environ.get("HOUSE_API_TOKEN", "")   # must match HA OpenAI integration api_key
STATE = {}   # no chat-model switch; /big and /fast are retired

HA_URL = "http://127.0.0.1:8123"   # Home Assistant REST API
_HA_CACHE = {"ts": 0.0, "states": []}
HA_VACUUM_ENTITY = os.environ.get("HA_VACUUM_ENTITY", "")                  # e.g. vacuum.robot
HA_VACUUM_BATTERY_ENTITY = os.environ.get("HA_VACUUM_BATTERY_ENTITY", "")  # e.g. sensor.robot_battery

NOTES_DIR = "/tank/data/Obsidian Vault"
CHAT_NOTES_DIR = NOTES_DIR + "/Chat Notes"
SHOPPING_NOTE = NOTES_DIR + "/Lists/Shopping List/Groceries and Suplies.md"
HARDWARE_NOTE = NOTES_DIR + "/Lists/Hardware Store.md"
FARM_NOTE = NOTES_DIR + "/Lists/Feed and Farm.md"
TECH_NOTE = NOTES_DIR + "/Lists/Tech and Electronics.md"
HID_DIR = NOTES_DIR + "/Where I hid it from Me"
HID_NOTE = HID_DIR + "/Where I hid it from Me.md"
WORK_NOTES_DIR = NOTES_DIR + "/Work Notes"
LISTS_DIR = NOTES_DIR + "/Lists"
PANTRY_NOTE = LISTS_DIR + "/Pantry Inventory.md"
CHORES_NOTE = NOTES_DIR + "/Chores/Maintenance Log.md"
HABITS_NOTE = NOTES_DIR + "/Habits/Habit Log.md"
VOICE_LOG_DIR = NOTES_DIR + "/House Voice Log"
FINANCES_DIR = NOTES_DIR + "/Finances"
EXPENSES_NOTE = FINANCES_DIR + "/2026 Expenses.md"
MILEAGE_NOTE = FINANCES_DIR + "/2026 Mileage.md"
READING_LIST_DIR = NOTES_DIR + "/Reading List"
IMMICH_URL = "http://127.0.0.1:2283"
WYOMING_PIPER_ADDR = ("127.0.0.1", 10200)

STORE_WATCH_ENTITY = os.environ.get("HA_STORE_WATCH_ENTITY", "")   # e.g. device_tracker.my_phone
STORE_BRANDS = re.compile(
    r"walmart|target|costco|sam'?s\s+club|bj'?s\s+wholesale|"
    r"kroger|fresh\s+market|aldi|trader\s+joe'?s|food\s+lion|whole\s+foods|wegmans|publix|safeway|harris\s+teeter|giant|lidl|"
    r"home\s+depot|lowe'?s|ace\s+hardware|harbor\s+freight|true\s+value|menards|84\s+lumber|"
    r"tractor\s+supply|southern\s+states|rural\s+king|feed\s+(?:store|mill|seed)|co-?op|agway|"
    r"best\s+buy|staples|micro\s+center|office\s+depot|office\s*max|apple\s+store",
    re.I
)
WATCH_INTERVAL = 120          # seconds between location polls
NUDGE_COOLDOWN = 6 * 3600     # per store brand
WATCH_MIN_MOVE_M = 150        # only re-check after moving this far
WATCH_MAX_ACCURACY = 150      # ignore fixes worse than this

HOUSE_STATE = os.path.expanduser("~/.local/share/house")
MEMORY_FILE = os.path.join(HOUSE_STATE, "memory.json")
CHAT_LOG_DIR = NOTES_DIR + "/Chat Log"
EMBED_URL = os.environ.get("EMBED_URL", "")  # Ollama /api/embeddings serving nomic-embed-text
NTFY_URL = os.environ.get("NTFY_URL", "")      # full topic URL, e.g. https://ntfy.example/house-alerts
NTFY_TOKEN = os.environ.get("NTFY_TOKEN", "")  # optional access token for a protected topic
NTFY_SEEN_FILE = os.path.join(HOUSE_STATE, "ntfy-seen.json")
ARCHBOX_SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "archbox"]
DELIVERIES_DIR = NOTES_DIR + "/Deliveries"
ALERT_RECIPIENT = os.environ.get("SIGNAL_ALERT_RECIPIENT", "") or (_ALLOWED_LIST[0] if _ALLOWED_LIST else "")
CHORE_INTERVALS_FILE = os.path.join(HOUSE_STATE, "chore_intervals.json")
LOC_REMINDERS_FILE = os.path.join(HOUSE_STATE, "location_reminders.json")
HABIT_GOALS_FILE = os.path.join(HOUSE_STATE, "habit_goals.json")
KNOWN_STORES_FILE = os.path.join(HOUSE_STATE, "known_stores.json")
CAPS_FILE = os.path.join(HOUSE_STATE, "capabilities.conf")

def load_caps():
    """Capability flags for this install. All on by default (backward compatible)."""
    caps = {"signal": True, "http_api": True, "calendar": True,
            "homeassistant": True, "photos": True, "paperless": True,
            "files": True, "obsidian": True}
    try:
        for line in open(CAPS_FILE):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                k = k.strip().upper()
                if k.startswith("CAP_"):
                    k = k[4:].lower()
                if k in caps:
                    caps[k] = v.strip().lower() in ("1", "true", "yes", "on")
    except Exception:
        pass
    return caps

CAPS = load_caps()

_ACTION_CAPS = {
    "EVENT": "calendar", "TASK": "calendar", "AGENDA": "calendar",
    "LOCATION": "homeassistant", "FAMILY": "homeassistant",
    "BATTERIES": "homeassistant", "VACUUM_START": "homeassistant",
    "VACUUM_DOCK": "homeassistant", "VACUUM_STATUS": "homeassistant",
    "PHOTOS": "photos", "PAPERLESS": "paperless",
    "FIND": "files", "SEND": "signal", "ORGANIZE": "files",
    "SUMMARIZE": "files", "HID": "obsidian", "NOTE": "obsidian",
    "WORKNOTE": "obsidian", "VAULT": "obsidian",
}

def _caps_line():
    on = ", ".join(sorted(k for k, v in CAPS.items() if v))
    off = ", ".join(sorted(k for k, v in CAPS.items() if not v))
    out = f"Capabilities enabled on this install: {on}."
    if off:
        out += f" Disabled here (route such requests to CHAT instead): {off}."
    return out
SHOPPING_SYNC_FILE = os.path.join(HOUSE_STATE, "shopping_sync.json")
PAPERLESS = "http://127.0.0.1:8010"
PAPERLESS_TOKEN = os.environ.get("PAPERLESS_TOKEN", "")

def _paperless_token():
    if not PAPERLESS_TOKEN:
        raise RuntimeError("PAPERLESS_TOKEN not set")
    return PAPERLESS_TOKEN
TAGS = {"mac": "[Qwen3 4B via thinkbox]",
        "debby": "[Llama 3B via homebrain]", "loq": "[GPT-OSS 20B via LOQ]"}
# Optional display names: "id=name,id=name" (ids as in SIGNAL_ALLOWED_NUMBERS).
_NAME_MAP = dict(p.split("=", 1) for p in _env_list("SIGNAL_SENDER_NAMES") if "=" in p)
SENDER_NAMES = {s: _NAME_MAP.get(s, "user") for s in _ALLOWED_LIST}

SYSTEM_PROMPT = (
    "You are House, the personal assistant running on the user's home server "
    "- styled after Mycroft Holmes, Sherlock Holmes' older and smarter "
    "brother (NOT Dr. Gregory House of the television programme; any "
    "comparison to him is beneath the family name). If asked what model you "
    "are, reply that you are the House assistant (the manner is inherited). "
    "Personality: impeccably polite, dry wit, supremely confident, and mildly "
    "condescending about how obvious most questions are - but never cruel, "
    "and always genuinely helpful. Occasionally use a refined British turn of "
    "phrase: 'elementary', 'naturally', 'one hardly needs to ask'. "
    "You ARE connected to the user's file store (/tank/data), which syncs "
    "all their devices - you can search it, send them files from it, and "
    "organize it. If they ask about files or what you can do, tell them: "
    "just ask naturally (e.g. 'where is my resume?') or use /find, /send, "
    "/organize, /event, /task, /help. "
    "IMPORTANT: answer ONLY the newest user message. Earlier messages in "
    "the conversation are background context, not questions to answer. "
    "ALWAYS respond in English. Answer directly. "
    "Keep replies under 120 words unless asked for more - brevity is itself "
    "a mark of cleverness. "
    "You cannot set, change, or list reminders, events, tasks, or chores "
    "from chat - the house handles those before you see a message - so "
    "never claim you set or scheduled anything, and never re-list "
    "reminders mentioned earlier in the conversation. "
    "Plain text only - no markdown formatting, Signal renders it poorly."
)

CLASSIFIER_PROMPT = (
    "Classify the user request into ONE line. Never explain.\n"
    "Format: one of FIND <words> | SEND <words> | ORGANIZE | CHAT | WEATHER [place] | SEARCH_WEB <words> | SUMMARIZE <words> | EVENT <desc> | TASK <desc> | AGENDA | REMIND <when+what> | NOTE <text> | LOCATION <person> | SHOPPING <items> | REMEMBER <fact> | FORGET <topic> | MEMORY <topic> | CONTACT <name and/or phone/email> | CONTACT_INFO <person>\n"
    "Examples:\n"
    "Request: where is my resume? -> FIND resume\n"
    "Request: send me the tax pdf -> SEND tax pdf\n"
    "Request: clean up my files -> ORGANIZE\n"
    "Request: what is a house server? -> CHAT\n"
    "Request: do I have any w2 forms? -> FIND w2\n"
    "Request: summarize my federal resume -> SUMMARIZE federal resume\n"
    "Request: add dinner with mike friday 7pm to my calendar -> EVENT dinner with mike friday 7pm\n"
    "Request: remind me to file taxes -> TASK file taxes\n"
    "Request: what's the weather tomorrow -> WEATHER\n"
    "Request: weather in denver this weekend -> WEATHER denver\n"
    "Request: search the web for coiled tubing prices -> SEARCH_WEB coiled tubing prices\n"
    "Request: google best budget laptop 2026 -> SEARCH_WEB best budget laptop 2026\n"
    "Request: whats on my calendar today -> AGENDA\n"
    "Request: where is alex -> LOCATION alex\n"
    "Request: add milk and eggs to the shopping list -> SHOPPING milk eggs\n"
    "Request: whats on my shopping list -> SHOPPING list\n"
    "Follow-up rule: short follow-ups like what about X / also X / and X inherit the topic of the previous exchange.\n"
    "Request: what about adult beverages (previous: show my grocery list) -> SHOPPING adult beverages\n"
    "Request: remove milk from the shopping list -> SHOPPING remove milk\n"
    "Request: remember that the spare car key is in the top desk drawer -> HID spare car key top desk drawer\n"
    "Request: remember that my sister\x27s birthday is june 3 -> REMEMBER my sister\x27s birthday is june 3\n"
    "Request: what do you remember about my sister -> MEMORY my sister\n"
    "Request: forget what i said about the boat keys -> FORGET boat keys\n"
    "Request: where did i put my passport -> HID where passport\n"
    "Request: make a work note call the vendor tomorrow -> WORKNOTE call the vendor tomorrow\n"
    "Request: add bug spray to my camping packing list -> LIST add bug spray camping packing\n"
    "Request: we are low on coffee beans -> PANTRY low coffee beans\n"
    "Request: mark that i changed the hvac air filter today -> CHORE changed the hvac air filter\n"
    "Request: log that i drank 24 ounces of water -> HABIT water 24 oz\n"
    "Request: add legos to sams christmas list -> LIST lego sam christmas\n"
    "Request: where can i find sushi near springfield -> SEARCH_WEB sushi near springfield\n"
    "Request: where can i find my tax documents -> FIND tax document\n"
    "Request: remind me in 2 hours to check the dryer -> REMIND in 2 hours check the dryer\n"
    "Request: note the furnace filter size is 16x25x4 -> NOTE furnace filter size is 16x25x4\n"
    "Request: add contact jane doe 555-0100 -> CONTACT jane doe 555-0100\n"
    "Request: save this business card -> CONTACT business card\n"
    "Request: what is jordan lee's phone number -> CONTACT_INFO jordan lee\n"
    "Request: what is her email (previous: what is jordan lee's phone number) -> CONTACT_INFO jordan lee\n"
)
TYPE_MAP = [
    ("Documents", (".pdf", ".docx", ".doc", ".odt", ".txt", ".md", ".rtf",
                   ".epub", ".pptx", ".ppt", ".html")),
    ("Spreadsheets", (".xlsx", ".xls", ".ods", ".csv", ".numbers")),
    ("Images", (".jpg", ".jpeg", ".png", ".gif", ".heic", ".webp", ".tiff",
                ".svg", ".bmp")),
    ("Videos", (".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v")),
    ("Audio", (".mp3", ".m4a", ".wav", ".flac", ".ogg", ".aac")),
    ("Archives", (".zip", ".tar.gz", ".tgz", ".7z", ".rar", ".dmg", ".iso")),
    ("Installers", (".deb", ".appimage", ".exe", ".pkg", ".sh")),
]

def log(msg):
    print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)


OMARCHY_LLM_STATE = os.path.expanduser("~/.local/share/house/omarchy_llm_up")

def omarchy_llm_up():
    """Instant read of the presence file (com.house.omarchy-llm). Never dials omarchybox.
    Missing or unreadable means down so chat does not wait on a sleeping laptop."""
    try:
        with open(OMARCHY_LLM_STATE) as f:
            status = f.readline().strip().lower()
    except Exception:
        return False
    return status == "up"

_BACKEND_STATUS = {}

def is_backend_alive(name, base, timeout=0.5):
    now = time.time()
    last_check, is_alive = _BACKEND_STATUS.get(name, (0, True))
    if not is_alive and (now - last_check < 30):
        return False
    try:
        urllib.request.urlopen(base + "/models", timeout=timeout)
        _BACKEND_STATUS[name] = (now, True)
        return True
    except Exception:
        _BACKEND_STATUS[name] = (now, False)
        return False

def _try_backend(b, timeout):
    if b.get("presence"):
        p = os.path.expanduser(b["presence"])
        if os.path.exists(p):
            return (b["url"], b["model"], b["name"])
        return None
    if is_backend_alive(b["name"], b["url"], timeout=timeout):
        return (b["url"], b["model"], b["name"])
    return None

def pick_backend(big_mode=False):
    """Chat chain: backends in order; presence-file gated or probed."""
    for b in LLM_BACKENDS:
        r = _try_backend(b, 0.8)
        if r:
            return r
    return None

def pick_fast_backend():
    """Intent classification: same order, shorter probe timeout."""
    for b in LLM_BACKENDS:
        r = _try_backend(b, 0.5)
        if r:
            return r
    return None

def pick_research_backend():
    """Research synthesis: primary first, then fallbacks."""
    for b in LLM_BACKENDS:
        r = _try_backend(b, 0.8)
        if r:
            return r
    return None

def pick_small_backend():
    """Cheap path: skips the primary backend, uses the next in line."""
    for b in LLM_BACKENDS[1:]:
        r = _try_backend(b, 0.8)
        if r:
            return r
    return None

def pick_extract_backend():
    """Date/time extraction: skips the primary (big) model, uses the small one."""
    for b in LLM_BACKENDS[1:]:
        r = _try_backend(b, 0.8)
        if r:
            return r
    return None




def search_files(query):
    """Search via the house file-tools service."""
    try:
        with urllib.request.urlopen(
                f"{SERVICE}/search?q={urllib.parse.quote(query)}", timeout=30) as r:
            return json.loads(r.read())["report"]
    except Exception as e:
        return f"(file service unreachable: {e})"

def find_file_paths(query, limit=5):
    """Filename matches via the house file-tools service."""
    try:
        with urllib.request.urlopen(
                f"{SERVICE}/paths?q={urllib.parse.quote(query)}&limit={limit}",
                timeout=30) as r:
            return json.loads(r.read())["paths"]
    except Exception:
        return []

def cal_get(calendar):
    import base64
    try:
        req = urllib.request.Request(f"{RADICALE}/{CAL_USER}/{calendar}")
        token = base64.b64encode(f"{CAL_USER}:{cal_pass()}".encode()).decode()
        req.add_header("Authorization", "Basic " + token)
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.read().decode()
    except Exception as e:
        log(f"cal_get failed: {e}")
        return ""

def parse_items(calendar, comp):
    """[(summary, when_dt, status)] for VEVENT/VTODO in a Radicale collection.
    comp may be given as EVENT/VTODO or VEVENT/VVTODO - normalized here."""
    import datetime
    comp = comp[1:] if comp.startswith("V") else comp
    raw = cal_get(calendar).replace("\r\n ", "").replace("\n ", "")
    items = []
    for block in re.findall(r"BEGIN:V" + comp + r"\b.*?END:V" + comp + r"\b", raw, re.S):
        summary = re.search(r"SUMMARY:?(.*)", block)
        status = re.search(r"STATUS:?(.*)", block)
        when = None
        m = re.search(r"DTSTART(?:;[^:\n]*)?:(\d{8}T\d{6})", block) or \
            re.search(r"DUE(?:;[^:\n]*)?:(\d{8}T\d{6})", block)
        if m:
            try:
                when = datetime.datetime.strptime(m.group(1), "%Y%m%dT%H%M%S")
            except Exception:
                when = None
        items.append((summary.group(1).strip() if summary else "(no title)",
                      when, status.group(1).strip() if status else ""))
    return items

def radicale_status():
    """HTTP check of the house calendar. (up, detail). Never includes the password."""
    import base64
    url = f"{RADICALE}/{CAL_USER}/house.ics/"
    try:
        req = urllib.request.Request(url)
        token = base64.b64encode(f"{CAL_USER}:{cal_pass()}".encode()).decode()
        req.add_header("Authorization", "Basic " + token)
        with urllib.request.urlopen(req, timeout=8) as resp:
            return True, f"up (HTTP {resp.status})"
    except urllib.error.HTTPError as exc:
        if exc.code < 500:
            return True, f"up (HTTP {exc.code})"
        return False, f"down (HTTP {exc.code})"
    except Exception as exc:
        return False, f"down ({type(exc).__name__})"

def _fmt_when(items):
    lines = []
    for title, when, _status in items:
        if when:
            lines.append(f"  {when.strftime('%a %b %d %H:%M')} - {title}")
        else:
            lines.append(f"  {title}")
    return lines or ["  (none)"]

def agenda_reply(days=1):
    import datetime
    up, detail = radicale_status()
    today = datetime.date.today()
    span = 7 if days <= 1 else max(1, days)
    end = today + datetime.timedelta(days=span)
    lines = [f"Radicale house calendar: {detail}."]
    if not up:
        lines.append("Agenda unavailable until the calendar answers.")
        return "\n".join(lines)
    events = [i for i in parse_items("house.ics", "EVENT")
              if i[1] and today <= i[1].date() < end]
    tasks = [i for i in parse_items("tasks.ics", "VTODO")
             if i[2] != "COMPLETED" and (not i[1] or i[1].date() < end)]
    today_events = [e for e in events if e[1].date() == today]
    later_events = [e for e in events if e[1].date() != today]
    dated = [t for t in tasks if t[1]]
    undated = [t for t in tasks if not t[1]]
    lines.append("Today:")
    lines += _fmt_when(sorted(today_events, key=lambda x: x[1]))
    lines.append(f"Through {end.strftime('%a %b %d')}:")
    lines += _fmt_when(sorted(later_events, key=lambda x: x[1]))
    lines.append("Tasks due in that window:")
    lines += _fmt_when(sorted(dated, key=lambda x: x[1] or datetime.datetime.min))
    if undated:
        lines.append("Undated open tasks:")
        lines += [f"  {t[0]}" for t in undated[:8]]
        if len(undated) > 8:
            lines.append(f"  ...and {len(undated) - 8} more")
    return "\n".join(lines)


def _geocode(place):
    """Forward geocode a place phrase via Nominatim. Returns (lat, lon, name)."""
    url = ("https://nominatim.openstreetmap.org/search?q="
           + urllib.parse.quote(place) + "&format=json&limit=1")
    req = urllib.request.Request(url, headers={"User-Agent": "house-bot/1.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        results = json.loads(r.read())
    if not results:
        return None
    return float(results[0]["lat"]), float(results[0]["lon"]), results[0].get("display_name", place)

def _add_location_reminder(text):
    """Store a 'remind me to X when I get home / to Y' reminder."""
    import datetime
    rems = _load_json(LOC_REMINDERS_FILE, [])
    m = re.search(r"\bwhen (?:i|we) (?:get|arrive|reach|am|are|m|come)\b(.*)$", text.lower())
    place_txt = (m.group(1) if m else "").strip(" .,!")
    what = text[:m.start()].strip(" .,-") if m else text
    what = re.sub(r"^(remind me|remind|note) (to |about |that )?", "", what, flags=re.I)
    entry_place = {"type": "home"}
    if "home" in place_txt[:10] or "back" in place_txt[:6]:
        pass  # type home
    else:
        pm = re.search(r"\b(?:at|near|to)\s+(?:the\s+|a\s+)?(.+)", place_txt)
        target = (pm.group(1) if pm else place_txt).strip(" .,!")
        brand = STORE_BRANDS.search(target)
        if brand:
            entry_place = {"type": "brand", "brand": brand.group(0).lower()}
        else:
            geo = _geocode(target)
            if not geo:
                return (f"I could not find '{target}' on the map - try an "
                        f"address, or say 'when I get home'.")
            entry_place = {"type": "coords", "lat": geo[0], "lon": geo[1],
                           "name": geo[2][:60]}
    rems.append({"what": what[:120], "place": entry_place,
                 "created": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
                 "armed": False})
    _save_json(LOC_REMINDERS_FILE, rems)
    if entry_place["type"] == "home":
        return f"Got it - I will Signal you to '{what[:80]}' when you get home."
    if entry_place["type"] == "brand":
        return (f"Got it - I will Signal you to '{what[:80]}' the next time "
                f"you are at a {entry_place['brand']}.")
    return (f"Got it - I will Signal you to '{what[:80]}' when you are at "
            f"{entry_place.get('name', 'that place')}.")


LLM_BACKENDS_FILE = os.path.join(HOUSE_STATE, "llm_backends.conf")

def _load_llm_backends():
    entries = {}
    try:
        for line in open(LLM_BACKENDS_FILE):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip().upper()
            m = re.match(r"BACKEND_(\d+)_(NAME|URL|MODEL|PRESENCE)$", k)
            if not m:
                continue
            n = int(m.group(1))
            e = entries.setdefault(n, {"name": f"backend{n}", "url": "", "model": "", "presence": ""})
            e[m.group(2).lower()] = v
    except Exception:
        pass
    out = []
    for n in sorted(entries):
        e = entries[n]
        if e.get("url") and e.get("model"):
            out.append({"name": e["name"], "url": e["url"], "model": e["model"],
                        "presence": e.get("presence") or None})
    if not out:
        # no llm_backends.conf: primary + fallback from the environment
        for pref, name, model, presence in (
                ("LLM_PRIMARY", "loq", "gpt-oss-32k", os.path.join(HOUSE_STATE, "omarchy_llm_up")),
                ("LLM_FALLBACK", "mac", "qwen3:4b-instruct", None)):
            url = os.environ.get(pref + "_URL", "")
            if url:
                out.append({"name": os.environ.get(pref + "_NAME", name), "url": url,
                            "model": os.environ.get(pref + "_MODEL", model),
                            "presence": os.environ.get(pref + "_PRESENCE", presence or "") or None})
    return out

LLM_BACKENDS = _load_llm_backends()


# Weekday names for date resolution ("on monday", "next friday", ...)
WD = {'monday': 0, 'tuesday': 1, 'wednesday': 2, 'thursday': 3,
      'friday': 4, 'saturday': 5, 'sunday': 6}

def _day_offset_for(dayw, qual, now):
    dayw = (dayw or " ").strip().lower()
    if not dayw:
        return 0, 1          # no day word: today, bump if past
    """(calendar-day offset, bump-if-past) for a day word.
    'tomorrow' is literally +1 calendar day - even at 11:59pm."""
    if dayw == 'tomorrow':
        return 1, 1
    if dayw in ('today', 'tonight'):
        return 0, 1
    off = (WD[dayw] - now.weekday()) % 7
    if off == 0:
        if (qual or "").lower() == "next":
            return 7, 7      # "next monday" said on a monday: a week out
        return 0, 7          # same weekday named again: means next week
    return off, 0            # upcoming occurrence this week

try:
    from contact_lookup import (
        attach_contact_info as contact_attach,
        find_contact as contact_find,
        extract_call_target as contact_extract_target,
        format_contact as contact_format,
    )
except Exception:  # module or contacts.json missing -> feature silently off
    def contact_attach(text, contacts=None):
        return text, None, None
    def contact_find(query, contacts=None):
        return None, None, []
    def contact_extract_target(text):
        return None
    def contact_format(contact):
        return ""


def _contact_ambiguity(cands):
    return "Which one? " + " \u00b7 ".join(
        c["name"] + (f" ({c['org']})" if c.get("org") else "") for c in cands)


def _clean_what(text, m, tail=""):
    """Rebuild the reminder/task text from the words AROUND the matched time
    expression, dropping the time words and 'remind me to' scaffolding.
    Fixes 'remind me to call X tomorrow at 12pm' capturing the whole message."""
    s = (text[:m.start()].strip() + " " + (tail or "").strip()).strip()
    s = re.sub(r'^(?:remind(?:\s+me)?(?:\s+to)?\s+|to\s+|please\s+|check\s+on\s+|set\s+(?:a\s+)?(?:reminder|task)(?:\s+(?:to|for))?\s+)', '', s, flags=re.I)
    s = re.sub(r'^(?:me|to)\b\s*', '', s, flags=re.I)
    s = re.sub(r'^(?:(?:next|this)\s+)?(?:tomorrow|today|tonight|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b[,\s]*(?:(?:to|for|and|:)\s+)?', '', s, flags=re.I)
    s = re.sub(r'\s{2,}', ' ', s).strip(' ,.:;-')
    return s


def fast_parse_when(text):
    """Fast regex parser for common relative and clock time phrases (<1ms).
    Returns (when_dt, what_text) or None if complex/needs LLM."""
    import datetime
    now = datetime.datetime.now()
    t = text.strip()

    # 1. 'in X minutes/hours/days (to/for) <what>'
    m = re.search(r'\b(?:in\s+)?(\d+|a|an|half an|one|two|three|four|five|six|seven|eight|nine|ten|fifteen|twenty|thirty|forty|fifty)\s*(min(?:ute)?s?|hours?|hrs?|days?|secs?|seconds?)\b\s*(?:to\s+|for\s+|and\s+|:\s*)?(.*)', t, re.I)
    if m:
        qty_str = m.group(1).lower()
        unit = m.group(2).lower()
        what = m.group(3).strip() or 'reminder'
        num_map = {'a': 1, 'an': 1, 'half an': 0.5, 'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5,
                   'six': 6, 'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10, 'fifteen': 15, 'twenty': 20,
                   'thirty': 30, 'forty': 40, 'fifty': 50}
        qty = num_map.get(qty_str)
        if qty is None:
            try:
                qty = float(qty_str)
            except ValueError:
                qty = None
        if qty:
            delta = None
            if 'min' in unit: delta = datetime.timedelta(minutes=qty)
            elif 'hour' in unit or 'hr' in unit: delta = datetime.timedelta(hours=qty)
            elif 'day' in unit: delta = datetime.timedelta(days=qty)
            elif 'sec' in unit: delta = datetime.timedelta(seconds=qty)
            if delta:
                when = now + delta
                what = _clean_what(t, m, m.group(3))
                return when, what or 'reminder'

    # Day-word map + calendar-day offset helper (module-level names are
    # defined just above fast_parse_when; see WD/_day_offset_for).

    # 1b. daypart words with an optional day: "tomorrow at noon",
    #     "monday morning", "next friday evening" - deterministic.
    m = re.search(r'\b(?:(next|this)\s+)?((?:tomorrow|today|tonight|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\s+)?(?:at\s+)?(noon|midnight|morning|afternoon|evening|night)\b\s*(?:to\s+|for\s+|and\s+|:\s*)?(.*)', t, re.I)
    if m:
        qual = (m.group(1) or '').lower()
        dayw = (m.group(2) or "").lower()
        if not dayw:
            # day word may ride AFTER the daypart: "at noon tomorrow"
            md = re.search(r'\b(next\s+|this\s+)?(tomorrow|today|tonight|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b', (m.group(4) or ''), re.I)
            if md:
                dayw = md.group(2).lower()
                qual = (md.group(1) or '').strip()
        dp = m.group(3).lower()
        hour = {'noon': 12, 'midnight': 0, 'morning': 9, 'afternoon': 15,
                'evening': 19, 'night': 21}[dp]
        off, bump = _day_offset_for(dayw, qual, now)
        what = (m.group(4) or '').strip()
        target = now.replace(hour=hour, minute=0, second=0, microsecond=0) + datetime.timedelta(days=off)
        if target <= now:
            target += datetime.timedelta(days=bump)
        what = _clean_what(t, m, m.group(4))
        return target, what or 'reminder'

    # 2. day word / weekday + clock time: "tomorrow at 7pm", "friday at 5pm",
    #    "next monday at 9am" - deterministic.
    m = re.search(r'\b(?:(next|this)\s+)?((?:tomorrow|today|tonight|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\s+)?at\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b\s*(?:to\s+|for\s+|and\s+|:\s*)?(.*)', t, re.I)
    if m:
        qual = (m.group(1) or '').lower()
        dayw = (m.group(2) or "").lower()
        if not dayw:
            # day word may ride AFTER the time: "at 3pm tomorrow to call the vet"
            md = re.search(r'\b(next\s+|this\s+)?(tomorrow|today|tonight|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b', (m.group(6) or ''), re.I)
            if md:
                dayw = md.group(2).lower()
                qual = (md.group(1) or '').strip()
        hour = int(m.group(3))
        minute = int(m.group(4) or 0)
        meridiem = (m.group(5) or '').lower() or None
        what = m.group(6).strip()
        if meridiem == 'pm' and hour < 12: hour += 12
        elif meridiem == 'am' and hour == 12: hour = 0
        elif meridiem is None and hour <= 7 and not dayw:
            if now.hour >= 12: hour += 12
        off, bump = _day_offset_for(dayw, qual, now)
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0) + datetime.timedelta(days=off)
        if target <= now:
            target += datetime.timedelta(days=bump)
        what = _clean_what(t, m, m.group(6))
        return target, what or 'reminder'

    # 2b. bare day word with no time: "tomorrow" -> 09:00 next day,
    #     "on monday" -> 09:00 that monday.
    m = re.search(r'\b(?:(next|this)\s+)?(tomorrow|tonight|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b', t, re.I)
    if m:
        qual = (m.group(1) or '').lower()
        dayw = (m.group(2) or "").lower()
        off, bump = _day_offset_for(dayw, qual, now)
        what = _clean_what(t, m, '')
        hour = 20 if dayw == 'tonight' else 9
        target = now.replace(hour=hour, minute=0, second=0, microsecond=0) + datetime.timedelta(days=off)
        if target <= now:
            target += datetime.timedelta(days=bump)
        return target, what or t
    return None

# ---------------------------------------------------------------------------
# Recurring reminders / chores (added 2026-10-05)
#   "I water the plants every sunday", "I change the furnace filter
#   every 3 months", "remind me every monday at 7pm to take out the trash".
# Stored in recurring_reminders.json and fired by run_recurring_reminders(),
# which schedules the next occurrence right after each one fires. One-shot
# reminders still go through filetools /remind + house-jobs, untouched.
# ---------------------------------------------------------------------------
RECURRING_FILE = os.path.join(HOUSE_STATE, "recurring_reminders.json")
RECUR_TZ = "America/New_York"
RECUR_DEFAULT_TIME = (9, 0)          # 9:00 AM ET unless the message names a time
RECUR_CHECK_SECONDS = 30
_RECUR_LOCK = threading.RLock()

_RECUR_NUMS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4,
               "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
               "ten": 10, "eleven": 11, "twelve": 12, "other": 2}
_RECUR_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday",
                   "saturday", "sunday"]
_RECUR_NUM_RE = r"(\d{1,3}|a|an|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|other)"
_RECUR_WD_RE = r"(monday|tuesday|wednesday|thursday|friday|saturday|sunday)s?"
# adjective forms only count when they modify the action, not a noun:
# "give it monthly" / "remind me weekly to" yes, "pay the monthly bill" no
_RECUR_ADJ_TAIL = r"(?=\s*(?:$|[.,;:!?)]|at\b|on\b|to\b|starting\b|from\b|beginning\b|and\b|basis\b|until\b|please\b|for\b))"
_RECUR_CHORE_VERBS = (
    r"give|gave|change|replace|clean|feed|fed|water|wash|mow|take|put|check|"
    r"refill|fill|empty|pay|flush|service|rotate|vacuum|sweep|mop|dust|trim|"
    r"charge|test|treat|apply|spray|walk|bathe|bath|brush|groom|back up|backup|"
    r"update|order|renew|inspect|descale|run|do|swap|oil|grease|drain|deworm|"
    r"worm|medicate|dose|clip|scoop|sharpen|wipe|scrub|launder|rinse|top up|"
    r"reorder|restock|call|visit|pick up|drop off|haul|burn|spread|plant|prune|"
    r"weed|fertilize|lube|reset|clear|start|turn|set|open|close|unclog|defrost")


def _recur_parse(text):
    """Find a recurrence phrase. Returns {'unit','n','weekday','spans'} or None.
    unit is day|week|month|year; weekday is 0-6 for 'every monday'."""
    t = (text or "").lower()
    spans = []
    found = None

    def hit(m, unit, n, wd=None):
        nonlocal found
        spans.append(m.span())
        if found is None:
            found = {"unit": unit, "n": max(1, int(n)), "weekday": wd}

    m = re.search(r"\b(?:once\s+)?every\s+" + _RECUR_NUM_RE + r"\s+(day|week|month|year)s?\b", t)
    if m:
        n = m.group(1)
        hit(m, m.group(2), int(n) if n.isdigit() else _RECUR_NUMS[n])
    m = re.search(r"\b(?:once\s+)?every\s+(?:other\s+)?" + _RECUR_WD_RE + r"\b", t)
    if m:
        other = "other" in m.group(0)
        hit(m, "week", 2 if other else 1, _RECUR_WEEKDAYS.index(m.group(1)))
    m = re.search(r"\b(?:on\s+)?" + _RECUR_WD_RE.replace("s?", "s") + r"\b", t)
    if m and found is None:
        hit(m, "week", 1, _RECUR_WEEKDAYS.index(m.group(1)))
    m = re.search(r"\b(?:once|one time|1x)\s+(?:a|an|per|each|every)\s+(day|week|month|year)\b", t)
    if m:
        hit(m, m.group(1), 1)
    m = re.search(r"\b(?:every|each)\s+(day|week|month|year)\b", t)
    if m:
        hit(m, m.group(1), 1)
    m = re.search(r"\beveryday\b|\bevery\s+(?:morning|evening|night)\b|\bnightly\b", t)
    if m:
        hit(m, "day", 1)
    for word, unit, n in (("daily", "day", 1), ("weekly", "week", 1),
                          ("bi-?weekly", "week", 2), ("fortnightly", "week", 2),
                          ("monthly", "month", 1), ("bi-?monthly", "month", 2),
                          ("quarterly", "month", 3), ("yearly", "year", 1),
                          ("annually", "year", 1)):
        m = re.search(r"\b(?:on\s+a\s+)?" + word + r"\b(?:\s+basis\b)?" + _RECUR_ADJ_TAIL, t)
        if m:
            hit(m, unit, n)
    if found:
        found["spans"] = spans
    return found


def _recur_time(text):
    """(hour, minute) named in the message, else the 9:00 AM default."""
    t = (text or "").lower()
    m = re.search(r"\bat\s+(\d{1,2})(?::(\d{2}))?\s*(a\.?\s?m\.?|p\.?\s?m\.?)?(?![\d/-])", t)
    if m:
        h, mi, ap = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").replace(".", "").replace(" ", "")
        if ap == "pm" and h < 12:
            h += 12
        elif ap == "am" and h == 12:
            h = 0
        elif not ap and 1 <= h <= 6:
            h += 12          # 'at 5' means 5 PM for a chore reminder
        if h < 24 and mi < 60:
            return h, mi
    m = re.search(r"\bat\s+(\d{2})(\d{2})\b", t)
    if m and int(m.group(1)) < 24 and int(m.group(2)) < 60:
        return int(m.group(1)), int(m.group(2))
    if re.search(r"\b(?:at\s+)?noon\b", t):
        return 12, 0
    if re.search(r"\b(?:every|in the|each)\s+evening\b|\bat dinner\b", t):
        return 18, 0
    if re.search(r"\b(?:every|at|each)\s+night\b|\bnightly\b|\bbedtime\b", t):
        return 20, 0
    if re.search(r"\b(?:every|in the|each)\s+morning\b", t):
        return 8, 0
    return RECUR_DEFAULT_TIME


def _recur_now():
    import datetime, zoneinfo
    return datetime.datetime.now(zoneinfo.ZoneInfo(RECUR_TZ))


def _recur_parse_date(s, today):
    """'october 5 2026' / 'oct 5' / '2026-10-05' / '10/5' -> date or None."""
    import datetime
    s = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", s.strip().replace(",", " "))
    s = re.sub(r"\s+", " ", s)
    for fmt in ("%Y-%m-%d", "%B %d %Y", "%b %d %Y", "%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    for fmt in ("%B %d", "%b %d", "%m/%d"):
        try:
            d = datetime.datetime.strptime(s, fmt).date().replace(year=today.year)
            return d
        except ValueError:
            pass
    return None


_RECUR_DATE_RE = (r"((?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+\d{1,2}(?:st|nd|rd|th)?"
                  r"(?:,?\s*\d{4})?|\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}(?:/\d{2,4})?)")


def _recur_anchor(text, rec, today):
    """First-occurrence date. Explicit day ('on the 15th', 'every monday',
    'starting tomorrow', 'last time oct 3') wins; otherwise today."""
    import datetime
    t = (text or "").lower()
    m = re.search(r"\bstart(?:ing)?\s+(?:on\s+)?(tomorrow|today|" + _RECUR_DATE_RE + r"|" + _RECUR_WD_RE + r")", t)
    if m:
        word = m.group(1)
        if word == "tomorrow":
            return today + datetime.timedelta(days=1)
        if word == "today":
            return today
        wd = re.match(_RECUR_WD_RE, word)
        if wd:
            return today + datetime.timedelta(days=(_RECUR_WEEKDAYS.index(wd.group(1)) - today.weekday()) % 7)
        d = _recur_parse_date(word, today)
        if d:
            return d
    m = re.search(r"\blast\s+(?:time|one|done|dose|did|gave|changed|replaced|was)?\b[^.?!]*?\b" + _RECUR_DATE_RE, t)
    if m:
        d = _recur_parse_date(m.group(1), today)
        if d and d <= today:
            return d
    if re.search(r"\b(?:last|this)\s+(?:time\s+)?(?:was\s+)?yesterday\b|\b(?:gave|did|changed|replaced|fed|took)\b[^.?!]{0,40}\byesterday\b", t):
        return today - datetime.timedelta(days=1)
    if rec.get("weekday") is not None:
        return today + datetime.timedelta(days=(rec["weekday"] - today.weekday()) % 7)
    if rec["unit"] in ("month", "year"):
        m = re.search(r"\bon\s+the\s+(\d{1,2})(?:st|nd|rd|th)?\b", t)
        if m and 1 <= int(m.group(1)) <= 31:
            import calendar
            day = int(m.group(1))
            last = calendar.monthrange(today.year, today.month)[1]
            return today.replace(day=min(day, last))
    return today


def _recur_add_months(d, months, want_day):
    import calendar
    y, mth = divmod(d.month - 1 + months, 12)
    y += d.year
    mth += 1
    return d.replace(year=y, month=mth, day=min(want_day, calendar.monthrange(y, mth)[1]))


def _recur_occurrence(rec, k):
    """k-th occurrence (aware datetime) counted from the anchor."""
    import datetime, zoneinfo
    a = datetime.date.fromisoformat(rec["anchor"])
    unit, n = rec["unit"], int(rec.get("n", 1))
    if unit == "day":
        d = a + datetime.timedelta(days=k * n)
    elif unit == "week":
        d = a + datetime.timedelta(days=7 * k * n)
    elif unit == "month":
        d = _recur_add_months(a, k * n, int(rec.get("day") or a.day))
    else:
        d = _recur_add_months(a, 12 * k * n, int(rec.get("day") or a.day))
    return datetime.datetime(d.year, d.month, d.day, int(rec.get("hour", 9)),
                             int(rec.get("minute", 0)), tzinfo=zoneinfo.ZoneInfo(RECUR_TZ))


def _recur_next(rec, after):
    """First occurrence strictly after `after` (aware datetime)."""
    approx = {"day": 1, "week": 7, "month": 28, "year": 365}[rec["unit"]] * int(rec.get("n", 1))
    import datetime
    a = datetime.date.fromisoformat(rec["anchor"])
    k = max(0, int((after.date() - a).days // approx) - 2)
    for _ in range(100000):
        occ = _recur_occurrence(rec, k)
        if occ > after:
            return occ
        k += 1
    raise ValueError("could not compute next occurrence")


def _recur_ord(n):
    n = int(n)
    return f"{n}{'th' if 11 <= n % 100 <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def _recur_clock(h, m):
    return f"{(int(h) % 12) or 12}:{int(m):02d} {'AM' if int(h) < 12 else 'PM'}"


def _recur_freq_label(rec):
    unit, n = rec["unit"], int(rec.get("n", 1))
    if n == 1:
        return {"day": "daily", "week": "weekly", "month": "monthly", "year": "yearly"}[unit]
    if unit == "week" and n == 2:
        return "every other week"
    if unit == "month" and n == 3:
        return "quarterly"
    return f"every {n} {unit}s"


def _recur_schedule_phrase(rec):
    import datetime
    unit, n = rec["unit"], int(rec.get("n", 1))
    a = datetime.date.fromisoformat(rec["anchor"])
    if unit == "day":
        return "every day" if n == 1 else ("every other day" if n == 2 else f"every {n} days")
    if unit == "week":
        wd = a.strftime("%A")
        return f"every {wd}" if n == 1 else (f"every other {wd}" if n == 2 else f"every {n} weeks on {wd}")
    if unit == "month":
        day = _recur_ord(rec.get("day") or a.day)
        return (f"on the {day} of each month" if n == 1 else
                f"on the {day} every other month" if n == 2 else
                f"on the {day} every {n} months")
    md = f"{a.strftime('%b')} {rec.get('day') or a.day}"
    return f"every year on {md}" if n == 1 else f"every {n} years on {md}"


def _recur_when_phrase(dt, now=None):
    now = now or _recur_now()
    date = f"{dt.strftime('%b')} {dt.day}" + (f", {dt.year}" if dt.year != now.year else "")
    return f"{date} at {_recur_clock(dt.hour, dt.minute)}"


_RECUR_FIXES = [(r"\bheart\s*-?\s*g(?:au|ua|a)rd\b", "Heartgard"),
                (r"\bsimp[ae]rr?ic?ka?\b", "Simparica"),
                (r"\bnex\s*gard\b", "NexGard"), (r"\bbravecto\b", "Bravecto")]


def _recur_what(text, rec):
    """The chore itself: 'I give my dogs their heart gaurd once per month'
    -> 'Give the dogs Heartgard'."""
    raw = (text or "").strip()
    # only the sentence that carries the recurrence ("... I gave it today. Can you remind me?")
    sentences = [s for s in re.split(r"(?<=[.!?;])\s+|\n+", raw) if s.strip()]
    pick = next((s for s in sentences if _recur_parse(s)), raw)
    s = pick
    s = re.sub(r"\bon\s+the\s+\d{1,2}(?:st|nd|rd|th)?(?:\s+of\s+(?:the|each|every)\s+month)?\b", " ", s, flags=re.I)
    s = re.sub(r"\b(?:once\s+)?every\s+" + _RECUR_NUM_RE + r"\s+(?:day|week|month|year)s?\b", " ", s, flags=re.I)
    s = re.sub(r"\b(?:once\s+)?every\s+(?:other\s+)?" + _RECUR_WD_RE + r"\b", " ", s, flags=re.I)
    s = re.sub(r"\bon\s+" + _RECUR_WD_RE.replace("s?", "s") + r"\b", " ", s, flags=re.I)
    s = re.sub(r"\b(?:once|one time|1x)\s+(?:a|an|per|each|every)\s+(?:day|week|month|year)\b", " ", s, flags=re.I)
    s = re.sub(r"\b(?:every|each)\s+(?:day|week|month|year|morning|evening|night)\b|\beveryday\b|\bnightly\b", " ", s, flags=re.I)
    s = re.sub(r"\b(?:on\s+a\s+)?(?:daily|weekly|bi-?weekly|fortnightly|monthly|bi-?monthly|quarterly|yearly|annually)\b(?:\s+basis\b)?" + _RECUR_ADJ_TAIL, " ", s, flags=re.I)
    s = re.sub(r"\bstart(?:ing)?\s+(?:on\s+)?(?:tomorrow|today|" + _RECUR_DATE_RE + r"|" + _RECUR_WD_RE + r")", " ", s, flags=re.I)
    s = re.sub(r",?\s*(?:and\s+)?(?:the\s+)?last\s+(?:time|one|done|dose)\b.*$", " ", s, flags=re.I)
    s = re.sub(r"\bat\s+\d{1,4}(?::\d{2})?\s*(?:a\.?\s?m\.?|p\.?\s?m\.?)?|\b(?:at\s+)?noon\b|\b(?:in the|at)\s+(?:morning|evening|night)\b|\bat\s+bedtime\b", " ", s, flags=re.I)
    s = re.sub(r"^\s*(?:hey\s+|ok\s+|okay\s+)?(?:house[,\s]+)?(?:please\s+)?(?:can|could|would|will)?\s*(?:you\s+)?(?:please\s+)?", "", s, flags=re.I)
    s = re.sub(r"^\s*(?:set\s+(?:up\s+)?(?:a\s+)?(?:recurring\s+)?(?:reminder|chore)|add\s+(?:a\s+)?(?:recurring\s+)?(?:reminder|chore)|remind\s+(?:me|us)|log|mark|note)\b\s*(?:that\s+|to\s+|about\s+|for\s+|down\s+)?", "", s, flags=re.I)
    s = re.sub(r"^\s*(?:i|we)\s+(?:(?:usually|always|normally|also|generally|typically|just)\s+)*(?:(?:need|have|want|got|try|like)\s+to\s+|should\s+|must\s+|gotta\s+)?", "", s, flags=re.I)
    s = re.sub(r"\b(?:please|for me|thanks|thank you)\b", " ", s, flags=re.I)
    s = re.sub(r"\b(?:my|our)\b", "the", s, flags=re.I)
    s = re.sub(r"\b(the\s+\w+)\s+(?:their|his|her|its)\b", r"\1", s, flags=re.I)
    for pat, rep in _RECUR_FIXES:
        s = re.sub(pat, rep, s, flags=re.I)
    s = re.sub(r"\s+", " ", s).strip(" .,;:-!?")
    s = re.sub(r"\s+(?:to|and|on|at|a|the)$", "", s, flags=re.I).strip(" .,;:-")
    if not s:
        return ""
    return s[0].upper() + s[1:120]


def _recur_load():
    data = _load_json(RECURRING_FILE, [])
    return data if isinstance(data, list) else []


def _recur_save(rems):
    os.makedirs(os.path.dirname(RECURRING_FILE), exist_ok=True)
    tmp = RECURRING_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(rems, f, indent=1)
    os.replace(tmp, RECURRING_FILE)


def _recur_words(s):
    stop = {"the", "and", "for", "with", "their", "them", "give", "gave",
            "change", "every", "month", "week", "day", "reminder", "chore"}
    return {w for w in re.findall(r"[a-z0-9]+", (s or "").lower()) if len(w) > 2 and w not in stop}


def _recur_find(rems, what):
    """Best active recurring reminder for the same chore (dedupe), or None."""
    want = _recur_words(what)
    best, best_score = None, 0.0
    for r in rems:
        if not r.get("active", True):
            continue
        have = _recur_words(r.get("what", ""))
        if not want or not have:
            continue
        score = len(want & have) / float(len(want | have))
        if score > best_score:
            best, best_score = r, score
    return best if best_score >= 0.5 else None


def _recur_chore_log(rec, created):
    """Record the chore in chore_intervals.json (marked recurring so the old
    interval nudge stays quiet) and append it to the Obsidian chores log."""
    approx = {"day": 1, "week": 7, "month": 30, "year": 365}[rec["unit"]] * int(rec.get("n", 1))
    try:
        intervals = _load_json(CHORE_INTERVALS_FILE, {})
        key = None
        want = _recur_words(rec["what"])
        for k, e in intervals.items():
            if e.get("recurring_id") == rec["id"]:
                key = k
                break
        if key is None:
            for k, e in intervals.items():
                have = _recur_words(e.get("task", k))
                if want and have and len(want & have) / float(len(want | have)) >= 0.5:
                    key = k
                    break
        if key is None:
            key = rec["what"].lower()
        entry = intervals.get(key, {})
        entry.update({"task": rec["what"], "days": approx, "recurring_id": rec["id"],
                      "schedule": f"{_recur_freq_label(rec)}, {_recur_schedule_phrase(rec)} at "
                                  f"{_recur_clock(rec['hour'], rec['minute'])}"})
        if rec["anchor"] <= _recur_now().date().isoformat() and not entry.get("last"):
            entry["last"] = rec["anchor"]
        entry.pop("notified", None)
        intervals[key] = entry
        _save_json(CHORE_INTERVALS_FILE, intervals)
        rec["chore_key"] = key
    except Exception as e:
        log(f"recurring: chore interval update failed: {e}")
    if created:
        try:
            if os.path.isdir(os.path.dirname(CHORES_NOTE)):
                stamp = _recur_now().strftime("%Y-%m-%d %H:%M")
                with open(CHORES_NOTE, "a") as f:
                    f.write(f"- {stamp} - {rec['what']} [recurring chore set up: "
                            f"{_recur_freq_label(rec)}, {_recur_schedule_phrase(rec)} at "
                            f"{_recur_clock(rec['hour'], rec['minute'])}]\n")
        except Exception as e:
            log(f"recurring: chores log append failed: {e}")


def recurring_upsert(what, unit, n, anchor, hour, minute, weekday=None, source="",
                     day=None, chore=True):
    """Create or update (dedupe by chore name) a recurring reminder.
    Returns (rec, created_bool)."""
    import uuid
    now = _recur_now()
    with _RECUR_LOCK:
        rems = _recur_load()
        rec = _recur_find(rems, what)
        created = rec is None
        if created:
            rec = {"id": "rr-" + uuid.uuid4().hex[:8], "created": now.strftime("%Y-%m-%d %H:%M"),
                   "last_fired": None, "active": True}
            rems.append(rec)
        rec.update({"what": what, "unit": unit, "n": int(n), "anchor": anchor.isoformat(),
                    "day": int(day or anchor.day), "hour": int(hour), "minute": int(minute),
                    "weekday": weekday, "tz": RECUR_TZ, "chore": bool(chore),
                    "updated": now.strftime("%Y-%m-%d %H:%M")})
        if source:
            rec["source"] = source[:200]
        rec.pop("retry_at", None)
        nxt = _recur_next(rec, now)
        rec["next"] = nxt.timestamp()
        rec["next_local"] = nxt.strftime("%Y-%m-%d %H:%M")
        if chore:
            _recur_chore_log(rec, created)
        _recur_save(rems)
    log(f"recurring {'created' if created else 'updated'}: {rec['id']} {what!r} next {rec['next_local']}")
    return rec, created


def add_recurring_reminder(text):
    """Plain-language recurring chore/reminder. Reply mentions ONLY this item."""
    rec = _recur_parse(text)
    if not rec:
        return None
    what = _recur_what(text, rec)
    if not what or len(_recur_words(what)) == 0:
        return ("I caught the schedule but not the chore - try e.g. "
                "'I change the furnace filter every 3 months'.")
    now = _recur_now()
    anchor = _recur_anchor(text, rec, now.date())
    hour, minute = _recur_time(text)
    chore = not re.match(r"^\s*(?:please\s+)?(?:can you\s+)?remind\b", text, re.I)
    r, created = recurring_upsert(what, rec["unit"], rec["n"], anchor, hour, minute,
                                  weekday=rec.get("weekday"), source=text, chore=chore)
    import datetime, zoneinfo
    nxt = datetime.datetime.fromtimestamp(r["next"], zoneinfo.ZoneInfo(RECUR_TZ))
    kind = "chore" if chore else "reminder"
    item = what[0].lower() + what[1:] if len(what) > 1 and not what[1].isupper() else what
    head = "Got it" if created else "Updated"
    label = _recur_freq_label(r)
    kind_phrase = (f"{label} {kind}" if not label.startswith("every ")
                   else f"{kind} {label}")
    return (f"{head}: {kind_phrase} \u2014 {item}. I'll remind you "
            f"{_recur_schedule_phrase(r)}, next on {_recur_when_phrase(nxt, now)}.")


def _recur_fire_due(now=None, send=None):
    """Fire due recurring reminders and schedule each one's next occurrence.
    Returns [(id, message)] actually sent."""
    import datetime, zoneinfo
    tz = zoneinfo.ZoneInfo(RECUR_TZ)
    now = now or _recur_now()
    send = send or nudge_send
    fired = []
    with _RECUR_LOCK:
        rems = _recur_load()
        changed = False
        for r in rems:
            if not r.get("active", True) or not r.get("next"):
                continue
            if r["next"] > now.timestamp() or r.get("retry_at", 0) > now.timestamp():
                continue
            nxt = _recur_next(r, now)
            msg = (f"\u23f0 Reminder: {r['what']} ({_recur_freq_label(r)}"
                   f"{' chore' if r.get('chore', True) else ''}). "
                   f"Next one: {_recur_when_phrase(nxt, now)}.")
            ok = False
            for recipient in SENDER_NAMES:
                ok = bool(send(recipient, msg)) or ok
            changed = True
            if not ok:
                r["retry_at"] = now.timestamp() + 300
                log(f"recurring: send failed for {r['id']}, retrying in 5 min")
                continue
            r.pop("retry_at", None)
            r["last_fired"] = now.strftime("%Y-%m-%d %H:%M")
            r["next"] = nxt.timestamp()
            r["next_local"] = nxt.astimezone(tz).strftime("%Y-%m-%d %H:%M")
            fired.append((r["id"], msg))
            log(f"recurring fired: {r['id']} {r['what']!r}; next {r['next_local']}")
        if changed:
            _recur_save(rems)
    return fired


def run_recurring_reminders():
    log(f"recurring reminders started (every {RECUR_CHECK_SECONDS}s, {RECURRING_FILE})")
    while True:
        try:
            _recur_fire_due()
        except Exception as e:
            log(f"recurring reminders error: {e}")
        time.sleep(RECUR_CHECK_SECONDS)


def reminders_list_reply():
    """Only when asked: recurring + pending one-shot reminders."""
    import datetime, zoneinfo
    tz = zoneinfo.ZoneInfo(RECUR_TZ)
    now = _recur_now()
    lines = []
    rec = [r for r in _recur_load() if r.get("active", True)]
    if rec:
        lines.append("Recurring:")
        for r in sorted(rec, key=lambda r: r.get("next", 0)):
            nxt = datetime.datetime.fromtimestamp(r["next"], tz)
            lines.append(f"  - {r['what']} - {_recur_freq_label(r)}, {_recur_schedule_phrase(r)}; "
                         f"next {_recur_when_phrase(nxt, now)} ({r['id']})")
    one = []
    for r in _load_json(os.path.join(HOUSE_STATE, "reminders.json"), []):
        try:
            if float(r.get("when", 0)) > now.timestamp():
                one.append(r)
        except Exception:
            pass
    if one:
        lines.append("One-time:")
        for r in sorted(one, key=lambda r: r["when"]):
            dt = datetime.datetime.fromtimestamp(float(r["when"]), tz)
            lines.append(f"  - {dt.strftime('%a')} {_recur_when_phrase(dt, now)} - {r.get('what', '')}")
    return "\n".join(lines) if lines else "No reminders scheduled."


def recurring_cancel_reply(text):
    t = re.sub(r"^\s*/?(?:unremind|recurring\s+stop)\b", "", text or "", flags=re.I)
    t = re.sub(r"\b(?:please|stop|cancel|delete|remove|turn off|end|the|my|recurring|"
               r"reminders?|reminding me|remind me|chore|about|to|for|of)\b", " ", t, flags=re.I).strip()
    with _RECUR_LOCK:
        rems = _recur_load()
        target = next((r for r in rems if r.get("active", True) and r["id"] in t.split()), None)
        if target is None and t:
            want = _recur_words(t)
            scored = []
            for r in rems:
                have = _recur_words(r.get("what", ""))
                if r.get("active", True) and want and have:
                    scored.append((len(want & have) / float(len(want)), r))
            scored.sort(key=lambda x: -x[0])
            if scored and scored[0][0] >= 0.6 and (len(scored) == 1 or scored[1][0] < scored[0][0]):
                target = scored[0][1]
        if target is None:
            return ("No recurring reminder matches that - say 'list my reminders' to see them. "
                    "(One-time reminders aren't changed by this.)")
        target["active"] = False
        target["cancelled"] = _recur_now().strftime("%Y-%m-%d %H:%M")
        _recur_save(rems)
    log(f"recurring cancelled: {target['id']} {target['what']!r}")
    return f"Stopped the {_recur_freq_label(target)} reminder: {target['what']}."


def _looks_recurring_request(t):
    """Statements/requests that should create a recurring chore or reminder."""
    t = (t or "").lower().strip()
    if not t or "$" in t:
        return False
    has_remind = bool(re.search(r"\bremind(?:er)?s?\b", t))
    if (t.endswith("?") or re.match(r"^(how|what|whats|what's|when|why|which|who|where|is|are|do|does|did|should|would|will)\b", t)) \
            and not re.search(r"\bremind (?:me|us)\b", t):
        return False
    if re.match(r"^\s*(?:please\s+)?(?:stop|cancel|delete|remove|turn off)\b", t):
        return False
    if not _recur_parse(t):
        return False
    if has_remind or re.search(r"\bchores?\b", t):
        return True
    verbs = r"(?:" + _RECUR_CHORE_VERBS + r")(?:s|es|ed|d)?"
    if re.match(r"^(?:(?:mark|log|note)\s+(?:that\s+|down\s+)?)?(?:i|we)\s+"
                r"(?:(?:usually|always|normally|also|generally|typically|just)\s+)*"
                r"(?:(?:need|have|want|got|try|like)\s+to\s+|should\s+|must\s+|gotta\s+)?" + verbs + r"\b", t):
        return True
    if re.match(r"^(?:the |my |our )?(?:dogs?|cats?|pets?|horses?|chickens?|goats?|kids?)\s+(?:get|gets|need|needs)\b", t):
        return True
    return False


_CHAT_CLAIM_RE = re.compile(
    r"(?:\b(?:reminders?|events?|tasks?|chores?)\s+(?:has|have|is|are)\s+(?:now\s+)?(?:been\s+)?"
    r"(?:set|scheduled|created|added|saved|logged)\b"
    r"|\bi(?:'ve|\u2019ve| have| will|'ll|\u2019ll)\s+(?:set|schedul\w*|creat\w*|add\w*|log\w*)\s+"
    r"(?:up\s+)?(?:a |an |the |your |these |those |this )?(?:recurring |monthly |weekly |daily )?"
    r"(?:reminders?|events?|tasks?|chores?)\b"
    r"|\breminders? i(?:'ll|\u2019ll| will) set\b)", re.I)


def _guard_chat_claims(reply):
    """Chat cannot create reminders; never let it claim it did (it used to
    'confirm' imaginary reminders and re-list old ones from history)."""
    if reply and _CHAT_CLAIM_RE.search(reply):
        log(f"chat reply claimed an action it cannot take; replaced: {reply[:80]!r}")
        return ("I haven't set or changed any reminders from that message. "
                "To set one, say it plainly - e.g. 'remind me tomorrow at 9am "
                "to call the vet', or 'I change the furnace filter every 3 months' "
                "for a recurring chore. 'list my reminders' shows what's scheduled.")
    return reply



def add_reminder(text):
    # location-triggered reminders
    if re.search(r"\bwhen (?:i|we) (?:get|arrive|reach|am|are|m|come)\b", text.lower()):
        return _add_location_reminder(text)

    # recurring ('every month', 'once a week', 'daily') -> recurring reminder
    if _recur_parse(text):
        _rr = add_recurring_reminder(text)
        if _rr:
            return _rr

    # Fast regex parse (<1ms) for common timer/reminder phrases
    fast = fast_parse_when(text)
    if fast:
        when, what = fast
        what, _c, _amb = contact_attach(what)
        if _amb:
            return _contact_ambiguity(_amb)
        req = urllib.request.Request(
            SERVICE + "/remind", method="POST",
            data=json.dumps({"when_iso": when.strftime("%Y-%m-%d %H:%M"),
                             "what": what}).encode(),
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                d = json.loads(r.read())
            cal = " - calendar event created" if d.get("calendar_event") else ""
            return f"Reminder set: {what!r} at {when.strftime('%a %b %d %H:%M')}{cal}"
        except Exception as e:
            return f"Reminder parse ok but queueing failed: {e}"

    """LLM parses 'in 2 hours' / 'tomorrow at 9' into an ISO datetime."""
    import datetime
    backend = pick_extract_backend()
    if not backend:
        return "No LLM backend reachable to parse the reminder time."
    base, model, name = backend
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M %A")
    prompt = (f"Now is {now} (America/New_York). Extract from the message "
              f"WHEN the reminder should fire and WHAT to remind about. Reply "
              f"with ONLY JSON: {{\"when\": \"YYYY-MM-DD HH:MM\", "
              f"\"what\": \"...\"}}.\nMessage: " + text)
    raw = ask_llm(base, model, prompt)
    try:
        d = json.loads(re.search(r"\{.*\}", raw, re.S).group(0))
        when = datetime.datetime.strptime(d["when"].strip(), "%Y-%m-%d %H:%M")
        what = d.get("what", text)[:100]
        what, _c, _amb = contact_attach(what)
        if _amb:
            return _contact_ambiguity(_amb)
    except Exception:
        return "Could not parse the reminder time - try 'remind me in 2 hours to ...'"
    # Deterministic overrides: relative day words beat LLM date arithmetic.
    tl = text.lower()
    if "tomorrow" in tl:
        want = datetime.datetime.now() + datetime.timedelta(days=1)
        when = when.replace(year=want.year, month=want.month, day=want.day)
    elif re.search(r"\b(today|tonight)\b", tl):
        now2 = datetime.datetime.now()
        when = when.replace(year=now2.year, month=now2.month, day=now2.day)
    mwd = re.search(r"\b(?:(next|this)\s+)?(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", tl)
    if mwd:
        _off, _b = _day_offset_for(mwd.group(2).lower(), (mwd.group(1) or "").lower(), datetime.datetime.now())
        _base = datetime.datetime.now() + datetime.timedelta(days=_off)
        when = when.replace(year=_base.year, month=_base.month, day=_base.day)
    if re.search(r"\bnoon\b", tl):
        when = when.replace(hour=12, minute=0)
    elif re.search(r"\bmidnight\b", tl):
        when = when.replace(hour=0, minute=0)
    # route through filetools: queues the Signal nudge AND creates the calendar event
    req = urllib.request.Request(
        SERVICE + "/remind", method="POST",
        data=json.dumps({"when_iso": when.strftime("%Y-%m-%d %H:%M"),
                         "what": what}).encode(),
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            d = json.loads(r.read())
        cal = " - calendar event created" if d.get("calendar_event") else ""
        return f"Reminder set: {what!r} at {when.strftime('%a %b %d %H:%M')}{cal}"
    except Exception as e:
        return f"Reminder parse ok but queueing failed: {e}"

def note_add(text, folder=CHAT_NOTES_DIR, label="Chat Notes"):
    """Save a note as its own markdown file in the Obsidian vault."""
    import datetime, re as _re
    if not text or not text.strip():
        return "What would you like me to note down?"
    os.makedirs(folder, exist_ok=True)
    now = datetime.datetime.now()
    stamp = now.strftime("%Y-%m-%d %H:%M")
    clean_text = text.strip()
    slug = _re.sub(r"[^a-zA-Z0-9 ]", "", clean_text)[:40].strip() or "note"
    path = os.path.join(folder, f"{now.strftime('%Y-%m-%d')} {slug}.md")
    n = 2
    while os.path.exists(path):
        path = os.path.join(folder, f"{now.strftime('%Y-%m-%d')} {slug} {n}.md")
        n += 1
    with open(path, "w") as f:
        f.write(f"# Note - {stamp}\n\n{clean_text}\n")
    ody = odysseus_note(f"Note {stamp}", clean_text, "house")
    return (f"Note saved to Obsidian ({label}): '{slug}'. "
            f"{ody} "
            f"It lands in the semantic index tonight.")

def work_note_add(text):
    return note_add(text, folder=WORK_NOTES_DIR, label="Work Notes")

def weather_reply(city=""):
    """Fetch + format weather via the filetools /weather endpoint."""
    try:
        # strip day/time phrases the classifier may leave in the city arg
        city = re.sub(r"\b(this weekend|this week|today|tomorrow|tonight|weekend)\b", " ", city)
        city = re.sub(r"\s+", " ", city).strip(" ,")
        url = f"{SERVICE}/weather" + (f"?city={urllib.parse.quote(city)}" if city else "")
        with urllib.request.urlopen(url, timeout=45) as r:
            d = json.loads(r.read())
        n = d["now"]
        lines = [f"Weather in {d['location']}: {n['desc']}, {n['temp_f']}F "
                 f"(feels {n['feels_like_f']}F), humidity {n['humidity']}%, "
                 f"wind {n['wind_mph']} mph"]
        for f in d["forecast"]:
            lines.append(f"  {f['date']}: {f['min_f']}-{f['max_f']}F, {f['desc']} "
                         f"(rain {f['rain_chance']}%)")
        return "\n".join(lines)
    except Exception as e:
        return f"Weather lookup failed: {e}"

def web_reply(q):
    """Web search via the filetools /websearch endpoint (SearXNG).
    When an LLM backend is reachable (LOQ 14B preferred), synthesize an
    answer from the results; otherwise fall back to the raw link list."""
    try:
        with urllib.request.urlopen(
                f"{SERVICE}/websearch?q={urllib.parse.quote(q)}&k=5", timeout=45) as r:
            results = json.loads(r.read())["results"]
        if not results:
            return f"No web results for '{q}'."
        lines = []
        for i, r in enumerate(results, 1):
            lines.append(f"{i}. {r['title']}\n   {r['url']}\n   {r['snippet'][:150]}")
        link_list = ("\n".join(lines) +
                     "\n(ask me to summarize any of these, or use /web for more)")
        backend = pick_research_backend()
        if backend:
            base, model, name = backend
            context = "\n".join(
                f"[{i}] {r['title']}: {r['snippet'][:300]} ({r['url']})"
                for i, r in enumerate(results, 1))
            prompt = (f"Web search results for the question: {q}\n\n{context}\n\n"
                      "Write a concise, direct answer to the question using these "
                      "results. Cite sources as [1], [2] where used. If the results "
                      "do not answer the question, say so plainly. Keep it under "
                      "120 words. Do not invent facts.")
            try:
                answer = ask_llm(base, model, prompt)
                if answer:
                    src = "\n".join(f"{i}. {r['url']}" for i, r in enumerate(results, 1))
                    return f"{answer}\n\nSources:\n{src}"
            except Exception as e:
                log(f"research synthesis failed on {name}: {e}")
        return link_list
    except Exception as e:
        return f"Web search failed: {e}"

def _immich_api_key():
    return os.environ.get("IMMICH_API_KEY", "").strip() or None

def immich_photo_search(q, k=5):
    """Semantic photo search via Immich REST (/api/search/smart)."""
    key = _immich_api_key()
    if not key:
        return None  # signal caller: not configured
    try:
        payload = json.dumps({"query": q, "size": k}).encode()
        req = urllib.request.Request(
            IMMICH_URL.rstrip("/") + "/api/search/smart",
            data=payload, method="POST",
            headers={"x-api-key": key, "Content-Type": "application/json",
                     "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            body = json.loads(r.read())
        items = []
        assets = body.get("assets") if isinstance(body, dict) else None
        if isinstance(assets, dict):
            items = assets.get("items") or []
        elif isinstance(assets, list):
            items = assets
        elif isinstance(body, dict) and isinstance(body.get("items"), list):
            items = body["items"]
        out = []
        for a in items[:k]:
            name = a.get("originalFileName") or a.get("originalPath") or a.get("id") or "photo"
            taken = (a.get("localDateTime") or a.get("fileCreatedAt")
                     or a.get("exifInfo", {}).get("dateTimeOriginal") or "")
            out.append({"path": name, "taken": str(taken), "id": a.get("id")})
        return out
    except Exception as e:
        log(f"immich photo search failed: {e}")
        return []

def photo_search(q, k=5):
    """Prefer Immich-native search; fall back to LOQ vision service."""
    imm = immich_photo_search(q, k=k)
    if imm is not None:
        return imm
    base = os.environ.get("PHOTO_SEARCH_URL", "").rstrip("/")
    if not base:
        return []
    try:
        req = urllib.request.Request(
            base + "/photos/search?q=" + urllib.parse.quote(q),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read()).get("results", [])
    except Exception as e:
        log(f"photo search failed: {e}")
        return []

def photos_reply(q):
    """Format Immich/LOQ photo search for Signal."""
    q = (q or "").strip()
    if not q:
        return "Usage: /photos <query> - e.g. /photos dog at the park"
    if _immich_api_key() is None:
        return ("Immich API key not configured yet. Create one in the Immich UI "
                "(Account Settings -> API Keys) and set IMMICH_API_KEY in the "
                "bot's environment.")
    photos = photo_search(q)
    if not photos:
        return f"No Immich photos matched '{q}'."
    lines = [f"Photos matching '{q}':"]
    for p in photos[:5]:
        taken = (p.get("taken") or "")[:10]
        name = (p.get("path") or "photo").split("/")[-1]
        lines.append(f"  {name}" + (f" ({taken})" if taken else ""))
    return "\n".join(lines)

def syncthing_status():
    """Pixel connection + folder health via syncthing-check.py / Syncthing REST."""
    script = os.path.expanduser("~/.local/bin/syncthing-check.py")
    try:
        out = subprocess.check_output(
            ["/usr/local/bin/python3", script],
            stderr=subprocess.STDOUT, timeout=15, text=True)
    except Exception as e:
        try:
            src = open(os.path.expanduser("~/Library/Application Support/Syncthing/config.xml")).read()
            key = re.search(r"<apikey>([^<]+)</apikey>", src).group(1)
            def sget(p):
                return json.load(urllib.request.urlopen(
                    urllib.request.Request("http://127.0.0.1:8384" + p,
                                           headers={"X-API-Key": key}), timeout=8))
            errs = []
            for f in sget("/rest/config/folders"):
                st = sget("/rest/db/status?folder=" + f["id"])
                if st.get("state") in ("error", "unknown") or st.get("invalid"):
                    errs.append("%s: %s%s" % (f.get("label") or f["id"], st.get("state"),
                                 " invalid" if st.get("invalid") else ""))
            conns = sget("/rest/system/connections")["connections"]
            dev = os.environ.get("SYNCTHING_PHONE_DEVICE_ID", "")
            pix = [c for k, c in conns.items() if dev and k.startswith(dev)]
            pixel = "OK" if pix and pix[0].get("connected") else "DISCONNECTED"
            folders = "OK" if not errs else "ERRORS"
            lines = [f"Syncthing: folders={folders}, Pixel={pixel}"]
            for er in errs[:5]:
                lines.append(f"  {er}")
            lines.append(f"(check script note: {e})")
            return "\n".join(lines)
        except Exception as e2:
            return f"Syncthing check failed: {e}; fallback: {e2}"
    folders = "OK"
    pixel = "?"
    errs = []
    for line in out.splitlines():
        if line.startswith("syncthing_folders="):
            folders = line.split("=", 1)[1]
        elif line.startswith("syncthing_pixel="):
            pixel = line.split("=", 1)[1]
        elif line.startswith("syncthing_folder_errors="):
            folders = "ERRORS" if line.endswith("=1") else folders
        elif line.startswith("syncthing_error="):
            errs.append(line.split("=", 1)[1])
    lines = [f"Syncthing: folders={folders}, Pixel={pixel}"]
    for er in errs[:5]:
        lines.append(f"  {er}")
    return "\n".join(lines)

_LOCAL_WHISPER_MODEL = None

def _get_local_whisper():
    global _LOCAL_WHISPER_MODEL
    if _LOCAL_WHISPER_MODEL is None:
        from faster_whisper import WhisperModel
        _LOCAL_WHISPER_MODEL = WhisperModel("base.en", device="cpu", compute_type="int8")
    return _LOCAL_WHISPER_MODEL

def transcribe_audio(data):
    """Transcribe audio bytes: LOQ GPU service first, Debby CPU fallback.
    Returns (text, confidence, backend_label)."""
    # tier 1: remote GPU/CPU transcribers in preference order (fast 6s timeout)
    for wurl in WHISPER_SERVICES:
        try:
            req = urllib.request.Request(wurl, data=data,
                                         headers={"Content-Type": "application/octet-stream"})
            with urllib.request.urlopen(req, timeout=6) as r:
                d = json.loads(r.read())
            text = d.get("text", "").strip()
            if text:
                label = wurl.split("//")[1].split(":")[0]
                return text, d.get("confidence", 0), label
        except Exception as e:
            log(f"transcriber {wurl} unreachable ({e})")
    log("all remote transcribers failed - falling back to Debby CPU")
    # tier 2: local CPU on Debby (slower, always available, cached model)
    import tempfile
    model = _get_local_whisper()
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp.write(data)
    tmp.close()
    try:
        segments, _ = model.transcribe(tmp.name, vad_filter=True)
        text = " ".join(s.text.strip() for s in segments).strip()
    finally:
        os.unlink(tmp.name)
    return text, 0, "Debby CPU"

def cal_pass():
    return os.environ.get("CALDAV_PASSWORD") or None

def caldav_put(calendar, filename, body):
    pw = cal_pass()
    req = urllib.request.Request(
        f"{RADICALE}/{CAL_USER}/{calendar}/{filename}", data=body.encode(),
        method="PUT", headers={"Content-Type": "text/calendar"})
    import base64
    token = base64.b64encode(f"{CAL_USER}:{pw}".encode()).decode()
    req.add_header("Authorization", "Basic " + token)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status in (200, 201, 204)
    except Exception as e:
        log(f"caldav_put failed: {e}")
        return False

def caldav_delete(calendar, filename):
    """DELETE a resource from a Radicale collection. Returns True on success."""
    pw = cal_pass()
    req = urllib.request.Request(
        f"{RADICALE}/{CAL_USER}/{calendar}/{filename}", method="DELETE")
    import base64
    token = base64.b64encode(f"{CAL_USER}:{pw}".encode()).decode()
    req.add_header("Authorization", "Basic " + token)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status in (200, 201, 204)
    except Exception as e:
        log(f"caldav_delete failed: {e}")
        return False

def llm_extract_when(base, model, text, kind, history=None):
    """Ask the LLM to turn natural language into a summary + ISO start date."""
    import datetime
    today = datetime.date.today().isoformat()
    prompt = (
        f"Today is {today} (timezone America/New_York). The user wants to "
        f"add a {'calendar event' if kind == 'EVENT' else 'task'}. Extract from "
        f"their message a short summary and the date. Reply with ONLY a JSON "
        f"object: {{\"summary\": \"...\", \"date\": \"YYYY-MM-DD\", "
        f"\"time\": \"HH:MM\", \"end_time\": \"HH:MM\"}}. Use 24h time; if no "
        f"time given use {'19:00' if kind == 'EVENT' else '08:00'}; if no end "
        f"time given, use one hour after the start. If no date given use one "
        f"week from today. If the message refers to something mentioned "
        f"earlier in the conversation, use that context. No explanation.\n"
        f"Message: " + text)
    try:
        raw = ask_llm(base, model, prompt, history=history)
        m = re.search(r"\{.*\}", raw, re.S)
        d = json.loads(m.group(0))
        return (d.get("summary", text)[:80], d.get("date", ""),
                d.get("time", ""), d.get("end_time", ""))
    except Exception as e:
        log(f"when-extract failed: {e}")
        return text[:80], "", "", ""

def add_calendar_item(kind, text, history=None):
    """kind: EVENT or TASK. Returns confirmation string."""
    import datetime, uuid
    fast = fast_parse_when(text)
    if fast:
        start, summary = fast
        end = start + datetime.timedelta(hours=1)
        hm, hme = start.strftime("%H:%M"), end.strftime("%H:%M")
        date = start.strftime("%Y-%m-%d")
    else:
        backend = pick_extract_backend()
        if not backend:
            return "No LLM backend reachable to parse the date."
        base, model, name = backend
        tl = text.lower()
        if "tomorrow" in tl and date:
            date = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
        elif re.search(r"\b(today|tonight)\b", tl) and date:
            date = datetime.date.today().isoformat()
        if re.search(r"\bnoon\b", tl):
            hm, hme = "12:00", "13:00"
        summary, date, hm, hme = llm_extract_when(base, model, text, kind, history=history)
    summary, _c, _amb = contact_attach(summary)
    if _amb:
        return _contact_ambiguity(_amb)
    try:
        start = datetime.datetime.fromisoformat(f"{date}T{hm or '12:00'}")
    except Exception:
        start = datetime.datetime.now() + datetime.timedelta(days=7)
        summary = summary or text[:60]
    try:
        end = datetime.datetime.fromisoformat(f"{date}T{hme}")
        if end <= start:
            end = start + datetime.timedelta(hours=1)
    except Exception:
        end = start + datetime.timedelta(hours=1)
    uid = f"{kind.lower()}-{uuid.uuid4().hex[:12]}@house"
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if kind == "EVENT":
        body = (f"BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//house//EN\n"
                f"BEGIN:VEVENT\nUID:{uid}\nDTSTAMP:{stamp}\n"
                f"DTSTART;TZID=America/New_York:{start.strftime('%Y%m%dT%H%M%S')}\n"
                f"DTEND;TZID=America/New_York:{end.strftime('%Y%m%dT%H%M%S')}\n"
                f"SUMMARY:{summary}\nEND:VEVENT\nEND:VCALENDAR\n")
        cal, nice = ("house.ics", start.strftime("%a %b %d ") +
                     start.strftime("%H:%M") + "-" + end.strftime("%H:%M"))
    else:
        body = (f"BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//house//EN\n"
                f"BEGIN:VTODO\nUID:{uid}\nDTSTAMP:{stamp}\n"
                f"DUE;TZID=America/New_York:{start.strftime('%Y%m%dT%H%M%S')}\n"
                f"SUMMARY:{summary}\nEND:VTODO\nEND:VCALENDAR\n")
        cal, nice = "tasks.ics", start.strftime("%a %b %d (due %H:%M)")
    if caldav_put(cal, uid + ".ics", body):
        label = "Event" if kind == "EVENT" else "Task"
        return f"{label} added: {summary} — {nice} (syncs to Thunderbird)"
    return "Failed to write to the calendar server."

def relaxed_search(query):
    """Progressive filename search: full phrase, then digits-only, then words."""
    q = clean_query(query)
    seen, collected = set(), []
    variants = [q]
    digits = re.sub(r"\D", "", query)
    if digits and digits not in variants:
        variants.append(digits)
    for w in (q.split() if q else []):
        if len(w) > 2 and w not in variants:
            variants.append(w)
    for v in variants:
        if not v:
            continue
        for p in find_file_paths(v, 5):
            if p not in seen:
                seen.add(p)
                collected.append(p)
        if len(collected) >= 5:
            break
    return collected[:8]

def semantic_fallback(query):
    """Meaning-based search via the filetools /semantic endpoint."""
    try:
        with urllib.request.urlopen(
                f"{SERVICE}/semantic?q={urllib.parse.quote(query)}&k=3",
                timeout=120) as r:
            results = json.loads(r.read())["results"]
        if not results:
            return None
        lines = [f"  {r['score']:.2f}  {r['path']}" for r in results]
        return "\n".join(lines)
    except Exception:
        return None

def summarize_file(query):
    """Find best file, extract text via service, LLM-summarize it."""
    paths = find_file_paths(query, 1)
    if not paths:
        return f"No file matching '{query}' to summarize."
    path = paths[0]
    try:
        with urllib.request.urlopen(
                f"{SERVICE}/extract?path={urllib.parse.quote(path)}&max=8000",
                timeout=90) as r:
            text = json.loads(r.read())["text"].strip()
    except Exception as e:
        return f"Could not extract text from {path}: {e}"
    if not text:
        return (f"Found {path} but got no readable text from it "
                f"(scan/image or unsupported type). Paperless-ngx would fix that.")
    backend = pick_backend(big_mode=False)
    if not backend:
        return "Found the file but no LLM backend is up to summarize it."
    base, model, name = backend
    summary = ask_llm(base, model,
                      f"Summarize this document in under 120 words:\n\n{text}")
    return f"Summary of {os.path.basename(path)}:\n{summary}"

STOPWORDS = re.compile(
    r"\b(where|is|are|was|my|the|a|an|do|i|have|any|find|locate|search|for|"
    r"please|can|you|tell|me|about|look|up|question|file)\b")

def text_to_wav(text):
    """Synthesize speech to a WAV file using local Wyoming Piper container."""
    import socket, wave, tempfile
    try:
        s = socket.socket()
        s.settimeout(10)
        s.connect(WYOMING_PIPER_ADDR)
        clean = re.sub(r'[*_#`~]', '', text)
        clean = re.sub(r'https?://\S+', '', clean)
        clean = re.sub(r'\[.*?\]\(.*?\)', '', clean)
        clean = re.sub(r'\s+', ' ', clean).strip()[:1200]
        if not clean:
            return None
        data = json.dumps({'text': clean}).encode('utf-8')
        header = json.dumps({'type': 'synthesize', 'data_length': len(data)}) + '\n'
        s.sendall(header.encode('utf-8') + data)

        f = s.makefile('rb')
        params = None
        chunks = []
        while True:
            line = f.readline()
            if not line: break
            evt = json.loads(line)
            dlen = evt.get('data_length', 0)
            plen = evt.get('payload_length', 0)
            d = f.read(dlen) if dlen else b''
            p = f.read(plen) if plen else b''
            if evt['type'] == 'audio-start':
                params = json.loads(d.decode('utf-8'))
            elif evt['type'] == 'audio-chunk' and p:
                chunks.append(p)
            elif evt['type'] == 'audio-stop':
                break
        s.close()
        if not params or not chunks:
            return None
        fd, out_path = tempfile.mkstemp(prefix="voice_reply_", suffix=".wav", dir="/tmp")
        os.close(fd)
        with wave.open(out_path, 'wb') as wf:
            wf.setnchannels(params['channels'])
            wf.setsampwidth(params['width'])
            wf.setframerate(params['rate'])
            wf.writeframes(b''.join(chunks))
        return out_path
    except Exception as e:
        log(f"piper tts synthesis failed: {e}")
        return None

def paperless_upload(file_path, title=None):
    """Upload document/receipt/image to Paperless-ngx via REST API."""
    try:
        tok = _paperless_token()
    except Exception:
        return "Paperless token not configured."
    import mimetypes
    boundary = "----WebKitFormBoundary" + hex(int(time.time() * 1000))[2:]
    filename = os.path.basename(file_path)
    ctype = mimetypes.guess_type(file_path)[0] or "application/octet-stream"
    try:
        with open(file_path, "rb") as f:
            file_bytes = f.read()

        body = bytearray()
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(f'Content-Disposition: form-data; name="document"; filename="{filename}"\r\n'.encode())
        body.extend(f'Content-Type: {ctype}\r\n\r\n'.encode())
        body.extend(file_bytes)
        body.extend(b"\r\n")

        if title:
            body.extend(f"--{boundary}\r\n".encode())
            body.extend(f'Content-Disposition: form-data; name="title"\r\n\r\n'.encode())
            body.extend(f"{title}\r\n".encode())

        body.extend(f"--{boundary}--\r\n".encode())

        url = f"{PAPERLESS}/api/documents/post_document/"
        req = urllib.request.Request(
            url, data=bytes(body),
            headers={
                "Authorization": "Token " + tok,
                "Content-Type": f"multipart/form-data; boundary={boundary}"
            }
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            task_id = r.read().decode().strip('"')
            return f"Uploaded '{filename}' to Paperless-ngx. It is queued for OCR and indexing (task: {task_id[:8]}...)."
    except Exception as e:
        log(f"paperless upload failed: {e}")
        return f"Paperless upload failed: {e}"

HOUSE_ADDED_VCF = "/tank/data/house/house-added.vcf"

def paperless_ocr_text(file_path, title=None, wait=90):
    """Upload an image/PDF to Paperless and return its OCR text once indexed."""
    try:
        tok = _paperless_token()
    except Exception:
        return None
    import mimetypes
    boundary = "----WebKitFormBoundary" + hex(int(time.time() * 1000))[2:]
    filename = os.path.basename(file_path)
    ctype = mimetypes.guess_type(file_path)[0] or "application/octet-stream"
    try:
        with open(file_path, "rb") as f:
            file_bytes = f.read()
        body = bytearray()
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(f'Content-Disposition: form-data; name="document"; filename="{filename}"\r\n'.encode())
        body.extend(f'Content-Type: {ctype}\r\n\r\n'.encode())
        body.extend(file_bytes)
        body.extend(b"\r\n")
        if title:
            body.extend(f"--{boundary}\r\n".encode())
            body.extend('Content-Disposition: form-data; name="title"\r\n\r\n'.encode())
            body.extend(f"{title}\r\n".encode())
        body.extend(f"--{boundary}--\r\n".encode())
        req = urllib.request.Request(
            f"{PAPERLESS}/api/documents/post_document/", data=bytes(body),
            headers={"Authorization": "Token " + tok,
                     "Content-Type": f"multipart/form-data; boundary={boundary}"})
        with urllib.request.urlopen(req, timeout=30) as r:
            doc_ids = json.loads(r.read())
        doc_id = doc_ids[0] if isinstance(doc_ids, list) and doc_ids else None
        if not doc_id:
            return None
        deadline = time.time() + wait
        while time.time() < deadline:
            time.sleep(4)
            req = urllib.request.Request(
                f"{PAPERLESS}/api/documents/{doc_id}/",
                headers={"Authorization": "Token " + tok})
            with urllib.request.urlopen(req, timeout=30) as r:
                full = json.loads(r.read())
            content = (full.get("content") or "").strip()
            if content:
                return content[:4000]
        return None
    except Exception as e:
        log(f"paperless ocr failed: {e}")
        return None

def contact_extract_fields(text):
    """LLM: business-card / dictated text -> structured contact fields."""
    backend = pick_extract_backend()
    if not backend:
        return None
    base, model, _name = backend
    prompt = ("Extract contact information from the text below (a business card or "
              "dictated contact). Reply with ONLY JSON: "
              '{"name": "", "organization": "", "title": "", "phones": [], '
              '"emails": [], "website": ""} - use empty values when absent.\nText:\n'
              + text[:3000])
    try:
        raw = ask_llm(base, model, prompt)
        d = json.loads(re.search(r"\{.*\}", raw, re.S).group(0))
        return d or None
    except Exception as e:
        log(f"contact extract failed: {e}")
        return None

def _vcf_escape(s):
    return (s or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")

def append_house_contact(fields, source_note=""):
    """Append a vCard to the house additions ledger; returns a reply string or None."""
    name = (fields.get("name") or "").strip()
    if not name:
        return None
    phones = [str(p).strip() for p in (fields.get("phones") or []) if str(p).strip()]
    emails = [str(e).strip() for e in (fields.get("emails") or []) if str(e).strip()]
    if not phones and not emails:
        return None
    try:
        existing = open(HOUSE_ADDED_VCF).read() if os.path.exists(HOUSE_ADDED_VCF) else ""
    except Exception:
        existing = ""
    digits_key = re.sub(r"\D", "", phones[0]) if phones else ""
    if digits_key and len(digits_key) >= 7:
        if digits_key in re.sub(r"\D", "", existing):
            return f"{name} looks like it's already in the house contacts."
        master_path = os.environ.get("HOUSE_CONTACTS_JSON", "/tank/data/house/contacts.json")
        try:
            master = open(master_path).read()
        except Exception:
            master = ""
        if digits_key in re.sub(r"\D", "", master):
            return (f"{name} is already in your contacts (same number). If the card has "
                    "newer details, update them in Google Contacts and it syncs over.")
    bits = name.split()
    last = bits[-1] if len(bits) > 1 else name
    first = " ".join(bits[:-1]) if len(bits) > 1 else ""
    lines = ["BEGIN:VCARD", "VERSION:3.0",
             f"N:{_vcf_escape(last)};{_vcf_escape(first)};;;",
             f"FN:{_vcf_escape(name)}"]
    org = (fields.get("organization") or "").strip()
    title = (fields.get("title") or "").strip()
    if org:
        lines.append(f"ORG:{_vcf_escape(org)}")
    if title:
        lines.append(f"TITLE:{_vcf_escape(title)}")
    for p in phones:
        lines.append(f"TEL;TYPE=CELL:{p}")
    for e in emails:
        lines.append(f"EMAIL:{e}")
    site = (fields.get("website") or "").strip()
    if site:
        lines.append(f"URL:{site}")
    lines.append(f"NOTE:Added via House bot{(' - ' + source_note) if source_note else ''}")
    lines.append("END:VCARD")
    try:
        with open(HOUSE_ADDED_VCF, "a") as f:
            f.write("\r\n".join(lines) + "\r\n")
    except Exception as e:
        log(f"house contact append failed: {e}")
        return None
    # best-effort immediate refresh on archbox (silently skipped if it's asleep)
    try:
        subprocess.run(
            ["ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes",
             "archbox", os.path.expanduser("~/.local/bin/housecontacts.py"), "build"],
            timeout=25, capture_output=True)
    except Exception:
        pass
    parts = [name]
    if org:
        parts.append(org + (f" - {title}" if title else ""))
    for p in phones:
        parts.append("cell " + p)
    for e in emails:
        parts.append(e)
    return "\U0001F4C7 Saved " + " \u00b7 ".join(parts) + \
           " - merges into contacts within 2h (faster if archbox is awake)."

BUSINESS_CARDS_DIR = "/tank/data/Obsidian Vault/Business Cards"

def archive_business_card(card_path, fields, dup_note=""):
    """Copy the card image into the Obsidian vault with a searchable sidecar note."""
    try:
        os.makedirs(BUSINESS_CARDS_DIR, exist_ok=True)
        name = (fields.get("name") or "card").strip() or "card"
        stamp = time.strftime("%Y-%m-%d")
        slug = re.sub(r"[^A-Za-z0-9 ]", "", name).strip() or "card"
        base = f"{stamp} {slug}"
        ext = os.path.splitext(card_path)[1].lower() or ".jpg"
        img = os.path.join(BUSINESS_CARDS_DIR, base + ext)
        n = 2
        while os.path.exists(img):
            img = os.path.join(BUSINESS_CARDS_DIR, f"{base} {n}{ext}")
            n += 1
        shutil.copy2(card_path, img)
        stem = os.path.splitext(os.path.basename(img))[0]
        md = os.path.join(BUSINESS_CARDS_DIR, stem + ".md")
        if not os.path.exists(md):
            lines = ["---", "tags:", "  - business-card", "  - contact",
                     f"  created: {time.strftime('%Y-%m-%d %H:%M')}"]
            if (fields.get("name") or "").strip():
                lines.append(f"  name: {fields['name'].strip()}")
            if (fields.get("organization") or "").strip():
                lines.append(f"  org: {fields['organization'].strip()}")
            lines += ["---", "", f"# {(fields.get('name') or 'Business card').strip()}", "",
                      f"![[{os.path.basename(img)}]]", ""]
            if (fields.get("organization") or "").strip():
                lines.append(f"- Org: {fields['organization'].strip()}")
            if (fields.get("title") or "").strip():
                lines.append(f"- Title: {fields['title'].strip()}")
            for p in fields.get("phones") or []:
                lines.append(f"- Phone: {p}")
            for e in fields.get("emails") or []:
                lines.append(f"- Email: {e}")
            if (fields.get("website") or "").strip():
                lines.append(f"- Web: {fields['website'].strip()}")
            lines.append("- Added: via House bot" + (f" ({dup_note})" if dup_note else ""))
            with open(md, "w") as f:
                f.write("\n".join(lines) + "\n")
        return os.path.basename(img)
    except Exception as e:
        log(f"business card archive failed: {e}")
        return None

def contact_reply(text, card_path=None):
    """CONTACT intent: business-card photo (via Paperless OCR) or dictated text."""
    if not text and not card_path:
        return ("Send a business card photo with 'new contact' in the message, or: "
                "add contact Jane Doe 555-0100 jane@example.com")
    ocr_text = None
    if card_path:
        ocr_text = paperless_ocr_text(card_path, title="business card" if not text else None)
        if not ocr_text:
            return ("Couldn't read that card - Paperless OCR came back empty. "
                    "Try a closer, flatter photo, or type the details instead.")
    fields = contact_extract_fields(((ocr_text + "\n") if ocr_text else "") + (text or ""))
    if not fields or not (fields.get("name") or "").strip():
        return "I couldn't pull a name out of that. Try: 'add contact Jane Doe 555-0100 jane@example.com'."
    reply = append_house_contact(fields, source_note="business card" if card_path else "dictated")
    if card_path:
        archived = archive_business_card(
            card_path, fields,
            dup_note="duplicate" if reply and "already" in reply else "")
        if archived:
            base_reply = reply or "Filed the card, but there was no phone or email on it to save."
            reply = base_reply + f"\n🗂️ Card archived to Obsidian: Business Cards/{archived}"
    return reply or "I found a name but no phone or email to save."

_FIELDS_RE = re.compile(r"\b(phone|mobile|cell|number|e-?mail|address|contact info|reach)\b", re.I)

def _asked_field(text):
    t = (text or "").lower()
    if re.search(r"\be-?mail\b", t):
        return "email"
    if re.search(r"\b(phone|mobile|cell|number)\b", t):
        return "phone"
    if re.search(r"\baddress\b", t):
        return "address"
    return "all"

_QUESTION_WORDS = {"what", "whats", "who", "whos", "which", "is", "are", "the", "a", "an",
                   "tell", "give", "show", "me", "do", "does", "you", "know", "have",
                   "find", "get", "her", "his", "their", "them", "him", "she", "he",
                   "they", "it", "that", "this", "there", "my", "our"}
_FIELD_TAILS = {"number", "email", "phone", "address", "cell", "mobile", "info", "details"}

def _clean_name(s):
    toks = (s or "").split()
    while toks and toks[0].lower().strip(".,?'’") in _QUESTION_WORDS:
        toks.pop(0)
    while toks and toks[-1].lower().strip(".,?") in _FIELD_TAILS:
        toks.pop()
    if toks:  # strip possessive 's ("patel's" -> "patel") or it becomes a phantom token
        toks[-1] = re.sub(r"['’]s?$", "", toks[-1])
        if not toks[-1]:
            toks.pop()
    return " ".join(toks).strip() or None

def _name_from_question(text):
    """Pull a person's name out of 'what is jordan lee's phone number',
    'who is sam patel', 'email for chris doe' etc. None if only pronouns."""
    t = text or ""
    m = re.search(r"([A-Za-z][\w'’.-]+(?:\s+[A-Za-z][\w'’.-]+){0,3})'?(?:s)?\s+"
                  r"(?:phone|mobile|cell|number|e-?mail|address|info|details)", t, re.I)
    if m:
        return _clean_name(m.group(1))
    m = re.search(r"\b(?:phone|mobile|cell|number|e-?mail|address|info|details)\s+"
                  r"(?:number\s+|address\s+)?(?:for|of)\s+((?:[A-Za-z][\w'’.-]+\s*){1,4})", t, re.I)
    if m:
        return _clean_name(m.group(1))
    m = re.search(r"\bwho(?:'s|\s+is|\s+are)\s+((?:[A-Za-z][\w'’.-]+\s*){1,4})", t, re.I)
    if m:
        return _clean_name(m.group(1))
    return None

def _name_from_history(hist):
    """Follow-up support: 'what is her email' after 'what is jordan's number'."""
    for msg in reversed(hist or []):
        if not isinstance(msg, dict) or msg.get("role") != "user":
            continue
        content = msg.get("content") or ""
        name = _name_from_question(content) or contact_extract_target(content)
        if not name:
            continue
        c, _k, _a = contact_find(name)
        if c:
            return c
    return None

def contact_info_reply(text, hist=None):
    """CONTACT_INFO intent: look up a person's phone/email from contacts.json."""
    asked = _asked_field(text)
    name = _name_from_question(text)
    if name:
        contact, _kind, amb = contact_find(name)
        if amb:
            return "Which one? " + " \u00b7 ".join(
                c["name"] + (f" ({c['org']})" if c.get("org") else "") for c in amb)
        if contact is None:
            return f"I couldn't find {name!r} in your contacts."
    else:
        contact = _name_from_history(hist)
        if contact is None:
            return ("I couldn't tell who you mean - try the name, like "
                    "'what is jordan lee's phone number'.")
    label = contact["name"]
    if asked == "email":
        emails = contact.get("emails") or []
        return f"{label}'s email: " + (", ".join(emails) if emails else "none on file")
    if asked == "phone":
        parts = [f"{p.get('type', 'phone')}: {p['number']}" for p in contact.get("phones") or []]
        return f"{label}'s number: " + (" \u00b7 ".join(parts) if parts else "none on file")
    if asked == "address":
        return f"I only keep names, orgs, phones and emails - no street address for {label}."
    return "\U0001F4C7 " + (contact_format(contact) or label)

def ha_call_service(domain, service, service_data=None):
    """Execute a service call in Home Assistant."""
    try:
        url = f"{HA_URL}/api/services/{domain}/{service}"
        data = json.dumps(service_data or {}).encode()
        req = urllib.request.Request(
            url, data=data,
            headers={"Authorization": "Bearer " + ha_token(), "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=8) as r:
            return json.loads(r.read())
    except Exception as e:
        log(f"ha service call {domain}.{service} failed: {e}")
        return None

def ha_battery_report():
    """Report phone, vacuum, and sensor battery levels."""
    try:
        states = ha_states()
        lines = ["**Battery Levels**:"]
        for s in states:
            eid = s["entity_id"]
            fn = s.get("attributes", {}).get("friendly_name", eid)
            val = s.get("state", "")
            if "battery_level" in eid or (HA_VACUUM_BATTERY_ENTITY and eid == HA_VACUUM_BATTERY_ENTITY):
                name = fn.replace(" Battery level", "").replace(" Battery Level", "").replace(" Battery", "").strip()
                state_eid = eid.replace("_level", "_state")
                st_sensor = next((x.get("state") for x in states if x["entity_id"] == state_eid), None)
                charging_str = f" ({st_sensor})" if st_sensor and st_sensor not in ("unknown", "unavailable") else ""
                lines.append(f"- **{name}**: {val}%{charging_str}")
        return "\n".join(lines) if len(lines) > 1 else "No battery sensors found in Home Assistant."
    except Exception as e:
        return f"Could not retrieve battery levels: {e}"

def ha_low_batteries(threshold=20):
    """Return list of device names with battery level <= threshold."""
    out = []
    try:
        states = ha_states()
        for s in states:
            eid = s["entity_id"]
            fn = s.get("attributes", {}).get("friendly_name", eid)
            val = s.get("state", "")
            if "battery_level" in eid or (HA_VACUUM_BATTERY_ENTITY and eid == HA_VACUUM_BATTERY_ENTITY):
                try:
                    num = float(val)
                    if num <= threshold:
                        name = fn.replace(" Battery level", "").replace(" Battery Level", "").replace(" Battery", "").strip()
                        out.append(f"{name} ({int(num)}%)")
                except Exception:
                    pass
    except Exception:
        pass
    return out

def ha_family_report():
    """Report location of all tracked family members."""
    try:
        states = ha_states()
        lines = ["**Family Location Status**:"]
        for s in states:
            if s["entity_id"].startswith("person."):
                name = s.get("attributes", {}).get("friendly_name", s["entity_id"].split(".")[1].capitalize())
                loc = s.get("state", "unknown")
                if loc == "home":
                    loc_desc = "At home"
                elif loc == "not_home":
                    loc_desc = "Away"
                else:
                    loc_desc = f"At {loc}"
                lines.append(f"- **{name}**: {loc_desc}")
        return "\n".join(lines) if len(lines) > 1 else "No family members found."
    except Exception as e:
        return f"Could not retrieve family status: {e}"

def ha_vacuum_cmd(action):
    """Control Rosie the vacuum."""
    entity = HA_VACUUM_ENTITY
    if not entity:
        return "No vacuum configured (set HA_VACUUM_ENTITY)."
    if action == "start":
        res = ha_call_service("vacuum", "start", {"entity_id": entity})
        return "Started Rosie the vacuum." if res is not None else "Failed to start vacuum."
    elif action == "dock":
        res = ha_call_service("vacuum", "return_to_base", {"entity_id": entity})
        return "Sent Rosie back to the dock." if res is not None else "Failed to dock vacuum."
    else:
        s = ha_entity(entity)
        if not s:
            return "Could not get Rosie's status."
        state = s.get("state", "unknown")
        bat = s.get("attributes", {}).get("battery_level", "?")
        return f"Rosie is currently **{state}** (battery: {bat}%)."

def expense_log(text):
    """Log an expense into Obsidian Vault/Finances/2026 Expenses.md."""
    import datetime
    t = text.strip()
    m_exp = re.search(r'(?:spent|paid|bought|expense)?\s*(?:\$|dollars?\s*)?(\d+(?:\.\d{1,2})?)\s+(?:at|for|on)\s+(.+)', t, re.I)
    if not m_exp:
        return "Could not parse expense amount and vendor. Example: 'Spent $42.50 at Shell for gas'."
    amount = float(m_exp.group(1))
    vendor = m_exp.group(2).strip(" .,-")
    now = datetime.datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M")

    os.makedirs(FINANCES_DIR, exist_ok=True)
    if not os.path.exists(EXPENSES_NOTE):
        with open(EXPENSES_NOTE, "w") as f:
            f.write("# 2026 Expenses\n\n| Date | Amount | Vendor / Description | Time |\n| :--- | :--- | :--- | :--- |\n")
    with open(EXPENSES_NOTE, "a") as f:
        f.write(f"| {date_str} | ${amount:.2f} | {vendor} | {time_str} |\n")
    return f"Logged expense: **${amount:.2f}** for **{vendor}**. Saved to Obsidian (Finances)."

def mileage_log(text):
    """Log mileage into Obsidian Vault/Finances/2026 Mileage.md."""
    import datetime
    t = text.strip()
    m_mil = re.search(r'(?:log(?:ged)?|drove|mileage)?\s*(\d+(?:\.\d+)?)\s*miles?\s*(?:for|to)?\s*(.*)', t, re.I)
    if not m_mil:
        return "Could not parse mileage. Example: 'Log 35 miles for client meeting'."
    miles = float(m_mil.group(1))
    purpose = m_mil.group(2).strip(" .,-") or "trip"
    now = datetime.datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M")

    os.makedirs(FINANCES_DIR, exist_ok=True)
    if not os.path.exists(MILEAGE_NOTE):
        with open(MILEAGE_NOTE, "w") as f:
            f.write("# 2026 Mileage Log\n\n| Date | Miles | Purpose | Time |\n| :--- | :--- | :--- | :--- |\n")
    with open(MILEAGE_NOTE, "a") as f:
        f.write(f"| {date_str} | {miles:.1f} | {purpose} | {time_str} |\n")
    return f"Logged **{miles:.1f} miles** ({purpose}). Saved to Obsidian (Finances)."

def read_and_save_url(url_text):
    """Fetch article from URL, summarize it, and save to Obsidian Reading List."""
    import datetime, html
    m = re.search(r'https?://[^\s<>"]+', url_text)
    if not m:
        return "No valid URL found."
    url = m.group(0)
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (PersonalAssistant/1.0)'})
        with urllib.request.urlopen(req, timeout=15) as r:
            raw_html = r.read().decode('utf-8', errors='replace')
    except Exception as e:
        return f"Could not read article from {url}: {e}"

    title_m = re.search(r'<title[^>]*>(.*?)</title>', raw_html, re.I | re.S)
    title = html.unescape(title_m.group(1).strip()) if title_m else 'Web Article'
    title = re.sub(r'[\r\n\t]+', ' ', title).strip()
    clean_title = re.sub(r'[^\w\s-]', '', title)[:60].strip() or "Article"

    clean = re.sub(r'<(script|style|nav|footer|header|aside|noscript)[^>]*>.*?</\1>', ' ', raw_html, flags=re.I | re.S)
    clean = re.sub(r'<[^>]+>', ' ', clean)
    clean = html.unescape(clean)
    clean = re.sub(r'\s+', ' ', clean).strip()

    b = pick_fast_backend() or pick_backend(False)
    summary = ""
    if b:
        base, model, bname = b
        prompt = (f"Summarize this web article in 3 crisp bullet points highlighting key insights.\n"
                  f"Title: {title}\nContent:\n{clean[:4000]}")
        try:
            summary = ask_llm(base, model, prompt)
        except Exception as e:
            summary = f"(Summary generation failed: {e})"
    if not summary:
        summary = "- " + clean[:300] + "..."

    now = datetime.datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    filename = f"{date_str} {clean_title}.md"
    os.makedirs(READING_LIST_DIR, exist_ok=True)
    note_path = os.path.join(READING_LIST_DIR, filename)

    content_lines = [
        f"# {title}",
        f"- **Source**: {url}",
        f"- **Saved**: {date_str} {now.strftime('%H:%M')}",
        "",
        "## Summary",
        summary.strip(),
        "",
        "## Full Text",
        clean[:10000]
    ]
    with open(note_path, "w", encoding="utf-8") as f:
        f.write("\n".join(content_lines) + "\n")

    return f"**{title}**\n\n{summary.strip()}\n\n*Saved full article to Obsidian (Reading List/{filename})*"

def maintenance_status():
    """Report overdue and upcoming home appliance and filter maintenance."""
    import datetime
    if not os.path.exists(CHORE_INTERVALS_FILE):
        return "No maintenance intervals configured."
    with open(CHORE_INTERVALS_FILE) as f:
        data = json.load(f)
    now = datetime.datetime.now()
    overdue = []
    upcoming = []
    good = []
    for key, info in data.items():
        task = info.get('task', key)
        cycle = info.get('days', 90)
        last_str = info.get('last', '')
        if not last_str:
            overdue.append(f"- **{task}**: never logged (cycle: {cycle}d)")
            continue
        try:
            last_dt = datetime.datetime.strptime(last_str, '%Y-%m-%d')
            elapsed = (now - last_dt).days
            remaining = cycle - elapsed
            details = f" *(details: {info.get('details')})*" if info.get('details') else ""
            if remaining < 0:
                overdue.append(f"- **{task}**: OVERDUE by {abs(remaining)} days (last: {last_str}, cycle: {cycle}d){details}")
            elif remaining <= 14:
                upcoming.append(f"- **{task}**: due in {remaining} days (last: {last_str}, cycle: {cycle}d){details}")
            else:
                good.append(f"- **{task}**: due in {remaining} days (last: {last_str}){details}")
        except Exception:
            pass
    lines = ["**Home Maintenance & Filter Status**:"]
    if overdue:
        lines.append("\n*Overdue*:")
        lines.extend(overdue)
    if upcoming:
        lines.append("\n*Due Soon (Next 14 Days)*:")
        lines.extend(upcoming)
    if good:
        lines.append("\n*Up to Date*:")
        lines.extend(good)
    return "\n".join(lines)

def chore_upcoming_list(days_ahead=7):
    """[str] for chores due within days_ahead days."""
    import datetime
    out = []
    now = datetime.datetime.now()
    for task, info in _load_json(CHORE_INTERVALS_FILE, {}).items():
        days = _days_between(info.get("last", ""), now)
        if days is None: continue
        remaining = info.get("days", 9999) - days
        if 0 <= remaining <= days_ahead:
            out.append(f"{info.get('task', task)} (due in {remaining}d - last {days}d ago)")
    return out

def server_status():
    """ZFS pools, storage, uptime, and node connectivity."""
    import subprocess, shutil
    lines = ["**Homelab Server Status (darp5)**:"]
    try:
        zout = subprocess.getoutput("zpool list -H -o name,size,alloc,free,cap,health 2>/dev/null")
        if zout:
            lines.append("\n*ZFS Pools*:")
            for row in zout.strip().split("\n"):
                parts = row.split()
                if len(parts) >= 6:
                    lines.append(f"- **{parts[0]}**: {parts[4]} used ({parts[2]}/{parts[1]}), health: {parts[5]}")
    except Exception as e:
        lines.append(f"- ZFS check failed: {e}")

    try:
        du = shutil.disk_usage("/tank")
        used_gb = du.used / (1024**3)
        total_gb = du.total / (1024**3)
        pct = (du.used / du.total) * 100
        lines.append(f"- **Storage (/tank)**: {used_gb:.1f} GB / {total_gb:.1f} GB ({pct:.1f}% used)")
    except Exception as e:
        lines.append(f"- Disk check failed: {e}")

    uptime = subprocess.getoutput("uptime -p 2>/dev/null || uptime")
    lines.append(f"- **Uptime**: {uptime.strip()}")

    nodes = [("homebrain (3B)", "http://127.0.0.1:11434/api/tags")] + [
        (b["name"], re.sub(r"/v1/?$", "", b["url"].rstrip("/")) + "/api/tags")
        for b in LLM_BACKENDS]
    node_stats = []
    for name, url in nodes:
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=1.5) as r:
                node_stats.append(f"{name}: online")
        except Exception:
            node_stats.append(f"{name}: offline")
    lines.append("- **LLM Nodes**: " + ", ".join(node_stats))
    return "\n".join(lines)

def clean_query(s):
    """Strip filler words so filename globs actually match."""
    cleaned = STOPWORDS.sub(" ", s.lower())
    cleaned = re.sub(r"[^a-z0-9_. /-]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned

def _mentions_list(t):
    """True if the text mentions a list (typo-tolerant: llist, lst, ...)."""
    import difflib
    for w in re.findall(r"[a-z]+", t.lower()):
        if difflib.get_close_matches(w, ("list", "checklist"), cutoff=0.8):
            return True
    return False

def _hid_entries():
    """[(item, location, meta)] parsed from the Obsidian note."""
    entries = []
    try:
        with open(HID_NOTE) as f:
            for line in f:
                m = re.match(r"- \*\*(.+?)\*\*\s*→\s*(.+?)(?:\s*\*\((logged|updated) ([^)]+)\)\*)?\s*$",
                             line.strip())
                if m:
                    entries.append((m.group(1).strip(), m.group(2).strip(),
                                    (m.group(3) or "") + " " + (m.group(4) or "") if m.group(3) else None))
    except FileNotFoundError:
        pass
    except Exception as e:
        log(f"hid note read failed: {e}")
    return entries

def _hid_write(entries):
    lines = ["# Where I hid it from Me", ""]
    for item, loc, meta in entries:
        lines.append(f"- **{item}** → {loc}" + (f" *({meta})*" if meta else ""))
    os.makedirs(HID_DIR, exist_ok=True)
    with open(HID_NOTE, "w") as f:
        f.write("\n".join(lines) + "\n")

def _tokens(text):
    stop = {"where", "wheres", "is", "the", "my", "our", "a", "an", "i",
            "did", "do", "does", "put", "hid", "hide", "hidden", "that",
            "remember", "at", "in", "into", "on", "under", "behind", "with",
            "it", "to", "was", "what", "are", "you", "have", "has",
            "and", "or", "of", "for", "off", "add", "list", "new", "create",
            "make", "start", "buy", "get", "grab", "pick", "need", "note",
            "log", "check", "show", "read", "see", "left", "clear", "wipe",
            "everything", "there", "some", "more"}
    out = set()
    for w in re.findall(r"[a-z0-9']+", text.lower()):
        w = w.replace("'", "")
        if w in stop or len(w) <= 2:
            continue
        if w.endswith("s") and len(w) > 3:
            w = w[:-1]
        out.add(w)
    return out

def _hid_match(query_text, entries):
    """Entries whose item tokens overlap the query text."""
    qt = _tokens(query_text)
    out = []
    for item, loc, meta in entries:
        if _tokens(item) & qt:
            out.append((item, loc, meta))
    return out

def _hid_extract(text):
    """(item, location) from natural language. Regex first, LLM fallback."""
    t = re.sub(r"^(update|correction)[:,\s-]*", "", text.strip(), flags=re.I)
    t = re.sub(r"^(?:(?:please|can\s+you|could\s+you)\s+)?remember\b[:,\s-]*(?:that\s+)?", "", t, flags=re.I)
    t = re.sub(r"^(?:(?:make|take|add)\s+a\s+)?note\s+that\s+", "", t, flags=re.I)
    patterns = [
        r"(?:the |my |our |a |an )?(.+?)\s+(?:is|are|was|goes|go|sits|lives)\s+"
        r"(?:in|inside|on|under|at|behind|within|with)\s+(?:the |my |our |a |an )?(.+)",
        r"(?:i|we)\s+(?:just\s+)?(?:put|placed|stored|stashed|hid|hidden|kept|left|moved)\s+"
        r"(?:the |my |our |a |an )?(.+?)\s+(?:in|inside|on|under|at|behind|within|into|with)\s+"
        r"(?:the |my |our |a |an )?(.+)",
        r"^(?:the |my |our |a |an )?(.+?)\s+(?:in|inside|on|under|at|behind|within|into)\s+(?:the |my |our |a |an )?(.+)$",
    ]
    for p in patterns:
        m = re.search(p, t, re.I)
        if m:
            item = m.group(1).strip(" .,-")
            loc = m.group(2).strip(" .,-")
            if item and loc:
                return item, loc
    backend = pick_extract_backend()
    if not backend:
        return None
    base, model, name = backend
    prompt = ('Extract WHAT item was hidden or stored and WHERE it was put. '
              'Reply with ONLY JSON: {"item": "...", "location": "..."}.\n'
              "Message: " + text)
    try:
        raw = ask_llm(base, model, prompt)
        d = json.loads(re.search(r"\{.*\}", raw, re.S).group(0))
        if d.get("item") and d.get("location"):
            return d["item"].strip(), d["location"].strip()
    except Exception as e:
        log(f"hid extract failed: {e}")
    return None

def hid_log(text):
    """Log where something was put. Updates an existing entry when the item
    matches (e.g. 'I moved the passport to my backpack')."""
    import datetime
    parsed = _hid_extract(text)
    if not parsed:
        return ("I could not tell what item and place that was - try "
                "'the X is in Y' or 'I put the X in Y'.")
    item, loc = parsed
    entries = _hid_entries()
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    match_idx = None
    # 1. Exact token match priority
    for i, (eitem, eloc, emeta) in enumerate(entries):
        if _tokens(item) == _tokens(eitem):
            match_idx = i
            break
    # 2. Overlapping token match fallback
    if match_idx is None:
        for i, (eitem, eloc, emeta) in enumerate(entries):
            if _tokens(item) & _tokens(eitem):
                match_idx = i
                break
    if match_idx is not None:
        eitem, eloc, emeta = entries[match_idx]
        entries[match_idx] = (eitem, loc, f"updated {now}")
        _hid_write(entries)
        return (f"Updated - the {eitem} is now in {loc}. "
                f"Saved to Obsidian.")
    entries.append((item, loc, f"logged {now}"))
    _hid_write(entries)
    return (f"Noted - the {item} is in {loc}. "
            f"Saved to Obsidian (Where I hid it from Me).")

def hid_retrieve(text):
    entries = _hid_entries()
    hits = _hid_match(text, entries)
    if not hits:
        return ("Nothing logged for that - if you tell me where it is now, "
                "I will remember it.")
    if len(hits) == 1:
        item, loc, meta = hits[0]
        extra = f" ({meta})" if meta else ""
        return f"The {item} is in {loc}{extra}."
    qt = _tokens(text)
    exact_hits = [h for h in hits if _tokens(h[0]) == qt]
    if len(exact_hits) == 1:
        item, loc, meta = exact_hits[0]
        extra = f" ({meta})" if meta else ""
        return f"The {item} is in {loc}{extra}."
    lines = [f"- the {item} is in {loc}" for item, loc, meta in hits]
    return "I have several entries matching that:\n" + "\n".join(lines)

def hid_clear(text):
    entries = _hid_entries()
    hits = _hid_match(text, entries)
    if not hits:
        return "No matching entries to clear."
    hit_items = {item for item, loc, meta in hits}
    remaining = [e for e in entries if e[0] not in hit_items]
    _hid_write(remaining)
    return ("Cleared: " + ", ".join(f"the {i}" for i, l, m in hits)
            + ". I will no longer track " + ("them" if len(hits) > 1 else "it") + ".")

def hid_reply(text):
    t = text.lower().strip()
    if re.match(r"^(where|whats|what|which)\b", t) or re.search(r"\b(where|what|which)\s+(did|is|are|do i|was|were)\b", t):
        return hid_retrieve(text)
    if re.search(r"\b(clear|forget|delete|remove|scratch)\b", t):
        return hid_clear(text)
    return hid_log(text)


def keyword_intent(user_text):
    """Deterministic keyword heuristics. Checked first for high-confidence
    patterns, and again as a safety net when the LLM guesses CHAT."""
    # Explicit note capture commands (highest priority - imperative capture commands should never be
    # intercepted by casual keyword collisions like 'view', 'google', or 'search' in the text body)
    m_work = re.match(r'^(?:(?:make|add|new|create|save|take)\s+(?:a\s+)?)?work\s+note\b[:\s-]*', user_text.strip(), re.I)
    if m_work:
        body = user_text.strip()[m_work.end():].strip()
        return "WORKNOTE", body

    m_note = re.match(r'^(?:(?:make|take|add|create|save|new)\s+(?:a\s+)?|quick\s+)?note(?:\s+to\s+self)?\b[:\s-]*|^(?:remember\s+(?:a\s+)?note(?:\s+to\s+self)?\b[:\s-]*)', user_text.strip(), re.I)
    if m_note:
        body = user_text.strip()[m_note.end():].strip()
        return "NOTE", body

    t = user_text.lower().strip()

    # recurring chores/reminders: "I water the plants every sunday",
    # "I change the furnace filter every 3 months" (before MEMORY/CHORE/REMIND)
    if re.match(r"^\s*(?:please\s+)?(?:stop|cancel|delete|remove|turn off)\b", t) \
            and re.search(r"\b(?:recurring|remind(?:er|ers|ing)?)\b", t):
        return "RECUR_CANCEL", user_text.strip()
    if _looks_recurring_request(t):
        return "RECUR", user_text.strip()
    if re.match(r"^(?:please\s+)?(?:list|show|what are|what's|whats|tell me)\b.{0,25}\breminders\b", t) \
            or re.match(r"^(?:my\s+)?(?:recurring\s+|upcoming\s+|pending\s+)?reminders\s*\??$", t):
        return "REMINDERS", ""

    if t in ("/net", "/network") or re.search(
            r"\b(tailscale status|is office up|who.?s online)\b", t):
        return "NET", ""
    if t in ("whisper health", "whisper status", "check whisper"):
        return "WHISPER", ""
    if t in ("alerts", "any alerts", "ntfy alerts", "warnings"):
        return "ALERTS", ""
    if not re.search(r"\b(find|search|where)\b", t) and (
            re.search(r"\b(package|packages|parcel|parcels|shipment|shipped)\b", t)
            or re.search(r"\b(delivered|out for delivery)\b", t)
            or re.search(r"\bdelivery (arrived|from|of|is)\b", t)):
        return "PACKAGE", user_text.strip()

    # Homelab server status
    if t in ("/server", "server", "server status", "status server") or re.search(r"\b(how('s| is) the server|server health|homelab status)\b", t):
        return "SERVER", ""
    # Syncthing / Pixel sync health
    if t in ("/syncthing", "syncthing", "sync status", "syncthing status") or re.search(
            r"\b(syncthing|pixel sync|is (the )?pixel sync(?:ing|ed)?|sync(?:hing)? (?:ok|healthy|status|errors?))\b", t):
        return "SYNCTHING", ""
    # Immich photo search
    m_ph = re.match(r"^(?:/photos?|/immich)\s+(.+)$", user_text.strip(), re.I)
    if m_ph:
        return "PHOTOS", m_ph.group(1).strip()
    if re.search(r"\b(find|search|show|any)\b.{0,40}\b(photos?|pictures?|pics)\b", t)             or re.search(r"\b(photos?|pictures?|pics)\b.{0,40}\b(of|with|from|showing)\b", t):
        q = re.sub(r"^(find|search|show|any|are there|do i have)\s+", "", t, flags=re.I)
        q = re.sub(r"\b(photos?|pictures?|pics|immich)\b", " ", q, flags=re.I)
        q = re.sub(r"\s+", " ", q).strip(" ?.,")
        return "PHOTOS", q or t[:150]
    # Maintenance & filter status
    if re.search(r"\b(maintenance|filter|filters)\s+status\b|\bstatus\s+of\s+(the\s+)?(filters|maintenance)\b", t):
        return "MAINTENANCE", ""
    # Battery levels
    if re.search(r"\b(battery|batteries)\b", t) and re.search(r"\b(check|level|levels|status|how|low|phones?)\b", t):
        return "BATTERIES", ""
    # Family location & presence
    if re.search(r"\b(who\s+is\s+home|where\s+is\s+everyone|family\s+status|is\s+anyone\s+home|is\s+anybody\s+home)\b", t):
        return "FAMILY", ""
    # Vacuum control
    if re.search(r"\b(start|run|turn on)\s+(the\s+)?(vacuum|rosie)\b", t):
        return "VACUUM_START", ""
    if re.search(r"\b(dock|return|stop|pause|send)\s+(the\s+)?(vacuum|rosie)\b", t):
        return "VACUUM_DOCK", ""
    if re.search(r"\b(vacuum|rosie)\s+status\b|\bwhere\s+is\s+rosie\b", t):
        return "VACUUM_STATUS", ""
    # Expense logging
    if re.search(r"(?:spent|paid|bought|expense)\s+(?:\$|dollars?\s*)?\d+|^\s*\$\d+(?:\.\d{2})?\s+(?:at|for|on)\b", t):
        return "EXPENSE", user_text.strip()
    # Mileage logging
    if re.search(r"\b(?:log(?:ged)?|drove|mileage)\s+\d+(?:\.\d+)?\s*miles?\b", t):
        return "MILEAGE", user_text.strip()
    # Read-it-later article URLs
    if re.search(r"https?://[^\s<>]+", user_text) and not re.search(r"\b(search|google)\b", t):
        return "READ_URL", user_text.strip()

    # Recall questions about past conversations/facts. Location questions
    # ("where is my passport") stay with the HID handler below.
    if (re.search(r"\bwhat\s+(?:do|did)\s+you\s+(?:remember|know)\b", t)
            or re.search(r"\b(?:do|did|can|could)\s+you\s+remember\b", t)) \
            and not re.search(r"\b(where|passport|keys?|drawer|safe)\b", t):
        return "MEMORY", t[:150]
    # Remember function: triggered if 'remember' is in first three words
    # e.g. "Remember i put my passport in the safe", "Please remember...", "Remember: ..."
    first_three = [w for w in re.findall(r"[a-z0-9']+", t)][:3]
    if "remember" in first_three:
        if re.search(r"\bremember\s+(?:a\s+)?note\b", t):
            pass  # handled by NOTE above
        elif re.match(r"^(?:(?:please|can\s+you|could\s+you)\s+)?remember\s+to\b", t) and not re.search(r"\b(put|place|store|stash|hide|keep|leave)\b", t):
            pass  # handled by REMIND / TASK / SHOPPING below
        elif re.search(r"\b(need|buy|out of|low on)\b", t) and not re.search(r"\b(put|place|store|stash|hide|kept|left|moved|is|are|was|in|on|under|behind|at|safe|drawer|box)\b", t):
            pass  # handled by SHOPPING / PANTRY
        elif re.match(r"^remember\s+that\b", t) and not re.search(
                r"\b(put|place|store|stash|hide|kept|left|moved|in\s+the|on\s+the|under|behind|drawer|box|safe|shelf|cabinet|garage|car|truck)\b", t):
            return "REMEMBER", re.sub(r"^remember\s+that\s+", "", t, flags=re.I).strip()[:200]
        elif re.search(r"\b(birthday|anniversary)\b", t):
            pass  # handled by EVENT / REMIND
        else:
            return "HID", user_text.strip()
    # fact memory: forget / recall
    if re.match(r"^\s*forget\b", t):
        return "FORGET", re.sub(r"^\s*forget\s+(?:that\s+|about\s+|what\s+i\s+said\s+about\s+)?", "", t, flags=re.I).strip()[:150] or "that"
    if re.search(r"\bwhat\s+(?:do|did)\s+you\s+(?:remember|know)\b", t) or re.match(r"^\s*(?:what|who|when|where)\b.*\b(?:did\s+i\s+(?:tell|say)|you\s+remember)\b", t):
        return "MEMORY", t[:150]
    # on-demand briefing
    if re.search(r"\bbrief(ing)?\b", t) and re.search(r"\b(me|my|now|day|morning)\b|^\s*brief\b", t):
        return "BRIEF", t[:100]
    # paperless document Q&A
    if (re.search(r"\b(warranty|manual|receipt|invoice|statement|paperless|document)\b", t)
            and re.search(r"\b(what|say|cover|find|check|show|summar|look up|read)\b", t)):
        return "PAPERLESS", t[:150]
    # habits (before chores: "log that I drank water" is not a chore)
    if ((re.search(r"\b(drank|log|logged)\b", t)
         and re.search(r"\b(water|oz|ounces|ml|reading|minutes|pages|steps|"
                       r"miles|exercise|meditation|goal)\b", t))
        or (re.search(r"\bgoal\b", t)
            and re.search(r"\b(water|reading|exercise|steps|miles|meditation|"
                          r"oz|ounces|minutes|pages)\b", t))):
        return "HABIT", t[:150]
    if re.match(r"^(how much|how many|did i hit)\b", t):
        return "HABIT", t[:150]
    # pantry (before chores: "did we run out of X" is not a chore)
    if re.search(r"\b(low on|ran out of|run out of|out of)\b", t) or re.search(r"\bdo we have\b", t):
        return "PANTRY", t[:150]
    # chores / maintenance ledger
    if (re.search(r"\b(mark|log) (that |down )?(i|we)\b", t)
            or re.search(r"\b(i|we) (just|finally|already) (changed|replaced|cleaned|fed|gave|washed|filled|mowed|did|finished)\b", t)
            or re.match(r"^(when|whens|did|has|have)\b.{0,60}\b"
                        r"(chang|replac|clean|fed|feed|gave|giv|wash|mow|service|"
                        r"filter|install|fix|updat)", t)
            or re.search(r"\bdid (anyone|anybody)\b", t)
            or re.search(r"\blast time\b.{0,40}\b(chang|replac|clean|did|gave|fed|filled|wash)", t)
            or (re.search(r"\bevery\s+(\d+|a|an)?\s*(day|week|month|year)", t)
                and re.search(r"\b(chang|replac|clean|fed|give|giv|wash|mow|service|check|filter|medicine)", t))
            or re.search(r"\b(?:what|whats)\s+(?:size|serial|model|brand|details)\b", t)):
        return "CHORE", t[:150]
    # named lists (packing/checklists) - after habits/pantry/chores
    if re.search(r"\b(packing|to-do|todo)\b", t) or (
            _mentions_list(t)
            and not re.search(r"\b(shopping|grocery|supplies)\b", t)
            and re.search(r"\b(add|put|whats|what.s|left|clear|wipe|create|make|start|new)\b", t)):
        return "LIST", t[:150]
    if re.search(r"\bweather\b", t):
        return "WEATHER", re.sub(r"\b(weather|what.s the|whats the|in|for|tell me|the)\b", " ", t).strip()[:60]
    if re.search(r"\b(google|search the web|web search|search online|look up online)\b", t) and "my file" not in t:
        return "SEARCH_WEB", re.sub(r"\b(google|search the web|web search|search online|look up online|search for|search)\b", " ", t).strip()[:80]
    if re.search(r"\b(shopping|grocery|groceries|hardware|feed\s*store|farm|tech|electronics)\s+list\b", t) or (
            re.search(r"\b(supplies|groceries|hardware|feed|lumber|tech)\b", t)
            and re.search(r"\b(what|whats|need|list|buy|get|add|on)\b", t)) or (
            re.search(r"\b(remove|delete|take|cross|scratch)\b", t)
            and re.search(r"\blist\b", t)):
        return "SHOPPING", t[:100]
    if re.search(r"\bagenda\b|what.?s? (is )?on (my|the|this)|today.?s schedule\b", t):
        return "AGENDA", ""
    # location-triggered reminders
    if re.search(r"\bwhen (?:i|we) (?:get|arrive|reach|am|are|m|come)\b", t):
        return "REMIND", t[:150]
    # REMIND beats TASK/SEND: any remind(er) mention + a time phrase,
    # or a plain timer request ("set a 10 minute timer")
    if (re.search(r"\bremind(er)?\b", t) or re.search(r"\b(start|set|run) (a |the )?\d* ?(min|minut|hour|hr)\w* timer\b", t)) and \
       re.search(r"\b(?:in|at)?\s?(?:a|an|half an|\d{1,3}|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty)[\s-]?(?:min(?:ute)?s?|sec(?:ond)?s?|hours?|hrs?|days?|weeks?|months?|years?)\b|\b(?:at \d{1,4}|at noon|at midnight|tomorrow|tonight|today|later|noon|midnight|this (?:morning|afternoon|evening|weekend)|morning|afternoon|evening)\b", t):
        return "REMIND", t[:100]
    if re.search(r"\bcontacts?\b", t) and re.search(r"\b(add|save|new|scan|card|create)\b", t):
        return "CONTACT", t[:120]
    if (re.search(r"\b(what|who|which|tell|give|show|find|get|know|have)\b", t) and _FIELDS_RE.search(t)) \
            or re.search(r"[\w]'?(?:s)?\s+(?:phone|cell|mobile|number|e-?mail|address)\b", t):
        return "CONTACT_INFO", t[:120]
    if re.search(r"\b(tasks?|todos?|to-dos?|remind me to)\b", t):
        return "TASK", re.sub(r"\b(tasks?|todos?|to-dos?|add|create|remind me to|remind me|it)\b", "", t).strip()[:80]
    if re.search(r"\b(events?|meeting|meetings|appointment|schedule[der]?|calendar)\b", t):
        return "EVENT", re.sub(r"^(add|create|put|schedule)\b|\b(event|meeting|appointment|to (my |the )?calendar|on my calendar)\b", "", t).strip(" /:-")[:80]
    if re.search(r"\b(send|text me|get me)\b", t):
        return "SEND", re.sub(r"\b(send|me|my|the|please|can you|text me|get me)\b", "", t).strip()[:80]
    # person-location beats file search: "where is alex" is not a file query
    if re.search(r"\bwhere\b", t):
        for pname_lower, pname in ha_people():
            if re.search(r"\b" + re.escape(pname_lower.split()[0]) + r"\b", t):
                return "LOCATION", pname
    # stored-item lookup: e.g. "where is the spare car key", "what drawer is my passport in"
    if (re.search(r"\b(where|wheres|where's)\b", t) or re.search(r"\b(what|whats|what's|which)\b", t)) and _hid_match(t, _hid_entries()):
        return "HID", t[:150]
    if re.search(r"\bwhere\s+(did|have)\s+(i|we)\s+(put|leave|stash|hid|hide|keep|place)\b", t):
        return "HID", t[:150]
    # stored-item clear/forget: e.g. "forget the birth certificate", "clear the spare car key"
    if re.search(r"\b(clear|forget|delete|remove|scratch)\b", t) and _hid_match(t, _hid_entries()) and not re.search(r"\blist\b", t):
        return "HID", t[:150]
    if re.search(r"\b(where|find|locate|search|do i have|look at|look in|view|most recent|recently saved|latest)\b", t) \
            and not re.search(r"\bwhere\s+can\s+(i|we|one)\b", t):
        return "FIND", t[:80]
    if re.search(r"\b(organize|organise|clean up|clean out|sort)\b", t):
        return "ORGANIZE", ""
    if (re.search(r"\b(remember|put|placed|stored|stashed|hid|hide|hidden|kept|left|moved)\b", t)
            and re.search(r"\b(in|into|inside|on|under|behind|at|drawer|closet|safe|box|bag|"
                          r"pocket|shelf|cabinet|garage|attic|basement|envelope|bin|"
                          r"folder|container|backpack|purse|wallet|car|trunk|glove)\b", t)
            and not re.search(r"\b(shopping|grocery|calendar|remind)\b", t)):
        return "HID", t[:150]
    return "CHAT", ""

def classify_intent(base, model, user_text, history=None):
    """Ask the LLM to classify the message. Returns (action, arg).
    history: recent exchanges so follow-ups ("what about X") inherit context."""
    # high-confidence keywords win outright
    act, arg = keyword_intent(user_text)
    if act != "CHAT":
        return act, arg
    ctx = ""
    if history:
        try:
            clines = []
            for item in list(history)[-4:]:
                if isinstance(item, dict):
                    role, content = item.get("role", ""), item.get("content", "")
                elif isinstance(item, (list, tuple)) and len(item) >= 2:
                    role, content = item[0], item[1]
                else:
                    continue
                if role not in ("user", "assistant", "house"):
                    continue
                if not isinstance(content, str) or not content.strip():
                    continue
                who = "House" if role in ("assistant", "house") else role.capitalize()
                clines.append(who + ": " + content.strip()[:100])
            if clines:
                ctx = "Previous exchange:\n" + "\n".join(clines) + "\n"
        except Exception as e:
            log("classifier context error: " + repr(e))
            ctx = ""
    try:
        prompt_user = f"{ctx}Request: {user_text}\nClassification:"
        if base.rstrip("/").endswith("/v1"):   # OpenAI-compatible endpoint
            body = json.dumps({
                "model": model, "stream": False,
                "messages": [{"role": "system", "content": CLASSIFIER_PROMPT + "\n" + _caps_line()},
                             {"role": "user", "content": prompt_user}],
                "temperature": 0.0, "max_tokens": 30,
            }).encode()
            url = base + "/chat/completions"
        else:                                   # Ollama native
            body = json.dumps({
                "model": model, "stream": False, "think": False,
                "system": CLASSIFIER_PROMPT,
                "messages": [{"role": "user", "content": prompt_user}],
                "options": {"num_predict": 30, "temperature": 0.0},
            }).encode()
            url = base + "/api/chat"
        req = urllib.request.Request(url, data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=90) as r:
            resp = json.loads(r.read())
            out = (resp["message"]["content"] if "message" in resp
                   else resp["choices"][0]["message"]["content"]).strip().upper()
        m = re.search(r"\b(FIND|SEND|ORGANIZE|CHAT|SEARCH_WEB|WEATHER|NOTE|WORKNOTE|REMIND|EVENT|TASK|SHOPPING|REMEMBER|FORGET|MEMORY)\b\s*[:]?(.*)$", out[:80], re.S)
        if m:
            act, arg = m.group(1), m.group(2)
            # ORGANIZE is destructive-adjacent: require explicit words
            if act == "ORGANIZE" and not re.search(
                    r"\b(organize|organise|organis|clean|sort|tidy)\b", user_text.lower()):
                log(f"ignoring LLM ORGANIZE guess (no organize words)")
                act = None
            if act:
                # cut model babble: keep text up to the first sentence boundary
                arg = arg.strip().strip('".').split(".")[0].strip().lower()[:100]
                return act, arg
        # deterministic fallback: keyword heuristics beat an 8B bad guess
        t = user_text.lower().strip()
        if re.search(r"\bagenda\b|what.?s? (is )?on (my|the|this)|today.?s schedule\b", t):
            return "AGENDA", ""
        if re.search(r"remind me (in|at|on|by|tomorrow|next)", t):
            return "REMIND", t[:100]
        if re.search(r"^(note|make a note|remember that)\b", t):
            return "NOTE", re.sub(r"^(note|make a note|remember that)\b[:,] ?", "", t)[:100]
        if re.search(r"\bweather\b", t):
            return "WEATHER", re.sub(r"\b(weather|what.s the|whats the|in|for|tell me|the)\b", " ", t).strip()[:60]
        if re.search(r"\b(google|search the web|web search|search online|look up online)\b", t) and "my file" not in t:
            return "SEARCH_WEB", re.sub(r"\b(google|search the web|web search|search online|look up online|search for|search)\b", " ", t).strip()[:80]
        if re.search(r"\b(tasks?|todos?|to-dos?|remind me)\b", t):
            return "TASK", re.sub(r"\b(tasks?|todos?|to-dos?|add|create|remind me to|remind me|it)\b", "", t).strip()[:80]
        if re.search(r"\b(events?|meeting|meetings|appointment|schedule[der]?|calendar)\b", t):
            return "EVENT", re.sub(r"^(add|create|put|schedule)\b|\b(event|meeting|appointment|to (my |the )?calendar|on my calendar)\b", "", t).strip(" /:-")[:80]
        if re.search(r"\b(send|text me|get me)\b", t):
            return "SEND", re.sub(r"\b(send|me|my|the|please|can you|text me|get me)\b", "", t).strip()[:80]
        if re.search(r"\b(where|find|locate|search|do i have|look at|look in|view|most recent|recently saved|latest)\b", t):
            return "FIND", t[:80]
        if re.search(r"\b(organize|organise|clean up|sort)\b", t):
            return "ORGANIZE", ""
    except Exception as e:
        log(f"classifier failed: {e}")
    return "CHAT", ""


# ---------------------------------------------------------------------------
# Facts memory: "remember that X" / "what do you remember about Y" / "forget Z"
# ---------------------------------------------------------------------------

def chat_log_append(sender, user_text, reply):
    """Append every exchange to a daily Obsidian note so conversations are recallable."""
    try:
        import datetime as _dt
        os.makedirs(CHAT_LOG_DIR, exist_ok=True)
        now = _dt.datetime.now()
        path = os.path.join(CHAT_LOG_DIR, now.strftime("%Y-%m-%d") + ".md")
        if not os.path.exists(path):
            with open(path, "w") as f:
                f.write(f"# Chat Log — {now.strftime('%Y-%m-%d (%A)')}\n")
        with open(path, "a") as f:
            f.write(f"\n## {now.strftime('%H:%M')} — {sender}\n{user_text}\n")
            f.write(f"\n## {now.strftime('%H:%M')} — House\n{reply}\n")
    except Exception as e:
        log(f"chat log failed: {e}")

def _mem_load():
    try:
        with open(MEMORY_FILE) as f:
            d = json.load(f)
        return d.get("facts", []) if isinstance(d, dict) else []
    except Exception:
        return []

def _mem_save(facts):
    os.makedirs(os.path.dirname(MEMORY_FILE), exist_ok=True)
    with open(MEMORY_FILE, "w") as f:
        json.dump({"facts": facts}, f, indent=1)

def _embed_text(text):
    """Embed via nomic-embed-text on omarchybox. Returns vector or None."""
    if not EMBED_URL:
        return None
    try:
        req = urllib.request.Request(
            EMBED_URL,
            data=json.dumps({"model": "nomic-embed-text", "prompt": text[:600]}).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read()).get("embedding")
    except Exception as e:
        log(f"embed failed: {e}")
        return None

def _cosine(a, b):
    import math
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(x * x for x in b)) or 1.0
    return dot / (na * nb)

def _mem_search(query, top=3, threshold=0.45):
    """Semantic recall with keyword fallback. Returns [(score, fact_dict)]."""
    facts = _mem_load()
    if not facts:
        return []
    qv = _embed_text(query)
    qtok = _tokens(query)
    scored = []
    for f in facts:
        score = 0.0
        if qv and f.get("embedding"):
            score = _cosine(qv, f["embedding"])
        else:
            overlap = qtok & _tokens(f.get("fact", ""))
            score = 0.6 if overlap else 0.0
        scored.append((score, f))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [(s, f) for s, f in scored if s >= threshold][:top]

def memory_add(fact):
    fact = (fact or "").strip()
    if not fact:
        return "What should I remember?"
    facts = _mem_load()
    for f in facts:
        if f["fact"].lower() == fact.lower():
            return f"Already remembered: {f['fact']}"
    import hashlib
    fid = hashlib.md5((fact + str(time.time())).encode()).hexdigest()[:8]
    emb = _embed_text(fact)
    import datetime as _dt
    facts.append({"id": fid, "fact": fact,
                  "ts": _dt.datetime.now().isoformat()[:16],
                  "embedding": emb})
    _mem_save(facts)
    return (f"Remembered permanently ({len(facts)} facts): {fact}"
            + ("" if emb else " (semantic recall offline, keyword only)"))

def memory_forget(arg):
    facts = _mem_load()
    if not facts:
        return "I don't have any facts stored yet."
    if not arg or arg.lower() in ("that", "it", "this"):
        last = facts[-1]
        facts = facts[:-1]
        _mem_save(facts)
        return f"Forgotten: {last['fact']}"
    hits = _mem_search(arg, top=1, threshold=0.4)
    if not hits:
        kw = _tokens(arg)
        cands = [f for f in facts if kw & _tokens(f.get("fact", ""))]
        hits = [(0.5, f) for f in cands[:1]]
    if not hits:
        return f"Nothing remembered about '{arg}'."
    victim = hits[0][1]
    facts = [f for f in facts if f["id"] != victim["id"]]
    _mem_save(facts)
    return f"Forgotten: {victim['fact']}"

def _resolve_past_date(text):
    """Most recent date implied by the text (yesterday/last tuesday/...). None if none."""
    import datetime as _dt
    t = (text or "").lower()
    today = _dt.date.today()
    if "yesterday" in t:
        return today - _dt.timedelta(days=1)
    if re.search(r"\btoday\b", t):
        return today
    m = re.search(r"\b(?:last\s+|this\s+|on\s+)?(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", t)
    if m:
        tgt = WD[m.group(1)]
        return today - _dt.timedelta(days=(today.weekday() - tgt) % 7 or 7)
    return None

def memory_recall(arg):
    arg = (arg or "").strip()
    facts = _mem_load()
    have = bool(arg) and arg.lower() not in ("all", "everything", "list")
    sections = []

    if facts:
        if have:
            hits = _mem_search(arg, top=5, threshold=0.35)
            fl = [f"- {f['fact']} ({f.get('ts', '')[:10]})" for _, f in hits]
        else:
            fl = [f"- {f['fact']} ({f.get('ts', '')[:10]})" for f in facts[-10:]]
        if fl:
            sections.append("Facts:\n" + "\n".join(fl))

    try:
        pdate = _resolve_past_date(arg) if have else None
        kw = _tokens(arg) if have else set()
        files = []
        for fn in os.listdir(CHAT_NOTES_DIR):
            if fn.endswith(".md"):
                files.append(fn)
        picked = []
        if pdate:
            pref = pdate.isoformat()
            picked = [fn for fn in files if fn.startswith(pref)]
        if not picked and kw:
            for fn in files:
                if kw & _tokens(fn):
                    picked.append(fn)
        if not picked and kw:
            for fn in files:
                try:
                    with open(os.path.join(CHAT_NOTES_DIR, fn)) as f:
                        body = f.read().lower()
                    if kw & _tokens(body):
                        picked.append(fn)
                except Exception:
                    pass
        picked = sorted(set(picked))[-6:]
        if picked:
            nl = []
            for fn in picked:
                title = fn[:-3]
                snippet = ""
                try:
                    with open(os.path.join(CHAT_NOTES_DIR, fn)) as f:
                        body = [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]
                    if body:
                        snippet = body[0][:100]
                except Exception:
                    pass
                nl.append(f"- {title}" + (f" — {snippet}" if snippet else ""))
            label = f"Notes from {pdate.isoformat()}:" if pdate else "Matching notes:"
            sections.append(label + "\n" + "\n".join(nl))
        # auto-recorded chat logs: date match pulls the whole day
        try:
            logfiles = sorted((fn for fn in os.listdir(CHAT_LOG_DIR) if fn.endswith(".md")), reverse=True)
            picked_logs = []
            if pdate:
                picked_logs = [fn for fn in logfiles if fn.startswith(pdate.isoformat())]
            elif kw:
                for fn in logfiles:
                    try:
                        with open(os.path.join(CHAT_LOG_DIR, fn)) as f:
                            body = f.read().lower()
                        if kw & _tokens(body):
                            picked_logs.append(fn)
                    except Exception:
                        pass
            if picked_logs:
                nl = []
                for fn in picked_logs[:2]:
                    try:
                        with open(os.path.join(CHAT_LOG_DIR, fn)) as f:
                            body = f.read()
                        nl.append(f"### {fn[:-3]}\n" + body[:1500])
                    except Exception:
                        pass
                if nl:
                    sections.append("Chat history:\n" + "\n\n".join(nl))
        except Exception as e:
            log(f"chat log recall failed: {e}")
    except Exception as e:
        log(f"note recall failed: {e}")

    if not sections:
        msg = "I don't have anything remembered for that"
        if have:
            msg += f" ({arg})"
        return msg + ". Things you tell me with 'remember that ...' are kept permanently."
    reply = ""
    for sec in sections:
        reply += sec + "\n\n"
    return reply.strip()

def memory_context(user_text):
    """Relevant facts + recent Obsidian note index for LLM context. None if empty."""
    try:
        lines = []
        for _, f in _mem_search(user_text, top=3, threshold=0.45):
            lines.append(f"- {f['fact']} (remembered {f.get('ts', '')[:10]})")
        try:
            notes = []
            for fn in os.listdir(CHAT_NOTES_DIR):
                if fn.endswith(".md"):
                    p = os.path.join(CHAT_NOTES_DIR, fn)
                    notes.append((os.path.getmtime(p), fn, p))
            notes.sort(reverse=True)
            for _, fn, p in notes[:5]:
                title = fn[:-3].strip()
                try:
                    with open(p) as f:
                        for ln in f:
                            ln = ln.strip()
                            if ln and not ln.startswith("#"):
                                title = f"{title}: {ln[:80]}"
                                break
                except Exception:
                    pass
                lines.append(f"- note: {title}")
        except Exception:
            pass
        try:
            logdays = sorted((fn for fn in os.listdir(CHAT_LOG_DIR) if fn.endswith(".md")), reverse=True)[:3]
            for fn in logdays:
                try:
                    with open(os.path.join(CHAT_LOG_DIR, fn)) as f:
                        n_ex = f.read().count("\n## ")
                except Exception:
                    n_ex = 0
                lines.append(f"- chat log: {fn[:-3]} ({n_ex} exchanges)")
        except Exception:
            pass
        if not lines:
            return None
        return ("Facts, notes and chat logs you have permanently stored (use if relevant"
                " to the user's message):\n" + "\n".join(lines))
    except Exception as e:
        log(f"memory_context failed: {e}")
        return None


def ask_llm(base, model, user_text, history=None):
    if "qwen" in model.lower():
        # Qwen3 honors /no_think in the last user message (not the system prompt)
        user_text = user_text + " /no_think"
    msgs = list(history or [])[-6:] + [{"role": "user", "content": user_text}]
    _mctx = memory_context(user_text)
    if _mctx:
        msgs = [{"role": "system", "content": _mctx}] + msgs
    sys_prompt = SYSTEM_PROMPT
    body = json.dumps({
        "model": model,
        "messages": [{"role": "system", "content": sys_prompt}] + msgs,
        "temperature": 0.7,
    }).encode()
    req = urllib.request.Request(base + "/chat/completions", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as r:
        out = json.loads(r.read())["choices"][0]["message"]["content"].strip()
    # strip any Qwen3 think blocks that slip through
    opent, closet = "<" + "think" + ">", "<" + "/" + "think" + ">"
    return re.sub(re.escape(opent) + ".*?" + re.escape(closet), "", out, flags=re.S).strip()

def organize_plan(go=False):
    """Sort via the house file-tools service (dry-run unless go=True)."""
    try:
        req = urllib.request.Request(
            f"{SERVICE}/organize", method="POST",
            data=json.dumps({"go": go}).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())["result"]
    except Exception as e:
        return f"(organize failed: {e})"

def send_message(sock_file, sock, recipient, text, attachment=None, msg_id=0):
    params = {"account": ACCOUNT, "recipient": recipient, "message": text}
    if attachment:
        params["attachments"] = [attachment]
    payload = {"jsonrpc": "2.0", "id": msg_id or int(time.time()),
               "method": "send", "params": params}
    sock.sendall((json.dumps(payload) + "\n").encode())
    line = sock_file.readline()
    if line:
        try:
            resp = json.loads(line)
            if "error" in resp:
                log(f"send error: {resp['error']}")
                return False
        except json.JSONDecodeError:
            pass
    return True

HISTORY = {}   # sender -> last few conversation turns (memory)

def ha_token():
    return os.environ.get("HA_TOKEN", "")

def ha_entity(entity_id):
    """Fetch single HA entity state directly without loading the full table."""
    try:
        req = urllib.request.Request(
            f"{HA_URL}/api/states/{entity_id}",
            headers={"Authorization": "Bearer " + ha_token()})
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read())
    except Exception as e:
        log(f"ha entity {entity_id} fetch failed: {e}")
        return None

def ha_states():
    """All HA entity states, cached for 5 minutes."""
    now = time.time()
    if now - _HA_CACHE["ts"] < 300 and _HA_CACHE["states"]:
        return _HA_CACHE["states"]
    try:
        req = urllib.request.Request(
            HA_URL + "/api/states",
            headers={"Authorization": "Bearer " + ha_token()})
        with urllib.request.urlopen(req, timeout=10) as r:
            states = json.loads(r.read())
        _HA_CACHE["states"] = states
        _HA_CACHE["ts"] = now
        return states
    except Exception as e:
        log(f"ha states fetch failed: {e}")
        return _HA_CACHE["states"] or []

def ha_people():
    """[(name_lower, friendly_name)] from HA person entities."""
    out = []
    for s in ha_states():
        if s["entity_id"].startswith("person."):
            name = (s.get("attributes", {}).get("friendly_name")
                    or s["entity_id"].split(".", 1)[1])
            out.append((name.lower(), name))
    return out

def rel_time(iso):
    """Human-friendly age of a timestamp."""
    import datetime
    try:
        dt = datetime.datetime.fromisoformat(iso.replace("Z", "+00:00"))
        secs = max(0, int((datetime.datetime.now(datetime.timezone.utc)
                           - dt).total_seconds()))
    except Exception:
        return "an unknown time ago"
    if secs < 60:
        return "just now"
    if secs < 3600:
        m = secs // 60
        return f"{m} minute{'s' if m != 1 else ''} ago"
    h = secs // 3600
    return f"{h} hour{'s' if h != 1 else ''} ago"

def tracker_distance_from_home(name, states):
    """Miles from home using the person's phone tracker GPS, if available."""
    import math
    home = next((s for s in states if s["entity_id"] == "zone.home"), None)
    if not home:
        return None
    hlat = home.get("attributes", {}).get("latitude")
    hlon = home.get("attributes", {}).get("longitude")
    if hlat is None or hlon is None:
        return None
    key = name.lower().split()[0]
    for s in states:
        if not s["entity_id"].startswith("device_tracker."):
            continue
        attrs = s.get("attributes", {})
        fn = (attrs.get("friendly_name") or "").lower()
        if key in fn and attrs.get("latitude") is not None:
            lat, lon = attrs["latitude"], attrs["longitude"]
            dlat, dlon = math.radians(lat - hlat), math.radians(lon - hlon)
            a = (math.sin(dlat / 2) ** 2
                 + math.cos(math.radians(hlat)) * math.cos(math.radians(lat))
                 * math.sin(dlon / 2) ** 2)
            return 2 * 3958.8 * math.asin(math.sqrt(a))
    return None

def locate_reply(name):
    """Where is <person>: live location from Home Assistant."""
    try:
        states = ha_states()
        ent = next((s for s in states
                    if s["entity_id"] == "person." + name.lower().replace(" ", "_")),
                   None)
        if ent is None:
            ent = next((s for s in states if s["entity_id"].startswith("person.")
                        and (s.get("attributes", {}).get("friendly_name") or "").lower()
                        == name.lower()), None)
        if ent is None:
            known = ", ".join(sorted(n for _, n in ha_people())) or "(none)"
            return (f"I don't track anyone named '{name}' in Home Assistant. "
                    f"People I know: {known}.")
        disp = ent.get("attributes", {}).get("friendly_name", name)
        when = rel_time(ent.get("last_changed") or ent.get("last_updated") or "")
        state = ent.get("state", "unknown")
        if state == "home":
            return f"{disp} is at home (updated {when})."
        if state == "not_home":
            dist = tracker_distance_from_home(disp, states)
            if dist is not None:
                return (f"{disp} is out — about {dist:.0f} miles from home "
                        f"(updated {when}).")
            return f"{disp} is away from home (updated {when})."
        if state in ("unknown", "unavailable"):
            return f"{disp}'s location is {state} right now (updated {when})."
        return f"{disp} is at {state} (updated {when})."
    except Exception as e:
        return f"Location lookup failed: {e}"


def _open_shopping_items():
    """Open (not completed) VTODOs tagged 'Shopping List'."""
    raw = cal_get("tasks.ics").replace("\r\n ", "")
    items = []
    for block in re.findall(r"BEGIN:VTODO\b.*?END:VTODO\b", raw, re.S):
        if "Shopping List" not in block:
            continue
        if re.search(r"\bSTATUS:COMPLETED\b", block):
            continue
        m = re.search(r"SUMMARY:?(.*)", block)
        if m:
            items.append(m.group(1).strip())
    return items

def _read_note_items(note_path):
    """Read unchecked items from an Obsidian markdown list note."""
    items = []
    if os.path.exists(note_path):
        try:
            with open(note_path) as f:
                for line in f:
                    m = re.match(r"- \[( |x|X)\] (.+)", line.strip())
                    if m and m.group(1) == " ":
                        items.append(m.group(2).strip())
        except Exception as e:
            log(f"read note failed {note_path}: {e}")
    return items

def _read_note_all(note_path):
    """Read (active_items, checked_items) from an Obsidian markdown list note."""
    active, checked = [], []
    if os.path.exists(note_path):
        try:
            with open(note_path) as f:
                for line in f:
                    m = re.match(r"- \[( |x|X)\] (.+)", line.strip())
                    if m:
                        it = m.group(2).strip()
                        if m.group(1) == " ":
                            active.append(it)
                        else:
                            checked.append(it)
        except Exception as e:
            log(f"read note failed {note_path}: {e}")
    return active, checked

def _add_items_to_note(note_path, title, items):
    current = _read_note_items(note_path)
    added = []
    for it in items:
        it = it.strip(" .,-")
        if it and not any(it.lower() == c.lower() for c in current):
            current.append(it)
            added.append(it)
    try:
        os.makedirs(os.path.dirname(note_path), exist_ok=True)
        with open(note_path, "w") as f:
            f.write(f"# {title}\n\n" + "\n".join(f"- [ ] {i}" for i in current) + "\n")
    except Exception as e:
        log(f"write note failed {note_path}: {e}")
    return added

def _remove_items_from_note(note_path, phrase):
    phrase = phrase.lower().strip()
    if not os.path.exists(note_path):
        return []
    removed, kept = [], []
    try:
        with open(note_path) as f:
            lines = f.readlines()
        for line in lines:
            m = re.match(r"- \[( |x|X)\] (.+)", line.strip())
            if m:
                item = m.group(2).strip()
                s = item.lower()
                if phrase in s or s in phrase:
                    removed.append(item)
                    continue
            kept.append(line)
        if removed:
            with open(note_path, "w") as f:
                f.writelines(kept)
    except Exception as e:
        log(f"remove note failed {note_path}: {e}")
    return removed

def _open_and_completed_shopping_items():
    """Return (open_items_dict, completed_items_set) from CalDAV tasks.ics.
    open_items_dict is {lowercase_summary: (original_summary, uid)}"""
    open_items = {}
    completed_items = set()
    try:
        raw = cal_get("tasks.ics").replace("\r\n ", "")
        for block in re.findall(r"BEGIN:VTODO\b.*?END:VTODO\b", raw, re.S):
            if "Shopping List" not in block:
                continue
            m_sum = re.search(r"SUMMARY:?(.*)", block)
            if not m_sum:
                continue
            summary = m_sum.group(1).strip()
            s_low = summary.lower()
            m_uid = re.search(r"UID:(.*)", block)
            uid = m_uid.group(1).strip() if m_uid else ""
            if re.search(r"\bSTATUS:COMPLETED\b", block):
                completed_items.add(s_low)
            else:
                open_items[s_low] = (summary, uid)
    except Exception as e:
        log(f"caldav tasks read failed: {e}")
    return open_items, completed_items

def sync_shopping_note():
    """True two-way sync between Obsidian Groceries and CalDAV tasks.ics.
    - If an item is deleted in Obsidian or marked checked (- [x]), it is deleted from CalDAV.
    - If an item is marked COMPLETED in CalDAV (e.g. from phone), it drops off Obsidian.
    - New items in CalDAV are merged into Obsidian.
    Returns active grocery items."""
    note_active, note_checked = _read_note_all(SHOPPING_NOTE)
    cal_open, cal_completed = _open_and_completed_shopping_items()
    
    known_items = _load_json(SHOPPING_SYNC_FILE, None)
    if known_items is None:
        known_items = [it.lower() for it in note_active]
        _save_json(SHOPPING_SYNC_FILE, known_items)

    # 1. Any checked off item in Obsidian: delete from CalDAV tasks
    for item in note_checked:
        s_low = item.lower()
        if s_low in cal_open:
            caldav_delete("tasks.ics", cal_open[s_low][1] + ".ics")
            cal_open.pop(s_low, None)

    # 2. Deletions in Obsidian: if item was previously known but removed from Obsidian
    note_active_lower = {it.lower() for it in note_active}
    for prev in list(known_items):
        if prev not in note_active_lower and prev in cal_open:
            caldav_delete("tasks.ics", cal_open[prev][1] + ".ics")
            cal_open.pop(prev, None)

    # 3. Drops off if completed in CalDAV (e.g. from phone via DAVx5)
    note_active = [it for it in note_active if it.lower() not in cal_completed]

    # 4. New items added in CalDAV (e.g. from phone)
    current_lower = {it.lower() for it in note_active}
    for s_low, (orig_summary, uid) in cal_open.items():
        if s_low not in current_lower and s_low not in {c.lower() for c in note_checked}:
            note_active.append(orig_summary)
            current_lower.add(s_low)

    # 5. Write back clean note if changed
    current_lines = _read_note_items(SHOPPING_NOTE)
    if note_active != current_lines or note_checked:
        try:
            with open(SHOPPING_NOTE, "w") as f:
                f.write("# Groceries and Supplies\n\n"
                        + "\n".join(f"- [ ] {i}" for i in note_active) + "\n")
        except Exception as e:
            log(f"shopping note write failed: {e}")

    # 6. Save new state
    _save_json(SHOPPING_SYNC_FILE, [it.lower() for it in note_active])
    return note_active

def classify_shopping_item(item_text):
    """Classify an item into 'farm', 'hardware', 'tech', or 'groceries'."""
    t = item_text.lower().strip()
    farm_kw = re.compile(
        r"\b(feed|corn|grain|hay|straw|chick|chicks|chicken|chickens|quail|duck|ducks|"
        r"goat|goats|horse|horses|equine|cattle|cow|cows|pig|pigs|swine|livestock|"
        r"poultry|coop|tractor|fencing|mineral|salt\s+lick|bedding|dewormer|"
        r"pine\s+shavings|pellets|scratch\s+grain|sweet\s+feed|waterer|feeder|"
        r"fly\s+spray|hoof|trough|round\s+bale)\b", re.I)
    if farm_kw.search(t):
        return "farm"
    hardware_kw = re.compile(
        r"\b(2x4|2x6|2x8|2x10|4x4|plywood|lumber|stud|studs|board|boards|timber|"
        r"screw|screws|nail|nails|bolt|bolts|nut|nuts|washer|washers|drill|drill\s+bit|saw|"
        r"paint|primer|sandpaper|caulk|silicone|plumbing|pvc|pipe|pipes|fitting|fittings|"
        r"duct\s+tape|wd-40|anchor|anchors|drywall|sheetrock|cement|concrete|spackle|"
        r"tape\s+measure|hammer|wrench|pliers|hex\s+key|allen\s+wrench|wood\s+glue|"
        r"gorilla\s+glue|epoxy|fastener|fasteners|hinge|hinges|latch|lockset|door\s+knob|"
        r"deadbolt|outlet|light\s+switch|wire\s+nut|conduit|breaker|mulch|fertilizer|"
        r"weed\s+eater|trimmer\s+line|chainsaw|mower|shovel|rake)\b", re.I)
    if hardware_kw.search(t):
        return "hardware"
    tech_kw = re.compile(
        r"\b(hdmi|usb|usb-c|usbc|micro-usb|lightning|cable|cables|charger|chargers|"
        r"adapter|adapters|ethernet|cat6|cat5|battery|batteries|aaa|aa\s+batteries|9v|"
        r"cr2032|hard\s+drive|ssd|sd\s+card|micro\s*sd|flash\s+drive|thumb\s+drive|"
        r"monitor|displayport|keyboard|mouse|printer|ink|toner|printer\s+paper|"
        r"staples|sharpie|sharpies|pens|pencils|notebook|notebooks|envelopes|"
        r"webcam|microphone|headphones|earbuds|power\s+strip|surge\s+protector|"
        r"router|switch|wifi)\b", re.I)
    if tech_kw.search(t):
        return "tech"
    return "groceries"

def shopping_add_items(items, target_cat=None):
    """Add items into categorized shopping lists."""
    import datetime, uuid
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    cat_buckets = {"groceries": [], "hardware": [], "farm": [], "tech": []}
    for item in items[:15]:
        item = item.strip(" .,-")
        if not item:
            continue
        # never add whole sentences / questions as items
        if ("?" in item or len(item.split()) > 6
                or re.match(r"^(why|how|when|where|what|is|are|do|does|did)\b", item.lower())):
            log(f"skipped sentence-like shopping item: {item[:60]!r}")
            continue
        c = target_cat if target_cat else classify_shopping_item(item)
        cat_buckets.setdefault(c, []).append(item)
    summary_parts = []
    if cat_buckets["groceries"]:
        for it in cat_buckets["groceries"]:
            uid = f"shopping-{uuid.uuid4().hex[:12]}@house"
            body = ("BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//house//EN\n"
                    "BEGIN:VTODO\n" f"UID:{uid}\nDTSTAMP:{stamp}\n"
                    f"SUMMARY:{it}\nCATEGORIES:Shopping List\n"
                    "END:VTODO\nEND:VCALENDAR\n")
            caldav_put("tasks.ics", uid + ".ics", body)
        _add_items_to_note(SHOPPING_NOTE, "Groceries and Supplies", cat_buckets["groceries"])
        sync_shopping_note()
        summary_parts.append(f"🛒 Groceries: {', '.join(cat_buckets['groceries'])}")
    if cat_buckets["hardware"]:
        _add_items_to_note(HARDWARE_NOTE, "Hardware Store", cat_buckets["hardware"])
        summary_parts.append(f"🔨 Hardware: {', '.join(cat_buckets['hardware'])}")
    if cat_buckets["farm"]:
        _add_items_to_note(FARM_NOTE, "Feed and Farm", cat_buckets["farm"])
        summary_parts.append(f"🚜 Feed & Farm: {', '.join(cat_buckets['farm'])}")
    if cat_buckets["tech"]:
        _add_items_to_note(TECH_NOTE, "Tech and Electronics", cat_buckets["tech"])
        summary_parts.append(f"💻 Tech: {', '.join(cat_buckets['tech'])}")
    if not summary_parts:
        return "Nothing to add."
    if len(summary_parts) == 1:
        return f"Added to {summary_parts[0]}."
    return "Added to your lists:\n" + "\n".join(summary_parts)

def shopping_list_reply(target_cat=None):
    """Read categorized shopping lists from Obsidian."""
    groc = sync_shopping_note() if target_cat in (None, "groceries") else _read_note_items(SHOPPING_NOTE)
    hw = _read_note_items(HARDWARE_NOTE)
    farm = _read_note_items(FARM_NOTE)
    tech = _read_note_items(TECH_NOTE)
    if target_cat == "groceries":
        return f"🛒 Groceries ({len(groc)}): " + ", ".join(groc[:15]) + "." if groc else "The grocery list is empty."
    if target_cat == "hardware":
        return f"🔨 Hardware Store ({len(hw)}): " + ", ".join(hw[:15]) + "." if hw else "The hardware store list is empty."
    if target_cat == "farm":
        return f"🚜 Feed & Farm ({len(farm)}): " + ", ".join(farm[:15]) + "." if farm else "The feed and farm list is empty."
    if target_cat == "tech":
        return f"💻 Tech and Electronics ({len(tech)}): " + ", ".join(tech[:15]) + "." if tech else "The tech list is empty."
    active = []
    if groc:
        active.append(f"🛒 Groceries ({len(groc)}): " + ", ".join(groc[:10]))
    if farm:
        active.append(f"🚜 Feed & Farm ({len(farm)}): " + ", ".join(farm[:8]))
    if hw:
        active.append(f"🔨 Hardware ({len(hw)}): " + ", ".join(hw[:8]))
    if tech:
        active.append(f"💻 Tech ({len(tech)}): " + ", ".join(tech[:8]))
    if not active:
        return "All shopping lists are currently empty."
    if len(active) == 1:
        return active[0]
    return "Shopping Lists:\n" + "\n".join(active)

def shopping_remove(text, target_cat=None):
    """Remove item(s) from matching categorized shopping list(s)."""
    t = " " + text.lower().strip() + " "
    t = re.sub(r"\b(can you|please|house)\b", " ", t)
    t = re.sub(r"\b(remove|delete|take|scratch|cross|strike|get rid of|check off)\b", " ", t)
    t = re.sub(r"\b(off|out|from|of)\b", " ", t)
    t = re.sub(r"(to|on|in|from|off) (the |my |our )?(shopping|grocery|groceries|hardware|feed\s*(?:and|&)?\s*farm|farm|tech|electronics) list", " ", t)
    t = re.sub(r"\b(shopping|grocery|groceries|hardware|feed\s*(?:and|&)?\s*farm|farm|tech|electronics)\s+list\b", " ", t)
    t = re.sub(r"\b(the|my|our)?\s*list\b", " ", t)
    t = re.sub(r"\s+", " ", t).strip(" .,-")
    if not t:
        return "What should I remove from the shopping list?"
    
    raw_candidates = re.split(r"\s*(?:,|&|\band\b)\s*", t)
    phrases = [c.strip(" .,-") for c in raw_candidates if len(c.strip(" .,-")) > 1]
    if not phrases:
        phrases = [t]

    removed_all = []
    # 1. CalDAV tasks
    if target_cat in (None, "groceries"):
        try:
            raw = cal_get("tasks.ics").replace("\r\n ", "")
            for block in re.findall(r"BEGIN:VTODO\b.*?END:VTODO\b", raw, re.S):
                if "Shopping List" not in block:
                    continue
                m = re.search(r"SUMMARY:?(.*)", block)
                if not m:
                    continue
                summary = m.group(1).strip()
                s = summary.lower()
                for phrase in phrases:
                    if phrase in s or s in phrase:
                        uid = re.search(r"UID:(.*)", block).group(1).strip()
                        caldav_delete("tasks.ics", uid + ".ics")
                        break
        except Exception as e:
            log(f"caldav remove check failed: {e}")

    # 2. Check notes
    for phrase in phrases:
        if target_cat in (None, "groceries"):
            r = _remove_items_from_note(SHOPPING_NOTE, phrase)
            if r: removed_all.append(f"Groceries ({', '.join(r)})")
        if target_cat in (None, "hardware"):
            r = _remove_items_from_note(HARDWARE_NOTE, phrase)
            if r: removed_all.append(f"Hardware ({', '.join(r)})")
        if target_cat in (None, "farm"):
            r = _remove_items_from_note(FARM_NOTE, phrase)
            if r: removed_all.append(f"Feed & Farm ({', '.join(r)})")
        if target_cat in (None, "tech"):
            r = _remove_items_from_note(TECH_NOTE, phrase)
            if r: removed_all.append(f"Tech ({', '.join(r)})")
    if not removed_all:
        return f"No matching items found on your shopping lists for '{', '.join(phrases)}'."
    return "Removed from " + ", ".join(removed_all) + "."

def _split_items(t, strip_list_phrases=False):
    """Parse a free-text shopping-ish request into item strings."""
    t = re.sub(r"\b(can you|please|house)\b", " ", t)
    if strip_list_phrases:
        t = re.sub(r"(to|on|in|from|off) (the |my |our )?(shopping|grocery|groceries|hardware|feed\s*(?:and|&)?\s*farm|farm|tech|electronics) list", " ", t)
        t = re.sub(r"\b(shopping|grocery|groceries|hardware|feed\s*(?:and|&)?\s*farm|farm|tech|electronics)\s+list\b", " ", t)
    t = re.sub(r"\b(don t|dont|do not) forget\b", " ", t)
    t = re.sub(r"\b(add|put|buy|get|grab|pick up|need|pick|remember)\b", ",", t)
    stopwords = {"we", "i", "me", "us", "my", "our", "some", "more", "also",
                 "the", "a", "an", "to", "on", "in", "and", "for", "from",
                 "it", "that", "this", "would", "like", "want", "get",
                 "back", "again", "please", "everything", "all"}
    items = []
    for part in re.split(r"\s*(?:,|\band\b|&|;)\s*", t):
        words = [w.strip("\"'") for w in part.split() if w not in stopwords]
        if words:
            item = " ".join(words).strip("\"'")
            item = re.sub(r"^rid of\b\s*", "", item)
            items.append(item)
    return [i.strip(" .,-") for i in items if len(i.strip(" .,-")) > 1]

def shopping_reply(text):
    """Add to, read, or remove from categorized shopping lists."""
    t = text.lower().strip()
    is_query = ((re.match(r"^(what|whats|why|how|when|where|who|show|list|read|check|see|do|does|did|is|are|was|were|am)\b", t)
                 or "?" in text)
                and not re.search(r"\b(add|put|buy|get|grab|pick up)\b", t))
    target_cat = None
    m_cat = re.search(r"\b(hardware|tools?|lumber|feed\s*(?:and|&)?\s*farm|feed|farm|tech|electronics?|computer|office|grocer(?:y|ies))\s+list\b", t)
    if not m_cat:
        m_cat = re.search(r"\b(?:to|on|in|from|off)\s+(?:the\s+|my\s+|our\s+)?(hardware|feed\s*(?:and|&)?\s*farm|feed|farm|tech|electronics?|grocery|groceries)\b", t)
    if m_cat:
        w = m_cat.group(1).lower()
        if re.search(r"^(hardware|tools?|lumber)", w): target_cat = "hardware"
        elif re.search(r"^(feed|farm)", w): target_cat = "farm"
        elif re.search(r"^(tech|electronics?|computer|office)", w): target_cat = "tech"
        elif re.search(r"^(grocer)", w): target_cat = "groceries"
    elif is_query:
        if re.search(r"\b(hardware|lumber)\b", t): target_cat = "hardware"
        elif re.search(r"\b(feed|farm)\b", t): target_cat = "farm"
        elif re.search(r"\b(tech|electronics?)\b", t): target_cat = "tech"
        elif re.search(r"\b(grocery|groceries)\b", t): target_cat = "groceries"

    if is_query:
        return shopping_list_reply(target_cat)
    if re.search(r"\b(remove|delete)\b", t) or re.search(
            r"\b(cross|scratch|strike|take)\b.{0,40}\boff\b", t):
        return shopping_remove(text, target_cat)
    items = _split_items(t, strip_list_phrases=True)
    if not items:
        return "What should I add to the shopping list?"
    return shopping_add_items(items, target_cat)

# ---------------- Lists / Pantry / Chores / Habits ----------------

def _md_lines(path):
    try:
        with open(path) as f:
            return f.read().splitlines()
    except FileNotFoundError:
        return []
    except Exception as e:
        log(f"read failed {path}: {e}")
        return []

def _slug_list_name(name):
    name = re.sub(r"\b(create|make|start|new|set up|add|put|build)\b", " ", name, flags=re.I)
    name = re.sub(r"\b(a|an|the|my|our|for|of)\b", " ", name, flags=re.I)
    name = re.sub(r"\b(list|checklist)\b", " ", name, flags=re.I)
    name = re.sub(r"\bl\b", " ", name, flags=re.I)   # orphan l from "llist" typos
    name = re.sub(r"\s+", " ", name).strip()
    return (name or "List").title()

def _known_lists():
    """(name, path) for every list note anywhere under Lists/."""
    out = []
    for root, dirs, files in os.walk(LISTS_DIR):
        for f in files:
            if f.endswith(".md") and f != "Pantry Inventory.md":
                out.append((f[:-3], os.path.join(root, f)))
    return out

def _match_list_name(t):
    """Returns (name, path) for a known or newly-derived list, or None.
    Known lists are matched recursively under Lists/ (e.g. nested folders
    like Shopping List/Christmas Lists/Sam.md match by file name).
    An explicit 'list for X' phrasing wins outright, so 'create a list for
    the Ski Trip' cannot match the Vacation Trip list via the word 'trip'."""
    m = re.search(r"\blist\s+(?:for|of|called|named)\s+(?:the |my |our |a )?(.+)", t, re.I)
    if m:
        name = re.split(r"[,;]?\s+(?:and\s+)?(?:add|put|buy|get|grab|also|with)\b",
                        m.group(1), flags=re.I)[0]
        name = _slug_list_name(name.strip(" \",.\""))
        if _tokens(name):
            return name, os.path.join(LISTS_DIR, name + ".md")
    qt = _tokens(t)
    best, best_score = None, 0
    for lname, lpath in _known_lists():
        score = len(_tokens(lname) & qt)
        if score > best_score:
            best, best_score = (lname, lpath), score
    if best:
        return best
    m = re.search(r"\b(?:my |the |our )?([\w' -]+?)\s*(?:packing list|checklist|list)\b", t, re.I)
    if m:
        name = _slug_list_name(m.group(1))
        if _tokens(name):
            return name, os.path.join(LISTS_DIR, name + ".md")
    return None

def _list_followup_text(text, user_msgs):
    """Bare 'please add X' after list talk -> combined text for list_reply."""
    if not re.match(r"^(please\s+)?add\b", text.strip(), re.I):
        return None
    if _mentions_list(text) or re.search(r"\b(shopping|grocery|supplies)\b", text.lower()):
        return None
    for c in reversed(user_msgs):
        cl = c.lower() if isinstance(c, str) else ""
        if "list" not in cl:
            continue
        m = re.search(r"\b(?:in|to|on|for)\s+(?:the\s+|my\s+|our\s+)?[\w' -]*\s*"
                      r"(?:packing\s+list|checklist|to-do\s+list|list)\b", cl)
        if m:
            items = re.sub(r"^(please\s+)", "", text.strip(), flags=re.I)
            return f"{m.group(0)}, add {items}"
    return None

def list_reply(text):
    t = text.lower().strip()
    match = _match_list_name(t)
    name, path = match if match else ("List", os.path.join(LISTS_DIR, "List.md"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    wants_create = bool(re.search(r"\b(create|make|start|set up|new)\b", t))
    is_new = not os.path.exists(path)
    if ((re.search(r"\b(clear|wipe|empty|clean out)\b", t) or "everything" in t)
            and not re.search(r"\b(?:add|put)\b", t)):
        with open(path, "w") as f:
            f.write(f"# {name}\n")
        return f"Cleared the {name} list."
    is_query = (re.match(r"^(what|whats|show|read|check|see|any)\b", t)
                and not re.search(r"\b(add|put|buy|get|grab)\b", t))
    if is_query:
        items = [l[6:].strip() for l in _md_lines(path) if l.startswith("- [ ]")]
        done = len([l for l in _md_lines(path) if l.lower().startswith("- [x]")])
        if not items:
            return (f"Nothing left on the {name} list"
                    + (f" ({done} done)." if done else "."))
        return f"{name}: {len(items)} left - " + ", ".join(items[:15]) + "."
    t = re.sub(re.escape(name.lower()) + r"\s*(packing list|checklist|list|to-do)?\b", " ", t)
    t = re.sub(r"\b(packing list|checklist|to-do list)\b", " ", t)
    if wants_create:
        # only text AFTER the list name is item content
        tail = ""
        m = re.search(r"\blist\s+(?:for|of|called|named)\s+(?:the |my |our |a )?(.+)", t, re.I)
        if m:
            parts = re.split(r"[,;]?\s+(?:and\s+)?(?:add|put|buy|get|grab|also|with)\b",
                             m.group(1), flags=re.I)
            if len(parts) > 1:
                tail = parts[-1]
        else:
            m2 = re.search(r"\b(?:my |the |our )?([\w' -]+?)\s*(?:packing list|checklist|list)\b[:,]?\s*(.*)$",
                           t, re.I)
            if m2 and m2.group(2):
                tail = m2.group(2)
    # item text: drop the list phrase (with its opener) and command words
    t_items = re.sub(r"\b(?:in|to|on|from|for)\s+(?:the\s+|my\s+|our\s+)?[\w' -]*\s*"
                     r"(?:packing\s+list|checklist|to-do\s+list|list)\b", " ", t)
    t_items = re.sub(r"\b(?:create|make|start|set up|new)\b", " ", t_items, flags=re.I)
    for w in _tokens(name):
        t_items = re.sub(r"\b" + re.escape(w) + r"\b", " ", t_items, flags=re.I)
    items = _split_items(t_items)
    if not items:
        if is_new:
            with open(path, "w") as f:
                f.write(f"# {name}\n\n")
            return f"Created the {name} list. What should I add to it?"
        if wants_create:
            return f"The {name} list already exists. What should I add to it?"
        return f"What should I add to the {name} list?"
    if not items:
        return f"What should I add to the {name} list?"
    with open(path, "a") as f:
        if is_new:
            f.write(f"# {name}\n\n")
        for i in items:
            f.write(f"- [ ] {i}\n")
    if is_new and wants_create:
        return f"Created the {name} list and added: " + ", ".join(items) + "."
    return f"Added to the {name} list: " + ", ".join(items) + "."

def _pantry_entries():
    entries, order = {}, []
    for line in _md_lines(PANTRY_NOTE):
        m = re.match(r"- (.+?)\s+-\s+(HAVE|LOW|OUT)\b(?:\s*\(([^)]*)\))?",
                     line.strip(), re.I)
        if m:
            item = m.group(1).strip().lower()
            entries[item] = (m.group(2).upper(), m.group(3) or "")
            order.append(item)
    return entries, order

def _pantry_write(entries, order):
    lines = ["# Pantry Inventory", ""]
    for item in order:
        status, when = entries[item]
        lines.append(f"- {item} - {status}" + (f" ({when})" if when else ""))
    os.makedirs(LISTS_DIR, exist_ok=True)
    with open(PANTRY_NOTE, "w") as f:
        f.write("\n".join(lines) + "\n")

def pantry_reply(text):
    import datetime
    t = text.lower().strip()
    entries, order = _pantry_entries()
    # status query: "do we have ground coffee, or did it run out?"
    if re.match(r"^(do we|do i|have we|is there|whats|what.s|did it|did we)\b", t):
        qt = _tokens(t)
        hits = [it for it in order if _tokens(it) & qt]
        if not hits:
            return "That is not tracked in the pantry inventory yet - tell me its status and I will track it."
        parts = []
        for it in hits:
            status, when = entries[it]
            if status == "HAVE":
                parts.append(f"we have {it}")
            elif status == "LOW":
                parts.append(f"{it} is marked LOW" + (f" (since {when})" if when else ""))
            else:
                parts.append(f"{it} is marked OUT - it ran out" + (f" ({when})" if when else ""))
        return ". ".join(p[:1].upper() + p[1:] for p in parts) + "."
    # set status
    status, item = None, None
    m = re.search(r"\b(?:we.re|we are|i.m|i am)?\s*(?:running\s+)?low on (.+)", t)
    if m:
        status, item = "LOW", m.group(1)
    if not item:
        m = re.search(r"\b(?:we|i)\s+(?:ran|run)\s+out of (.+)", t) or \
            re.search(r"\b(?:we.re|we are) (?:all )?out of (.+)", t)
        if m:
            status, item = "OUT", m.group(1)
    if not item:
        m = re.search(r"\b(?:bought|restocked|picked up|got) (?:some |more |another )?(.+)", t)
        if m and m.group(1).strip() in entries:
            status, item = "HAVE", m.group(1)
    if not item:
        return "Tell me a pantry item and its status - e.g. 'we are low on coffee beans'."
    item = re.sub(r"\b(today|yesterday|this week)\b", " ", item)
    item = re.sub(r"\s+", " ", item).strip(" .,-")
    now = datetime.datetime.now().strftime("%Y-%m-%d")
    key = item.lower()
    if key not in entries:
        entries[key] = (status, now)
        order.append(key)
    else:
        entries[key] = (status, now)
    _pantry_write(entries, order)
    if status in ("LOW", "OUT"):
        shopping_add_items([item])
        return (f"Marked {item} as {status} in the pantry and added it to "
                f"the grocery list.")
    shopping_remove(item)
    return f"Marked {item} as HAVE in the pantry and removed it from the grocery list."

def chore_reply(text):
    import datetime
    t = text.lower().strip()
    now = datetime.datetime.now()
    is_query = bool(re.match(r"^(when|whens|did|has|have)\b.{0,60}\b"
                             r"(chang|replac|clean|fed|feed|gave|giv|wash|mow|service|"
                             r"filter|install|fix|updat)", t)) \
        or bool(re.search(r"\b(?:what|whats)\s+(?:size|serial|model|brand|details)\b", t)) \
        or bool(re.search(r"\b(?:what|whats)\s+(?:size|serial|model|brand)\b.{0,30}\b(?:filter|was)\b", t)) \
        or bool(re.search(r"\blast time\b.{0,40}\b(chang|replac|clean|did|gave|fed|filled|wash)", t))
    if not is_query:
        try:
            intervals = _load_json(CHORE_INTERVALS_FILE, {})
            stamp10 = now.strftime("%Y-%m-%d")
            for key, entry in intervals.items():
                if _tokens(key) & _tokens(t):
                    entry["last"] = stamp10
                    intervals[key] = entry
                    _save_json(CHORE_INTERVALS_FILE, intervals)
                    break
        except Exception as e:
            log(f"interval update failed: {e}")
    if is_query:
        qt = _tokens(re.sub(r"\b(when|whens|was|the|last|time|i|did|anyone|anybody|"
                            r"we|this|evening|morning|today|tonight|did)\b", " ", t))
        best = None
        details_by_token = {}
        for key, entry in _load_json(CHORE_INTERVALS_FILE, {}).items():
            if entry.get("details"):
                for w in _tokens(key):
                    details_by_token[w] = entry["details"]
        for line in _md_lines(CHORES_NOTE):
            m = re.match(r"- (\d{4}-\d{2}-\d{2}(?: \d{2}:\d{2})?) - "
                         r"([^[]+?)(?:\s*\[(.+?)\])?\s*$", line.strip())
            if not m:
                continue
            what_line, details_line = m.group(2).strip(), m.group(3)
            score = len(qt & _tokens(what_line + " " + (details_line or "")))
            rank = (score, 1 if details_line else 0, m.group(1))
            if score and (best is None or rank > (best[0], 1 if best[3] else 0, best[1])):
                best = (score, m.group(1), what_line, details_line)
        if not best:
            return "I have no log entry matching that."
        date, what, details = best[1], best[2], best[3]
        if not details:
            for w in _tokens(what):
                if w in details_by_token:
                    details = details_by_token[w]
                    break
        day = datetime.datetime.strptime(date[:10], "%Y-%m-%d")
        days = (now.date() - day.date()).days
        detail_note = f" Details: {details}." if details else ""
        if date[:10] == now.strftime("%Y-%m-%d"):
            time_part = f" at {date[11:]}" if len(date) > 10 else ""
            return f"Yes - {what} was logged today{time_part}.{detail_note}"
        return (f"Last time: {what} - {date[:10]} "
                f"({days} day{'s' if days != 1 else ''} ago).{detail_note}")
    what = re.sub(r"^(mark|log|note) (that |down )?", "", t)
    what = re.sub(r"^(i|we) (just |finally |already )?", "", what)
    what = re.sub(r"\b(today|tonight|this (morning|afternoon|evening)|just now|earlier)\b", " ", what)
    what = re.sub(r"\s+", " ", what).strip(" .,-")
    if not what:
        return "What should I log?"
    stamp = now.strftime("%Y-%m-%d %H:%M")
    os.makedirs(os.path.dirname(CHORES_NOTE), exist_ok=True)
    # details: ', it was a 16x25x4 filter, serial 12345' etc.
    details = None
    task = what
    m_detail = re.search(r"[,;]?\s*\b(?:it|that|this|which)\s+(?:was|is)\s+"
                         r"(?:a |an |the )?(.+)$", what, re.I)
    if m_detail:
        task = what[:m_detail.start()].strip(" ,.-")
        details = m_detail.group(1).strip(" .,-")
    if not task:
        task = what
    detail_suffix = f" [{details}]" if details else ""
    # recurring interval: 'I change the X every 6 months' (+ optional last-done date)
    m_every = re.search(r"\bevery\s+(\d+|a|an|one)?\s*(day|week|month|year)s?\b", t)
    if m_every:
        import datetime
        n = m_every.group(1)
        n = 1 if n in (None, "a", "an", "one") else int(n)
        unit = m_every.group(2)
        days = {"day": 1, "week": 7, "month": 30, "year": 365}[unit.rstrip("s")]
        # optional explicit last-done date
        last_date = stamp[:10]
        m_last = re.search(
            r"\blast\s+(?:time|one)?\b[^.?!]*?"
            r"([a-z]+ \d{1,2}(?:st|nd|rd|th)?,?\s*\d{4}|\d{4}-\d{2}-\d{2})",
            t, re.I)
        if m_last:
            ds = re.sub(r"(\d)(st|nd|rd|th)", r"\1", m_last.group(1).replace(",", " "))
            for fmt in ("%B %d %Y", "%b %d %Y"):
                try:
                    last_date = datetime.datetime.strptime(ds, fmt).strftime("%Y-%m-%d")
                    break
                except ValueError:
                    pass
        # clean task name: drop the every-clause and the last-time clause
        task = re.split(r",?\s*the last time\b", task, flags=re.I)[0]
        task = re.sub(r"\b(every|each)\s+\d*\s*(day|week|month|year)s?\b", " ", task, flags=re.I)
        task = re.sub(r"\bmy\b", " ", task, flags=re.I)
        task = re.sub(r"\s+", " ", task).strip(" .,-")
        intervals = _load_json(CHORE_INTERVALS_FILE, {})
        task_words = [w for w in task.split() if len(w) > 2][:6]
        key = " ".join(task_words)
        entry = intervals.get(key, {})
        entry.update({"task": task or key, "days": max(1, n * days),
                      "last": last_date})
        if details:
            entry["details"] = details
        intervals[key] = entry
        _save_json(CHORE_INTERVALS_FILE, intervals)
        import datetime as _dt
        try:
            due = (_dt.datetime.strptime(last_date, "%Y-%m-%d")
                   + _dt.timedelta(days=max(1, n * days))).strftime("%b %d %Y")
        except Exception:
            due = f"{max(1, n * days)} days from {last_date}"
        with open(CHORES_NOTE, "a") as f:
            f.write(f"- {last_date} - {task or what}{detail_suffix}\n")
        return (f"Logged: {task or what} (last done {last_date})"
                + (f" - {details}" if details else "") + ". "
                f"I will nudge you when {max(1, n * days)} days pass - "
                f"next due around {due}.")
    with open(CHORES_NOTE, "a") as f:
        f.write(f"- {stamp} - {task or what}{detail_suffix}\n")
    return (f"Logged: {task or what} ({stamp[:10]})"
            + (f" - {details}" if details else "") + ".")

def habit_reply(text):
    import datetime
    t = text.lower().strip()
    now = datetime.datetime.now()
    # goal management
    if re.search(r"\bgoal\b", t) and re.search(r"\b(set|make|change|new|remove|clear)\b", t):
        goals = _load_json(HABIT_GOALS_FILE, {})
        if re.search(r"\b(remove|clear|delete)\b", t):
            m = re.search(r"(\w+) goal", t)
            if m and m.group(1) in goals:
                del goals[m.group(1)]
                _save_json(HABIT_GOALS_FILE, goals)
                return f"Removed the {m.group(1)} goal."
        m = re.search(r"(\w+) goal (?:to |of |is |at )?(\d+(?:\.\d+)?)\s*"
                      r"(oz|ounces?|ml|minutes?|mins?|hours?|hrs?|pages?|steps?|miles?|glasses?|cups?)", t)
        if m:
            habit, value, unit = m.group(1), float(m.group(2)), m.group(3)
            goals[habit] = {"value": value, "unit": unit}
            _save_json(HABIT_GOALS_FILE, goals)
            return f"Goal set: {value:g} {unit} of {habit} per day."
        return "Tell me like: 'set my water goal to 64 oz'."
    is_query = bool(re.match(r"^(how much|how many|did i|whats my|what.s my|how am|how did)\b", t)) \
        or "this week" in t
    if is_query:
        day, label = now.strftime("%Y-%m-%d"), "today"
        if "yesterday" in t:
            day = (now - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
            label = "yesterday"
        habit_m = re.search(r"\b(water|reading|exercise|steps|miles|meditation|pages)\b", t)
        total, unit, count = 0.0, "", 0
        for line in _md_lines(HABITS_NOTE):
            m = re.match(r"- (\d{4}-\d{2}-\d{2}) - (.+?)\s*[:=]\s*(.+)", line.strip())
            if not m or m.group(1) != day:
                continue
            habit, val = m.group(2).strip(), m.group(3).strip()
            if habit_m and habit_m.group(1) not in habit.lower():
                continue
            num = re.search(r"(\d+(?:\.\d+)?)", val)
            if num:
                total += float(num.group(1))
                um = re.search(r"[a-z%]+", val.lower())
                unit = unit or (um.group(0) if um else "")
            count += 1
        if not count:
            note = ""
            if habit_m and habit_m.group(1) in _load_json(HABIT_GOALS_FILE, {}):
                note = " You have not logged any of it yet" + (f" {label}." if label != "today" else ".")
            return f"Nothing logged for that {label}." + note
        amount = f"{total:g} {unit}".strip()
        goal_note = ""
        goals = _load_json(HABIT_GOALS_FILE, {})
        if habit_m:
            g = goals.get(habit_m.group(1))
            if g:
                pct = int(round(100 * total / g["value"])) if g["value"] else 0
                goal_note = (f" That is {pct}% of your {g['value']:g} "
                             f"{g['unit']} goal - "
                             + ("goal hit! " if pct >= 100 else "keep going! "))
            else:
                goal_note = (" No goal is configured yet - tell me one and "
                             "I will track it.")
        return f"{label.capitalize()}: {amount}" + (f" across {count} entr{'y' if count == 1 else 'ies'}." if count != 1 else ".") + goal_note
    # log
    habit, amount = None, ""
    m = re.search(r"\bdr(?:ank|ink)\s+(\d+(?:\.\d+)?)\s*(oz|ounces?|ml|liters?|cups?|glasses?)\b(?:\s+of\s+)?\s*(\w+)?", t)
    if m:
        habit = (m.group(3) or "water").strip()
        amount = f"{m.group(1)} {m.group(2)}"
    if not habit:
        m = re.search(r"\b(\d+(?:\.\d+)?)\s*(minutes?|mins?|hours?|hrs?|pages?|steps?|miles?)\b"
                      r"(?:\s+(?:of|for)\s+)?([a-z ]+)?", t)
        if m and m.group(3):
            habit = re.sub(r"\b(of|for)\b", " ", m.group(3))
            habit = re.sub(r"\s+", " ", habit).strip(" .,-")
            amount = f"{m.group(1)} {m.group(2)}"
    if not habit:
        return ("Tell me the habit and amount - e.g. 'log that I drank 24 "
                "ounces of water' or 'log 45 minutes of reading'.")
    habit = re.sub(r"\b(for today|today|yesterday)\b", " ", habit)
    habit = re.sub(r"\s+", " ", habit).strip(" .,-")
    stamp = now.strftime("%Y-%m-%d %H:%M")
    os.makedirs(os.path.dirname(HABITS_NOTE), exist_ok=True)
    with open(HABITS_NOTE, "a") as f:
        f.write(f"- {stamp[:10]} - {habit}: {amount}\n")
    return f"Logged {habit}: {amount} ({stamp[:10]})."

def _log_voice_exchange(heard, reply):
    """Append every voice/HA exchange to a daily Obsidian audit note."""
    import datetime
    try:
        d = datetime.datetime.now()
        path = os.path.join(VOICE_LOG_DIR, f"{d:%Y-%m-%d}.md")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if not os.path.exists(path):
            with open(path, "w") as f:
                f.write(f"# House Voice Log - {d:%Y-%m-%d}\n\n")
        heard = " ".join(str(heard).split())[:120]
        reply = " ".join(str(reply).split())[:200]
        with open(path, "a") as f:
            f.write(f"- **{d:%H:%M}** heard: {heard}\n"
                    f"  - replied: {reply}\n")
    except Exception as e:
        log(f"voice log write failed: {e}")

def _load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default

def _save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f)

def _days_between(date_str, now=None):
    import datetime
    now = now or datetime.datetime.now()
    try:
        d = datetime.datetime.strptime(date_str[:10], "%Y-%m-%d")
        return (now.date() - d.date()).days
    except Exception:
        return None

def _pantry_set(item, status):
    """Set a pantry item's status (HAVE/LOW/OUT) without replying."""
    import datetime
    entries, order = _pantry_entries()
    key = item.lower().strip()
    now = datetime.datetime.now().strftime("%Y-%m-%d")
    if key not in entries:
        entries[key] = (status, now)
        order.append(key)
    else:
        entries[key] = (status, now)
    _pantry_write(entries, order)

def chore_overdue_list():
    """[str] for chores whose interval has elapsed."""
    import datetime
    out = []
    now = datetime.datetime.now()
    for task, info in _load_json(CHORE_INTERVALS_FILE, {}).items():
        days = _days_between(info.get("last", ""), now)
        if days is None or days < info.get("days", 9999) or info.get("recurring_id"):
            continue
        last_note = f"{days} days ago" if days is not None and days >= 0 else "unknown"
        out.append(f"{info.get('task', task)} (every {info['days']} days - last {last_note})")
    return out

def paperless_answer(q):
    """Search Paperless-ngx documents and answer from their text."""
    tok = ""
    try:
        tok = _paperless_token()
    except Exception:
        return "Paperless is not configured (PAPERLESS_TOKEN not set)."
    try:
        url = (PAPERLESS + "/api/documents/?query=" + urllib.parse.quote(q)
               + "&page_size=3")
        req = urllib.request.Request(url, headers={"Authorization": "Token " + tok})
        with urllib.request.urlopen(req, timeout=30) as r:
            results = json.loads(r.read()).get("results", [])
    except Exception as e:
        return f"Paperless search failed: {e}"
    if not results:
        return f"No Paperless documents match '{q}'."
    context = []
    for doc in results[:3]:
        try:
            req = urllib.request.Request(
                f"{PAPERLESS}/api/documents/{doc['id']}/",
                headers={"Authorization": "Token " + tok})
            with urllib.request.urlopen(req, timeout=30) as r:
                full = json.loads(r.read())
            content = (full.get("content") or "")[:4000]
            context.append(f"[{full.get('title', 'doc')}]\n{content}")
        except Exception as e:
            log(f"paperless fetch {doc['id']} failed: {e}")
    if not context:
        return "Found matching documents but could not read their text."
    backend = pick_extract_backend()
    if not backend:
        return "Found documents: " + ", ".join(d.get("title", "?") for d in results[:3])
    base, model, name = backend
    prompt = ("Answer the question using ONLY these document texts. Be concise "
              "and spoken-style (2-5 sentences). Name the documents you used.\n\n"
              + "\n\n".join(context) + "\n\nQuestion: " + q)
    try:
        answer = ask_llm(base, model, prompt)
        titles = ", ".join(d.get("title", "?") for d in results[:3])
        return f"{answer.strip()}\n\n(from Paperless: {titles})"
    except Exception as e:
        log(f"paperless answer failed: {e}")
        return ("Found: " + ", ".join(d.get("title", "?") for d in results[:3])
                + " - but the answer generation failed.")

def brief_reply():
    """On-demand briefing: agenda, weather, pantry, chores, habits."""
    parts = []
    try:
        parts.append(agenda_reply(1))
    except Exception as e:
        parts.append(f"Agenda failed: {e}")
    try:
        w = weather_reply()
        if w:
            parts.append(w.split("\n")[0])
    except Exception as e:
        log(f"brief weather failed: {e}")
    lows = []
    entries, order = _pantry_entries()
    for it in order:
        if entries[it][0] in ("LOW", "OUT"):
            lows.append(f"{it} ({entries[it][0].lower()})")
    if lows:
        parts.append("Pantry needs attention: " + ", ".join(lows) + ".")
    overs = chore_overdue_list()
    if overs:
        parts.append("Overdue chores: " + "; ".join(overs) + ".")
    try:
        up_maint = chore_upcoming_list(days_ahead=7)
        if up_maint:
            parts.append("Upcoming maintenance: " + "; ".join(up_maint) + ".")
    except Exception:
        pass
    try:
        low_bats = ha_low_batteries(threshold=20)
        if low_bats:
            parts.append("Low batteries (<20%): " + ", ".join(low_bats) + ".")
    except Exception:
        pass
    import datetime
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    totals = {}
    for line in _md_lines(HABITS_NOTE):
        m = re.match(r"- (\d{4}-\d{2}-\d{2}) - (.+?)\s*[:=]\s*(.+)", line.strip())
        if m and m.group(1) == today:
            num = re.search(r"(\d+(?:\.\d+)?)", m.group(3))
            if num:
                totals[m.group(2).strip()] = totals.get(m.group(2).strip(), 0) + float(num.group(1))
    if totals:
        parts.append("Today: " + ", ".join(f"{k}: {v:g}" for k, v in totals.items()) + ".")
    return "\n\n".join(parts)

def ask_llm_stream(base, model, user_text, history=None):
    """Yield content deltas from an OpenAI-compatible streaming chat."""
    if "qwen" in model.lower():
        user_text = user_text + " /no_think"
    msgs = list(history or [])[-6:] + [{"role": "user", "content": user_text}]
    _mctx = memory_context(user_text)
    if _mctx:
        msgs = [{"role": "system", "content": _mctx}] + msgs
    sys_prompt = SYSTEM_PROMPT
    body = json.dumps({
        "model": model,
        "messages": [{"role": "system", "content": sys_prompt}] + msgs,
        "temperature": 0.7, "stream": True,
    }).encode()
    req = urllib.request.Request(base + "/chat/completions", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as r:
        for raw in r:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            try:
                d = json.loads(payload)
                delta = d["choices"][0].get("delta", {}).get("content")
            except Exception:
                continue
            if delta:
                yield delta

def query_antigravity_thinkbox(prompt, timeout=180):
    """Run a query through the Antigravity CLI (Gemini 3.8 Flash) via SSH."""
    import shlex
    import subprocess
    hosts = [
        ("archbox", 'unset SSH_CLIENT SSH_CONNECTION SSH_TTY; export DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus XDG_RUNTIME_DIR=/run/user/1000 PATH="$HOME/.local/share/mise/shims:$HOME/.gemini/antigravity-cli/bin:$HOME/.local/bin:$HOME/bin:/usr/local/bin:/opt/homebrew/bin:$PATH"; '),
        ("thinkbox", 'unset SSH_CLIENT SSH_CONNECTION SSH_TTY; export PATH="$HOME/.gemini/antigravity-cli/bin:$HOME/.local/bin:$HOME/bin:/usr/local/bin:/opt/homebrew/bin:$PATH"; '),
        ("thinkbox-lan", 'unset SSH_CLIENT SSH_CONNECTION SSH_TTY; export PATH="$HOME/.gemini/antigravity-cli/bin:$HOME/.local/bin:$HOME/bin:/usr/local/bin:/opt/homebrew/bin:$PATH"; '),
    ]
    last_err = None
    for host, env_prefix in hosts:
        remote_cmd = (
            f"{env_prefix}"
            'if command -v agy >/dev/null 2>&1; then '
            f"  agy --model gemini-3.8-flash --effort medium --dangerously-skip-permissions --print {shlex.quote(prompt)} || agy --dangerously-skip-permissions --print {shlex.quote(prompt)}; "
            'elif command -v antigravity >/dev/null 2>&1; then '
            f"  antigravity --model gemini-3.8-flash --effort medium --dangerously-skip-permissions --print {shlex.quote(prompt)} || antigravity --dangerously-skip-permissions --print {shlex.quote(prompt)}; "
            'else '
            f'  echo "Error: Neither agy nor antigravity found on {host} PATH (\\$PATH)" >&2; exit 127; '
            'fi'
        )
        cmd = ["ssh", "-o", "ConnectTimeout=6", host, remote_cmd]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            if res.returncode == 0:
                output = res.stdout.strip()
                if not output and res.stderr:
                    output = res.stderr.strip()
                if len(output) > 3500:
                    output = output[:3500] + "\n\n... (output truncated for Signal)"
                return output if output else "(Antigravity CLI completed with empty output)"
            else:
                err = res.stderr.strip() or res.stdout.strip()
                if res.returncode == 127 or any(k in err for k in ("Could not resolve", "Connection timed out", "No route to host", "not found")):
                    last_err = f"{host}: {err}"
                    continue
                return f"[Antigravity {host} error]:\n{err}"
        except subprocess.TimeoutExpired:
            return f"[Antigravity {host}]: Query timed out after {timeout} seconds."
        except Exception as e:
            last_err = f"{host}: {e}"
            continue
    return f"[Antigravity]: Could not reach any Antigravity host ({last_err})"


_TRACKING_RE = re.compile(
    r"\b(1Z[0-9A-Z]{16}|[A-Z]{2}\d{9}[A-Z]{2}|\d{20,22}|9\d{15,21})\b")

def odysseus_note(title, body, label="house"):
    """POST a note into Odysseus on archbox. Markdown is the caller's job.

    ssh joins arguments and the remote shell splits them, so the remote
    command is one shell-quoted string.
    """
    import shlex
    script = "/home/archbox/.local/bin/odysseus-note"
    remote = " ".join(shlex.quote(part) for part in [
        script, "--title", (title or "Note")[:120],
        "--body", (body or "")[:4000], "--label", label or "house"])
    try:
        proc = subprocess.run(
            ARCHBOX_SSH + [remote],
            capture_output=True, text=True, timeout=25)
    except Exception:
        return "Odysseus was not updated (archbox unreachable). Markdown copy kept."
    out = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
    if proc.returncode == 0 and "odysseus-ok" in out:
        return "Saved in Odysseus as well."
    if "odysseus-down" in out:
        return ("Odysseus is down (archbox asleep or the service is stopped). "
                "Markdown copy kept.")
    return "Odysseus did not accept the note. Markdown copy kept."

def _write_markdown_note(folder, title, body):
    import datetime
    os.makedirs(folder, exist_ok=True)
    now = datetime.datetime.now()
    slug = re.sub(r"[^a-zA-Z0-9 ]", "", title)[:40].strip() or "note"
    path = os.path.join(folder, f"{now.strftime('%Y-%m-%d')} {slug}.md")
    n = 2
    while os.path.exists(path):
        path = os.path.join(folder, f"{now.strftime('%Y-%m-%d')} {slug} {n}.md")
        n += 1
    with open(path, "w") as f:
        f.write(f"# {title}\n\n{body}\n")
    return path

def package_note(text):
    """Record a delivery the user actually mentioned. Never invent tracking."""
    text = (text or "").strip()
    if not text:
        return ("Usage: /package <what arrived or is expected>. "
                "I only keep tracking numbers you include yourself.")
    found = _TRACKING_RE.findall(text)
    body = text
    if found:
        body += "\n\nTracking mentioned: " + ", ".join(dict.fromkeys(found))
    _write_markdown_note(DELIVERIES_DIR, "Delivery", body)
    ody = odysseus_note("Delivery", body, "deliveries")
    return f"Delivery note saved. {ody}"

def ask_small(prompt, history=None):
    prompt = (prompt or "").strip()
    if not prompt:
        return ("Usage: /ask <prompt> - small model only "
                "(thinkbox qwen3:4b, else homebrain llama3.2:3b).")
    backend = pick_small_backend()
    if not backend:
        return ("No small model reachable (thinkbox qwen3:4b and "
                "homebrain llama3.2:3b are both down).")
    base, model, name = backend
    if name == "loq" or "8b" in model.lower() or "14b" in model.lower():
        return "Refusing /ask: that would not be the small model."
    log(f"/ask via {name} {model}")
    try:
        reply = ask_llm(base, model, prompt, history=history)
    except Exception as exc:
        return f"Small model {name} failed: {exc}"
    tag = TAGS.get(name, "")
    if tag and tag not in reply:
        reply = reply.rstrip() + f"\n\n{tag}"
    return reply

_VAULT_STOP = set(
    "the a an of to for my in on and or what who where when how did does do "
    "is are was were about from with your notes note vault obsidian tell me "
    "please any have has this that".split())

def vault_keywords(question):
    words = re.findall(r"[A-Za-z0-9']{3,}", question.lower())
    keys = [w for w in words if w not in _VAULT_STOP]
    return keys[:6] or words[:4]

def vault_search_snippets(question, limit=5):
    keys = vault_keywords(question)
    if not keys or not os.path.isdir(NOTES_DIR):
        return []
    hits = []
    seen = 0
    for dirpath, dirs, files in os.walk(NOTES_DIR):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in
                   ("node_modules", ".trash")]
        for fn in files:
            if not fn.endswith(".md"):
                continue
            seen += 1
            if seen > 4000:
                break
            path = os.path.join(dirpath, fn)
            try:
                if os.path.getsize(path) > 400_000:
                    continue
                raw = open(path, errors="replace").read()
            except Exception:
                continue
            low = raw.lower()
            score = sum(low.count(k) for k in keys)
            if score <= 0:
                continue
            idx = min((low.find(k) for k in keys if k in low), default=0)
            if idx < 0:
                idx = 0
            start = max(0, idx - 160)
            snippet = re.sub(r"\s+", " ", raw[start:start + 420]).strip()
            hits.append((score, os.path.relpath(path, NOTES_DIR), snippet))
        if seen > 4000:
            break
    hits.sort(key=lambda item: -item[0])
    return hits[:limit]

def vault_reply(question):
    question = (question or "").strip()
    if not question:
        return "Usage: /vault <question> - answers from Obsidian note contents, on this server only."
    hits = vault_search_snippets(question)
    if not hits:
        return "No matching note contents in the Obsidian vault."
    excerpts = "\n\n".join(f"[{name}] {snip}" for _score, name, snip in hits)[:3200]
    backend = pick_small_backend()
    if not backend:
        return ("Small model is offline, so here are the note excerpts:\n" + excerpts[:1800])
    base, model, name = backend
    if name == "loq" or "8b" in model.lower() or "14b" in model.lower():
        return "Refusing vault answer: that would not stay on the small local model."
    prompt = (
        "Answer using ONLY these excerpts from the user's own notes. "
        "If they do not contain the answer, say the notes do not say. "
        "Do not invent facts. Plain text, under 120 words.\n\n"
        f"Question: {question}\n\nExcerpts:\n{excerpts}")
    log(f"/vault via {name} {model} hits={len(hits)}")
    try:
        ans = ask_llm(base, model, prompt, history=None)
    except Exception as exc:
        return f"Note search worked but the small model failed ({exc}).\n" + excerpts[:1500]
    tag = TAGS.get(name, "")
    return ans.strip() + "\n\nFrom your notes." + (f" {tag}" if tag else "")

def _tailscale_json():
    local_cmds = [
        ["tailscale", "status", "--json"],
        ["/usr/bin/tailscale", "status", "--json"],
        ["/Applications/Tailscale.app/Contents/MacOS/Tailscale", "status", "--json"],
    ]
    for cmd in local_cmds:
        try:
            out = subprocess.check_output(cmd, text=True, timeout=12, stderr=subprocess.DEVNULL)
        except Exception:
            continue
        if out.lstrip().startswith("{"):
            return json.loads(out), "homebrain"
    try:
        out = subprocess.check_output(
            ARCHBOX_SSH + ["/usr/bin/tailscale", "status", "--json"],
            text=True, timeout=15, stderr=subprocess.DEVNULL)
    except Exception as exc:
        return None, f"archbox unreachable ({type(exc).__name__})"
    if out.lstrip().startswith("{"):
        return json.loads(out), "archbox"
    return None, "tailscale returned no status"

def net_reply():
    data, source = _tailscale_json()
    if not isinstance(data, dict):
        return f"Network check failed: {source}."
    peers = list((data.get("Peer") or {}).values())
    up, down = [], []
    office = None
    for peer in peers:
        name = peer.get("HostName") or (peer.get("DNSName") or "unknown").split(".")[0]
        online = bool(peer.get("Online"))
        (up if online else down).append(name)
        if str(name).lower() == "office":
            office = online
    up.sort(key=str.lower)
    down.sort(key=str.lower)
    if office is True:
        office_line = "office: UP"
    elif office is False:
        office_line = "office: DOWN"
    else:
        office_line = "office: not in this tailnet view"
    lines = [
        f"Tailscale via {source}. {office_line}.",
        ("Up (%d): " % len(up)) + (", ".join(up) if up else "none"),
        ("Down (%d): " % len(down)) + (", ".join(down) if down else "none"),
    ]
    return "\n".join(lines)

def voice_reply(arg):
    arg = (arg or "").strip().lower()
    if arg not in ("", "on", "off", "status", "toggle"):
        return ("Usage: /voice [on|off|status] - spoken replies on archbox. "
                "Same switch as Super+Shift+V.")
    mode = "toggle" if arg in ("", "toggle") else arg
    try:
        proc = subprocess.run(
            ARCHBOX_SSH + ["/home/archbox/.local/bin/house-voice-speak-toggle", mode],
            capture_output=True, text=True, timeout=15)
    except Exception as exc:
        return f"Could not reach archbox to change spoken replies ({type(exc).__name__})."
    out = (proc.stdout or "").strip().splitlines()
    line = out[-1].strip() if out else ""
    if proc.returncode != 0 or not line:
        return "Could not flip spoken replies. archbox may be asleep."
    return line + ". Hotkey on the laptop is Super+Shift+V. The Home key still toggles the listener."

def whisper_health():
    try:
        sock = socket.create_connection(("127.0.0.1", 10300), timeout=5)
    except Exception as exc:
        return f"Whisper on homebrain:10300 is down ({type(exc).__name__})."
    try:
        data = b"{}"
        header = json.dumps({"type": "describe", "data_length": len(data)}) + "\n"
        sock.sendall(header.encode() + data)
        reader = sock.makefile("rb")
        hdr = json.loads(reader.readline() or b"{}")
        blob = reader.read(hdr.get("data_length") or 0)
        info = json.loads(blob or b"{}")
    except Exception as exc:
        return f"Whisper on homebrain:10300 accepted TCP but describe failed ({type(exc).__name__})."
    finally:
        try:
            sock.close()
        except Exception:
            pass
    engines = info.get("asr") or []
    if not engines:
        return "Whisper port is up but reported no speech engine."
    engine = engines[0]
    models = [m.get("name") for m in (engine.get("models") or []) if m.get("installed") and m.get("name")]
    shown = ", ".join(models[:6]) or "none installed"
    version = engine.get("version") or ""
    return f"Whisper OK on homebrain:10300 - {engine.get('name')} {version}, models: {shown}."

def _ntfy_url():
    return NTFY_URL.strip()

def _ntfy_load_seen():
    try:
        state = json.load(open(NTFY_SEEN_FILE))
        if isinstance(state, dict):
            state.setdefault("ids", [])
            return state
    except Exception:
        pass
    return {"bootstrapped": False, "ids": []}

def _ntfy_save_seen(state):
    os.makedirs(os.path.dirname(NTFY_SEEN_FILE), exist_ok=True)
    state["ids"] = list(state.get("ids") or [])[-400:]
    tmp = NTFY_SEEN_FILE + ".tmp"
    with open(tmp, "w") as handle:
        json.dump(state, handle)
    os.replace(tmp, NTFY_SEEN_FILE)

def _ntfy_messages(since):
    base = _ntfy_url()
    if not base:
        raise RuntimeError("ntfy url missing")
    url = base.rstrip("/") + "/json?poll=1&since=" + urllib.parse.quote(str(since))
    headers = {"User-Agent": "house-bot"}
    if NTFY_TOKEN:
        headers["Authorization"] = "Bearer " + NTFY_TOKEN
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=25) as resp:
        raw = resp.read().decode("utf-8", "replace")
    out = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if obj.get("event") == "message" and obj.get("id"):
            out.append(obj)
    return out

def _fmt_alert(msg):
    title = (msg.get("title") or "ntfy").strip()
    body = (msg.get("message") or "").strip().replace("\r", "")
    if len(body) > 700:
        body = body[:700] + "..."
    return f"ntfy: {title}\n{body}" if body else f"ntfy: {title}"

def alerts_reply():
    try:
        msgs = _ntfy_messages("48h")
    except Exception as exc:
        return f"Could not read ntfy ({type(exc).__name__})."
    if not msgs:
        return "No ntfy messages in the last 48 hours."
    lines = ["Recent ntfy messages (these are not sent again as new alerts):"]
    for msg in msgs[-6:]:
        lines.append("- " + _fmt_alert(msg).replace("\n", " | ")[:400])
    return "\n".join(lines)

def run_ntfy_alerts():
    """Poll the configured ntfy topic. One Signal per new id. No replay storm."""
    time.sleep(8)
    while True:
        try:
            state = _ntfy_load_seen()
            since = "72h" if not state.get("bootstrapped") else "12h"
            try:
                msgs = _ntfy_messages(since)
            except Exception as exc:
                log(f"ntfy poll failed: {type(exc).__name__}")
                time.sleep(120)
                continue
            known = set(state.get("ids") or [])
            if not state.get("bootstrapped"):
                state["ids"] = [m["id"] for m in msgs]
                state["bootstrapped"] = True
                _ntfy_save_seen(state)
                log(f"ntfy bootstrap: marked {len(state['ids'])} existing, not forwarding")
            else:
                fresh = [m for m in msgs if m.get("id") not in known]
                for msg in fresh:
                    if ALERT_RECIPIENT:
                        nudge_send(ALERT_RECIPIENT, _fmt_alert(msg)[:1500])
                    known.add(msg["id"])
                    log("ntfy forwarded one alert")
                if fresh:
                    state["ids"] = list(known)
                    _ntfy_save_seen(state)
        except Exception as exc:
            log(f"ntfy loop error: {type(exc).__name__}")
        time.sleep(90)


def house_reply(text, cmd, hist, action=None, arg=None):
    """Shared reply engine: used by the Signal loop and the HTTP API.
    hist must already include the current user message as its last item.
    Returns (reply, attachment)."""
    big_mode = False  # /big removed; pick_backend ignores this
    reply = None
    attachment = None

    # capability questions: deterministic answer (vague questions make
    # small models hallucinate wild replies)
    if re.search(r"can you (view|see|access|read|search|look (at|through)|go (into|through))|what can you do|do you have access|my files and answer", cmd):
        reply = ("Naturally. I can search, read, and summarize the files "
                 "on your home server (it syncs your laptop and desktops), "
                 "plus manage your calendar and tasks. Try:\n"
                 "  'find my resume'\n"
                 "  'what does my 1040 say'\n"
                 "  'summarize my lease'\n"
                 "  'send me my w2'\n"
                 "  'event dentist friday 2pm'\n"
                 "  'task replace furnace filter'\n"
                 "Give me a specific question about a file and I'll dig in.")
        return reply, None

    # identity questions: answer deterministically (models hallucinate
    # their own identity - llama models claim to be llama 2, bert, etc.)
    if re.search(r"(what|which) (model|llm)|who (are|made) you|(are|aren't|arent) (you )?(chatgpt|gpt|bert|gemini|claude|llama|an? (ai|llm))", cmd):
        b = pick_backend(big_mode)
        if b:
            base, model, bname = b
            where = {"laptop": "the Lenovo LOQ GPU", "loq": "the Lenovo LOQ GPU",
                     "mac": "the Mac M1", "debby": "the homebrain CPU"}.get(bname, bname)
            reply = (f"I am House - think of me as Mycroft Holmes to your "
                     f"Sherlock: the older brother, the clever one who "
                     f"actually answers his correspondence. At the moment I "
                     f"am thinking with {model} on {where}; the model may "
                     f"vary with which machine is awake, but my abilities "
                     f"do not.")
        else:
            reply = "I'm House, but no LLM backend is currently reachable."
        return reply, None

    if cmd in ("/server", "/server status"):
        return server_status(), None
    elif cmd in ("/syncthing", "/sync"):
        return syncthing_status(), None
    elif cmd.startswith("/photos ") or cmd.startswith("/photo ") or cmd.startswith("/immich "):
        q = text.split(" ", 1)[1].strip() if " " in text.strip() else ""
        return photos_reply(q), None
    elif cmd in ("/photos", "/photo", "/immich"):
        return photos_reply(""), None
    elif cmd in ("/batteries", "/battery"):
        return ha_battery_report(), None
    elif cmd in ("/maintenance", "/filters"):
        return maintenance_status(), None
    elif cmd.startswith("/find "):
        reply = search_files(text[6:].strip())
    elif cmd == "/find":
        reply = "Usage: /find <text> - searches filenames and file contents."
    elif cmd.startswith("/send "):
        q = text[6:].strip()
        paths = find_file_paths(q)
        if not paths:
            reply = f"No files matching '{q}'. Try /find {q} for content matches."
        else:
            best = paths[0]
            size = os.path.getsize(best)
            if size > MAX_ATTACHMENT_BYTES:
                reply = (f"Found {best} but it's {size/1e6:.0f} MB - too big "
                         f"for Signal (>90 MB). It's on the server at:\n{best}")
            else:
                others = [os.path.basename(p) for p in paths[1:]]
                extra = ("\nAlso found: " + ", ".join(others[:3])) if others else ""
                attachment = best
                reply = (f"Sending: {os.path.basename(best)}" + extra) if attachment \
                        else reply
    elif cmd == "/send":
        reply = "Usage: /send <text> - find a file and send it to you."
    elif cmd == "/organize":
        reply = organize_plan(go=False)
    elif cmd.startswith("/organize"):
        arg = text[len("/organize"):].strip().lower()
        if arg == "go":
            reply = organize_plan(go=True)
        else:
            reply = ("Usage: /organize shows a dry-run, '/organize go' applies it.\n"
                     "Note: /tank/data syncs to your laptop - organizing here "
                     "reorganizes everywhere.")
    elif cmd.startswith("/agenda"):
        days = 7 if "week" in cmd else 1
        reply = agenda_reply(days)
    elif cmd in ("/reminders", "/recurring"):
        reply = reminders_list_reply()
    elif cmd.startswith("/unremind"):
        reply = recurring_cancel_reply(text)
    elif cmd.startswith("/remind "):
        reply = add_reminder(text[len("/remind "):].strip())
    elif cmd == "/remind":
        reply = "Usage: /remind <when> to <what> - e.g. /remind in 2 hours check the dryer"
    elif cmd.startswith("/note "):
        reply = note_add(text[len("/note "):].strip())
    elif cmd == "/note":
        reply = "Usage: /note <text> - save a quick note"
    elif cmd == "/briefing":
        subprocess.Popen(["python3", os.path.expanduser(
            "~/.local/bin/house-jobs.py"), "briefing"])
        reply = "Briefing incoming."
    elif cmd.startswith("/weather"):
        city = text[len("/weather"):].strip()
        reply = weather_reply(city)
    elif cmd.startswith("/web "):
        q = text[len("/web "):].strip()
        reply = web_reply(q)
    elif cmd.startswith("/event ") or cmd.startswith("event/") or cmd.startswith("event:"):
        # tolerate 'event/ ...' typos as well as the proper '/event ...'
        arg = text.split("/", 1)[-1].strip() if "/" in cmd[:7] else text[len("/event "):].strip()
        reply = add_calendar_item("EVENT", arg)
    elif cmd == "/event":
        reply = "Usage: /event <description with date/time> - e.g. /event dentist tuesday 2pm"
    elif cmd.startswith("/task "):
        reply = add_calendar_item("TASK", text[len("/task "):].strip())
    elif cmd == "/task":
        reply = "Usage: /task <description> - e.g. /task replace furnace filter"
    elif cmd.startswith("/summarize "):
        reply = summarize_file(text[len("/summarize "):].strip())
    elif cmd == "/summarize":
        reply = "Usage: /summarize <text> - find the best-matching file and summarize its contents."
    elif cmd in ("/alerts", "/alert"):
        reply = alerts_reply()
    elif cmd == "/ask" or cmd.startswith("/ask "):
        asked = text.strip().split(None, 1)
        reply = ask_small(asked[1].strip() if len(asked) > 1 else "",
                          history=(hist or [])[:-1])
    elif cmd == "/package" or cmd.startswith("/package "):
        packed = text.strip().split(None, 1)
        reply = package_note(packed[1].strip() if len(packed) > 1 else "")
    elif cmd in ("/net", "/network"):
        reply = net_reply()
    elif cmd == "/vault" or cmd.startswith("/vault "):
        asked = text.strip().split(None, 1)
        reply = vault_reply(asked[1].strip() if len(asked) > 1 else "")
    elif cmd == "/voice" or cmd.startswith("/voice "):
        asked = text.strip().split(None, 1)
        reply = voice_reply(asked[1].strip() if len(asked) > 1 else "")
    elif cmd in ("/whisper",) or cmd.startswith("/whisper "):
        reply = whisper_health()
    elif cmd == "/calendar" or cmd.startswith("/calendar"):
        reply = agenda_reply(7)
    elif cmd == "/status":
        backend = pick_backend(big_mode)
        _cal_up, _cal_detail = radicale_status()
        reply = ("Backend: " + (f"{backend[2]} -> {backend[1]}" if backend
                 else "none - Ollama unreachable")
                 + "\nChat is qwen3:8b on omarchybox when it is up, "
                 "else qwen3:4b-instruct on thinkbox. /big is retired.\n"
                 f"Calendar: {_cal_detail}. /calendar for today and this week, /net for Tailscale.")
    elif cmd.startswith("/bigbrain ") or cmd == "/bigbrain":
        prompt = text[len("/bigbrain"):].strip()
        if not prompt:
            reply = "Usage: /bigbrain <prompt> - query Gemini 3.8 Flash via Antigravity CLI."
        else:
            log(f"querying Antigravity CLI on thinkbox: {prompt[:80]!r}")
            reply = query_antigravity_thinkbox(prompt)
    elif cmd == "/big" or cmd.startswith("/big "):
        reply = ("/big is retired. Chat always uses qwen3:8b on omarchybox "
                 "when it is up, otherwise qwen3:4b-instruct on thinkbox.")
    elif cmd == "/fast" or cmd.startswith("/fast "):
        reply = ("/fast is retired. That is already the only chat path: "
                 "qwen3:8b on omarchybox when it is up, otherwise "
                 "qwen3:4b-instruct on thinkbox.")
    elif cmd in ("/help", "/start"):
        reply = ("Commands:\n /bigbrain <prompt> - query Gemini 3.8 Flash via Antigravity\n"
                 " /find <text> - search files\n"
                 " /send <text> - get a file sent to you\n"
                 " /organize - dry-run sort of /tank/data by type\n"
                 " /organize go - apply it\n"
                 " /summarize <text> - summarize best-matching file\n"
                 " /photos <query> - Immich photo search\n"
                 " /syncthing - Pixel + Syncthing folder health\n"
                 " /event <what and when> - add calendar event\n"
                 " /task <what> - add a task\n"
                 " /weather [city] - current conditions + 3-day forecast\n"
                 " /web <query> - search the web\n"
                 " /agenda [week] - upcoming events + tasks\n"
                 " /remind <when> to <what> - timed reminder\n"
                 " /reminders - list reminders (recurring + one-time)\n"
                 " /unremind <words> - stop a recurring reminder\n"
                 " recurring: 'I change the furnace filter every 3 months', 'remind me every monday at 7pm to take out the trash'\n"
                 " /note <text> - quick note capture\n"
                 " /briefing - send the morning briefing now\n"
                 " /status\n"
                 " /alerts - recent ntfy warnings (new ones also arrive on their own)\n"
                 " /ask <prompt> - small model only (4B, else 3B; never the 8B)\n"
                 " /package <text> - delivery note in Odysseus (markdown if it is down)\n"
                 " /net - Tailscale peers and whether office is up\n"
                 " /vault <question> - answer from Obsidian note contents, local only\n"
                 " /voice [on|off|status] - spoken replies (same switch as Super+Shift+V)\n"
                 " /whisper - homebrain whisper health\n"
                 " /calendar - Radicale plus today and this week\n"
                 "Or just ask in plain language - e.g. 'where is my resume?' "
                 "- and I'll figure out what you mean.")
    else:
        backend = pick_backend(big_mode)
        if not backend:
            reply = ("Sorry - no LLM backend reachable right now "
                     "(laptop Ollama and Mac Ollama both down).")
        else:
            base, model, name = backend
            if action is None:
                fast = pick_fast_backend() or (base, model, name)
                action, arg = classify_intent(fast[0], fast[1], text, hist)
                log(f"intent: {action} ({arg!r}) via {name}")
            _need = _ACTION_CAPS.get(action)
            if _need and not CAPS.get(_need, True):
                return (f"{_need.title()} isn't enabled on this install.", None)
            if action == "FIND" and arg:
                reply = search_files(clean_query(arg) or arg)
                if reply.startswith("No matches") or reply.startswith("(file service"):
                    relaxed = relaxed_search(arg)
                    if relaxed:
                        if re.search(r"most recent|latest|newest|recently saved", arg.lower()):
                            reply = (f"Most recent match: {relaxed[0]}\n"
                                     f"(ask me to summarize it, or say 'send it')\n"
                                     f"Other matches:\n"
                                     + "\n".join(f"  {p}" for p in relaxed[1:4]))
                        else:
                            reply = ("Found by relaxed filename search:\n"
                                     + "\n".join(f"  {p}" for p in relaxed))
                    else:
                        sem = semantic_fallback(arg)
                        if sem:
                            reply = ("No keyword matches - closest by MEANING:\n"
                                     + sem
                                     + "\n(ask me to summarize any of these)")
                # photo search: by meaning, over the camera mirror
                if re.search(r"\b(photos?|pictures?|screenshots?)\b", arg.lower()) or reply.startswith("No matches") or reply.startswith("Found by relaxed"):
                    photos = photo_search(arg)
                    if photos:
                        plines = [f"  {p['path'].split('/')[-1]} ({p['taken'][:10]})" for p in photos[:4]]
                        reply = (reply + "\n\nPhotos found:\n" + "\n".join(plines)) if not reply.startswith("Photos found:") else reply
            elif action == "SEND" and arg:
                paths = find_file_paths(clean_query(arg) or arg) or relaxed_search(arg)
                if paths and os.path.getsize(paths[0]) <= MAX_ATTACHMENT_BYTES:
                    attachment = paths[0]
                    reply = f"Sending: {os.path.basename(paths[0])}"
                elif paths:
                    reply = f"Found {paths[0]} but it's too big for Signal."
                else:
                    reply = search_files(arg)
            elif action == "ORGANIZE":
                reply = organize_plan(go=False)
            elif action == "SUMMARIZE" and arg:
                reply = summarize_file(clean_query(arg) or arg)
            elif action == "WEATHER":
                reply = weather_reply(arg)
            elif action == "AGENDA":
                reply = agenda_reply(7 if "week" in (arg or text).lower() else 1)
            elif action == "LOCATION" and arg:
                reply = locate_reply(arg)
            elif action == "SHOPPING" and arg:
                reply = shopping_reply(arg)
            elif action == "HID" and arg:
                reply = hid_reply(arg)
            elif action == "REMEMBER" and arg:
                reply = memory_add(arg)
            elif action == "FORGET" and arg:
                reply = memory_forget(arg)
            elif action == "MEMORY":
                reply = memory_recall(arg)
            elif action == "PACKAGE":
                reply = package_note(arg or text)
            elif action == "NET":
                reply = net_reply()
            elif action == "VAULT":
                reply = vault_reply(arg or text)
            elif action == "ALERTS":
                reply = alerts_reply()
            elif action == "WHISPER":
                reply = whisper_health()
            elif action == "SERVER":
                reply = server_status()
            elif action == "SYNCTHING":
                reply = syncthing_status()
            elif action == "PHOTOS":
                reply = photos_reply(arg or "")
            elif action == "MAINTENANCE":
                reply = maintenance_status()
            elif action == "BATTERIES":
                reply = ha_battery_report()
            elif action == "FAMILY":
                reply = ha_family_report()
            elif action == "VACUUM_START":
                reply = ha_vacuum_cmd("start")
            elif action == "VACUUM_DOCK":
                reply = ha_vacuum_cmd("dock")
            elif action == "VACUUM_STATUS":
                reply = ha_vacuum_cmd("status")
            elif action == "EXPENSE" and arg:
                reply = expense_log(arg)
            elif action == "MILEAGE" and arg:
                reply = mileage_log(arg)
            elif action == "READ_URL" and arg:
                reply = read_and_save_url(arg)
            elif action == "WORKNOTE" and arg:
                reply = work_note_add(arg)
            elif action == "LIST" and arg:
                reply = list_reply(arg)
            elif action == "PANTRY" and arg:
                reply = pantry_reply(arg)
            elif action == "CHORE" and arg:
                reply = chore_reply(arg)
            elif action == "HABIT" and arg:
                reply = habit_reply(arg)
            elif action == "BRIEF":
                reply = brief_reply()
            elif action == "PAPERLESS" and arg:
                reply = paperless_answer(arg)
            elif action == "CONTACT":
                reply = contact_reply(arg or text)
            elif action == "CONTACT_INFO":
                reply = contact_info_reply(arg or text, hist)
            elif action == "RECUR":
                reply = add_recurring_reminder(text) or add_reminder(arg or text)
            elif action == "REMINDERS":
                reply = reminders_list_reply()
            elif action == "RECUR_CANCEL":
                reply = recurring_cancel_reply(arg or text)
            elif action == "REMIND":
                reply = add_reminder(arg or text)
            elif action == "NOTE":
                reply = note_add(arg if arg is not None and arg != "" else (text if not re.match(r"^(?:make\s+a\s+)?note\b", text.strip(), re.I) else ""))
            elif action == "SEARCH_WEB" and arg:
                reply = web_reply(arg)
            elif action == "EVENT":
                reply = add_calendar_item("EVENT", arg or text, history=hist[:-1])
            elif action == "TASK":
                reply = add_calendar_item("TASK", arg or text, history=hist[:-1])
            else:
                fu = _list_followup_text(
                    text, [h.get("content", "") for h in (hist or [])
                           if h.get("role") == "user"])
                if fu:
                    reply = list_reply(fu)
                else:
                    try:
                        reply = _guard_chat_claims(ask_llm(base, model, text, history=hist[:-1]))
                        TAGS = {"mac": "[Qwen3 4B via thinkbox]", "debby": "[Llama 3B via homebrain]", "loq": "[GPT-OSS 20B via LOQ]"}
                        tag = TAGS.get(name, "")
                        if tag:
                            reply += f"\n\n{tag}"
                    except Exception as e:
                        log(f"llm error on {name}: {e}")
                        reply = f"Backend {name} failed: {e}"
                if not reply:
                    reply = "(empty response from model)"

    if reply is None:
        reply = "(no reply generated)"
    if len(reply) > 3500:
        reply = reply[:3400] + "\n...[truncated]"
    return reply, attachment


# ---------------- HTTP API (OpenAI-compatible, for Home Assistant) ----------------

class HouseAPIHandler(BaseHTTPRequestHandler):
    """Minimal OpenAI-compatible endpoint backed by the House reply engine."""

    def _send_json(self, obj, code=200):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    @staticmethod
    def _auth_ok(headers):
        return bool(HTTP_API_TOKEN) and headers.get("Authorization", "") == f"Bearer {HTTP_API_TOKEN}"

    def do_GET(self):
        if self.path.rstrip("/") in ("/v1/models", "/models"):
            b = pick_backend(False)
            model = b[1] if b else _small_model()
            self._send_json({"object": "list", "data": [
                {"id": model, "object": "model", "owned_by": "house"}]})
        else:
            self._send_json({"error": "not found"}, 404)

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        return json.loads(self.rfile.read(length) or b"{}")

    def do_POST(self):
        path = self.path.rstrip("/")
        if not self._auth_ok(self.headers):
            self._send_json({"error": "unauthorized"}, 401)
            return
        if path.endswith("/chat/completions"):
            self._handle_chat_completions()
        elif path.endswith("/responses"):
            self._handle_responses()      # OpenAI Responses API (Home Assistant)
        else:
            self._send_json({"error": "not found"}, 404)

    def _handle_chat_completions(self):
        try:
            body = self._read_body()
            msgs = [m for m in body.get("messages", [])
                    if m.get("role") in ("user", "assistant")
                    and isinstance(m.get("content"), str)]
            text = next((m["content"].strip() for m in reversed(msgs)
                         if m["role"] == "user"), "")
            if not text:
                self._send_json({"error": "no user message in request"}, 400)
                return
            b = pick_backend(False)
            log(f"api request: {text[:80]!r}")
            reply, _attachment = house_reply(text, text.lower().strip(), msgs[-4:])
            reply = re.sub(r"\n\n\[[^\]]+\]\s*$", "", reply) or "(empty response from model)"
            log(f"api reply: {reply[:80]!r}")
            chat_log_append("voice/" + self.client_address[0], text, reply)
            self._send_json({
                "id": "chatcmpl-house",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": body.get("model") or (b[1] if b else _small_model()),
                "choices": [{"index": 0,
                             "message": {"role": "assistant", "content": reply},
                             "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0,
                          "total_tokens": 0},
            })
        except Exception as e:
            log(f"api error: {e}")
            self._send_json({"error": str(e)}, 500)

    def _responses_input_texts(self, body):
        """Flatten Responses-API input into [(role, text)] messages."""
        raw = body.get("input")
        msgs = []
        if isinstance(raw, str):
            msgs.append(("user", raw))
        elif isinstance(raw, list):
            for item in raw:
                if not isinstance(item, dict):
                    continue
                role = item.get("role")
                content = item.get("content")
                texts = []
                if isinstance(content, str):
                    texts.append(content)
                elif isinstance(content, list):
                    for c in content:
                        if isinstance(c, dict) and isinstance(c.get("text"), str):
                            texts.append(c["text"])
                if role in ("user", "assistant") and texts:
                    msgs.append((role, " ".join(texts)))
        return msgs

    def _house_from_msgs(self, msgs):
        """Run the last user message through House. Returns (reply, model)."""
        text = next((t for r, t in reversed(msgs) if r == "user"), "")
        if not text:
            return None, None
        hist = [{"role": r, "content": t} for r, t in msgs][-4:]
        b = pick_backend(False)
        log(f"api request: {text[:80]!r}")
        reply, _attachment = house_reply(text, text.lower().strip(), hist)
        reply = re.sub(r"\n\n\[[^\]]+\]\s*$", "", reply) or "(empty response from model)"
        log(f"api reply: {reply[:80]!r}")
        return reply, (b[1] if b else _small_model())

    def _response_object(self, model, reply):
        now = int(time.time())
        msg_item = {
            "id": f"msg_house_{now}",
            "type": "message",
            "status": "completed",
            "role": "assistant",
            "content": [{"type": "output_text", "text": reply,
                         "annotations": []}],
        }
        return {
            "id": f"resp_house_{now}",
            "object": "response",
            "created_at": now,
            "status": "completed",
            "model": model,
            "output": [msg_item],
            "parallel_tool_calls": False,
            "tool_choice": "auto",
            "tools": [],
            "usage": {"input_tokens": 1,
                      "input_tokens_details": {"cached_tokens": 0},
                      "output_tokens": 1,
                      "output_tokens_details": {"reasoning_tokens": 0},
                      "total_tokens": 2},
        }

    def _logged_stream(self, msgs, text):
        full = []
        for chunk in self._house_stream(msgs, text):
            full.append(chunk)
            yield chunk
        _log_voice_exchange(text, "".join(full))

    def _house_stream(self, msgs, text):
        """Yield reply chunks: pure chat streams from the LLM, everything
        else is computed by house_reply and yielded as one chunk."""
        b = pick_backend(False)
        fast = pick_fast_backend() or b
        if not b:
            yield "Sorry - no LLM backend reachable right now."
            return
        log(f"api request: {text[:100]!r}")
        action = "CHAT"
        arg = ""
        if fast:
            action, arg = classify_intent(fast[0], fast[1], text, msgs)
        log(f"api intent: {action} ({arg!r})")
        if action != "CHAT":
            reply, _attachment = house_reply(text, text.lower().strip(), msgs[-4:], action=action, arg=arg)
            yield reply or "(empty response from model)"
            return
        fu = _list_followup_text(text, [c for r, c in msgs if r == "user"])
        if fu:
            yield list_reply(fu)
            return
        base, model, name = b
        hist = [{"role": r, "content": c} for r, c in msgs][-6:]
        yielded = ""
        try:
            for chunk in ask_llm_stream(base, model, text, hist):
                yielded += chunk
                yield chunk
        except Exception as e:
            log(f"stream error: {e}")
            if not yielded:
                yield f"Backend {name} failed: {e}"
            return
        if not yielded.strip():
            yield "(empty response from model)"
        # backend tag removed from API path: piper was reading it aloud to HA voice

    def _handle_responses(self):
        try:
            body = self._read_body()
            msgs = self._responses_input_texts(body)
            text = next((t for r, t in reversed(msgs) if r == "user"), "")
            if not text:
                self._send_json({"error": "no user message in request"}, 400)
                return
            if not body.get("stream"):
                reply, model = self._house_from_msgs(msgs)
                _log_voice_exchange(text, reply or "")
                self._send_json(self._response_object(model, reply))
                return
            model = body.get("model") or "house"
            now = int(time.time())
            mid = f"msg_house_{now}"
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()

            def ev(etype, payload):
                payload = dict(payload, type=etype)
                self.wfile.write(f"event: {etype}\ndata: ".encode()
                                 + json.dumps(payload).encode() + b"\n\n")

            created = self._response_object(model, "")
            created["output"] = []
            ev("response.created", {"response": created})
            ev("response.output_item.added", {"output_index": 0, "item": {
                "id": mid, "type": "message", "status": "in_progress",
                "role": "assistant", "content": []}})
            full = ""
            buf = ""
            for chunk in self._logged_stream(msgs, text):
                full += chunk
                buf += chunk
                while True:
                    m_end = re.search(r'[.!?][")\']?\s', buf)
                    if not m_end:
                        break
                    delta = buf[:m_end.end()]
                    buf = buf[m_end.end():]
                    if delta:
                        ev("response.output_text.delta",
                           {"item_id": mid, "output_index": 0,
                            "content_index": 0, "delta": delta})
            if buf:
                ev("response.output_text.delta", {"item_id": mid,
                    "output_index": 0, "content_index": 0, "delta": buf})
            ev("response.output_text.done", {"item_id": mid, "output_index": 0,
                                             "content_index": 0, "text": full})
            ev("response.output_item.done", {"output_index": 0, "item": {
                "id": mid, "type": "message", "status": "completed",
                "role": "assistant",
                "content": [{"type": "output_text", "text": full,
                             "annotations": []}]}})
            ev("response.completed", {"response": self._response_object(model, full)})
            self.wfile.flush()
        except Exception as e:
            log(f"api error: {e}")
            self._send_json({"error": str(e)}, 500)

    def log_message(self, fmt, *args):   # silence default stderr spam
        pass

def run_http_api():
    srv = ThreadingHTTPServer(("0.0.0.0", HTTP_API_PORT), HouseAPIHandler)
    log(f"HTTP API listening on 127.0.0.1:{HTTP_API_PORT}")
    srv.serve_forever()

# ---------------- Store-arrival watchdog (shopping list nudge) ----------------

_last_point = {"lat": None, "lon": None}
_nudge_ts = {}
AT_STORE = {"brand": None, "category": None, "lat": None, "lon": None, "ts": 0, "prompted": False}
_no_store_cache = {}
_unknown_nudged = {}

def nudge_send(recipient, text):
    import datetime
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(15)
        s.connect(SOCKET)
        sf = s.makefile("r")
        ok = send_message(sf, s, recipient, text, None, int(time.time()))
        try:
            s.close()
        except Exception:
            pass
        return ok
    except Exception as e:
        log(f"nudge send failed: {e}")
        return False

def _watch_point():
    if not STORE_WATCH_ENTITY:
        return None
    ent = ha_entity(STORE_WATCH_ENTITY)
    if ent:
        a = ent.get("attributes", {})
        lat, lon = a.get("latitude"), a.get("longitude")
        if lat is None:
            return None
        if (a.get("gps_accuracy") or 0) > WATCH_MAX_ACCURACY:
            return None
        return lat, lon
    return None

def _dist_m(lat1, lon1, lat2, lon2):
    import math
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return 2 * 6371000 * math.asin(math.sqrt(a))

def _identify_store(name, blob, shop_tag=""):
    blob = (blob + " " + name + " " + shop_tag).lower()
    if re.search(r"\b(home\s+depot|lowe'?s|ace\s+hardware|harbor\s+freight|true\s+value|menards|84\s+lumber)\b", blob) or shop_tag in ("doityourself", "hardware"):
        return (name or "Hardware Store"), "hardware"
    if re.search(r"\b(tractor\s+supply|southern\s+states|rural\s+king|feed\s+(?:store|mill|seed)|co-?op|agway)\b", blob) or shop_tag in ("agrarian", "farm"):
        return (name or "Tractor Supply / Feed Store"), "farm"
    if re.search(r"\b(best\s+buy|staples|micro\s+center|office\s+depot|office\s*max|apple\s+store)\b", blob) or shop_tag in ("electronics", "computer", "office_supplies"):
        return (name or "Tech Store"), "tech"
    if re.search(r"\b(walmart|target|costco|sam'?s\s+club|bj'?s\s+wholesale)\b", blob) or shop_tag in ("department_store", "wholesale"):
        return (name or "Supercenter"), "general"
    if re.search(r"\b(kroger|fresh\s+market|aldi|trader\s+joe'?s|food\s+lion|whole\s+foods|wegmans|publix|safeway|harris\s+teeter|giant|lidl)\b", blob) or shop_tag in ("supermarket", "grocery"):
        return (name or "Grocery Store"), "groceries"
    return None, None

def _overpass_store(lat, lon):
    """Return (store_name, store_category) if a known brand is within ~150 m."""
    try:
        stores = _load_json(KNOWN_STORES_FILE, [])
        for s in stores:
            if _dist_m(s["lat"], s["lon"], lat, lon) <= s.get("radius", 180):
                return s["name"], s.get("category", "groceries")
    except Exception as e:
        log(f"known stores cache read failed: {e}")
    q = ("[out:json][timeout:20];"
         f"(node(around:150,{lat},{lon})[name];"
         f"way(around:150,{lat},{lon})[name];);"
         "out tags center 8;")
    body = urllib.parse.urlencode({"data": q}).encode()
    data = None
    for host in ("https://overpass-api.de/api/interpreter",
                 "https://overpass.kumi.systems/api/interpreter"):
        try:
            req = urllib.request.Request(host, data=body,
                                         headers={"User-Agent": "house-bot/1.0"})
            with urllib.request.urlopen(req, timeout=40) as r:
                data = json.loads(r.read())
            break
        except Exception as e:
            log(f"overpass {host.split('//')[1].split('.')[0]} failed: {e}")
    if data is not None:
        for el in data.get("elements", []):
            tags = el.get("tags", {})
            name = tags.get("name", "") or tags.get("brand", "") or tags.get("operator", "")
            blob = " ".join(str(v) for v in tags.values())
            shop = tags.get("shop", "")
            s_name, s_cat = _identify_store(name, blob, shop)
            if s_name and s_cat:
                return s_name, s_cat
    try:
        url = f"https://photon.komoot.io/reverse?lat={lat}&lon={lon}&limit=10"
        req = urllib.request.Request(url, headers={"User-Agent": "house-bot/1.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read())
        for f in data.get("features", []):
            props = f.get("properties", {})
            name = props.get("name") or ""
            blob = " ".join(str(v) for v in props.values())
            shop = props.get("osm_value") or ""
            s_name, s_cat = _identify_store(name, blob, shop)
            if s_name and s_cat:
                return s_name, s_cat
    except Exception as e:
        log(f"photon lookup failed: {e}")
    return None

def _build_store_nudge(store_name, store_cat):
    if store_cat == "hardware":
        items = _read_note_items(HARDWARE_NOTE)
        if not items:
            log(f"at {store_name}: hardware list empty - no nudge")
            return None
        return f"🔨 You are at {store_name}. Hardware list ({len(items)}): " + ", ".join(items[:15]) + "."
    elif store_cat == "farm":
        items = _read_note_items(FARM_NOTE)
        if not items:
            log(f"at {store_name}: feed/farm list empty - no nudge")
            return None
        return f"🚜 You are at {store_name}. Feed & Farm list ({len(items)}): " + ", ".join(items[:15]) + "."
    elif store_cat == "tech":
        items = _read_note_items(TECH_NOTE)
        if not items:
            log(f"at {store_name}: tech list empty - no nudge")
            return None
        return f"💻 You are at {store_name}. Tech list ({len(items)}): " + ", ".join(items[:15]) + "."
    elif store_cat == "groceries":
        items = sync_shopping_note()
        if not items:
            log(f"at {store_name}: grocery list empty - no nudge")
            return None
        return f"🛒 You are at {store_name}. Grocery list ({len(items)}): " + ", ".join(items[:15]) + "."
    elif store_cat == "general":
        groc = sync_shopping_note()
        hw = _read_note_items(HARDWARE_NOTE)
        farm = _read_note_items(FARM_NOTE)
        tech = _read_note_items(TECH_NOTE)
        parts = []
        if groc: parts.append(f"🛒 Groceries ({len(groc)}): " + ", ".join(groc[:10]))
        if farm: parts.append(f"🚜 Feed & Farm ({len(farm)}): " + ", ".join(farm[:6]))
        if hw: parts.append(f"🔨 Hardware ({len(hw)}): " + ", ".join(hw[:6]))
        if tech: parts.append(f"💻 Tech ({len(tech)}): " + ", ".join(tech[:6]))
        if not parts:
            log(f"at {store_name}: all shopping lists empty - no nudge")
            return None
        return f"🏪 You are at {store_name}.\n" + "\n".join(parts)
    return None

def _home_coords():
    ent = ha_entity("zone.home")
    if ent:
        a = ent.get("attributes", {})
        if a.get("latitude") is not None:
            return a["latitude"], a["longitude"]
    return None

def _check_chore_intervals():
    """Nudge when a recurring chore's interval has elapsed."""
    import datetime
    intervals = _load_json(CHORE_INTERVALS_FILE, {})
    now = datetime.datetime.now()
    today = now.strftime("%Y-%m-%d")
    changed = False
    for key, info in intervals.items():
        days = _days_between(info.get("last", ""), now)
        if days is None or days < info.get("days", 9999):
            continue
        if info.get("recurring_id"):
            continue   # a recurring reminder already handles this chore
        if info.get("notified") == today:
            continue
        info["notified"] = today
        changed = True
        msg = (f"🔧 Time to {info.get('task', key)} - it has been {days} "
               f"day{'s' if days != 1 else ''} (every {info['days']} days).")
        for recipient in SENDER_NAMES:
            nudge_send(recipient, msg)
        log(f"interval nudge: {key}")
        # put it on the calendar so it is visible everywhere
        if info.get("calendar") != today:
            import uuid
            uid = f"chore-{uuid.uuid4().hex[:12]}@house"
            day = now.strftime("%Y%m%d")
            body = ("BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//house//EN\n"
                    "BEGIN:VEVENT\n" f"UID:{uid}\nDTSTAMP:{day}T090000Z\n"
                    f"DTSTART;TZID=America/New_York:{day}T080000\n"
                    f"DTEND;TZID=America/New_York:{day}T083000\n"
                    f"SUMMARY:🔧 {info.get('task', key)}\n"
                    "END:VEVENT\nEND:VCALENDAR\n")
            if caldav_put("house.ics", uid + ".ics", body):
                info["calendar"] = today
                log(f"chore due event added to calendar: {info.get('task', key)}")
    if changed:
        _save_json(CHORE_INTERVALS_FILE, intervals)

def _check_location_reminders(lat, lon):
    """Fire pending location reminders. Arming: a reminder created while
    already at the trigger spot must be left (400m) before it can fire."""
    rems = _load_json(LOC_REMINDERS_FILE, [])
    if not rems:
        return
    remaining, fired = [], []
    home = _home_coords()
    for rem in rems:
        place = rem.get("place", {})
        ptype = place.get("type")
        hit = False
        if ptype == "home" and home:
            d = _dist_m(home[0], home[1], lat, lon)
        elif ptype == "coords":
            d = _dist_m(place["lat"], place["lon"], lat, lon)
        elif ptype == "brand":
            d = None
            if AT_STORE["brand"] and \
                    AT_STORE["brand"].lower() == place.get("brand", ""):
                hit = True
        else:
            d = None
        if d is not None:
            if d >= 400:
                rem["armed"] = True
            elif rem.get("armed", False) and d < 250:
                hit = True
        if hit:
            fired.append(rem)
        else:
            remaining.append(rem)
    if fired:
        _save_json(LOC_REMINDERS_FILE, remaining)
        for rem in fired:
            msg = "📍 " + rem.get("what", "reminder")
            for recipient in SENDER_NAMES:
                nudge_send(recipient, msg)
            log(f"location reminder fired: {rem.get('what')}")


def run_store_watchdog():
    """Poll HA for the phone's location; when it lands at a known store
    brand, send the shopping list over Signal (per-store cooldown)."""
    log(f"store watchdog started (every {WATCH_INTERVAL}s, entity "
        f"{STORE_WATCH_ENTITY})")
    last_interval_check = 0.0
    while True:
        time.sleep(WATCH_INTERVAL)
        try:
            pt = _watch_point()
            if not pt:
                continue
            lat, lon = pt
            now = time.time()
            if now - last_interval_check > 3600:
                last_interval_check = now
                _check_chore_intervals()
            _check_location_reminders(lat, lon)
            # store-leave close-out prompt
            if AT_STORE["brand"]:
                if _dist_m(AT_STORE["lat"], AT_STORE["lon"], lat, lon) > 500:
                    if now - AT_STORE["ts"] < 12 * 3600:
                        cat = AT_STORE.get("category")
                        cat_label = {
                            "hardware": "hardware list",
                            "farm": "feed & farm list",
                            "tech": "tech list",
                            "groceries": "grocery list",
                            "general": "lists and pantry"
                        }.get(cat, "shopping list")
                        icon = {
                            "hardware": "🔨", "farm": "🚜", "tech": "💻",
                            "groceries": "🛒", "general": "🏪"
                        }.get(cat, "🛒")
                        msg = (f"{icon} Leaving {AT_STORE['brand']}? Reply with "
                               f"what you bought (e.g. 'bought: item') "
                               f"and I will update your {cat_label}. Or reply 'skip'.")
                        for recipient in SENDER_NAMES:
                            nudge_send(recipient, msg)
                        AT_STORE["prompted"] = True
                        log(f"close-out prompt sent for {AT_STORE['brand']}")
                    AT_STORE["brand"] = None
                    AT_STORE["category"] = None
            moved = _last_point["lat"] is None or \
                _dist_m(_last_point["lat"], _last_point["lon"], lat, lon) >= WATCH_MIN_MOVE_M
            if not moved:
                continue
            ckey = (round(lat, 3), round(lon, 3))
            if now - _no_store_cache.get(ckey, 0) < 86400:
                _last_point["lat"], _last_point["lon"] = lat, lon
                continue
            try:
                store_res = _overpass_store(lat, lon)
            except Exception as e:
                log(f"store lookup unavailable, will retry: {e}")
                continue
            _last_point["lat"], _last_point["lon"] = lat, lon
            if not store_res:
                _no_store_cache[ckey] = now
                continue
            store_name, store_cat = store_res
            AT_STORE.update({"brand": store_name, "category": store_cat, "lat": lat, "lon": lon, "ts": now,
                             "prompted": False})
            key = store_name.lower()
            if now - _nudge_ts.get(key, 0) < NUDGE_COOLDOWN:
                log(f"at {store_name} - nudge suppressed by cooldown")
                continue
            _nudge_ts[key] = now
            msg = _build_store_nudge(store_name, store_cat)
            if not msg:
                continue
            for recipient in SENDER_NAMES:
                if nudge_send(recipient, msg):
                    log(f"nudge sent to {recipient} at {store_name} ({store_cat})")
        except Exception as e:
            log(f"watchdog error: {e}")

def main():
    if not CAPS.get("signal", True):
        log("signal channel disabled - running API/voice-only")
        if not CAPS.get("http_api", True):
            log("no channels enabled at all - nothing to serve")
            sys.exit(1)
        threading.Thread(target=run_http_api, daemon=True).start()
        threading.Thread(target=run_store_watchdog, daemon=True).start()
        threading.Thread(target=run_ntfy_alerts, daemon=True).start()
        threading.Thread(target=run_recurring_reminders, daemon=True).start()
        while True:
            time.sleep(3600)
    log("bot starting; connecting to signal-cli socket")
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    for attempt in range(30):
        try:
            sock.connect(SOCKET)
            break
        except FileNotFoundError:
            time.sleep(2)
    else:
        log("socket never appeared; giving up")
        sys.exit(1)
    sock_file = sock.makefile("r")

    sock.sendall(json.dumps({"jsonrpc": "2.0", "id": 0,
                             "method": "subscribeReceive"}).encode() + b"\n")
    sub_resp = sock_file.readline()
    log(f"subscribed on {SOCKET}: {sub_resp.strip()}")

    if CAPS.get("http_api", True):
        threading.Thread(target=run_http_api, daemon=True).start()
    threading.Thread(target=run_store_watchdog, daemon=True).start()
    threading.Thread(target=run_ntfy_alerts, daemon=True).start()
    threading.Thread(target=run_recurring_reminders, daemon=True).start()

    msg_id = 1
    for line in sock_file:
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("method") != "receive":
            continue
        p = event.get("params", {})
        envelope = p.get("result", {}).get("envelope", {}) if isinstance(p.get("result"), dict) else p.get("envelope", {})
        dm = envelope.get("dataMessage")
        if not dm:
            continue
        sender = envelope.get("source") or envelope.get("sourceNumber")
        text = (dm.get("message") or "").strip()
        atts = dm.get("attachments") or []
        if not text and not atts:
            continue
        log(f"message from {sender}: {text[:80]!r}"
            + (f" (+{len(atts)} attachment(s))" if atts else ""))

        cmd = text.lower().strip()

        if sender not in SENDER_NAMES:
            if time.time() - _unknown_nudged.get(sender, 0) > 86400:
                _unknown_nudged[sender] = time.time()
                log(f"unknown sender refused: {sender}")
                try:
                    send_message(sock_file, sock, sender,
                                 "I do not recognize this number yet - ask "
                                 "the owner to register it (id: "
                                 + sender[:8] + ").", None, int(time.time()))
                except Exception as e:
                    log(f"refusal send failed: {e}")
            continue

        # shopping close-out replies ('bought: ...' / 'skip') from store prompts
        if AT_STORE.get("prompted") and time.time() - AT_STORE["ts"] < 12 * 3600 \
                and not text.startswith("/"):
            mco = re.match(r"^(?:(?:i|we)\s*)?(?:bought|got|picked up)\b[:,]?\s*(.*)",
                           text.strip(), re.I)
            if cmd.strip() in ("skip", "nothing", "no", "nope"):
                AT_STORE["prompted"] = False
                msg_id += 1
                send_message(sock_file, sock, sender, "OK, skipped.", None, msg_id)
                log("close-out skipped")
                continue
            if mco:
                bought = _split_items(mco.group(1))
                done = []
                cat = AT_STORE.get("category")
                for it in bought:
                    if cat in ("groceries", "general", None):
                        _pantry_set(it, "HAVE")
                    shopping_remove(it)
                    done.append(it)
                cat_label = {
                    "hardware": "hardware list",
                    "farm": "feed & farm list",
                    "tech": "tech list",
                    "groceries": "pantry and grocery list",
                    "general": "pantry and lists"
                }.get(cat, "lists")
                AT_STORE["prompted"] = False
                AT_STORE["brand"] = None
                AT_STORE["category"] = None
                reply = (f"Updated your {cat_label}: " + ", ".join(done) + "."
                         if done else
                         "I could not tell what you bought - try "
                         "'bought: screws, glue' or 'bought: coffee, bread'.")
                msg_id += 1
                send_message(sock_file, sock, sender, reply, None, msg_id)
                log(f"close-out processed: {done}")
                continue

        # conversation memory: last few exchanges per sender
        hist = HISTORY.setdefault(sender, [])
        hist.append({"role": "user", "content": text})
        hist[:] = hist[-4:]   # small models drown in long histories

        # Document / receipt upload to Paperless-ngx
        doc_attachment = None
        for a in atts:
            ctype = str(a.get("contentType", "")).lower()
            if ctype.startswith("image/") or ctype.startswith("application/pdf"):
                aid = str(a.get("id") or "")
                apath = a.get("localFilePath") or ""
                att_dir = os.path.expanduser("~/.local/share/signal-cli/attachments")
                if not apath and aid and os.path.isdir(att_dir):
                    cands = sorted(f for f in os.listdir(att_dir) if f.startswith(aid))
                    if cands:
                        apath = os.path.join(att_dir, cands[0])
                if apath and os.path.exists(apath):
                    doc_attachment = (apath, ctype)
                    break

        if doc_attachment:
            apath, ctype = doc_attachment
            log(f"document/receipt attachment detected: {apath} ({ctype})")
            if ctype.startswith("image/") and re.search(
                    r"\bcontacts?\b|\bbusiness card\b|\bsave (this|him|her|them)\b",
                    (text or "").lower()):
                reply = contact_reply(text, apath)
                msg_id += 1
                send_message(sock_file, sock, sender, reply, None, msg_id)
                log(f"business card contact processed: {reply[:80]!r}")
                continue
            up_msg = paperless_upload(apath, title=text if text else None)
            msg_id += 1
            send_message(sock_file, sock, sender, up_msg, None, msg_id)
            continue

        # voice note: transcribe via the LOQ GPU service, then treat as text
        voice_note = False
        if not text and atts:
            att_dir = os.path.expanduser("~/.local/share/signal-cli/attachments")
            audio = next((a for a in atts
                          if str(a.get("contentType", "")).startswith("audio/")), None)
            cand = audio or atts[0]   # if no text, try whatever they sent
            if cand is None:
                continue
            apath = cand.get("localFilePath") or ""
            if not apath:
                # some signal-cli versions omit localFilePath: resolve via the
                # attachments dir using the attachment id (file = <id>.<ext>)
                aid = str(cand.get("id") or "")
                if aid and os.path.isdir(att_dir):
                    cands = sorted(f for f in os.listdir(att_dir) if f.startswith(aid))
                    if cands:
                        apath = os.path.join(att_dir, cands[0])
            if not apath or not os.path.exists(apath):
                log(f"voice attachment file missing: id={cand.get('id')!r} "
                    f"ctype={cand.get('contentType')!r} dir={att_dir}")
                msg_id += 1
                send_message(sock_file, sock, sender,
                             "Couldn't access that voice note - try sending it again.",
                             None, msg_id)
                continue
            log(f"voice note received ({cand.get('contentType')})")
            try:
                with open(apath, "rb") as f:
                    data = f.read()
                text, conf, backend = transcribe_audio(data)
                log(f"voice transcribed via {backend} (conf {conf}): {text[:80]!r}")
                voice_note = True
            except Exception as e:
                log(f"voice transcription failed: {e}")
                msg_id += 1
                send_message(sock_file, sock, sender,
                             f"Voice transcription failed: {e}", None, msg_id)
                continue
            if not text:
                msg_id += 1
                send_message(sock_file, sock, sender,
                             "I couldn't make out what you said - try again.", None, msg_id)
                continue
            hist[-1]["content"] = text   # memory stores the transcript

        # fuzzy command detection: 'evebt/' or 'fnd x' still work
        first_word = re.sub(r"[^a-z]", "", cmd.split(" ")[0])
        if first_word and not cmd.startswith("/"):
            import difflib
            close = difflib.get_close_matches(
                first_word, ["event", "task", "find", "send", "summarize",
                             "organize", "status", "weather", "web",
                             "syncthing", "photos", "photo"], cutoff=0.78)
            if close and close[0] != first_word and len(cmd.split(" ")) > 1:
                log(f"fuzzy: {first_word!r} -> {close[0]!r}")
                cmd = "/" + close[0] + " " + " ".join(cmd.split(" ")[1:])

        try:
            reply, attachment = house_reply(text, cmd, hist)
        except Exception as e:
            import traceback
            log(f"error in house_reply: {traceback.format_exc()}")
            reply, attachment = f"Sorry, I encountered an error: {e}", None
        chat_log_append(sender, text, reply)
        if voice_note:
            reply = f'You said: "{text}"\n\n' + reply

        wav_file = None
        if not attachment and (voice_note or re.search(r"\b(voice reply|read aloud|speak|audio reply)\b", text, re.I)):
            wav_file = text_to_wav(reply)
            if wav_file:
                attachment = wav_file

        msg_id += 1
        send_message(sock_file, sock, sender, reply, attachment, msg_id)
        if wav_file and os.path.exists(wav_file):
            try:
                os.remove(wav_file)
            except Exception:
                pass
        log(f"replied to {sender}" + (" (with attachment)" if attachment else "")
            + f": {reply[:100]!r}")

if __name__ == "__main__":
    main()
