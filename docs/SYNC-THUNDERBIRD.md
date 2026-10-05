# Syncing with Thunderbird — two-way CalDAV

Thunderbird speaks CalDAV natively (calendar *and* tasks), so it becomes a full client
for whatever HouseBot writes.

## Connect your Radicale/CalDAV account

1. **File → New → Calendar** (or Calendar tab → right-click → New Calendar)
2. Choose **On the Network** → **CalDAV**
3. Location: `http://your-server:5232/me/calendar/` (or just the user URL and let
   Thunderbird discover collections)
4. Sign in with your CalDAV username/password, pick offline caching if you like
5. Repeat with `http://your-server:5232/me/tasks/` — tick **"Show reminders"** /
   **Email reminders** if desired; Thunderbird treats the VTODO collection as a task list

## What you get

- Events HouseBot creates → appear in Thunderbird's calendar
- Tasks HouseBot creates → appear in Thunderbird's **Tasks** tab, completable there
  (completion syncs back to the server and to your phone)
- Edit/add from Thunderbird → visible to HouseBot's `/agenda` and task views

## Email accounts

- **Gmail**: Thunderbird's Gmail wizard (OAuth) — contacts also sync via Google's
  CardDAV (that's how Thunderbird-cached address books feed
  [house-contacts](https://github.com/popbox99/house-contacts))
- **Proton**: install [Proton Mail Bridge](https://proton.me/mail/bridge), it exposes
  IMAP/SMTP locally; add the account in Thunderbird with the Bridge credentials.
  Email only — see [SYNC-PROTON.md](SYNC-PROTON.md) for the calendar limitation.
