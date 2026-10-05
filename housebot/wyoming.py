"""Wyoming client helpers (voice notes transcription; see docs/VOICE.md)."""

import json
import socket


def wyoming_event(sock, type_, data=None, payload=b""):
    blob = json.dumps(data or {}).encode()
    header = json.dumps({"type": type_, "data_length": len(blob),
                         "payload_length": len(payload)}).encode() + b"\n"
    sock.sendall(header + blob + payload)


def read_wyoming_event(sockfile):
    header = json.loads(sockfile.readline())
    data = json.loads(sockfile.read(header["data_length"]).decode()) \
        if header["data_length"] else {}
    payload = sockfile.read(header["payload_length"]) if header["payload_length"] else b""
    return header["type"], data, payload


def transcribe(wav_bytes, host="127.0.0.1", port=10300, language="en", timeout=60):
    """Stream WAV audio to a Wyoming faster-whisper server; returns transcript text."""
    with socket.create_connection((host, port), timeout=timeout) as sock:
        f = sock.makefile("rb")
        wyoming_event(sock, "transcribe", {"language": language})
        wyoming_event(sock, "audio-start",
                      {"rate": 16000, "width": 2, "channels": 1, "timestamp": 0})
        # chunk so large voice notes don't blow up framing
        CHUNK = 16000 * 2 * 1  # 1s of 16kHz 16-bit mono
        ts = 0
        for i in range(0, len(wav_bytes), CHUNK):
            chunk = wav_bytes[i:i + CHUNK]
            wyoming_event(sock, "audio-chunk",
                          {"rate": 16000, "width": 2, "channels": 1, "timestamp": ts},
                          payload=chunk)
            ts += 1000
        wyoming_event(sock, "audio-stop", {})
        while True:
            etype, data, _payload = read_wyoming_event(f)
            if etype == "transcript":
                return data.get("text") or ""