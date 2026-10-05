"""Contacts skill — wraps the house-contacts lookup module.

Enable by setting skills.contacts_json in config to your contacts.json path
(built by https://github.com/.../house-contacts). Uses the same tiered matcher:
exact -> nickname -> swapped -> unique last/first -> substring.
"""

import os


class Contacts:
    def __init__(self, cfg):
        self.path = cfg["skills"].get("contacts_json") or ""
        self._lookup = None
        if self.path and os.path.exists(self.path):
            try:
                import contact_lookup as _m   # from the house-contacts repo
                self._lookup = _m
            except ImportError:
                self._lookup = None

    @property
    def available(self):
        return self._lookup is not None

    def enrich(self, text):
        """Append a contact block to call/text/email phrasing. (text, contact, ambiguous)"""
        if not self.available:
            return text, None, None
        return self._lookup.attach_contact_info(text)

    def info(self, text, hist=None):
        """Answer 'what is dana's phone number' / 'what is her email' follow-ups."""
        if not self.available:
            return "Contacts skill is not configured."
        ask = self._lookup
        name = _name_from_question(text) or ask.extract_call_target(text)
        if name:
            contact, _k, amb = ask.find_contact(name)
            if amb:
                return "Which one? " + " · ".join(c["name"] for c in amb)
            if contact is None:
                return f"I couldn't find {name!r} in your contacts."
        else:
            contact = _from_history(hist, ask)
            if contact is None:
                return "Who do you mean? Try the name, like 'what is dana reyes's phone number'."
        asked = _asked_field(text)
        label = contact["name"]
        if asked == "email":
            emails = contact.get("emails") or []
            return f"{label}'s email: " + (", ".join(emails) if emails else "none on file")
        if asked == "phone":
            parts = [f"{p.get('type', 'phone')}: {p['number']}"
                     for p in contact.get("phones") or []]
            return f"{label}'s number: " + (" · ".join(parts) if parts else "none on file")
        return "📇 " + (ask.format_contact(contact) or label)


# -- name extraction for contact questions ----------------------------------

import re

_QUESTION_WORDS = {"what", "whats", "who", "whos", "which", "is", "are", "the", "a",
                   "an", "tell", "give", "show", "me", "do", "does", "you", "know",
                   "have", "find", "get", "her", "his", "their", "them", "him",
                   "she", "he", "they", "it", "that", "this", "there", "my", "our"}
_FIELD_TAILS = {"number", "email", "phone", "address", "cell", "mobile", "info", "details"}


def _clean_name(s):
    toks = (s or "").split()
    while toks and toks[0].lower().strip(".,?'’") in _QUESTION_WORDS:
        toks.pop(0)
    while toks and toks[-1].lower().strip(".,?") in _FIELD_TAILS:
        toks.pop()
    if toks:
        toks[-1] = re.sub(r"['’]s?$", "", toks[-1])
        if not toks[-1]:
            toks.pop()
    return " ".join(toks).strip() or None


def _name_from_question(text):
    t = text or ""
    m = re.search(r"([A-Za-z][\w'’.-]+(?:\s+[A-Za-z][\w'’.-]+){0,3})'?(?:s)?\s+"
                  r"(?:phone|mobile|cell|number|e-?mail|address|info|details)", t, re.I)
    if m:
        return _clean_name(m.group(1))
    m = re.search(r"\b(?:phone|mobile|cell|number|e-?mail|address|info|details)\s+"
                  r"(?:number\s+|address\s+)?(?:for|of)\s+((?:[A-Za-z][\w'’.-]+\s*){1,4})",
                  t, re.I)
    if m:
        return _clean_name(m.group(1))
    m = re.search(r"\bwho(?:'s|\s+is|\s+are)\s+((?:[A-Za-z][\w'’.-]+\s*){1,4})", t, re.I)
    if m:
        return _clean_name(m.group(1))
    return None


def _asked_field(text):
    t = (text or "").lower()
    if re.search(r"\be-?mail\b", t):
        return "email"
    if re.search(r"\b(phone|mobile|cell|number)\b", t):
        return "phone"
    if re.search(r"\baddress\b", t):
        return "address"
    return "all"


def _from_history(hist, ask):
    for msg in reversed(hist or []):
        if not isinstance(msg, dict) or msg.get("role") != "user":
            continue
        content = msg.get("content") or ""
        name = _name_from_question(content) or ask.extract_call_target(content)
        if not name:
            continue
        c, _k, _a = ask.find_contact(name)
        if c:
            return c
    return None