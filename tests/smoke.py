#!/usr/bin/env python3
"""HouseBot smoke test — run from the repo root:

    python3 tests/smoke.py

Uses temp dirs and a stub LLM (no network). Verifies the intent pipeline,
reminder parsing (both word orders), notes/find, lists skills, contacts skill
(if configured), and graceful degradation of unconfigured skills.
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from housebot.config import Config          # noqa: E402
from housebot.engine import Engine          # noqa: E402

tmp = tempfile.mkdtemp(prefix="housebot-smoke-")

config = {
    "bot": {"data_dir": os.path.join(tmp, "data"), "owner": "tester"},
    "caldav": {"base_url": "", "user": ""},   # unconfigured on purpose
    "llm": {"backends": [{"name": "stub", "base_url": "http://127.0.0.1:1",
                          "model": "stub", "api": "ollama"}]},
    "skills": {"notes_dir": os.path.join(tmp, "notes"),
               "search_dirs": [os.path.join(tmp, "docs")]}
}
os.makedirs(os.path.join(tmp, "docs"), exist_ok=True)
open(os.path.join(tmp, "docs", "lease.pdf"), "w").write("x")

cfg_path = os.path.join(tmp, "config.json")
json.dump(config, open(cfg_path, "w"))
os.environ["HOUSEBOT_CONFIG"] = cfg_path

# rebuild Config now that env is set
cfg = Config()
engine = Engine(cfg)

# stub the LLM so chat/classify have deterministic answers
import housebot.llm as llm

_REAL_ASK_LLM = llm.ask_llm


def _stub_ask(cfg, prompt, history=None, backend=None, temperature=None):
    if "Classify the user request" in prompt:
        return "CHAT", "stub"
    return f"[stub llm] {prompt[:40]}...", "stub"


llm.ask_llm = _stub_ask
engine.chat.cfg = cfg

checks = []


def check(name, fn):
    try:
        fn()
        checks.append((name, True, ""))
    except Exception as e:
        checks.append((name, False, str(e)))


check("reminder: what-before-when", lambda: (
    engine.handle("t", "remind me to call Dana Reyes tomorrow at 3pm")[0]
    .startswith("Reminder set: 'call Dana Reyes'")))
check("reminder: when-before-what", lambda: (
    engine.handle("t", "remind me at 3pm tomorrow to call Dana Reyes")[0]
    .startswith("Reminder set: 'call Dana Reyes'")))
check("reminder listed", lambda: (
    "Dana Reyes" in engine.handle("t", "what are my reminders")[0]))
check("note saved", lambda: (
    "Noted" in engine.handle("t", "note the furnace filter is 16x25x4")[0]))
check("find hit", lambda: "lease.pdf" in engine.handle("t", "find lease")[0])
check("shopping add", lambda: (
    "Added" in engine.handle("t", "add milk and eggs to the shopping list")[0]))
check("shopping show", lambda: (
    "milk" in engine.handle("t", "whats on my shopping list")[0]))
check("shopping remove", lambda: (
    "Removed" in engine.handle("t", "remove milk from my shopping list")[0]))
check("chore log", lambda: (
    "Logged" in engine.handle("t", "i changed the hvac air filter today")[0]))
check("chore status", lambda: (
    "hvac" in engine.handle("t", "chores status")[0]))
check("habit log", lambda: (
    "Logged" in engine.handle("t", "log that i drank 24 ounces of water")[0]))
check("remember", lambda: (
    "Noted" in engine.handle("t", "remember that the spare key is in the desk drawer")[0]))
check("recall (memory)", lambda: (
    "desk drawer" in engine.handle("t", "where did i put the spare key")[0]))
check("agenda graceful without caldav", lambda: (
    "not configured" in engine.handle("t", "whats on my agenda")[0].lower()))
check("chat graceful without llm", lambda: (
    engine.handle("t", "tell me about quantum computing")[0] != ""))

# -- fixes from the security review ------------------------------------------

import socket
import threading
import time
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from housebot import intents
from housebot.skills.web import Web, _Blocked, _Redirect, _NoRedirect
from housebot.transports.api import ApiTransport
from housebot.transports.signal import sender_id


def _post(port, messages, token=None):
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/v1/chat/completions",
        data=json.dumps({"messages": messages}).encode(),
        headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


_API_TOKEN = "smoke-test-token"


def _start_api():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    transport = ApiTransport(
        {"transports": {"api": {"port": port, "token": _API_TOKEN}}}, engine)
    threading.Thread(target=transport.serve_forever, daemon=True).start()
    for _ in range(50):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return port
        except OSError:
            time.sleep(0.05)
    raise RuntimeError("API did not start listening")


def _raises(exc, fn):
    try:
        fn()
    except exc:
        return True
    return False


def _api_auth():
    port = _start_api()
    shopping = [{"role": "user", "content": "whats on my shopping list"}]
    no_auth = _post(port, shopping, token=None)
    bad = _post(port, shopping, token="not-the-token")
    prior = [
        {"role": "user", "content": "what is Dana Reyes's phone number"},
        {"role": "assistant", "content": "noted"},
        {"role": "user", "content": "whats on my shopping list"},
    ]
    good_status, good_body = _post(port, prior, token=_API_TOKEN)
    if no_auth[0] != 401 or bad[0] != 401:
        raise AssertionError(f"expected 401s, got {no_auth[0]} and {bad[0]}")
    if good_status != 200 or "shopping" not in good_body.lower():
        raise AssertionError(f"good token failed: {good_status}")
    hist = engine.history.get("api") or []
    if not any("Dana Reyes" in (h.get("content") or "") for h in hist):
        raise AssertionError("request history was not applied")


def _classifier_labels():
    saved = llm.ask_llm

    def _reply(text):
        def _ask(cfg, prompt, history=None, backend=None, temperature=None):
            return text, "stub"
        return _ask

    try:
        llm.ask_llm = _reply("PHOTOS the dog")
        action, arg = intents.classify_llm(engine.chat, "show the album")
        if action != "PHOTOS" or "dog" not in arg:
            raise AssertionError(f"{action} {arg}")
        llm.ask_llm = _reply("VACUUM start")
        action, arg = intents.classify_llm(engine.chat, "hello robot")
        if action != "VACUUM" or arg != "start":
            raise AssertionError(f"{action} {arg}")
        llm.ask_llm = _reply("READ_URL https://example.com/article")
        action, arg = intents.classify_llm(engine.chat, "look at this")
        if action != "READ_URL" or "example.com" not in arg:
            raise AssertionError(f"{action} {arg}")
    finally:
        llm.ask_llm = saved
    return True


def _signal_allowlist():
    uuid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    allowed = {"+15555550199", uuid}
    if sender_id({"sourceNumber": "+15555550199"}, allowed) != "+15555550199":
        raise AssertionError("sourceNumber")
    if sender_id({"sourceUuid": uuid}, allowed) != uuid:
        raise AssertionError("sourceUuid")
    if sender_id({"source": "+15555550199"}, allowed) != "+15555550199":
        raise AssertionError("legacy source")
    if sender_id({"sourceNumber": "+15555550100", "sourceUuid": uuid}, {uuid}) != uuid:
        raise AssertionError("uuid when number is present but not allowed")
    if sender_id({"source": "+15555550199", "sourceUuid": "nope"}, allowed) != "+15555550199":
        raise AssertionError("legacy source among other fields")
    if sender_id({"sourceUuid": "nope"}, allowed) is not None:
        raise AssertionError("unknown sender admitted")
    if sender_id({"sourceNumber": "+15555550199"}, set()) is not None:
        raise AssertionError("empty allowlist admitted a sender")
    return True


def _ssrf():
    web = Web({"skills": {}})
    blocked = [
        "https://100.64.0.1/latest",
        "https://100.127.255.254/",
        "https://10.1.2.3/",
        "https://127.0.0.1/",
        "https://169.254.169.254/",
        "file:///etc/passwd",
        "http://1.1.1.1/",
    ]
    for url in blocked:
        if web._safe_url(url) is not None:
            raise AssertionError(f"allowed {url}")
    if web._safe_url("https://1.1.1.1/") != "https://1.1.1.1/":
        raise AssertionError("public literal blocked")
    if web._safe_url("https://100.128.0.1/") is None:
        raise AssertionError("address just outside CGNAT was blocked")

    real = socket.getaddrinfo

    def _mapped(host, port, *a, **k):
        return [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::ffff:100.64.0.1", port, 0, 0))]

    socket.getaddrinfo = _mapped
    try:
        if web._safe_url("https://mapped.example/") is not None:
            raise AssertionError("v4-mapped CGNAT allowed")
    finally:
        socket.getaddrinfo = real

    class _Seq:
        def __init__(self, events):
            self.events = list(events)
            self.urls = []

        def open(self, req, timeout=30):
            self.urls.append(req.full_url)
            item = self.events.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

    class _Body:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"<p>ok</p>"

    seq = _Seq([_Redirect("https://100.64.1.5/secret")])
    web._opener = lambda: seq
    try:
        web._fetch_public("https://1.1.1.1/start")
        raise AssertionError("redirect to CGNAT was fetched")
    except _Blocked:
        pass
    if seq.urls != ["https://1.1.1.1/start"]:
        raise AssertionError(f"unexpected fetches: {seq.urls}")

    seq_ok = _Seq([_Redirect("https://1.0.0.1/next"), _Body()])
    web._opener = lambda: seq_ok
    body = web._fetch_public("https://1.1.1.1/start")
    if body != b"<p>ok</p>" or seq_ok.urls != ["https://1.1.1.1/start", "https://1.0.0.1/next"]:
        raise AssertionError("public redirect was not followed")

    class _H(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", "https://100.64.0.1/x")
            self.end_headers()

        def log_message(self, *a):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        opener = urllib.request.build_opener(_NoRedirect)
        req = urllib.request.Request(f"http://127.0.0.1:{server.server_address[1]}/")
        try:
            opener.open(req, timeout=3)
            raise AssertionError("redirect handler followed the hop")
        except _Redirect as caught:
            if caught.url != "https://100.64.0.1/x":
                raise AssertionError(f"redirect url {caught.url}")
    finally:
        server.shutdown()
    return True


def _caldav_starts():
    path = os.path.join(tmp, "caldav-config.json")
    data = json.load(open(cfg_path))
    data["caldav"] = {"base_url": "http://127.0.0.1:9", "user": "me"}
    json.dump(data, open(path, "w"))
    eng = Engine(Config(path))
    if eng.calendar is None:
        raise AssertionError("calendar was not constructed")
    return True


check("api refuses placeholder token", lambda: (
    _raises(RuntimeError, lambda: ApiTransport(
        {"transports": {"api": {"token": "change-me"}}}, engine))))
check("api disabled unless configured", lambda: (
    cfg["transports"]["api"]["enabled"] is False and
    json.load(open(os.path.join(os.path.dirname(__file__), "..", "config.example.json")))
    ["transports"]["api"]["enabled"] is False))
check("complete keyword", lambda: (
    intents.keyword_intent("mark the task file taxes done") == ("COMPLETE", "file taxes")
    and intents.keyword_intent("todo buy stamps")[0] == "TASK"))
check("classifier keeps skill labels", lambda: _classifier_labels())
check("signal allowlist fields", lambda: _signal_allowlist())
check("ssrf blocks cgnat and rechecks redirects", lambda: _ssrf())
check("caldav base_url does not crash startup", lambda: _caldav_starts())
check("api auth no/bad/good token", _api_auth)

# -- week 1 refactor test cases --

def _test_attachment_tuple():
    res = engine.handle("t", "", "/tmp/nonexistent.png")
    if not isinstance(res, tuple) or len(res) != 2:
        raise AssertionError(f"expected 2-tuple, got {type(res)}")
    reply, att = res  # verify unpacking works
    if not isinstance(reply, str):
        raise AssertionError(f"expected string reply, got {type(reply)}")
    return True

check("attachment returns unpackable 2-tuple", _test_attachment_tuple)

def _test_reminder_llm_fallback():
    from housebot.skills.reminders import Reminders
    # stub returns a json string (not a tuple)
    stub_llm = lambda p: '{"when": "2026-10-15 14:00", "what": "inspect roof"}'
    rem = Reminders(cfg, stub_llm)
    # text that fails fast_parse_when and triggers llm_extract_when
    msg = rem.add("remind me when it is sunny outside to inspect roof")
    if "inspect roof" not in msg or "Oct 15" not in msg:
        raise AssertionError(f"unexpected reminder confirmation: {msg}")
    return True

check("reminder llm fallback unpacks cleanly", _test_reminder_llm_fallback)

def _test_caldav_complete_envelope():
    from housebot.caldav import CalDAV
    c = CalDAV(cfg)
    c.get = lambda col: (
        "BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//test//EN\n"
        "BEGIN:VTODO\nUID:task-123\nSUMMARY:test\nEND:VTODO\n"
        "END:VCALENDAR"
    )
    captured = []
    c.put = lambda col, fn, body: (captured.append(body) or True)
    c.complete_vtodo("task-123")
    if not captured:
        raise AssertionError("put was not called")
    body = captured[0]
    if "BEGIN:VCALENDAR" not in body or "END:VCALENDAR" not in body:
        raise AssertionError(f"missing VCALENDAR envelope in: {body}")
    if "STATUS:COMPLETED" not in body:
        raise AssertionError("missing STATUS:COMPLETED")
    return True

check("caldav complete_vtodo preserves vcalendar envelope", _test_caldav_complete_envelope)

def _test_config_expand_lists():
    c = Config()
    c._data["skills"]["search_dirs"] = ["~/Documents", "~/Downloads"]
    c._expand(c._data)
    dirs = c._data["skills"]["search_dirs"]
    if any(d.startswith("~") for d in dirs):
        raise AssertionError(f"tilde not expanded in lists: {dirs}")
    return True

check("config _expand handles string lists", _test_config_expand_lists)

def _test_shopping_order():
    from housebot.skills.lists import Lists
    import tempfile
    test_cfg = Config()
    test_cfg._data["skills"]["notes_dir"] = tempfile.mkdtemp()
    l = Lists(test_cfg)
    l.shopping_add(["first item"])
    l.shopping_add(["second item"])
    content = open(l.shopping_note, encoding="utf-8").read()
    idx1 = content.find("first item")
    idx2 = content.find("second item")
    if idx1 == -1 or idx2 == -1 or idx1 >= idx2:
        raise AssertionError(f"shopping list items not in chronological order:\n{content}")
    return True

check("shopping list preserves chronological insertion order", _test_shopping_order)

def _test_hardware_module():
    from housebot.hardware import inspect_hardware, recommend_model
    hw = inspect_hardware()
    if hw.total_ram_gb <= 0:
        raise AssertionError("invalid RAM detected")
    rec = recommend_model(hw)
    if "recommended_model" not in rec or "tier" not in rec:
        raise AssertionError(f"invalid recommendation: {rec}")
    return True

check("hardware detection and llm recommendation", _test_hardware_module)

def _test_llm_api_key_header():
    import housebot.llm as llm
    test_cfg = {
        "llm": {
            "backends": [{
                "name": "test-cloud",
                "base_url": "http://127.0.0.1:9999",
                "api": "openai",
                "model": "test-model",
                "api_key": "secret-test-key",
                "timeout": 1
            }]
        }
    }
    # Intercept urllib to verify request headers
    captured_reqs = []
    real_urlopen = urllib.request.urlopen
    def _mock_urlopen(req, **k):
        captured_reqs.append(req)
        class _Resp:
            def read(self):
                return b'{"choices": [{"message": {"content": "ok"}}]}'
        return _Resp()
    urllib.request.urlopen = _mock_urlopen
    try:
        reply, name = _REAL_ASK_LLM(test_cfg, "hello")
        if not captured_reqs:
            raise AssertionError("urlopen not called")
        auth_header = captured_reqs[0].headers.get("Authorization")
        if auth_header != "Bearer secret-test-key":
            raise AssertionError(f"unexpected auth header: {auth_header}")
    finally:
        urllib.request.urlopen = real_urlopen
    return True

check("llm client supports api_key authorization header", _test_llm_api_key_header)

# -- week 2 onboarding & installer test cases --

def _test_detect_vaults():
    from housebot.wizard import detect_obsidian_vaults
    vaults = detect_obsidian_vaults()
    if not isinstance(vaults, list):
        raise AssertionError("detect_obsidian_vaults should return a list")
    return True

check("obsidian vault auto-detection", _test_detect_vaults)

def _test_service_paths():
    from housebot.service import get_service_paths
    os_type, srv_path = get_service_paths()
    if os_type not in ("linux", "darwin", "win32", "unknown"):
        raise AssertionError(f"unexpected os_type: {os_type}")
    if not str(srv_path):
        raise AssertionError("service path cannot be empty")
    return True

check("cross-platform background service paths", _test_service_paths)

def _test_cli_transport():
    from housebot.transports.cli import CliTransport
    cli = CliTransport(cfg, engine)
    if cli.push is not False:
        raise AssertionError("cli push should be False")
    return True

check("interactive cli transport initialization", _test_cli_transport)

def _test_webui_server():
    from housebot.webui import find_available_port, WebUIServer
    import urllib.request
    import json

    test_port = find_available_port(47500)
    server = WebUIServer("127.0.0.1", test_port, cfg=cfg, engine=engine)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()

    try:
        # 1. GET /
        with urllib.request.urlopen(f"http://127.0.0.1:{test_port}/", timeout=3) as r:
            if r.status != 200:
                raise AssertionError(f"GET / failed with status {r.status}")
            html = r.read().decode("utf-8")
            if "HouseBot" not in html or "Dashboard" not in html:
                raise AssertionError("Dashboard HTML missing expected title")

        # 2. GET /api/status
        with urllib.request.urlopen(f"http://127.0.0.1:{test_port}/api/status", timeout=3) as r:
            data = json.loads(r.read().decode("utf-8"))
            if "configured" not in data or "version" not in data:
                raise AssertionError("status API missing fields")

        # 3. GET /api/hardware
        with urllib.request.urlopen(f"http://127.0.0.1:{test_port}/api/hardware", timeout=3) as r:
            hw_data = json.loads(r.read().decode("utf-8"))
            if "profile" not in hw_data or "recommendation" not in hw_data:
                raise AssertionError("hardware API missing fields")

        # 4. POST /api/chat
        req = urllib.request.Request(
            f"http://127.0.0.1:{test_port}/api/chat",
            data=json.dumps({"message": "whats on my shopping list"}).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=3) as r:
            chat_data = json.loads(r.read().decode("utf-8"))
            if "reply" not in chat_data:
                raise AssertionError("chat API missing reply field")
    finally:
        server.shutdown()
        server.server_close()
    return True

check("webui server and rest endpoints", _test_webui_server)

def _test_standalone_binary():
    import subprocess
    from pathlib import Path
    bin_path = Path("dist") / "HouseBot"
    if not bin_path.exists():
        raise AssertionError(f"dist/HouseBot binary does not exist at {bin_path}")
    res = subprocess.run([str(bin_path), "--hardware"], capture_output=True, text=True, timeout=10)
    if res.returncode != 0 or "Hardware Profile" not in res.stdout:
        raise AssertionError(f"Binary execution failed: {res.stderr or res.stdout}")
    return True

check("standalone executable execution", _test_standalone_binary)

failed = [(n, e) for n, ok, e in checks if not ok]
for name, ok, err in checks:
    print(("✓" if ok else "✗") + " " + name + (f"  — {err}" if err else ""))
print()
if failed:
    print(f"{len(failed)} FAILED")
    sys.exit(1)
print(f"All {len(checks)} smoke checks passed.")
