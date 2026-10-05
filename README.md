# HouseBot

A privacy-first, messenger-agnostic home-assistant bot. One brain, many transports,
your own calendar server, your own LLMs — nothing about your life leaves your network.

```
Signal ─┐
Telegram ├─→  engine  ─→  skills ─→  CalDAV (Radicale/Nextcloud)
HTTP API ┘      │                └→  contacts.json (house-contacts)
                └→  LLM fallback chain (Ollama on 1..n machines)
                     ↑ voice: wake word → Wyoming STT → API → piper TTS
```

Born from a production household bot ("House") running daily for reminders, calendar
and task management, contact lookups, voice control, and document handling.

## Highlights

- **Messenger-agnostic**: Signal (signal-cli), Telegram, and an OpenAI-compatible
  HTTP API (so Home Assistant or any script can drive the same brain)
- **Natural-language reminders** with a word-order-independent time parser:
  *"remind me to call Dana tomorrow at 3pm"* and *"remind me at 3pm tomorrow to
  call Dana"* both work — deterministic (<1ms) with LLM fallback
- **CalDAV-native**: tasks (VTODO) and events (VEVENT) live on Radicale/Nextcloud
  and sync to Thunderbird, Android (DAVx⁵ + Tasks.org), and iOS
- **Contacts**: built by [house-contacts](https://github.com/popbox99/house-contacts)
  from Google/iCloud/Outlook/Thunderbird; the bot enriches call/text/email tasks and
  answers *"what is Dana's number?"* with *"what is her email?"* follow-up context
- **LLM fallback chain** across one or many machines; skills fail soft when a
  backend or integration is down
- **Voice**: wake word → Wyoming (whisper/piper) pipeline, documented end-to-end
- **Stdlib-only Python** for the core; no lock-in to any LLM vendor

## Quickstart (single machine)

```bash
git clone https://github.com/popbox99/housebot && cd housebot
# 1. Set up CalDAV (Radicale) + contacts:      see docs/CALDAV-RADICALE.md
# 2. Configure:
cp config.example.json ~/.config/housebot/config.json
$EDITOR ~/.config/housebot/config.json          # LLM backend + one transport
# 3. Run:
python3 -m housebot
```

Enable a transport, say *"remind me in 2 hours to check the dryer"*, and you're off.

## Docs

| Topic | Doc |
|---|---|
| Capability map (what's ported from production) | [docs/CAPABILITIES.md](docs/CAPABILITIES.md) |
| CalDAV server (Radicale) setup | [docs/CALDAV-RADICALE.md](docs/CALDAV-RADICALE.md) |
| Thunderbird sync (two-way, calendar + tasks) | [docs/SYNC-THUNDERBIRD.md](docs/SYNC-THUNDERBIRD.md) |
| Proton Mail/Calendar (honest limitations) | [docs/SYNC-PROTON.md](docs/SYNC-PROTON.md) |
| Google Calendar/Contacts sync | [docs/SYNC-GMAIL.md](docs/SYNC-GMAIL.md) |
| Android (DAVx⁵, Tasks.org) | [docs/CALDAV-ANDROID.md](docs/CALDAV-ANDROID.md) |
| iPhone/iPad | [docs/APPLE-IPHONE.md](docs/APPLE-IPHONE.md) |
| Voice pipeline | [docs/VOICE.md](docs/VOICE.md) |
| Single vs multi-machine | [docs/MULTI-MACHINE.md](docs/MULTI-MACHINE.md) |
| Transports (Signal/Telegram/API) | [docs/TRANSPORTS.md](docs/TRANSPORTS.md) |

## Status

Core is functional and in daily-family-use lineage; optional skills and remaining
roadmap items are tracked in [docs/CAPABILITIES.md](docs/CAPABILITIES.md). APIs may
still move before 1.0.

## License

MIT — see [LICENSE](LICENSE).
