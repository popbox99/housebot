"""OpenAI-compatible HTTP API transport — lets Home Assistant, scripts, or any
OpenAI-speaking client use the same brain (POST /v1/chat/completions)."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class ApiTransport:
    def __init__(self, cfg, engine):
        self.engine = engine
        self.port = cfg["transports"]["api"].get("port", 8082)
        self.token = cfg["transports"]["api"].get("token", "change-me")

    def serve_forever(self):
        engine, token = self.engine, self.token

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _auth(self):
                return self.headers.get("Authorization", "") == f"Bearer {token}"

            def do_POST(self):
                if not self._auth():
                    self.send_response(401); self.end_headers(); return
                if not self.path.endswith("/chat/completions"):
                    self.send_response(404); self.end_headers(); return
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length) or b"{}")
                sender = "api"
                messages = body.get("messages") or []
                text = messages[-1]["content"] if messages else ""
                hist = [{"role": m.get("role", "user"), "content": m.get("content", "")}
                        for m in messages[-4:]]
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

        server = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        print(f"[api] listening on 127.0.0.1:{self.port} (token required)")
        server.serve_forever()