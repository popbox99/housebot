#!/usr/bin/env python3
"""Voice listener skeleton — wake word → Wyoming STT → HouseBot API → Wyoming TTS.

This is the documented pattern from docs/VOICE.md; audio I/O (wake-word detection,
recording, playback) is desktop-specific, so the hooks are left to you. The
production reference is a single ~300-line daemon using openWakeWord, the Wyoming
client protocol and pulseaudio/pipeWire.

Wyoming 1.x wire format (the gotcha that breaks everyone):
    line 1: {"type": ..., "data_length": N, "payload_length": M}   (JSON + \\n)
    then:   N bytes of JSON data blob
    then:   M bytes of payload (PCM audio)
    The data object is NOT part of the header line.

Key config (environment):
    HOUSEBOT_API=http://127.0.0.1:8082/v1/chat/completions
    HOUSEBOT_API_TOKEN=...
    STT_HOST=127.0.0.1  STT_PORT=10300
    TTS_HOST=127.0.0.1  TTS_PORT=10200
"""

import json
import os
import socket
import urllib.request

API = os.environ.get("HOUSEBOT_API", "http://127.0.0.1:8082/v1/chat/completions")
TOKEN = os.environ.get("HOUSEBOT_API_TOKEN", "")


def ask(text):
    req = urllib.request.Request(
        API, data=json.dumps({"messages": [{"role": "user", "content": text}]}).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {TOKEN}"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())["choices"][0]["message"]["content"]


def wyoming_event(sock, type_, data=None, payload=b""):
    """Send one Wyoming 1.x framed event."""
    blob = json.dumps(data or {}).encode()
    header = json.dumps({"type": type_, "data_length": len(blob),
                         "payload_length": len(payload)}).encode() + b"\n"
    sock.sendall(header + blob + payload)


def read_wyoming_event(sockfile):
    """Read one framed event; returns (type, data_dict, payload_bytes)."""
    header = json.loads(sockfile.readline())
    data = json.loads(sockfile.read(header["data_length"]).decode()) if header["data_length"] else {}
    payload = sockfile.read(header["payload_length"]) if header["payload_length"] else b""
    return header["type"], data, payload


def transcribe(wav_bytes, host, port):
    """Stream WAV to a Wyoming faster-whisper server, return transcript text."""
    with socket.create_connection((host, port), timeout=30) as sock:
        f = sock.makefile("rb")
        wyoming_event(sock, "transcribe",
                      {"language": "en"})
        wyoming_event(sock, "audio-start",
                      {"rate": 16000, "width": 2, "channels": 1, "timestamp": 0})
        wyoming_event(sock, "audio-chunk", {"rate": 16000, "width": 2,
                                            "channels": 1, "timestamp": 0},
                      payload=wav_bytes)
        wyoming_event(sock, "audio-stop", {})
        while True:
            etype, data, _payload = read_wyoming_event(f)
            if etype == "transcript":
                return data.get("text") or ""


def synthesize(text, host, port):
    """Ask piper to speak `text`; yields audio chunks (bytes)."""
    with socket.create_connection((host, port), timeout=30) as sock:
        f = sock.makefile("rb")
        wyoming_event(sock, "synthesize", {"text": text})
        while True:
            etype, data, payload = read_wyoming_event(f)
            if etype == "audio-chunk" and payload:
                yield payload
            elif etype == "synthesize-stop":
                break


if __name__ == "__main__":
    # Skeleton: plug in openWakeWord detection + mic capture + playback for your OS.
    print(__doc__)
