# Transport integrations

HouseBot's brain is transport-agnostic: `engine.handle(sender, text, attachment)`
returns `(reply, attachment)`. Three transports ship built-in; adding one (Matrix,
XMPP, Discord, MQTT...) is ~50 lines: receive → call handle → send the reply.

## Signal (signal-cli daemon)

Requires [signal-cli](https://github.com/AsamK/signal-cli) running in daemon mode
with JSON-RPC over a UNIX socket:

```bash
signal-cli -u +1YOURNUMBER daemon --socket ~/.local/run/signal-cli/socket
```

Config:

```json
"signal": {"enabled": true, "socket": "~/.local/run/signal-cli/socket",
           "account": "+1YOURNUMBER"}
```

Set `bot.allowed_senders` to your sender UUIDs — otherwise anyone who gets your
number can talk to your bot.

## Telegram (Bot API, long polling)

1. Talk to @BotFather → `/newbot` → save the token
2. Config: `"telegram": {"enabled": true, "token_file": "~/.config/housebot/telegram_token"}`
3. `bot.allowed_senders` takes Telegram numeric user IDs

## HTTP API (OpenAI-compatible)

Always-on JSON brain for Home Assistant (`openai_conversation`), scripts, or custom
UIs: `POST http://host:8082/v1/chat/completions` with `Authorization: Bearer <token>`
and an OpenAI-style messages array. History: send the recent exchanges as messages —
the engine treats the last four as context (that's what powers "what is her email"
follow-ups).

## Attachments

Transports resolve attachments to a local file path and hand them to the engine.
Today the brain accepts them (and skill modules like the Paperless OCR flow in the
production House bot consume them); more attachment-driven skills are on the roadmap
(see CAPABILITIES.md).
