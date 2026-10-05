# Single machine vs multi-machine

HouseBot is one process, but its dependencies can live anywhere.

## Single machine (simplest)

Everything on one box: HouseBot + Ollama + Radicale + (optionally) signal-cli.

```json
{
  "llm": {"backends": [{"name": "local", "base_url": "http://127.0.0.1:11434",
                         "model": "qwen3:4b-instruct", "api": "ollama"}]},
  "caldav": {"base_url": "http://127.0.0.1:5232", "user": "me"}
}
```

Works fine on a Raspberry Pi 5, mini-PC, or an always-on laptop.

## Multi-machine (the production pattern)

Separate concerns so a laptop can sleep while the brain stays up:

```
[bot host]        HouseBot + Radicale + signal-cli     (always-on mini-PC/Mac)
    │
    ├── LLM chain: backends tried in order, first reachable wins
    │     gpu-box:11434 (qwen3:8b)  →  mini:11434 (qwen3:4b)  →  localhost (3b)
    ├── skills may point at other machines (Paperless, Immich, Home Assistant)
    └── contacts.json built on a desktop (house-contacts), synced over
        Syncthing/rsync — the lookup picks it up by mtime, no restart
```

Rules of thumb learned the hard way:

- **The bot host and the CalDAV server must be always-on.** LLM boxes can sleep —
  the chain just falls through (that's why it's a chain).
- **LLMs sleep, files don't**: reminders.json, contacts.json and CalDAV are all
  file-based, so any machine going down never loses state.
- **Pin models that should stay loaded**: Ollama `OLLAMA_KEEP_ALIVE=-1` (or
  `keep_alive: -1` per request) for your primary extraction model, so the first
  voice interaction of the morning isn't a cold load.
- Put the tailnet/VPN between machines; bind services to loopback or VPN IPs only.
- One writer per synced file: schedule `housecontacts.py build` on exactly one
  machine.

## The HTTP API as a glue layer

`transports.api` exposes an OpenAI-compatible `/v1/chat/completions` — anything that
speaks that protocol (Home Assistant's `openai_conversation`, scripts, other UIs)
drives the same brain with the same history rules. Point it at
`http://bot-host:8082/v1` with the API token from config.
