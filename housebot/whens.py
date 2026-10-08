"""Natural-language time parsing.

The fast regex parser handles the common cases deterministically (<1ms):
  "in 45 minutes to flip laundry", "tomorrow at 7pm", "next monday at 9",
  "friday evening", "tomorrow", "at noon" — with word order INDEPENDENCE:
  "remind me to call Dana tomorrow at 5" and "remind me at 5 tomorrow to call
  Dana" both yield what="call Dana".

Complex phrases fall through (caller may use an LLM extractor).
"""

import re
from datetime import datetime, timedelta

WD = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
      "friday": 4, "saturday": 5, "sunday": 6}


def _day_offset_for(dayw, qual, now):
    """(calendar-day offset, bump-if-past) for a day word."""
    if dayw == "tomorrow":
        return 1, 1
    if dayw in ("today", "tonight"):
        return 0, 1
    if dayw not in WD:
        return 0, 0   # no day word present (guards "at 3pm tomorrow" phrasings)
    off = (WD[dayw] - now.weekday()) % 7
    if off == 0:
        if (qual or "").lower() == "next":
            return 7, 7
        return 0, 7
    return off, 0


def _clean_what(text, m, tail=""):
    """Rebuild the task text from the words AROUND the matched time expression,
    dropping the time words and 'remind me to' scaffolding."""
    s = (text[:m.start()].strip() + " " + (tail or "").strip()).strip()
    s = re.sub(r"^(?:remind(?:\s+me)?(?:\s+to)?\s+|to\s+|please\s+|check\s+on\s+"
               r"|set\s+(?:a\s+)?(?:reminder|task)(?:\s+(?:to|for))?\s+)", "", s, flags=re.I)
    s = re.sub(r"^(?:me|to)\b\s*", "", s, flags=re.I)
    s = re.sub(r"^(?:(?:next|this)\s+)?(?:tomorrow|today|tonight|monday|tuesday|"
               r"wednesday|thursday|friday|saturday|sunday)\b[,\s]*"
               r"(?:(?:to|for|and|:)\s+)?", "", s, flags=re.I)
    s = re.sub(r"\s{2,}", " ", s).strip(" ,.:;-")
    return s


def fast_parse_when(text, now=None):
    """Returns (when_dt, what_text) or None if the phrase needs an LLM."""
    now = now or datetime.now()
    t = text.strip()

    # 1. "in X minutes/hours/days (to/for) <what>"
    m = re.search(r"\b(?:in\s+)?(\d+|a|an|half an|one|two|three|four|five|six|seven|"
                  r"eight|nine|ten|fifteen|twenty|thirty|forty|fifty)\s*"
                  r"(min(?:ute)?s?|hours?|hrs?|days?|secs?|seconds?)\b"
                  r"\s*(?:to\s+|for\s+|and\s+|:\s*)?(.*)", t, re.I)
    if m:
        num_map = {"a": 1, "an": 1, "half an": 0.5, "one": 1, "two": 2, "three": 3,
                   "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
                   "ten": 10, "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40,
                   "fifty": 50}
        qty = num_map.get(m.group(1).lower())
        if qty is None:
            try:
                qty = float(m.group(1))
            except ValueError:
                qty = None
        unit = m.group(2).lower()
        delta = None
        if qty:
            if "min" in unit:
                delta = timedelta(minutes=qty)
            elif "hour" in unit or "hr" in unit:
                delta = timedelta(hours=qty)
            elif "day" in unit:
                delta = timedelta(days=qty)
            elif "sec" in unit:
                delta = timedelta(seconds=qty)
        if delta:
            when = now + delta
            what = _clean_what(t, m, m.group(3))
            return when, what or "reminder"

    # 1b. daypart words: "tomorrow at noon", "monday morning"
    m = re.search(r"\b(?:(next|this)\s+)?((?:tomorrow|today|tonight|monday|tuesday|"
                  r"wednesday|thursday|friday|saturday|sunday)\s+)?(?:at\s+)?"
                  r"(noon|midnight|morning|afternoon|evening|night)\b\s*"
                  r"(?:to\s+|for\s+|and\s+|:\s*)?(.*)", t, re.I)
    if m:
        dayw = (m.group(2) or "").lower()
        qual = m.group(1)
        tail = m.group(4) or ""
        if not dayw:
            # day word may ride AFTER the daypart: "at noon tomorrow"
            md = re.search(r"\b(next\s+|this\s+)?(tomorrow|today|tonight|monday|tuesday|"
                           r"wednesday|thursday|friday|saturday|sunday)\b", tail, re.I)
            if md:
                dayw = md.group(2).lower()
                qual = (md.group(1) or "").strip()
        hour = {"noon": 12, "midnight": 0, "morning": 9, "afternoon": 15,
                "evening": 19, "night": 21}[m.group(3).lower()]
        off, bump = _day_offset_for(dayw.strip(), qual, now)
        target = now.replace(hour=hour, minute=0, second=0, microsecond=0) + timedelta(days=off)
        if target <= now:
            target += timedelta(days=bump)
        what = _clean_what(t, m, m.group(4))
        return target, what or "reminder"

    # 2. day word + clock time: "tomorrow at 7pm", "friday at 5pm"
    m = re.search(r"\b(?:(next|this)\s+)?((?:tomorrow|today|tonight|monday|tuesday|"
                  r"wednesday|thursday|friday|saturday|sunday)\s+)?at\s+"
                  r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b\s*(?:to\s+|for\s+|and\s+|:\s*)?(.*)",
                  t, re.I)
    if m:
        dayw = (m.group(2) or "").lower()
        qual = m.group(1)
        tail = m.group(6) or ""
        if not dayw:
            # day word may ride AFTER the time: "at 3pm tomorrow to call Dana"
            md = re.search(r"\b(next\s+|this\s+)?(tomorrow|today|tonight|monday|tuesday|"
                           r"wednesday|thursday|friday|saturday|sunday)\b", tail, re.I)
            if md:
                dayw = md.group(2).lower()
                qual = (md.group(1) or "").strip()
        hour = int(m.group(3))
        minute = int(m.group(4) or 0)
        meridiem = (m.group(5) or "").lower() or None
        if meridiem == "pm" and hour < 12:
            hour += 12
        elif meridiem == "am" and hour == 12:
            hour = 0
        elif meridiem is None and hour <= 7 and not dayw and now.hour >= 12:
            hour += 12
        off, bump = _day_offset_for(dayw.strip(), qual, now)
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0) + timedelta(days=off)
        if target <= now:
            target += timedelta(days=bump)
        what = _clean_what(t, m, m.group(6))
        return target, what or "reminder"

    # 2b. bare day word: "tomorrow" -> 09:00 next day
    m = re.search(r"\b(?:(next|this)\s+)?(tomorrow|tonight|monday|tuesday|wednesday|"
                  r"thursday|friday|saturday|sunday)\b", t, re.I)
    if m:
        dayw = m.group(2).lower()
        off, bump = _day_offset_for(dayw, m.group(1), now)
        what = _clean_what(t, m, "")
        hour = 20 if dayw == "tonight" else 9
        target = now.replace(hour=hour, minute=0, second=0, microsecond=0) + timedelta(days=off)
        if target <= now:
            target += timedelta(days=bump)
        return target, what or "reminder"
    return None


def llm_extract_when(ask_llm_fn, text, now=None):
    """LLM fallback: returns (when_dt, what) or (None, None)."""
    now = now or datetime.now()
    prompt = (f"Now is {now.strftime('%Y-%m-%d %H:%M %A')}. Extract WHEN the reminder "
              f'should fire and WHAT to remind about. Reply with ONLY JSON: '
              f'{{"when": "YYYY-MM-DD HH:MM", "what": "..."}}.\nMessage: ' + text)
    res = ask_llm_fn(prompt)
    if isinstance(res, tuple):
        raw = res[0]
    else:
        raw = res
    if not raw:
        return None, None
    try:
        d = json_loads_loose(raw)
        when = datetime.strptime(d["when"].strip(), "%Y-%m-%d %H:%M")
        # deterministic day-word overrides beat LLM date arithmetic
        tl = text.lower()
        if "tomorrow" in tl:
            want = datetime.now() + timedelta(days=1)
            when = when.replace(year=want.year, month=want.month, day=want.day)
        elif re.search(r"\b(today|tonight)\b", tl):
            n2 = datetime.now()
            when = when.replace(year=n2.year, month=n2.month, day=n2.day)
        mwd = re.search(r"\b(?:(next|this)\s+)?(monday|tuesday|wednesday|thursday|"
                        r"friday|saturday|sunday)\b", tl)
        if mwd:
            off, _b = _day_offset_for(mwd.group(2).lower(), mwd.group(1), datetime.now())
            base = datetime.now() + timedelta(days=off)
            when = when.replace(year=base.year, month=base.month, day=base.day)
        return when, d.get("what", text)[:100]
    except Exception:
        return None, None


def json_loads_loose(raw):
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        raise ValueError("no json")
    return __import__("json").loads(m.group(0))