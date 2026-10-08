"""The brain: one dispatch table shared by every transport. Skills fail soft."""

import json
import os
import re

from . import intents
from .skills.reminders import Reminders
from .skills.contacts import Contacts
from .skills.basic import Notes, Find
from .skills.chat import Chat


def _read_token(path):
    if path and os.path.exists(os.path.expanduser(path)):
        return open(os.path.expanduser(path)).read().strip()
    return ""


class Engine:
    """Builds available skills from config and answers messages.
    Transport-agnostic: handle(sender, text, attachment) -> (reply, attachment)."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.chat = Chat(cfg)
        self.reminders = Reminders(cfg, lambda p: (self.chat.extract(p), None))
        self.notes = Notes(cfg)
        self.find = Find(cfg)
        self.history = {}

        self.calendar = None
        if cfg["caldav"].get("base_url"):
            from .skills.calendar import Calendar
            # llm_extract_when unpacks (text, backend_name)
            self.calendar = Calendar(cfg, lambda p: (self.chat.extract(p), None))

        self.contacts = Contacts(cfg)

        from .skills.lists import Lists
        self.lists = Lists(cfg)

        self._ha = None
        if (cfg["skills"].get("homeassistant") or {}).get("enabled"):
            from .skills.homeassistant import HomeAssistant
            self._ha = HomeAssistant(cfg)

        self._paperless = None
        if (cfg["skills"].get("paperless") or {}).get("enabled"):
            from .skills.paperless import Paperless
            self._paperless = Paperless(cfg, log=print)

        self._photos = None
        if (cfg["skills"].get("immich") or {}).get("enabled"):
            from .skills.photos import Photos
            self._photos = Photos(cfg, log=print)

        from .skills.web import Web
        self.web = Web(cfg)

        self._stt = (cfg["skills"].get("wyoming_stt")
                     or {"host": "127.0.0.1", "port": 10300})

        self.dispatch = {
            "REMIND": self._remind,
            "REMINDERS": lambda arg, text, hist: self.reminders.list(),
            "TASK": lambda arg, text, hist: self._add_item("TASK", arg or text, hist),
            "EVENT": lambda arg, text, hist: self._add_item("EVENT", arg or text, hist),
            "AGENDA": lambda arg, text, hist: self._agenda(arg),
            "COMPLETE": self._complete,
            "NOTE": lambda arg, text, hist: self.notes.add(arg or text),
            "FIND": self._find,
            "SUMMARIZE": lambda arg, text, hist: self.chat.summarize(arg or text, hist),
            "CONTACT": self._contact_add,
            "CONTACT_INFO": lambda arg, text, hist: self.contacts.info(arg or text, hist),
            "LOCATION": lambda arg, text, hist: self._location_reminder(arg or text),
            "SHOPPING": lambda arg, text, hist: self._shopping(arg or text),
            "CHORE": lambda arg, text, hist: self._chore(arg or text),
            "HABIT": lambda arg, text, hist: self.lists.habit_log(arg or text),
            "REMEMBER": lambda arg, text, hist: self._remember(arg or text),
            "FORGET": lambda arg, text, hist: self.lists.forget(arg or text),
            "MEMORY": lambda arg, text, hist: self._recall(arg or text, hist),
            "HID": lambda arg, text, hist: self.lists.hide(*(arg.split(" in ", 1) + [""])[:2]),
            "PHOTOS": lambda arg, text, hist: self._photos_reply(arg),
            "PAPERLESS": lambda arg, text, hist: self._paperless_answer(arg),
            "WEATHER": lambda arg, text, hist: self.web.weather(arg),
            "SEARCH_WEB": lambda arg, text, hist: self.web.search_reply(arg),
            "READ_URL": self._read_url,
            "BATTERIES": lambda arg, text, hist: self._batteries(),
            "VACUUM": self._vacuum,
            "DEVICE": self._device_control,
            "HA_STATE": self._ha_state,
        }

    # -- availability shims ---------------------------------------------------

    def _unconfigured(self, what, setting):
        return f"{what} is not configured (skills.{setting} in config)."

    # -- individual handlers ----------------------------------------------------

    def _remind(self, arg, text, hist):
        try:
            return self.reminders.add(arg or text)
        except ValueError as e:
            return str(e)

    def _add_item(self, kind, text, hist):
        if not self.calendar:
            return self._unconfigured("Calendar", "caldav.base_url")
        return self.calendar.add_item(kind, text, history=hist)

    def _agenda(self, arg):
        if not self.calendar:
            return self._unconfigured("Calendar", "caldav.base_url")
        return self.calendar.agenda(arg or "today")

    def _complete(self, arg, text, hist):
        if not self.calendar:
            return self._unconfigured("Calendar", "caldav.base_url")
        words = (arg or "").lower().strip()
        matches = [i for i in self.calendar.dav.parse_items(self.calendar.dav.tasks, "VTODO")
                   if words in (i[0] or "").lower() and i[2] != "COMPLETED"]
        if not matches:
            return f"No open task matching {words!r}."
        if len(matches) > 1:
            return "Which one? " + " · ".join(m[0] for m in matches[:5])
        ok = self.calendar.dav.complete_vtodo(matches[0][3])
        return f"Completed: {matches[0][0]}" if ok else "Failed to update the task."

    def _find(self, arg, text, hist):
        hits = self.find.search(arg)
        if hits is None:
            return self._unconfigured("Search", "search_dirs")
        if not hits:
            return f"No files matching {arg!r}."
        return "Found:\n" + "\n".join(f"  {h}" for h in hits)

    def _contact_add(self, arg, text, hist):
        ledger = os.path.expanduser(self.cfg["skills"].get("contact_ledger_vcf") or "")
        if not ledger:
            return self._unconfigured("Contact adding", "contact_ledger_vcf")
        raw = self.chat.extract(
            'Extract contact info. Reply ONLY JSON: {"name": "", "phones": [], '
            '"emails": [], "organization": ""}\nText: ' + (arg or text))
        try:
            fields = json.loads(re.search(r"\{.*\}", raw, re.S).group(0))
        except Exception:
            return "I couldn't parse that into a contact - include at least a name and a number."
        if not (fields.get("name") or "").strip():
            return "I couldn't pull a name out of that."
        esc = lambda s: (s or "").replace("\\", "\\\\").replace(";", "\\;") \
                                           .replace(",", "\\,").replace("\n", "\\n")
        bits = fields["name"].split()
        last = bits[-1] if len(bits) > 1 else fields["name"]
        first = " ".join(bits[:-1]) if len(bits) > 1 else ""
        lines = ["BEGIN:VCARD", "VERSION:3.0", f"N:{esc(last)};{esc(first)};;;",
                 f"FN:{esc(fields['name'])}"]
        if fields.get("organization"):
            lines.append(f"ORG:{esc(fields['organization'])}")
        for p in fields.get("phones") or []:
            lines.append(f"TEL;TYPE=CELL:{p}")
        for e in fields.get("emails") or []:
            lines.append(f"EMAIL:{e}")
        lines.append("END:VCARD")
        with open(ledger, "a") as f:
            f.write("\r\n".join(lines) + "\r\n")
        return (f"📇 Saved {fields['name']} - merges into contacts on the next "
                "contacts build.")

    def _location_reminder(self, text):
        if not self._ha:
            return self._unconfigured("Location reminders", "homeassistant")
        return self._ha.add_location_reminder(text)

    def _shopping(self, text):
        t = text.strip()
        if re.search(r"\b(list|show|whats|what.s|check)\b", t, re.I) and \
                not re.search(r"\b(add|buy|remove|pick)\b", t, re.I):
            return self.lists.shopping_show()
        m = re.search(r"^(?:remove|take(?: off)?)\s+(.+?)(?:\s+from\s+(?:my |the )?"
                      r"shopping list)?$", t, re.I)
        if m:
            return self.lists.shopping_remove(m.group(1).strip())
        t2 = re.sub(r"^(?:add\s+|buy\s+)", "", t, flags=re.I)
        t2 = re.sub(r"\s+to\s+(?:my |the )?shopping list\s*$", "", t2, flags=re.I)
        t2 = re.sub(r"^(?:some\s+)?", "", t2, flags=re.I)
        items = [i.strip() for i in re.split(r",|\band\b", t2, flags=re.I) if i.strip()]
        if not items:
            return "What should I add to the shopping list?"
        return self.lists.shopping_add(items)

    def _chore(self, text):
        t = text.strip()
        if re.search(r"^(?:chores?|status|chores status)\b", t, re.I) or t.lower() in ("chores", "chore status"):
            return self.lists.chore_status()
        task = re.sub(r"^(?:mark that |log that |i |chores?:?\s*)"
                      r"(?:changed|cleaned|did|replaced)?\s*", "", t, flags=re.I).strip()
        return self.lists.chore_log(task or t)

    def _remember(self, text):
        m = re.search(r"^(?:that\s+)?(.+?)\s+(?:is|are|in|at|on)\s+(.+)$", text, re.I)
        if m:
            return self.lists.remember(m.group(1).strip(), m.group(2).strip())
        return self.lists.remember(text[:40], text)

    def _recall(self, text, hist):
        answer = self.lists.recall(text)
        if answer:
            return answer
        return self.chat.reply(f"Question about: {text}", history=hist)

    def _photos_reply(self, arg):
        if not self._photos:
            return self._unconfigured("Photos", "immich")
        return self._photos.reply(arg)

    def _paperless_answer(self, arg):
        if not self._paperless:
            return self._unconfigured("Paperless", "paperless")
        return self._paperless.answer(arg, lambda p: self.chat.summarize(p))

    def _batteries(self):
        if not self._ha:
            return self._unconfigured("Battery report", "homeassistant")
        return self._ha.battery_report()

    def _vacuum(self, arg, text, hist):
        if not self._ha or not self._ha.enabled:
            return self._unconfigured("Vacuum control", "homeassistant")
        return self._ha.vacuum((arg or "").strip().lower() or "status")

    def _device_control(self, arg, text, hist):
        if not self._ha or not self._ha.enabled:
            return self._unconfigured("Home Assistant device control", "homeassistant")
        if ":" in (arg or ""):
            action, target = (arg or "").split(":", 1)
        else:
            action, target = "toggle", (arg or text or "")
        return self._ha.device_control(action, target)

    def _ha_state(self, arg, text, hist):
        if not self._ha or not self._ha.enabled:
            return self._unconfigured("Home Assistant state query", "homeassistant")
        return self._ha.device_state(arg or text or "")

    def _read_url(self, arg, text, hist):
        m = re.search(r"https?://\S+", arg or text or "")
        if not m:
            return "Give me a URL to read."
        return self.web.read_url(m.group(0), lambda p: self.chat.summarize(p))

    # -- attachments (voice notes, documents, business cards) --------------------

    def _handle_attachment(self, sender, text, path):
        ctype = path.rsplit(".", 1)[-1].lower() if path else ""
        if ctype in ("jpg", "jpeg", "png", "webp", "heic"):
            if self._paperless:
                wants_contact = re.search(r"\bcontacts?\b|\bbusiness card\b",
                                          (text or "").lower())
                doc_id, ocr = self._paperless.upload(path, title=text or None)
                if wants_contact and ocr:
                    return self._contact_add(ocr, text, None), None
                if ocr and not text:
                    return ((f"Saved to Paperless (doc {doc_id}). OCR: "
                             + ocr[:200] + "..."), None)
                return f"Saved to Paperless (doc {doc_id}).", None
            return "Received the image, but Paperless isn't configured.", None
        if ctype in ("pdf", "docx", "doc"):
            if self._paperless:
                doc_id, _ocr = self._paperless.upload(path, title=text or None)
                return (f"Saved to Paperless (doc {doc_id})." if doc_id else "Paperless upload failed."), None
            return "Received the document, but Paperless isn't configured.", None
        if ctype in ("ogg", "oga", "m4a", "mp3", "wav", "aac"):
            try:
                from .wyoming import transcribe
                with open(path, "rb") as f:
                    data = f.read()
                wav = _any_to_wav_stub(data)
                text = transcribe(wav, self._stt.get("host", "127.0.0.1"),
                                  int(self._stt.get("port", 10300)))
                if text:
                    return self.handle(sender, text)
                return "The voice note came back empty - try again?", None
            except Exception as e:
                return f"Voice transcription failed: {e}", None
        return "I got the attachment but don't have a skill for that type yet.", None

    # -- main entry ----------------------------------------------------------

    def handle(self, sender, text, attachment=None):
        """Returns (reply, attachment). sender is any stable per-person key."""
        if attachment:
            return self._handle_attachment(sender, text, attachment)
        if not text or not text.strip():
            return "Send me a message and I'll help.", None
        hist = list(self.history.get(sender, []))
        cmd = text.lower().strip()

        action, arg = intents.keyword_intent(cmd)
        if action is None:
            action, arg = intents.classify_llm(self.chat, cmd, hist)

        handler = self.dispatch.get(action)
        if handler:
            reply = handler(arg, text, hist)
        else:
            reply = self.chat.reply(text, hist)

        if self.contacts.available and action in (None, "REMIND", "TASK", "EVENT", "CHAT"):
            enriched, _c, amb = self.contacts.enrich(reply)
            if enriched and enriched != reply and not amb:
                reply = enriched
        self._record_turn(sender, text, reply)
        return reply, None

    def _record_turn(self, sender, user_text, bot_reply):
        h = self.history.setdefault(sender, [])
        h.append({"role": "user", "content": user_text})
        if bot_reply:
            h.append({"role": "assistant", "content": bot_reply})
        h[:] = h[-8:]

    def _hist(self, sender, text=None):
        return list(self.history.get(sender, []))


# helper: most voice sources are already 16k mono wav; extend here if not
def _any_to_wav_stub(data):
    return data