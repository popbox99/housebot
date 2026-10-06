# live/ — the deployed Signal bot

`signal-llm-bot.py` is the **single-file Signal bot that actually runs on the home server** (a Mac mini, `homebrain`). It is a snapshot of the live script, **not** part of the packaged `housebot` app in [`../housebot/`](../housebot/). The two share ideas but not code; nothing in `housebot/` imports this file.

The snapshot was taken on 2026-10-05 and scrubbed before it was committed. Tokens, passwords, phone numbers, Signal UUIDs, private (Tailscale/LAN) IP addresses, and family names were removed. Everything machine- or person-specific now comes from environment variables, and none of them has a hardcoded default.

## What it does

- Talks to a local `signal-cli` daemon over its JSON-RPC socket (`$XDG_RUNTIME_DIR/signal-cli/socket`) and answers allowlisted senders.
- Routes each message with keyword rules first and an LLM intent classifier second: files (`/find`, `/send`, `/organize`), calendar and tasks on Radicale (CalDAV), reminders, Obsidian notes and lists, pantry/chores/habits, Home Assistant (people, batteries, vacuum), Paperless-ngx, Immich photo search, web search, weather, ntfy alerts, and plain chat.
- Serves an OpenAI-compatible HTTP API on port 8082 (`/v1/chat/completions` and `/v1/responses`) so Home Assistant voice can use the same engine.
- Runs background threads: a store-arrival watchdog (shopping-list nudges), ntfy alert forwarding, and the recurring-reminder loop.

It depends on other local services (a file-tools API on `127.0.0.1:8000`, Radicale, Home Assistant, Paperless, Immich, Ollama-compatible LLM servers). Without them, the matching features fail on their own and the rest of the bot keeps working.

## Environment variables

All are read with `os.environ.get(...)`. See [`.env.example`](.env.example) for the full list with empty values. Keep the filled-in copy **outside** the repo.

| Variable | Needed for | Notes |
| --- | --- | --- |
| `SIGNAL_ACCOUNT` | Signal (required) | The number signal-cli is registered as, E.164 (`+1…`). |
| `SIGNAL_ALLOWED_NUMBERS` | Signal (required) | Comma-separated E.164 numbers and/or Signal UUIDs. Empty = nobody is answered. Everyone listed also receives reminders and nudges. |
| `SIGNAL_SENDER_NAMES` | optional | `id=name,id=name` display names for allowlisted ids. |
| `SIGNAL_ALERT_RECIPIENT` | optional | Who gets ntfy alerts. Defaults to the first entry of `SIGNAL_ALLOWED_NUMBERS`. |
| `HOUSE_API_TOKEN` | HTTP API | Bearer token Home Assistant must send. Empty = every API request gets 401. |
| `HA_TOKEN` | Home Assistant | Long-lived access token. |
| `HA_STORE_WATCH_ENTITY` | store watchdog, location reminders | Phone `device_tracker.*` entity to poll. Empty = watchdog idle. |
| `HA_VACUUM_ENTITY`, `HA_VACUUM_BATTERY_ENTITY` | vacuum commands, battery report | `vacuum.*` and its battery `sensor.*`. |
| `CALDAV_USER`, `CALDAV_PASSWORD` | calendar, tasks, shopping sync | Radicale credentials (server is `127.0.0.1:5232`). |
| `PAPERLESS_TOKEN` | Paperless upload/Q&A, business-card OCR | Paperless-ngx API token. |
| `IMMICH_API_KEY` | `/photos` | Immich API key. |
| `NTFY_URL`, `NTFY_TOKEN` | `/alerts`, alert forwarding | Full topic URL; token only if the topic is protected. |
| `LLM_PRIMARY_URL`, `LLM_FALLBACK_URL` (+ `_MODEL`, `_NAME`, `_PRESENCE`) | chat, intent, extraction | OpenAI-compatible base URLs (`http://host:11434/v1`). Only used when `~/.local/share/house/llm_backends.conf` is missing. The primary is presence-gated by `~/.local/share/house/omarchy_llm_up` unless `LLM_PRIMARY_PRESENCE` says otherwise. |
| `WHISPER_URLS` | voice notes | Comma-separated remote `/transcribe` URLs. If none answer, transcription falls back to local faster-whisper. |
| `EMBED_URL` | semantic memory | Ollama `/api/embeddings` URL (nomic-embed-text). Empty = keyword-only recall. |
| `PHOTO_SEARCH_URL` | photo search fallback | Base URL of the vision photo-search service, used when Immich isn't configured. |
| `SYNCTHING_PHONE_DEVICE_ID` | `/syncthing` fallback | Syncthing device ID (or prefix) of the phone. |
| `HOUSE_CONTACTS_JSON` | contact dedupe | Defaults to `/tank/data/house/contacts.json`. |

## Recurring reminders (added 2026-10-05)

Plain-language repeating reminders and chores:

- "I change the furnace filter every 3 months", "I water the plants every sunday", "remind me every monday at 7pm to take out the trash", "… once a month", "… quarterly", "every other friday", "on the 15th", "starting tomorrow", "last time was oct 3".
- They are stored in `~/.local/share/house/recurring_reminders.json`, one record per item (`id`, `what`, `unit`/`n`, `anchor`, `hour`/`minute`, `next`, `last_fired`, `active`). A new item with the same chore wording updates the existing one instead of adding a duplicate.
- A background thread checks every **30 seconds** (`RECUR_CHECK_SECONDS`). Each due item is sent over Signal to every allowlisted sender, then its next occurrence is scheduled. If a send fails, it is retried 5 minutes later. Times are America/New_York, and the default is 9:00 AM when the message names no time.
- Chore-style items ("I change…") are also recorded in `chore_intervals.json` and the Obsidian maintenance log, and the older interval nudge stays quiet for them. Items that start with "remind me" are reminders only.
- `/reminders` (or "list my reminders") shows recurring items plus pending one-time reminders. `/unremind <words or id>` (or "stop the … reminder") deactivates a recurring item. One-time reminders are not touched.
- A confirmation mentions **only the item just set**. It never re-lists older reminders.
- Guard: the chat model cannot create reminders. If a chat reply claims it "set", "scheduled", or "added" a reminder, event, task, or chore, the bot replaces that reply with a note saying nothing was set and showing how to phrase a real reminder.

## Running

```sh
# with the variables exported (see .env.example)
python3 live/signal-llm-bot.py
```

Python 3.9+ (uses `zoneinfo`), standard library only, apart from optional `faster_whisper` for the local transcription fallback and the optional `contact_lookup` module.
