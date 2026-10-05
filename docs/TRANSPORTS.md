# Transport integrations

HouseBot's brain is transport-agnostic: `engine.handle(sender, text, attachment)`
returns `(reply, attachment)`. Three transports ship built-in; adding one (Matrix,
XMPP, Discord, MQTT...) is ~50 lines: receive → call handle → send the reply.

## Signal (signal-cli daemon)

Requires [signal-cli](https://github.com/AsamK/signal-cli) running in daemon mode
with JSON-RPC over a UNIX socket:

```bash
signal-cli -u +15555550100 daemon --socket ~/.local/run/signal-cli/socket
```

`+15555550100` stands in for the bot's own account.

Config:

```json
"signal": {"enabled": true, "socket": "~/.local/run/signal-cli/socket",
           "account": "+15555550100"}
```

`bot.allowed_senders` is required. An empty list rejects every incoming message.
Each entry is compared to the envelope's `sourceNumber` (E.164 phone number),
`sourceUuid` (account UUID), or the legacy `source` field. Put the phone number,
the UUID, or both. The same list is shared with Telegram, where the entries are
numeric user ids, so a Signal-only number will not admit a Telegram user.

## Telegram (Bot API, long polling)

1. Talk to @BotFather → `/newbot` → save the token
2. Config: `"telegram": {"enabled": true, "token_file": "~/.config/housebot/telegram_token"}`
3. `bot.allowed_senders` takes Telegram numeric user IDs

## HTTP API (OpenAI-compatible)

Off unless `transports.api.enabled` is true. It listens on `127.0.0.1` and
`transports.api.port` (default 8082). Startup refuses an empty token or the
placeholder `change-me`.

`POST /v1/chat/completions` with `Authorization: Bearer <token>` and an
OpenAI-style `messages` array. A missing or wrong token is 401. The last message
is the turn; up to three earlier messages in that request are the context for
follow-ups such as "what is her email".

## Attachments

Transports resolve attachments to a local file path and hand them to the engine.
Today the brain accepts them (and skill modules like the Paperless OCR flow in the
production House bot consume them); more attachment-driven skills are on the roadmap
(see CAPABILITIES.md).
