"""Minimal CalDAV client: GET/PUT/DELETE collections on Radicale/Nextcloud.

Enough for VEVENT + VTODO create/list/complete — no heavy dependencies.
"""

import re
import uuid
import urllib.request
import base64
from datetime import datetime, timezone


class CalDAV:
    def __init__(self, cfg):
        cal = cfg["caldav"]
        self.base = cal["base_url"].rstrip("/")
        self.user = cal["user"]
        self.tz = cal.get("tz", "America/New_York")
        self.calendar = cal.get("calendar", "calendar")
        self.tasks = cal.get("tasks", "tasks")
        token = base64.b64encode(
            f"{self.user}:{cfg.caldav_password()}".encode()).decode()
        self.headers = {"Authorization": "Basic " + token}

    def _url(self, collection):
        return f"{self.base}/{self.user}/{collection}/"

    def get(self, collection):
        req = urllib.request.Request(self._url(collection), headers=self.headers)
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.read().decode()

    def put(self, collection, filename, body):
        req = urllib.request.Request(
            self._url(collection) + filename, data=body.encode(),
            headers={**self.headers, "Content-Type": "text/calendar"})
        req.get_method = lambda: "PUT"
        with urllib.request.urlopen(req, timeout=30) as r:
            return 200 <= r.status < 300

    def delete(self, collection, filename):
        req = urllib.request.Request(
            self._url(collection) + filename, headers=self.headers)
        req.get_method = lambda: "DELETE"
        with urllib.request.urlopen(req, timeout=30) as r:
            return 200 <= r.status < 300

    # -- structured helpers -------------------------------------------------

    def parse_items(self, collection, comp):
        """[(summary, when_dt, status, uid)] for VEVENT/VTODO in a collection."""
        raw = self.get(collection).replace("\r\n ", "")
        comp_u = comp.upper()
        items = []
        for block in re.findall(r"BEGIN:V" + comp_u + r"\b.*?END:V" + comp_u + r"\b",
                                raw, re.S):
            summary = (re.search(r"SUMMARY:?(.*)", block) or [None, ""])[1].strip()
            status = (re.search(r"STATUS:?(.*)", block) or [None, ""])[1].strip()
            uid = (re.search(r"UID:?(.*)", block) or [None, ""])[1].strip()
            when = None
            m = re.search(r"(?:DTSTART|DUE)[^:]*:(\d{8}T\d{6})", block)
            if m:
                try:
                    when = datetime.strptime(m.group(1), "%Y%m%dT%H%M%S")
                except ValueError:
                    when = None
            items.append((summary, when, status, uid))
        return items

    def put_vtodo(self, summary, due=None, categories=None, note=None):
        uid = f"task-{uuid.uuid4().hex[:12]}@housebot"
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//housebot//EN",
                 "BEGIN:VTODO", f"UID:{uid}", f"DTSTAMP:{stamp}",
                 f"SUMMARY:{_esc(summary)}"]
        if due:
            lines.append(f"DUE;TZID={self.tz}:{due.strftime('%Y%m%dT%H%M%S')}")
        if categories:
            lines.append(f"CATEGORIES:{_esc(categories)}")
        if note:
            lines.append(f"DESCRIPTION:{_esc(note)}")
        lines += ["END:VTODO", "END:VCALENDAR"]
        ok = self.put(self.tasks, uid + ".ics", "\n".join(lines) + "\n")
        return (uid, ok)

    def put_vevent(self, summary, start, end):
        uid = f"event-{uuid.uuid4().hex[:12]}@housebot"
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        body = (f"BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//housebot//EN\n"
                f"BEGIN:VEVENT\nUID:{uid}\nDTSTAMP:{stamp}\n"
                f"DTSTART;TZID={self.tz}:{start.strftime('%Y%m%dT%H%M%S')}\n"
                f"DTEND;TZID={self.tz}:{end.strftime('%Y%m%dT%H%M%S')}\n"
                f"SUMMARY:{_esc(summary)}\nEND:VEVENT\nEND:VCALENDAR\n")
        ok = self.put(self.calendar, uid + ".ics", body)
        return (uid, ok)

    def complete_vtodo(self, uid):
        """Mark a VTODO completed by rewriting its ics with STATUS:COMPLETED."""
        raw = self.get(self.tasks)
        block = None
        for b in re.findall(r"BEGIN:VTODO\b.*?END:VTODO\b", raw.replace("\r\n ", ""), re.S):
            if f"UID:{uid}" in b:
                block = b
                break
        if not block:
            return False
        if "STATUS:" in block:
            new_block = re.sub(r"STATUS:.*", "STATUS:COMPLETED", block)
        else:
            new_block = block.replace("END:VTODO", "STATUS:COMPLETED\nEND:VTODO")
        body = (
            "BEGIN:VCALENDAR\n"
            "VERSION:2.0\n"
            "PRODID:-//housebot//EN\n"
            f"{new_block}\n"
            "END:VCALENDAR\n"
        )
        return self.put(self.tasks, uid + ".ics", body)


def _esc(s):
    return (s or "").replace("\\", "\\\\").replace(";", "\\;") \
                     .replace(",", "\\,").replace("\n", "\\n")