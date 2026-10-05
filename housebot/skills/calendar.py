"""Calendar & tasks skill: EVENT / TASK / AGENDA via CalDAV."""

from datetime import datetime, timedelta

from .. import whens


class Calendar:
    def __init__(self, cfg, ask_llm_fn):
        from ..caldav import CalDAV
        self.dav = CalDAV(cfg)
        self.ask_llm_fn = ask_llm_fn
        self.tz = cfg["caldav"].get("tz", "America/New_York")

    def add_item(self, kind, text, history=None):
        """kind: EVENT or TASK. Returns a confirmation string."""
        fast = whens.fast_parse_when(text)
        if fast:
            start, summary = fast
            date = start.strftime("%Y-%m-%d")
            hm = start.strftime("%H:%M")
        else:
            summary, date, hm = None, "", ""
            when, w = whens.llm_extract_when(self.ask_llm_fn, text)
            if when:
                date, hm, summary = when.strftime("%Y-%m-%d"), when.strftime("%H:%M"), w
            else:
                summary = text[:80]
                date = ""
        try:
            start = datetime.fromisoformat(f"{date}T{hm or '09:00'}")
        except Exception:
            start = datetime.now() + timedelta(days=7)
            summary = summary or text[:60]
        if kind == "EVENT":
            uid, ok = self.dav.put_vevent(summary, start, start + timedelta(hours=1))
            label = "Event"
            nice = start.strftime("%a %b %d %H:%M")
        else:
            uid, ok = self.dav.put_vtodo(summary, due=start)
            label = "Task"
            nice = start.strftime("%a %b %d (due %H:%M)")
        if ok:
            return f"{label} added: {summary} — {nice} (syncs to your devices)"
        return "Failed to write to the CalDAV server."

    def agenda(self, when="today"):
        import datetime as dt
        today = dt.date.today()
        end = today + (dt.timedelta(days=7) if "week" in (when or "") else dt.timedelta(days=1))
        try:
            events = [i for i in self.dav.parse_items(self.dav.calendar, "EVENT")
                      if i[1] and today <= i[1].date() < end]
            tasks = [i for i in self.dav.parse_items(self.dav.tasks, "VTODO")
                     if i[2] != "COMPLETED" and (not i[1] or i[1].date() < end)]
        except Exception as e:
            return f"CalDAV unreachable: {e}"
        lines = []
        today_events = [e for e in events if e[1].date() == today]
        if today_events:
            lines.append("Today:")
            lines += [f"  {e[1].strftime('%H:%M')} {e[0]}" for e in sorted(today_events, key=lambda x: x[1])]
        later = [e for e in events if e[1].date() != today]
        if later:
            lines.append("Upcoming:")
            lines += [f"  {e[1].strftime('%a %b %d %H:%M')} {e[0]}" for e in sorted(later, key=lambda x: x[1])]
        if tasks:
            lines.append("Open tasks:")
            lines += [f"  {t[0]}" + (f" (due {t[1].strftime('%a %b %d')})" if t[1] else "")
                      for t in tasks[:10]]
        return "\n".join(lines) if lines else "Nothing on the agenda."