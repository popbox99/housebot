# Apple iPhone/iPad — calendars, tasks & contacts over CalDAV

iOS speaks CalDAV and CardDAV natively, with one big exception: **Apple Reminders does
not read CalDAV VTODOs** (tasks). Here's what works and what doesn't.

## Calendar events — native, two-way ✓

1. **Settings → Apps → Calendar → Calendar Accounts → Add Account → Other → Add CalDAV Account**
2. Server / User Name / Password → Next
   (for Radicale: `https://your-server/user/`; for Nextcloud: the server root)
3. Enable the calendars. Events appear in Apple Calendar, sync both ways, and can be
   edited or created from the phone.

## Tasks (VTODO) — the limitation ✗ / workarounds

Apple's Reminders app uses Apple's own protocol and **ignores CalDAV VTODOs entirely**.
Options:

1. **Third-party App Store clients** — several iOS apps speak CalDAV VTODOs
   (search the App Store for *"CalDAV tasks"*; quality varies, so try before committing).
2. **Read-only event view** — subscribe to the calendar instead:
   *Settings → Calendar → Calendar Accounts → Add Subscribed Calendar* → paste the
   `.ics` URL. Good for seeing what's scheduled; read-only, and still no VTODOs.
3. **Web UI** — Radicale/Nextcloud web interfaces work fine in Safari if you just
   need to check a list occasionally.

## Contacts — native, two-way ✓ (CardDAV)

**Settings → Apps → Contacts → Contacts Accounts → Add Account → Other → Add CardDAV
Account** — same server/user/password pattern. Contacts sync into the native Contacts
app, are searchable from Spotlight, and dial from anywhere.

> If your contacts live in Google (like a Google-synced Thunderbird setup), the iPhone
> already syncs those via the Google account option — you don't need CardDAV for that.

## Summary

| Data | iPhone support |
|---|---|
| Calendar events (VEVENT) | ✓ native, two-way |
| Tasks (VTODO) | ✗ Apple Reminders — needs a third-party CalDAV-tasks app |
| Contacts (CardDAV) | ✓ native, two-way |
| Read-only .ics | ✓ via Subscribed Calendar |

- **Android?** See [CALDAV-ANDROID.md](CALDAV-ANDROID.md) — DAVx⁵ + Tasks.org covers
  everything including VTODOs, two-way.
