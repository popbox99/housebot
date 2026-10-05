# Syncing with Proton Mail / Proton Calendar — the honest version

## Email: works, via Proton Mail Bridge

[Proton Mail Bridge](https://proton.me/mail/bridge) runs on your desktop and exposes a
local IMAP/SMTP server. Add the account to Thunderbird/any client with the Bridge
credentials — full two-way email. HouseBot doesn't touch email directly today, but any
mail automation you build can talk to the local Bridge port.

## Calendar: **no CalDAV — this is a Proton limitation, not yours**

Proton Calendar uses Proton's own encrypted sync protocol and does **not** offer CalDAV
or any third-party sync. That means:

- You **cannot** see Proton Calendar events in Radicale/Thunderbird/DAVx⁵/HouseBot
- There is no official workaround; third-party export tools exist but are clunky
  and one-way

**Practical layouts people use:**

1. **Keep Proton for email, use a CalDAV calendar for scheduling.** Your "real"
   calendar (the one HouseBot, your phone, and Thunderbird all share) lives on
   Radicale/Nextcloud. Proton Calendar stays as an archive.
2. **Manual migration** — export Proton Calendar events periodically (Settings →
   Export) and import the `.ics` into your CalDAV calendar. One-way, on-demand.
3. If CalDAV support ever matters to your choice of provider: Nextcloud, Fastmail,
   iCloud, and mailbox.org all speak native CalDAV (and CardDAV) today.

## Contacts

Proton Contacts also has no CardDAV. If your phone contacts live in Google or a
CardDAV server, that's independent of Proton — house-contacts reads whatever
Thunderbird caches or whatever `.vcf` you export.
