# HouseBot

HouseBot is a small home chatbot. One Python process, standard library only, reads a JSON config, and answers messages through Signal, Telegram, or a local HTTP API. Reminders, lists, notes, and a memory file live on disk. Calendar items are written to a CalDAV server when that server is configured. Optional skills talk to Home Assistant, Paperless-ngx, Immich, and a SearXNG instance you run yourself.

The intent matcher is a set of regular expressions, then an LLM classifier for whatever those expressions miss. Skills that are switched off answer with a short "not configured" line and the process keeps running. Two startup crashes are the exception: a configured CalDAV URL, and an HTTP API token left as `change-me`. Both are described under [Known issues](#known-issues).

Version in the package is 0.1.0. The longer setup guides live in [`docs/`](docs/) and are linked from the sections below.

## Feature overview

- **Transports.** Signal via signal-cli's JSON-RPC socket, Telegram via long polling, and an OpenAI-shaped HTTP API bound to `127.0.0.1`.
- **Reminders.** Natural-language times ("in 2 hours", "tomorrow at 3pm", either word order) saved to `reminders.json` and delivered by a background thread.
- **Lists and memory.** Shopping, chores, and habits as Markdown notes. Short facts in `memory.json`.
- **Notes and file find.** A dated Markdown note, or a filename search under directories you list.
- **Calendar and tasks.** VEVENT and VTODO written to CalDAV (Radicale or Nextcloud). Agenda and "mark complete" are implemented beside that writer. See [Known issues](#known-issues) before enabling this.
- **Contacts.** Lookup through the separate [house-contacts](https://github.com/popbox99/house-contacts) module, and appending a vCard when you dictate a new contact.
- **Web.** Weather from Open-Meteo (no key). Web search through your SearXNG. Fetch-and-summarize for public `https://` URLs, with a partial SSRF check.
- **Home Assistant.** Battery report, vacuum status, and "remind me when I get home" against a person entity.
- **Paperless and Immich.** Document upload and questions, photo search by text.
- **Watchdog.** HTTP health checks with a notification to ntfy.sh when a check flips between up and down.
- **Voice.** Signal voice-note attachments go to a Wyoming speech-to-text server. A separate listener script is the wake-word → API → piper path ([`docs/VOICE.md`](docs/VOICE.md)).
- **LLM chain.** Ollama or any OpenAI-compatible server. The first backend that answers is the one that is used.

## How a message is handled

`Engine.handle(sender, text, attachment)` is the only entry point. Transports differ in how they receive and send; they share this path.

1. An attachment (Signal only) is handled by file extension: image or PDF/DOC toward Paperless, audio toward Wyoming. See [Attachments and voice notes](#attachments-and-voice-notes).
2. Otherwise `keyword_intent` in `housebot/intents.py` tries the regular expressions in order. The first match wins.
3. If nothing matches, the LLM is asked to print one label such as `REMIND` or `CHAT`. The parser only keeps a subset of those labels (`FIND`, `CHAT`, `WEATHER`, `SEARCH_WEB`, `SUMMARIZE`, `EVENT`, `TASK`, `AGENDA`, `REMIND`, `NOTE`, `CONTACT`, `CONTACT_INFO`, `REMINDERS`, `COMPLETE`). Any other label the model prints, including `READ_URL`, `PHOTOS`, `VACUUM`, and the list skills, is discarded and the turn becomes ordinary chat.
4. The matching skill runs. If there is no handler, the text goes to chat.
5. When contacts are loaded, replies for reminders, tasks, events, and chat are passed through house-contacts so a "call Dana" style answer can pick up a number.

Each sender keeps the last four user turns in memory. That memory is per process and is dropped on restart. The HTTP API does not feed the `messages` array into this history; see [HTTP API](#http-api).

## Capabilities

Example sentences below are ones the keyword matcher actually classifies. Where the bot then depends on a server, the "Unconfigured" line is the reply you get (or the startup behavior) without that server.

### Reminders

| You send | What happens |
|---|---|
| `remind me in 2 hours to check the dryer` | Parses a relative time and stores the reminder. |
| `remind me to call Dana tomorrow at 3pm` | Same, with the clock time after the task. |
| `remind me at 3pm tomorrow to call Dana` | Same reminder; the time may come first. |
| `what are my reminders` | Lists pending rows from `reminders.json` (up to 10). |
| `pending reminders` | Same list. |

Times the fast parser accepts include `in 45 minutes`, `in 2 hours`, `in 3 days`, `tomorrow at 7pm`, `at 3pm tomorrow`, `next monday at 9`, `friday evening`, `tomorrow at noon`, a bare `tomorrow` (09:00), and `tonight` (20:00). Dayparts map to morning 09:00, afternoon 15:00, evening 19:00, night 21:00, noon 12:00, midnight 00:00. Anything else is sent to the LLM as a JSON extraction. The stored timestamp is minute precision (`YYYY-MM-DD HH:MM`).

`remind me to file taxes`, with no time word, still matches the reminder rule, then fails with `Could not parse the reminder time`. It does not become a CalDAV task. Use `todo file taxes` for an untimed task.

**Config.** `bot.data_dir` (default `~/.local/share/housebot`) holds `reminders.json`. Delivery uses `bot.owner` and a push transport; see [Scheduled jobs](#scheduled-jobs).

**Unconfigured.** No extra service is required to *store* a reminder. With no Signal or Telegram transport running, the background job still deletes the row when it is due and has nowhere to send it.

### Calendar, tasks, and agenda

| You send | Keyword result | What the handler does |
|---|---|---|
| `todo file taxes` | `TASK` | Creates a VTODO due at the parsed time, or in 7 days at 09:00 if no time is found. |
| `add dinner with Mike Friday 7pm to my calendar` | `EVENT` | Creates a VEVENT lasting one hour. |
| `whats on my calendar today` | `AGENDA` | Lists today's events and open tasks. |
| `whats on my agenda this week` | `AGENDA` with an empty range | Same today-sized window. The keyword always passes an empty argument, so the 7-day branch does not run for this sentence. |
| `mark the task file taxes done` | `TASK`, not complete | Stored as a new task whose title is the leftover words (`mark the file taxes done`). The complete handler never sees it. |

**Config.** `caldav.base_url`, `caldav.user`, `caldav.password` or `caldav.password_file`, `caldav.calendar` (default `calendar`), `caldav.tasks` (default `tasks`), `caldav.tz` (default `America/New_York`).

The client sends HTTP Basic auth and `PUT`s one `.ics` per item at `{base_url}/{user}/{collection}/{uid}.ics`. Reads `GET` the collection URL and scan the body for `BEGIN:VEVENT` / `BEGIN:VTODO`. That is plain HTTP, not a CalDAV `REPORT`.

**Unconfigured.** Leave `caldav.base_url` empty. Agenda, tasks, and events then reply `Calendar is not configured (skills.caldav.base_url in config).` The setting is actually `caldav.base_url`. Setting a URL currently aborts startup; see [Known issues](#known-issues).

Device sync, once a server really has the objects: [Radicale](docs/CALDAV-RADICALE.md), [Thunderbird](docs/SYNC-THUNDERBIRD.md), [Android (DAVx⁵ + Tasks.org)](docs/CALDAV-ANDROID.md), [iPhone and iPad](docs/APPLE-IPHONE.md), [Google](docs/SYNC-GMAIL.md), [Proton](docs/SYNC-PROTON.md).

### Contacts

| You send | What happens |
|---|---|
| `what is Dana Reyes's phone number` | Looks up that name and returns phone numbers. |
| `what is her email` | Uses the previous user turn in this chat to recover the name, then returns emails. |
| `add contact John Smith 555-123-4567` | Asks the LLM for JSON and appends a vCard to the ledger file. |

Lookup needs two things at once: `skills.contacts_json` pointing at a real `contacts.json`, and an importable `contact_lookup` module from [house-contacts](https://github.com/popbox99/house-contacts). The matcher (exact, nickname, swapped names, unique first or last, substring) lives in that module. Ambiguous names get a `Which one?` question.

Adding a contact needs `skills.contact_ledger_vcf` and a reachable LLM. The bot does not rebuild `contacts.json` itself. The reply says the card merges on the next house-contacts build. Put the ledger path in house-contacts as a source.

**Unconfigured.** Lookup replies `Contacts skill is not configured.` Adding replies `Contact adding is not configured (skills.contact_ledger_vcf in config).` A missing LLM on add replies that the contact could not be parsed.

### Notes and file find

| You send | What happens |
|---|---|
| `note the furnace filter size is 16x25x4` | Writes a Markdown file in the notes directory, named with today's date and a short slug. |
| `find lease` | Returns up to five file paths whose names contain `lease`. |
| `find the lease pdf` | Same search. |
| `summarize the meeting notes from yesterday` | Sends your words to the LLM. This does not open a URL or a file. |

Find walks `skills.search_dirs` and skips dot-directories, `.git`, `node_modules`, `__pycache__`, and `.cache`. It matches filenames only, not file contents.

**Config.** `skills.notes_dir` (default `~/Documents/Notes`, created on startup). `skills.search_dirs` (list of directories; empty disables find).

**Unconfigured.** Notes always have a directory. Find replies `Search is not configured (skills.search_dirs in config).`

### Shopping, chores, habits, and memory

State files:

| File | Default location | Config override |
|---|---|---|
| Shopping List.md | `skills.notes_dir` | `skills.shopping_note` (filename only) |
| Chores.md | `skills.notes_dir` | `skills.chores_note` |
| Habits.md | `skills.notes_dir` | `skills.habits_note` |
| memory.json | `bot.data_dir` | none |

These filename keys are read by the code and are absent from `config.example.json`. A relative name is joined onto `notes_dir`.

| You send | What happens |
|---|---|
| `add milk and eggs to the shopping list` | Appends `- milk` and `- eggs` under a `## Groceries and Supplies` heading. |
| `whats on my shopping list` | Prints every `- ` line in the file. |
| `remove milk from my shopping list` | Deletes the first matching bullet. |
| `i changed the hvac air filter today` | Appends `- YYYY-MM-DD — changed the hvac air filter`. |
| `chores status` | Prints the last 10 chore bullets. |
| `log that i drank 24 ounces of water` | Appends a timestamped habit line. |
| `remember that the spare key is in the desk drawer` | Saves a fact under a short topic in `memory.json`. |
| `where did i put the spare key` | Searches topics and facts for those words. |
| `what do you remember about my sister` | Same memory search. |
| `forget what i said about the boat keys` | Deletes the first memory key that contains that text. |

Shopping add splits on commas and the word `and`. Remove is case-insensitive and stops at the first hit. Chores are an append-only log: `changed`, `cleaned`, `replaced`, or `fixed` together with `today` or `yesterday` is enough to log, even without the word "chore". Habit logging requires `log` or `logged` plus one of `water`, `oz`, `ounces`, `ml`, `reading`, `minutes`, `pages`, `steps`, `miles`, `exercise`, `meditation`.

`hide the passport in the desk` does not match any keyword. There is an `HID` handler, and the LLM prompt mentions it, but the classifier parser drops `HID`, so that handler never runs. `where did i hide the passport` is a memory search, which finds a fact if you stored one with `remember`.

**Unconfigured.** These four need no network. They create their files on first use.

### Weather, search, and reading a URL

| You send | What happens |
|---|---|
| `what's the weather in Springfield` | Geocodes the place and returns a two-day forecast in °F and mph. |
| `search the web coiled tubing prices` | Queries SearXNG and lists titles and URLs. |
| `read https://example.com/article` | Fetches the page and asks the LLM to summarize the visible text. |

`summarize this page https://example.com/article` matches **summarize**, which sends the sentence to the LLM and does not fetch the URL. Use `read` plus an `https://` URL to fetch.

Weather uses `https://geocoding-api.open-meteo.com` and `https://api.open-meteo.com`. No API key. With no place in the sentence it uses `skills.default_place`.

Search uses `skills.searxng.base_url` (for example `http://127.0.0.1:8888`) and requests `{base}/search?q=...&format=json`. The keyword requires a word like `search`, `google`, or `look up` **and** a word like `web`, `internet`, or `online`. `google coiled tubing` alone falls through to the LLM.

`read_url` keeps only `https://` URLs whose DNS answers are all public addresses under Python's `ipaddress` flags (`is_private`, `is_loopback`, `is_link_local`, `is_reserved`, `is_multicast`, `is_unspecified`). It then strips tags and summarizes at most 12,000 characters. Limits of that check are in [Known issues](#known-issues).

**Unconfigured.** Weather with no place and no `default_place`: `Weather needs a place (or skills.default_place in config).` Search with an empty `base_url`: `Web search is not configured (skills.searxng.base_url).` A SearXNG error is reported with that same sentence. A blocked URL: `I can only fetch https:// pages on public hosts.`

### Home Assistant

| You send | What happens |
|---|---|
| `how are the batteries` | Lists entities whose `battery_level` or `battery` attribute is 30% or below. |
| `vacuum status` | Reads `skills.homeassistant.vacuum_entity` (default `vacuum.robot`). |
| `start the vacuum` | `POST /api/services/vacuum/start` with an empty JSON body. |
| `dock the vacuum` | `POST /api/services/vacuum/return_to_base` with an empty JSON body. |
| `remind me when I get home to unload the car` | Saves a location reminder for zone `home`. |
| `remind me when I get to work to ...` | Same, for zone `work`. |

`start` and `dock` do not send `entity_id`, so `vacuum_entity` applies to the status read only. `stop` and `clean` match the keyword and then take the status path, because the service map only has `start`, `dock`, and `status`.

Location reminders poll the person entity every 30 seconds. The entity's state is lowercased and compared to the words `home` or `work`. `skills.homeassistant.zones` is stored and never read. The saved record has no "already here" marker, so a reminder can fire on the next poll while you are already in that zone.

**Config.** `skills.homeassistant.enabled`, `base_url` (example `http://127.0.0.1:8123`), `token_file` (example `~/.config/housebot/ha_token`), `person_entity` (example `person.me`), `vacuum_entity`, `zones`.

**Unconfigured.** With `enabled` false the replies are `Battery report is not configured (skills.homeassistant in config).`, and the same shape for vacuum and location reminders. The object is created whenever `enabled` is true, even if `base_url` is empty; calls then fail when they hit the network. Location polling also requires `person_entity`.

### Paperless-ngx

| You send | What happens |
|---|---|
| `what does my warranty say about the furnace` | Searches `/api/documents/?query=`, takes up to three hits, and asks the LLM to answer from the OCR text. |
| A Signal image, PDF, or DOC/DOCX | Uploads to `/api/documents/post_document/` and waits up to 90 seconds for OCR text. |
| An image whose caption mentions `contact` or `business card` | After OCR, runs the add-contact path on the extracted text. |

Auth header is `Authorization: Token <contents of token_file>`.

**Config.** `skills.paperless.enabled`, `base_url` (example `http://127.0.0.1:8010`), `token_file` (example `~/.config/housebot/paperless_token`).

**Unconfigured.** Questions: `Paperless is not configured (skills.paperless in config).` Attachments: `Received the image, but Paperless isn't configured.` or the document equivalent. Telegram and the HTTP API never pass attachments into this skill.

### Immich photos

| You send | What happens |
|---|---|
| `photos of the dog at the lake` | Smart-searches Immich and lists up to five file names and dates. |

The reply is text. Image bytes are not sent back. If Immich errors or is disabled and `fallback_url` is set, the bot `GET`s `{fallback_url}/photos/search?q=...` and expects JSON `{"results": [...]}`.

**Config.** `skills.immich.enabled`, `base_url` (example `http://127.0.0.1:2283`), `api_key_file` (example `~/.config/housebot/immich_key`), `fallback_url`.

**Unconfigured.** `Photos is not configured (skills.immich in config).` With the skill enabled and no matches: `No photos matched '...'.`

### Chat

Anything that misses the keywords (and misses the small set of LLM labels) is chat. Example: `tell me a joke`.

`llm.backends` is tried in order. Each entry has `name`, `base_url`, `model`, and `api` (`ollama` or `openai`). Ollama is `POST {base_url}/api/chat`. OpenAI-compatible is `POST {base_url}/v1/chat/completions`. Timeout is 120 seconds. With two or more backends, the reply ends with a `[name]` tag of whichever one answered. If every backend fails: `No LLM backend reachable - check your config's llm.backends.`

`llm.extract_model` and `pick_extract_backend` exist in the tree and are not called. Extraction uses the same chain as chat.

More than one machine: [`docs/MULTI-MACHINE.md`](docs/MULTI-MACHINE.md).

## Transports

Enable each one under `transports.<name>.enabled`. `python3 -m housebot` starts a daemon thread per enabled transport, then the job thread. Construction of the API transport happens before any thread starts, so a bad API token stops Signal and Telegram as well.

`bot.allowed_senders` is **one list** shared by Signal and Telegram. The HTTP API does not read it.

- An empty list rejects every Signal message and every Telegram message. Both transports print a warning at startup.
- Signal compares the list to `envelope.source` only.
- Telegram compares the list to the sender's numeric user id, as a string.
- A list that only contains `+15555550100` can admit that Signal phone number and will reject every Telegram user. A list that only contains `"123456789"` does the reverse. Put both kinds of id in the list when both transports are on.

`bot.owner` is a single string. Reminder and location alerts call `send(owner, text)` on every push transport. Use a Signal number here when Signal should receive alerts. Telegram will also be called with that same string as `chat_id`, which only works when `owner` is the numeric Telegram chat id. One value cannot be both a phone number and a Telegram id.

### Signal (signal-cli JSON-RPC)

Run signal-cli in daemon mode against a UNIX socket:

```bash
mkdir -p ~/.local/run/signal-cli
signal-cli -u +15555550100 daemon --socket ~/.local/run/signal-cli/socket
```

`+15555550100` stands in for the bot account. Register and verify that account with signal-cli's own `link` or `register` flow before starting the daemon.

```json
"signal": {
  "enabled": true,
  "socket": "~/.local/run/signal-cli/socket",
  "account": "+15555550100"
}
```

The bot connects to the socket and reads one JSON object per line. Incoming texts are `params.envelope.dataMessage.message`. The account that owns the daemon is `transports.signal.account` and is sent on outbound `send` calls.

Outbound payload:

```json
{"jsonrpc": "2.0", "method": "send",
 "params": {"account": "+15555550100", "recipient": "+15555550199", "message": "..."}}
```

Attachments on the way out are a list of local paths. On the way in, the bot uses `dataMessage.attachments[].localFilePath`, or a file under `~/.local/share/signal-cli/attachments` whose name starts with the attachment id.

**Allowlist, phone number, and UUID.** signal-cli puts the sender on the envelope. Recent signal-cli fills `sourceNumber` (E.164 phone) and `sourceUuid` (the account UUID). The legacy `source` field is deprecated and is often missing. This bot reads only `envelope.source`:

```python
sender = envelope.get("source") or ""
if not self.allowed or sender not in self.allowed:
    continue
```

So:

- Put the phone number in `allowed_senders` only if your signal-cli still copies that number into `source`. The comparison is an exact string match (`+15555550100`, not `15555550100`).
- A UUID in `allowed_senders` does not match, including when the startup warning tells you to add one.
- If `source` is absent, the sender is `""` and the message is dropped even when `sourceNumber` or `sourceUuid` would have matched.

Replies are a direct `send` to that `source` value, including for group messages. The socket is shared by the receive loop and the reminder thread with no lock. Details and the older (now inaccurate) allowlist note: [`docs/TRANSPORTS.md`](docs/TRANSPORTS.md).

### Telegram

1. Create a bot with [@BotFather](https://t.me/BotFather) and store the token in a file.
2. Discover your numeric user id (a bot such as a user-id bot, or `getUpdates` once).
3. Config:

```json
"telegram": {
  "enabled": true,
  "token_file": "~/.config/housebot/telegram_token"
}
```

`transports.telegram.token` is accepted inline as well. The file wins when it exists. The bot long-polls `https://api.telegram.org/bot<token>/getUpdates` and sends with `sendMessage`. Text only; photos, files, and voice notes are ignored. Allowlist entries are decimal ids: `"allowed_senders": ["123456789", "+15555550100"]`.

### HTTP API

Bound to `127.0.0.1` and `transports.api.port` (default `8082`).

| | |
|---|---|
| Method and path | `POST` whose path ends with `/chat/completions` (so `/v1/chat/completions` works). |
| Auth header | `Authorization: Bearer <transports.api.token>` |
| Body | JSON `{"messages": [{"role": "user", "content": "remind me in 2 hours to check the dryer"}]}` |
| Success body | OpenAI-style `chat.completion` with the reply in `choices[0].message.content`. |
| Other paths | `404` intended. |
| Missing or wrong token | `401` intended. |
| Push | `push = False`. Jobs never call `send` on this transport, and the class has no `send` method. |

Startup refuses a missing token and refuses the literal `change-me` (`openssl rand -hex 32` is the hint in the error). The comparison helper hashes both sides with SHA-256 and uses `hmac.compare_digest`.

That helper is not what the request handler calls. `do_POST` invokes `self._auth` on the `BaseHTTPRequestHandler` subclass, which has no `_auth` method. Every POST, with or without a correct bearer token, raises `AttributeError` and the client sees a dropped connection. Treat the API as down until that is fixed. The voice listener posts to this same endpoint.

The handler builds a four-message `hist` list and then ignores it. Only the last message's `content` is passed to the engine, and the sender id is always the string `api`. Follow-ups such as "what is her email" work after an earlier API call in the same process, because the engine stored that earlier turn under `api`. They do not work from history that exists only inside the JSON body of one request.

There is no `/v1/models`, no GET, and no streaming.

```bash
curl -s http://127.0.0.1:8082/v1/chat/completions \
  -H "Authorization: Bearer $HOUSEBOT_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"whats on my shopping list"}]}'
```

Today that `curl` does not return JSON. See [Known issues](#known-issues).

## Scheduled jobs

`housebot/jobs.py` starts one daemon thread. Every 30 seconds it:

1. Pops due rows from `reminders.json` and sends `⏰ Reminder: ...` to each transport with `push = True` (Signal and Telegram). The recipient is `bot.owner`.
2. If Home Assistant is loaded, polls location reminders and sends any that fire (`📍 ...`).
3. Every 900 seconds, runs the watchdog.

Due reminders are removed from disk before the send is attempted. A send failure, or a process with zero push transports, still drops the reminder. The API transport is skipped on purpose (`push = False`), which is what keeps a missing `send` method from crashing the job loop.

### Watchdog

Separate from chat alerts. When `skills.watchdog.enabled` is true and `ntfy_topic` is non-empty, each `checks` entry is fetched with `urlopen`.

```json
"watchdog": {
  "enabled": true,
  "ntfy_topic": "my-housebot-watchdog",
  "checks": [{"name": "webui", "url": "http://127.0.0.1:8080"}]
}
```

State is `watchdog_state.json` in the data directory. ntfy.sh is notified only when a check newly fails (`🚨 DOWN`) or recovers (`✅ RECOVERED`). Messages go to `https://ntfy.sh/<topic>`, a public service, not to Signal or Telegram. The topic name is a secret in practice: anyone who knows it can subscribe.

## Voice and Wyoming

Two different paths:

**Voice notes on Signal.** Extensions `ogg`, `oga`, `m4a`, `mp3`, `wav`, and `aac` are read and passed to `housebot/wyoming.py`, which speaks the Wyoming protocol to `skills.wyoming_stt` (default `127.0.0.1:10300`). The client tells the server the audio is 16 kHz, 16-bit, mono WAV and chunks one second at a time. The transcript is fed back into `handle`. `_any_to_wav_stub` returns the file bytes unchanged, so an Opus voice note from Signal is not converted. Transcription then fails unless the bytes already are that WAV format.

**Wake word on a desktop.** [`scripts/voice-listener.py`](scripts/voice-listener.py) is a skeleton: Wyoming STT, `POST` the transcript to the HTTP API, Wyoming TTS (piper) for the reply. Wake-word detection, the microphone, and playback are left as hooks. Environment variables: `HOUSEBOT_API` (default `http://127.0.0.1:8082/v1/chat/completions`), `HOUSEBOT_API_TOKEN`, `STT_HOST`, `STT_PORT` (10300), `TTS_HOST`, `TTS_PORT` (10200). This path needs a working HTTP API.

Full pipeline, including a file toggle at `~/.local/state/housebot/voice-active`: [`docs/VOICE.md`](docs/VOICE.md).

## Install and quickstart

Requires Python 3 and a reachable LLM if you want chat or any skill that extracts with a model. The bot itself has no third-party packages.

```bash
git clone https://github.com/popbox99/housebot
cd housebot
mkdir -p ~/.config/housebot ~/.local/share/housebot
cp config.example.json ~/.config/housebot/config.json
```

Edit `~/.config/housebot/config.json` before the first run:

1. Set `caldav.base_url` to `""`. The example points at `http://127.0.0.1:5232`, and any non-empty value crashes startup ([Known issues](#known-issues)).
2. Either set `transports.api.enabled` to `false`, or replace `transports.api.token`. The example value `change-me` also crashes startup.
3. Set `bot.allowed_senders` to the ids you will actually send from, and `bot.owner` to the id that should receive reminders.
4. Point `llm.backends[0].base_url` at an Ollama you run, or set the backend list to servers you have. The example uses `http://192.168.1.50:11434` and `http://192.168.1.60:11434` as placeholders.
5. Leave `skills.homeassistant`, `paperless`, `immich`, and `watchdog` at `"enabled": false` until those servers exist.

```bash
python3 -m housebot
```

The config path is `HOUSEBOT_CONFIG` if set, otherwise `~/.config/housebot/config.json`.

A first useful message, once a transport is up and `allowed_senders` contains you: `remind me in 2 hours to check the dryer`. Then `whats on my shopping list` after `add milk to the shopping list`.

## Config reference

`config.example.json` is the annotated sample. `housebot/config.py` deep-merges it onto defaults. Strings that contain `~` are expanded. Secret files are read when a skill or transport starts; they are not pulled into the example.

| Key | Example / default | Role |
|---|---|---|
| `bot.name` | `"house"` | Present in config. No code reads it. |
| `bot.data_dir` | `~/.local/share/housebot` | Reminders, memory, watchdog state, location reminders. Created on startup. |
| `bot.owner` | `"+15550100"` in the example | Recipient for reminder and location pushes. |
| `bot.allowed_senders` | `["+15550100"]` | Shared Signal and Telegram allowlist. Empty rejects everyone. |
| `llm.backends[]` | name, `base_url`, `model`, `api` | Ordered fallback chain. `api` is `ollama` or `openai`. |
| `llm.extract_model` | `null` | Unused. |
| `caldav.base_url` | `http://127.0.0.1:5232` | Empty disables calendar. Non-empty currently crashes startup. |
| `caldav.user` | `"me"` | Collection owner and Basic-auth user. |
| `caldav.password` | `""` | Inline password. Prefer the file. |
| `caldav.password_file` | `~/.config/housebot/caldav_password` | First line is the password. |
| `caldav.calendar` / `caldav.tasks` | `calendar`, `tasks` | Collection names. |
| `caldav.tz` | `America/New_York` | Written into `TZID=` on VEVENT/VTODO. |
| `transports.signal.enabled` | `false` | |
| `transports.signal.socket` | `~/.local/run/signal-cli/socket` | |
| `transports.signal.account` | `"+15550100"` | Account passed to signal-cli `send`. |
| `transports.telegram.enabled` | `true` in the example | Turn off if you have no bot token. |
| `transports.telegram.token` | `""` | Inline token. |
| `transports.telegram.token_file` | `~/.config/housebot/telegram_token` | Used when the file exists. |
| `transports.api.enabled` | `true` | Default in code is also enabled. |
| `transports.api.port` | `8082` | Localhost only. |
| `transports.api.token` | `"change-me"` | Startup aborts on this value and on an empty token. |
| `skills.contacts_json` | path under `~/.local/share/house-contacts/` | Enables lookup when the file exists and `contact_lookup` imports. |
| `skills.contact_ledger_vcf` | `~/Documents/sync/bot-added.vcf` | Append-only vCard file. |
| `skills.notes_dir` | `~/Documents/Notes` | Notes, shopping, chores, habits. |
| `skills.search_dirs` | `["~/Documents"]` | Filename search. `[]` disables it. |
| `skills.default_place` | `""` | Weather fallback. |
| `skills.searxng.base_url` | `http://127.0.0.1:8888` | Empty disables web search. |
| `skills.wyoming_stt.host` / `port` | `127.0.0.1` / `10300` | Voice-note transcription. |
| `skills.immich.*` | enabled `false`, port `2283`, key file `immich_key` | Photo search. |
| `skills.paperless.*` | enabled `false`, port `8010`, `paperless_token` | Documents. |
| `skills.homeassistant.*` | enabled `false`, port `8123`, `ha_token`, `person.me`, `vacuum.robot` | |
| `skills.watchdog.*` | enabled `false`, ntfy topic, `checks[]` of `{name, url}` | |
| `skills.shopping_note`, `chores_note`, `habits_note` | filenames, not in the example | See the lists section. |

`config.py` still comments that an empty `allowed_senders` accepts everyone. The transports reject everyone. Trust the transports.

## Security

**Allowlist.** Signal and Telegram fail closed. Empty `bot.allowed_senders` drops every inbound message. The list does not apply to the HTTP API; that door is the bearer token, and it is localhost-only. Limitations of the Signal field that is compared are in [Known issues](#known-issues).

**API token.** Set a long random `transports.api.token`. Do not ship `change-me`. The process refuses to start with that default, which is the right failure, and then the request handler never reaches the constant-time compare, which is not. Keep the port on localhost or put a proxy you control in front of a future fixed build. The example and the code default both enable the API.

**Where secrets live.** Put them in `~/.config/housebot/` (mode `0700`) and point the `*_file` keys at them. The files the code actually opens:

| File | Used for |
|---|---|
| `~/.config/housebot/config.json` | The whole config. May contain `caldav.password` and `transports.api.token` inline. |
| `caldav_password` | CalDAV Basic auth. |
| `telegram_token` | Bot API token. |
| `ha_token` | Home Assistant long-lived token. |
| `paperless_token` | Paperless token. |
| `immich_key` | Immich `x-api-key`. |

`config.json` itself is gitignored. Prefer password files over inline secrets so a copied config is not enough to authenticate.

**What `.gitignore` covers.** Personal data: `config.json`, `contacts.json`, `*.vcf`, `*.sqlite`, `reminders.json`, `location_reminders.json`, `memory.json`, `watchdog_state.json`. Secret-looking names: `*_password*`, `*token*`, `*_token*`, `immich_key*`, `ha_token*`, `paperless_token*`, `.env`, `.env*` (so `.env.local` and similar are included), `secrets.json`, `ADMIN_CREDENTIALS`, `*.pem`, `*.key`. Logs: `*.log` in any directory, which includes `logs/*.log`. Caches: `__pycache__/`, `*.pyc`, `.venv/`.

Gitignore does not rewrite history. It only stops untracked files from being added.

**URL fetch.** `read_url` is the user-controlled fetch. Weather and search targets come from config or from hard-coded Open-Meteo URLs. Watchdog URLs come from config and are fetched with no address check, so point them at your own hosts.

**Watchdog topic.** ntfy.sh topics are public names. Pick an unguessable topic and treat it like a password.

## Running as a service

The repo does not ship a unit file. A user systemd service matches the default config path:

```ini
# ~/.config/systemd/user/housebot.service
[Unit]
Description=housebot
After=network-online.target

[Service]
Type=simple
WorkingDirectory=%h/housebot
Environment=HOUSEBOT_CONFIG=%h/.config/housebot/config.json
ExecStart=/usr/bin/python3 -m housebot
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
```

```bash
systemctl --user daemon-reload
systemctl --user enable --now housebot.service
```

Run signal-cli's daemon as its own service so the socket exists before HouseBot connects. HouseBot retries the socket every 10 seconds. Telegram and the API need no extra daemon. Ollama, Radicale, SearXNG, Wyoming, and Home Assistant are separate services; the bot waits for them on each call and fails that skill softly, except for the CalDAV startup crash.

## Testing

From the repo root:

```bash
python3 tests/smoke.py
```

The smoke test uses a temp directory and a stub LLM. It checks reminder word order, the reminder list, notes, find, shopping, chores, habits, memory, an agenda reply when CalDAV is off, and a chat reply when the LLM is down. It does not open a socket, does not bind the API, and does not exercise the SSRF check. On a clean tree this is 15 checks.

## Docs

| Topic | Doc |
|---|---|
| Module index | [`docs/CAPABILITIES.md`](docs/CAPABILITIES.md) |
| Radicale | [`docs/CALDAV-RADICALE.md`](docs/CALDAV-RADICALE.md) |
| Thunderbird | [`docs/SYNC-THUNDERBIRD.md`](docs/SYNC-THUNDERBIRD.md) |
| Proton | [`docs/SYNC-PROTON.md`](docs/SYNC-PROTON.md) |
| Google | [`docs/SYNC-GMAIL.md`](docs/SYNC-GMAIL.md) |
| Android | [`docs/CALDAV-ANDROID.md`](docs/CALDAV-ANDROID.md) |
| iPhone and iPad | [`docs/APPLE-IPHONE.md`](docs/APPLE-IPHONE.md) |
| Voice pipeline | [`docs/VOICE.md`](docs/VOICE.md) |
| One machine or several | [`docs/MULTI-MACHINE.md`](docs/MULTI-MACHINE.md) |
| Transport sketches | [`docs/TRANSPORTS.md`](docs/TRANSPORTS.md) |

`docs/CAPABILITIES.md` is the module map. Where a row there calls a feature done, and [Known issues](#known-issues) disagrees, the known issue is what the current code does. `docs/TRANSPORTS.md` still says an empty allowlist lets anyone who knows the number talk to the bot, and that allowlist entries should be Signal UUIDs. The code rejects an empty list, and it does not compare UUIDs.

## Known issues

These are behaviors of the current tree, commit `32cd921` on top of `f18f9d9`.

**HTTP API handler never authenticates.** `ApiTransport._auth` uses `hmac.compare_digest`, and startup rejects `change-me`. `Handler.do_POST` calls `self._auth` on the HTTP handler class, which does not define it. Verified: a missing token, a wrong token, and a correct token all raise `AttributeError` and the connection closes. Voice-listener and Home Assistant's OpenAI conversation integration depend on this endpoint.

**Signal allowlist ignores `sourceUuid` and `sourceNumber`.** Empty allowlists fail closed, and the sender is read from the envelope rather than from `dataMessage`. Only `envelope.source` is compared. UUID entries never match. Phone-number entries match only when signal-cli still populates `source`.

**`read_url` still allows Tailscale CGNAT on Python 3.12.** `file://`, plain `http://`, loopback, RFC1918, and link-local (including `169.254.169.254`) are refused. `100.64.0.0/10` is not marked private, reserved, or link-local by `ipaddress` on Python 3.12.3, and a hostname that resolves there is allowed. `urlopen` follows redirects with the default opener, and the redirect target is not passed through `_safe_url`. The DNS check and the later fetch are separate lookups (the code comment calls this out).

**CalDAV crashes the process.** `Engine` calls `Calendar(cfg)` when `base_url` is set. `Calendar.__init__` requires a second argument, `ask_llm_fn`. The result is `TypeError` during startup, before any transport listens. `config.example.json` sets a base URL, so a copied example hits this immediately.

**Marking a task complete does not run.** The `TASK` keyword matches any sentence containing "task" and is registered before `COMPLETE`, which also requires the word "task". `mark the task file taxes done` creates a task.

**Several skills never come back from the LLM classifier.** Keywords cover the happy path listed above. Phrases that miss the keyword and would need the model (photos, vacuum, batteries, paperless, shopping, habits, memory, reading a URL, location) are parsed as chat even when the model prints the right label.

**Agenda phrases do not select the week view.** The keyword returns an empty argument.

**Vacuum start and dock ignore `vacuum_entity`.** Status reads that entity. The service calls post `{}`.

**Location zones and "wait until I arrive".** `zones` is unused. Reminders can fire while the person entity is already `home` or `work`.

**Voice notes are not transcoded.** Non-WAV attachments are sent to Wyoming as if they were 16 kHz PCM WAV.

**One allowlist and one owner for two networks.** Documented above under transports. A Signal-only phone list silently drops Telegram, and reminder delivery uses a single `bot.owner` string for every push transport.

**API message history is local.** The OpenAI `messages` array is not applied, except the last `content`.

## License

MIT. See [LICENSE](LICENSE).
