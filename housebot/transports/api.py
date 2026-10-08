"""OpenAI-compatible HTTP API transport — lets Home Assistant, scripts, or any
OpenAI-speaking client use the same brain (POST /v1/chat/completions).

Security: requires a real token (refuses to start on the default), compared with
hmac.compare_digest.
"""

import hashlib
import hmac
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DEFAULT_TOKEN = "change-me"


class ApiTransport:
    # push-capable flag: Jobs skips transports that can't proactively message
    push = False

    def __init__(self, cfg, engine):
        self.engine = engine
        self.host = cfg["transports"]["api"].get("host", "127.0.0.1")
        self.port = cfg["transports"]["api"].get("port", 8082)
        self.token = cfg["transports"]["api"].get("token", "")
        if not self.token or self.token == DEFAULT_TOKEN:
            raise RuntimeError(
                "[api] refusing to start: set transports.api.token in config to a "
                "long random value (e.g. `openssl rand -hex 32`). The default "
                "token would let anyone on the network drive the bot.")
        self.token_hash = hashlib.sha256(self.token.encode()).digest()

    def _auth(self, header):
        provided = header.replace("Bearer ", "", 1) if header.startswith("Bearer ") else ""
        return hmac.compare_digest(
            hashlib.sha256(provided.encode()).digest(), self.token_hash)

    def serve_forever(self):
        engine = self.engine
        auth = self._auth

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                if not auth(self.headers.get("Authorization", "")):
                    self.send_response(401); self.end_headers(); return
                if self.path.endswith("/models"):
                    out = {
                        "object": "list",
                        "data": [{
                            "id": "housebot",
                            "object": "model",
                            "created": 1677610602,
                            "owned_by": "housebot"
                        }]
                    }
                    data = json.dumps(out).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                self.send_response(404); self.end_headers()

            def do_POST(self):
                if not auth(self.headers.get("Authorization", "")):
                    self.send_response(401); self.end_headers(); return
                if not self.path.endswith("/chat/completions"):
                    self.send_response(404); self.end_headers(); return
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length) or b"{}")
                sender = "api"
                messages = body.get("messages") or []
                last = messages[-1] if messages and isinstance(messages[-1], dict) else {}
                text = last.get("content") if isinstance(last.get("content"), str) else ""
                # The request carries its own context. Keep the three messages
                # before the last one; handle() appends the current turn.
                prior = []
                for m in messages[:-1][-3:]:
                    if not isinstance(m, dict):
                        continue
                    content = m.get("content")
                    if isinstance(content, str) and content:
                        prior.append({"role": m.get("role") or "user", "content": content})
                engine.history[sender] = prior
                reply, _att = engine.handle(sender, text)
                if reply is None:
                    reply = ""
                out = {"id": "housebot", "object": "chat.completion",
                       "choices": [{"index": 0, "message": {"role": "assistant",
                                    "content": reply}, "finish_reason": "stop"}]}
                data = json.dumps(out).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        server = ThreadingHTTPServer((self.host, self.port), Handler)
        print(f"[api] listening on {self.host}:{self.port} (token required)")
        server.serve_forever()