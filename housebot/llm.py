"""LLM backend chain: multiple machines, first reachable wins.

Backends speak either Ollama's /api/chat or any OpenAI-compatible /v1/chat/completions
(Ollama, LM Studio, llama.cpp server, vLLM...).
"""

import json
import urllib.request


def ask_llm(cfg, prompt, history=None, backend=None, temperature=None):
    """Send `prompt` (+optional history) to `backend` (dict) or the first alive one.
    Returns (text, backend_name) or (None, None) if every backend is down."""
    backends = cfg["llm"]["backends"]
    order = [backend] if backend else backends
    for b in order:
        try:
            if b.get("api") == "openai":
                url = b["base_url"].rstrip("/") + "/v1/chat/completions"
                messages = (history or []) + [{"role": "user", "content": prompt}]
                payload = {"model": b["model"], "messages": messages, "stream": False}
                if temperature is not None:
                    payload["temperature"] = temperature
            else:  # ollama
                url = b["base_url"].rstrip("/") + "/api/chat"
                payload = {"model": b["model"], "messages": (history or []) +
                           [{"role": "user", "content": prompt}], "stream": False}
                if temperature is not None:
                    payload["options"] = {"temperature": temperature}
            req = urllib.request.Request(
                url, data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=120) as r:
                data = json.loads(r.read())
            if b.get("api") == "openai":
                text = data["choices"][0]["message"]["content"]
            else:
                text = data["message"]["content"]
            if text and text.strip():
                return text.strip(), b.get("name", b["base_url"])
        except Exception:
            continue
    return None, None


def pick_extract_backend(cfg):
    """Backend used for strict JSON extraction tasks (cheapest first)."""
    backends = cfg["llm"]["backends"]
    return (backends[0]["base_url"], backends[0]["model"],
            backends[0].get("name", "primary"))