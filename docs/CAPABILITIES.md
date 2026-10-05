# Capability map

Where each production-House capability lives in this generic build. "Optional skill"
= works when the matching infrastructure is configured.

| Capability | Status | Module / doc |
|---|---|---|
| Intent pipeline (keyword regex → LLM classifier with context) | ✓ core | `intents.py` |
| LLM fallback chain across machines | ✓ core | `llm.py`, [MULTI-MACHINE](MULTI-MACHINE.md) |
| Timed reminders ("remind me to X tomorrow at 3pm", both word orders) | ✓ core | `skills/reminders.py` |
| Word-order-independent time parsing (the production fix) | ✓ core | `whens.py` |
| Reminder dispatcher (scheduled brain) | ✓ core | `jobs.py` |
| Tasks (VTODO) via CalDAV, incl. "mark the task done" | ✓ core | `skills/calendar.py` |
| Calendar events (VEVENT) via CalDAV | ✓ core | `skills/calendar.py` |
| Agenda view (today/week) | ✓ core | `skills/calendar.py` |
| Contact lookup ("what is her email" + follow-ups) | ✓ core | `skills/contacts.py` + house-contacts |
| Contact enrichment on call/text/email | ✓ core | `skills/contacts.py` |
| Contact adding (dictated; business-card via Paperless OCR) | ✓ core | `engine.py` |
| Markdown notes | ✓ core | `skills/basic.py` |
| File find | ✓ core | `skills/basic.py` |
| Chat + summarize with backend tags | ✓ core | `skills/chat.py` |
| Shopping list (markdown note, add/remove/show) | ✓ core | `skills/lists.py` |
| Chores log + status | ✓ core | `skills/lists.py` |
| Habits logging | ✓ core | `skills/lists.py` |
| Remember/forget/recall (HID "where did I put X") | ✓ core | `skills/lists.py` |
| Weather (Open-Meteo, no API key) | ✓ core | `skills/web.py` |
| Web search via self-hosted SearXNG | ✓ optional | `skills/web.py` |
| Read URL + summarize | ✓ core | `skills/web.py` |
| OpenAI-compatible HTTP API (Home Assistant etc.) | ✓ core | `transports/api.py` |
| Signal transport | ✓ core | `transports/signal.py` |
| Telegram transport | ✓ core | `transports/telegram.py` |
| Voice notes → Wyoming STT → intent | ✓ core | `wyoming.py`, `engine.py` |
| Voice: wake word → STT → API → piper TTS | ✓ documented + skeleton | [VOICE.md](VOICE.md), `scripts/voice-listener.py` |
| Home Assistant: presence, location reminders, batteries, vacuum | ✓ optional | `skills/homeassistant.py` |
| Paperless: upload, OCR, document Q&A, business-card contacts | ✓ optional | `skills/paperless.py` |
| Immich photo search (with fallback service) | ✓ optional | `skills/photos.py` |
| Watchdog: health checks + change-only ntfy alerts | ✓ optional | `skills/watchdog.py` |
| Radicale/CalDAV + Thunderbird/Proton/Gmail sync | ✓ documented | [CALDAV-RADICALE](CALDAV-RADICALE.md), SYNC-* |
| Obsidian-vault-specific Q&A (grep the vault) | use FIND | `skills/basic.py` with search_dirs |

Design rule: **skills fail soft.** Anything unconfigured silently declines rather
than crashing the brain.

Smoke test: `python3 tests/smoke.py` (no network needed — stubs the LLM).
