# Syncing with Google (Gmail / Google Calendar / Contacts)

## Gmail email

Thunderbird's Gmail wizard (OAuth) just works. HouseBot doesn't do email natively;
any mail tooling can use Thunderbird or app passwords/OAuth separately.

## Google Calendar — the honest version

Google's CalDAV endpoint is **effectively deprecated for third-party sync**: since
2015 it's limited to read-only access for most clients, and Google steers everyone to
the proprietary Calendar API. Your realistic options:

| Approach | Direction | Effort |
|---|---|---|
| **Subscribed calendar** (`https://calendar.google.com/calendar/ical/.../basic.ics`) | Google → your CalDAV world, read-only | trivial |
| **Google Calendar API** (OAuth app) | two-way, programmatic | real dev work; OAuth setup |
| **Periodic export/import** (.ics download → import) | one-way, manual | trivial, tedious |
| **Keep scheduling in CalDAV** (Radicale), treat Google Calendar as read-mostly | — | none |

HouseBot's calendar lives on **your** CalDAV server — that's the point. If you need
Google events visible there, the subscribed-calendar route gets them read-only with
zero code.

## Google Contacts — works, via CardDAV ✓

Google still fully supports **CardDAV**:

- Thunderbird syncs Google contacts as CardDAV address books (this is exactly what
  house-contacts' Thunderbird exporter reads — it's a local, fresh cache)
- `https://www.googleapis.com/carddav/v1/principals/<account>/lists/default/` is the
  endpoint if you're wiring something by hand
- On phones: Android syncs natively via your Google account; iPhone via the
  Google account option or CardDAV

## Summary

| Data | Google → CalDAV world |
|---|---|
| Email | via Thunderbird OAuth (or IMAP app-passwords where still allowed) |
| Calendar | read-only subscription; two-way needs the Google API |
| Contacts | ✓ full CardDAV, two-way |
