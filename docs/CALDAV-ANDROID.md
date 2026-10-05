# Android: CalDAV tasks & calendars without Google

The recommended stack for de-Googled Android phones (works on any Android):

| App | Role | Install |
|---|---|---|
| **DAVx⁵** | the sync engine — talks CalDAV/CardDAV to your server | Play Store or F-Droid |
| **Fossify Calendar** | events UI (reads any Android calendar provider) | F-Droid / Play |
| **Tasks.org** | VTODO task UI, syncs through DAVx⁵ | F-Droid / Play |
| **ICSx⁵** *(optional)* | read-only .ics subscriptions | F-Droid / Play |

## 1. Connect DAVx⁵ to your CalDAV server

1. Open DAVx⁵ → **+** → *Enter URL or search*
2. Enter your server details:
   - Radicale: `http://your-server:5232/` + username + password
   - Nextcloud: just the server URL; it auto-discovers
3. Grant DAVx⁵ **calendar** and **contacts** permissions when prompted
4. Tap the account → tick the collections to sync (your `calendar` and `tasks`
   collections; address books if your server has CardDAV)
5. Sync now → green dots everywhere

## 2. Events — Fossify Calendar

Fossify Calendar reads whatever the Android calendar provider holds — and DAVx⁵
writes your CalDAV calendars into that provider.

- Open Fossify Calendar → Settings → **Calendars / Visible calendars** → enable the
  CalDAV calendars (they appear with their collection colors)
- Create/edit events normally; they sync **two-way** via DAVx⁵
- Widgets and notifications work like any local calendar

## 3. Tasks — Tasks.org

Tasks.org is a CalDAV VTODO client that syncs through DAVx⁵:

1. Open Tasks.org → on first run it offers to link with DAVx⁵ (or:
   Settings → **Sync** → enable the DAVx⁵ account)
2. Your server's task lists appear as local task lists
3. Create/complete/reorder — completion syncs **back** to the server, so checking
   a task off on your phone marks it done in Thunderbird and for the bot's agenda

## 4. Getting away from Google Calendar

What you lose and what replaces it:

| Google did... | Now handled by |
|---|---|
| Background calendar sync | DAVx⁵ (works without Play Services) |
| Calendar UI | Fossify Calendar |
| Tasks/Reminders | Tasks.org |
| Contacts | DAVx⁵ CardDAV → native contacts app (or keep the Google account just for contacts if you like) |

Steps:

1. Get DAVx⁵ + Fossify + Tasks.org syncing (steps 1–3) and confirm your events/tasks
   are visible **and editable** from the CalDAV side first
2. In the Google Calendar app/settings, uncheck the Google account's calendars from
   Fossify's visible-calendars list (or remove the Google account entirely if you
   don't need Gmail/Play on the phone)
3. Set DAVx⁵ to sync on account-change + periodically (Settings → sync interval;
   disable battery optimization for reliable background sync)
4. If any recurring events lived only in Google Calendar: export them
   (calendar.google.com → Settings → Export) and import the `.ics` into your CalDAV
   calendar via Thunderbird before disconnecting

## Notes & gotchas

- **Self-signed certificates**: DAVx⁵ can trust your own CA, or route through
  Tailscale/Wireguard so the server presents a real certificate
- **Battery**: exclude DAVx⁵ from battery optimization or syncs get delayed
- **Read-only subscriptions** (holidays, sports schedules): ICSx⁵
- **iOS?** Different story — see [APPLE-IPHONE.md](APPLE-IPHONE.md)
