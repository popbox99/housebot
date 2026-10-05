# Voice: wake word → STT → intent → TTS

HouseBot's voice pipeline mirrors the architecture proven in production ("house-voice"):

```
"Hey Mycroft" (openWakeWord)
   → record with silence detection
   → Wyoming STT server (whisper, e.g. faster-whisper small-int8)
   → HouseBot HTTP API  (POST /v1/chat/completions — same brain as Signal/Telegram)
   → Wyoming TTS server (piper)
   → play audio
```

## Server side (one machine, often the bot host)

Home Assistant's Wyoming add-ons are the easiest way to run the servers, or run them
standalone:

```bash
pip install wyoming-faster-whisper   # STT   (port 10300)
pip install wyoming-piper            # TTS   (port 10200)
pip install wyoming-openwakeword     # wake  (port 10400)
```

## Client side

A small daemon that:

1. Waits for the wake word (openWakeWord, local — audio never leaves the machine
   until after the wake word fires)
2. Records until silence
3. Streams audio to the Wyoming STT server — **Wyoming 1.x wire format**: a header
   JSON line (`type`, `data_length`, `payload_length`), then a data JSON blob, then
   the audio payload. The data object is NOT part of the header line — getting this
   wrong yields `text=None` transcripts
4. POSTs the transcript to HouseBot's HTTP API
5. Sends the reply text to the Wyoming TTS server and plays the audio

## Toggle pattern

A file-based kill switch works well across desktops:
`~/.local/state/housebot/voice-active` present = listening, absent = off (SIGHUP or
poll to reload). Bind a global hotkey to toggle it.

## Reference

The production implementation this design came from is a single ~300-line Python
daemon (openWakeWord + Wyoming client + pulseaudio). A portable skeleton lives in
`scripts/voice-listener.py` — wire your own audio I/O for your desktop environment.
