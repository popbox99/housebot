# CalDAV server setup — Radicale

HouseBot stores events and tasks on a CalDAV server, which is what makes them appear
on every device you own. **Radicale** is the lightest good option (a single Python
package); Nextcloud works identically.

## Install Radicale (Linux server)

```bash
# Arch:
sudo pacman -S radicale python-passlib
# Debian/Ubuntu:
sudo apt install radicale python3-passlib
# Or via pip in a venv, or docker: https://radicale.org/v3.html#run
```

Minimal `/etc/radicale/config` (or `~/.config/radicale/config`):

```ini
[server]
hosts = 127.0.0.1:5232        # bind your LAN/tailnet IP too if phones sync directly

[auth]
type = htpasswd
htpasswd_filename = /etc/radicale/users
htpasswd_encryption = bcrypt

[storage]
filesystem_folder = /var/lib/radicale/collections
```

Create a user:

```bash
htpasswd -B -c /etc/radicale/users me
sudo systemctl enable --now radicale
```

## Create the collections HouseBot uses

With Radicale running, create `calendar` (VEVENT) and `tasks` (VTODO) collections for
your user — easiest via the Radicale web UI at `http://host:5232` (log in, create two
collections, one "Calendar", one "Task list"), name them exactly `calendar` and `tasks`
(or match `caldav.calendar` / `caldav.tasks` in HouseBot's config).

## Point HouseBot at it

```json
"caldav": {
  "base_url": "http://127.0.0.1:5232",
  "user": "me",
  "password_file": "~/.config/housebot/caldav_password",
  "calendar": "calendar",
  "tasks": "tasks"
}
```

Then sync your devices:
- **Thunderbird** → [SYNC-THUNDERBIRD.md](SYNC-THUNDERBIRD.md)
- **Android** → [CALDAV-ANDROID.md](CALDAV-ANDROID.md)
- **iPhone** → [APPLE-IPHONE.md](APPLE-IPHONE.md)
- **Proton / Gmail** → [SYNC-PROTON.md](SYNC-PROTON.md) / [SYNC-GMAIL.md](SYNC-GMAIL.md)
