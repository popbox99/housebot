"""Signal transport via the signal-cli daemon socket (JSON-RPC)."""

import json
import os
import socket
import threading
import time


def sender_id(envelope, allowed):
    """Return the allowlisted id to reply to, or None.

    signal-cli puts the sender on the envelope. Match sourceNumber (E.164),
    sourceUuid, or the legacy source field. An empty allowlist matches nothing.
    """
    if not allowed:
        return None
    for key in ("sourceNumber", "sourceUuid", "source"):
        val = str((envelope or {}).get(key) or "").strip()
        if val and val in allowed:
            return val
    return None


class SignalTransport:
    push = True   # can proactively message the owner (reminders, alerts)

    def __init__(self, cfg, engine):
        t = cfg["transports"]["signal"]
        self.engine = engine
        self.account = t.get("account", "")
        self.socket_path = os.path.expanduser(
            t.get("socket", "~/.local/run/signal-cli/socket"))
        self.allowed = set(cfg["bot"].get("allowed_senders") or [])
        if not self.allowed:
            print("[signal] SECURITY: bot.allowed_senders is empty - ALL incoming "
                  "messages will be rejected. Add the sender's phone number "
                  "(sourceNumber) or UUID (sourceUuid). A legacy envelope "
                  "source value is accepted too.")
        self.sockfile = self.sock = None

    def _connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(self.socket_path)
        self.sockfile = self.sock.makefile("r")

    def send(self, recipient, text, attachment=None):
        params = {"account": self.account, "recipient": recipient, "message": text}
        if attachment:
            params["attachments"] = [attachment]
        payload = {"jsonrpc": "2.0", "id": int(time.time() * 1000),
                   "method": "send", "params": params}
        self.sock.send((json.dumps(payload) + "\n").encode())

    def _resolve_attachment(self, att):
        apath = att.get("localFilePath") or ""
        if not apath:
            att_dir = os.path.expanduser("~/.local/share/signal-cli/attachments")
            aid = str(att.get("id") or "")
            if aid and os.path.isdir(att_dir):
                cands = sorted(f for f in os.listdir(att_dir) if f.startswith(aid))
                if cands:
                    apath = os.path.join(att_dir, cands[0])
        return apath if apath and os.path.exists(apath) else None

    def serve_forever(self):
        while True:
            try:
                self._connect()
                for line in self.sockfile:
                    if not line.strip():
                        continue
                    try:
                        msg = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    envelope = msg.get("params", {}).get("envelope") or {}
                    dm = envelope.get("dataMessage") or {}
                    # sourceNumber / sourceUuid, with legacy source as a fallback
                    sender = sender_id(envelope, self.allowed)
                    if not sender:
                        continue
                    text = dm.get("message") or ""
                    att_path = None
                    for a in (dm.get("attachments") or []):
                        att_path = self._resolve_attachment(a)
                        if att_path:
                            break
                    if not text and not att_path:
                        continue
                    reply, attachment = self.engine.handle(sender, text, att_path)
                    if reply:
                        self.send(sender, reply, attachment)
            except Exception as e:
                print(f"[signal] connection error: {e}; retrying in 10s")
                time.sleep(10)