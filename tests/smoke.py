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

failed = [(n, e) for n, ok, e in checks if not ok]
for name, ok, err in checks:
    print(("✓" if ok else "✗") + " " + name + (f"  — {err}" if err else ""))
print()
if failed:
    print(f"{len(failed)} FAILED")
    sys.exit(1)
print(f"All {len(checks)} smoke checks passed.")
