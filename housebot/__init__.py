"""housebot — a privacy-first, messenger-agnostic home assistant bot.

Core ideas (learned from running "House" in production):
  * One brain, many transports: Signal, Telegram, and an OpenAI-compatible HTTP
    API all feed the same intent engine.
  * Calendar, tasks and reminders live in CalDAV (Radicale/Nextcloud) so they
    sync to every device you own.
  * LLM backends are a fallback CHAIN across one or more machines.
  * Everything is config-driven; skills that need extra infrastructure
    (Home Assistant, Paperless, Immich) are optional and safely disable
    themselves when not configured.
"""

__version__ = "0.1.0"