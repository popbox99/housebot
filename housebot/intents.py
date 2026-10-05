"""Intent pipeline: fast keyword regexes first (high-confidence), LLM classifier fallback."""

import re

CLASSIFIER_PROMPT = (
    "Classify the user request into ONE line. Never explain.\n"
    "Format: one of FIND <words> | CHAT | WEATHER [place] | SEARCH_WEB <words> | "
    "SUMMARIZE <words> | READ_URL <url> | EVENT <desc> | TASK <desc> | AGENDA | "
    "REMIND <when+what> | NOTE <text> | CONTACT <name and/or phone/email> | "
    "CONTACT_INFO <person> | REMINDERS | COMPLETE <task words> | LOCATION <person> | "
    "SHOPPING <items or 'remove X' or 'list'> | CHORE <what was done or 'status'> | "
    "HABIT <what to log> | REMEMBER <fact> | FORGET <topic> | MEMORY <topic> | "
    "HID <item> in <location> | PHOTOS <query> | PAPERLESS <question> | "
    "BATTERIES | VACUUM <start|dock|status>\n"
    "Examples:\n"
    "Request: remind me in 2 hours to check the dryer -> REMIND in 2 hours check the dryer\n"
    "Request: remind me to call dana reyes tomorrow at 3pm -> REMIND call dana reyes tomorrow at 3pm\n"
    "Request: add dinner with mike friday 7pm to my calendar -> EVENT dinner with mike friday 7pm\n"
    "Request: remind me to file taxes -> TASK file taxes\n"
    "Request: whats on my calendar today -> AGENDA\n"
    "Request: note the furnace filter size is 16x25x4 -> NOTE furnace filter size is 16x25x4\n"
    "Request: add contact john smith 555-123-4567 -> CONTACT john smith 555-123-4567\n"
    "Request: what is dana reyes's phone number -> CONTACT_INFO dana reyes\n"
    "Request: what is her email (previous: what is dana reyes's phone number) -> CONTACT_INFO dana reyes\n"
    "Request: search the web for coiled tubing prices -> SEARCH_WEB coiled tubing prices\n"
    "Request: what tasks do i have -> REMINDERS\n"
    "Request: remind me when i get home to unload the car -> LOCATION unload the car\n"
    "Request: add milk and eggs to the shopping list -> SHOPPING milk and eggs\n"
    "Request: whats on my shopping list -> SHOPPING list\n"
    "Request: remove milk from my shopping list -> SHOPPING remove milk\n"
    "Request: i changed the hvac air filter today -> CHORE changed the hvac air filter\n"
    "Request: chores status -> CHORE status\n"
    "Request: log that i drank 24 ounces of water -> HABIT drank 24 ounces of water\n"
    "Request: remember that the spare key is in the desk drawer -> REMEMBER the spare key is in the desk drawer\n"
    "Request: where did i put the passport -> MEMORY passport\n"
    "Request: what do you remember about my sister -> MEMORY my sister\n"
    "Request: forget what i said about the boat keys -> FORGET boat keys\n"
    "Request: where can i find sushi near springfield -> SEARCH_WEB sushi near springfield\n"
    "Request: summarize this page https://example.com/article -> READ_URL https://example.com/article\n"
    "Request: photos of the dog at the lake -> PHOTOS dog at the lake\n"
    "Request: what does my warranty say about the furnace -> PAPERLESS warranty furnace\n"
    "Request: how are the batteries -> BATTERIES\n"
    "Request: start the vacuum -> VACUUM start\n"
    "Follow-up rule: short follow-ups like what about X / and X inherit the topic of the previous exchange.\n"
)


def keyword_intent(t):
    """High-confidence regex intents. Returns (action, arg) or (None, None)."""
    # location reminders BEFORE REMIND ("remind me when i get home" contains "remind")
    if re.search(r"\bwhen (?:i|we) (?:get|arrive|reach|am|are|come)\b", t):
        return "LOCATION", t[:150]
    if re.search(r"\bremind(er)?\b", t) and re.search(
            r"\b(?:in|at|on|by|tomorrow|tonight|today|later|next|noon|midnight)\b", t) \
            or re.search(r"\bremind me\b", t):
        return "REMIND", t[:150]
    if re.search(r"\bcontacts?\b", t) and re.search(r"\b(add|save|new|scan|card|create)\b", t):
        return "CONTACT", t[:120]
    if (re.search(r"\b(what|who|which|tell|give|show|find|get|know|have)\b", t)
            and re.search(r"\b(phone|mobile|cell|number|e-?mail|address|contact info)\b", t)) \
            or re.search(r"[\w]'?(?:s)?\s+(?:phone|cell|mobile|number|e-?mail|address)\b", t):
        return "CONTACT_INFO", t[:120]
    # Before TASK: any sentence containing "task" would otherwise be a new task,
    # including "mark the task … done".
    if re.search(r"\b(?:mark|check off|complete[d]?)\b", t) and re.search(r"\btask\b", t):
        arg = re.sub(
            r"^(?:please\s+)?(?:mark|check off|complete[d]?)\s+(?:the\s+)?(?:task\s+)?",
            "", t, count=1, flags=re.I)
        arg = re.sub(r"\s+\b(?:as\s+)?(?:done|complete[d]?)\b\s*$", "", arg, flags=re.I)
        return "COMPLETE", arg.strip()[:80]
    if re.search(r"\b(tasks?|todos?|to-dos?|remind me to)\b", t):
        return "TASK", re.sub(r"\b(tasks?|todos?|to-dos?|add|create|remind me to|remind me|it)\b",
                              "", t).strip()[:100]
    if re.search(r"\b(events?|meeting|appointments?|schedule[der]?|calendar)\b", t) \
            and re.search(r"\b(add|create|schedule|set|put)\b", t):
        return "EVENT", t[:120]
    if re.search(r"\b(agenda|calendar|schedule)\b", t) and re.search(
            r"\b(what|whats|show|view|today|tomorrow|this week|week)\b", t):
        return "AGENDA", ""
    if re.search(r"\b(?:pending |open )?reminders\b", t) and not re.search(r"\bremind me\b", t):
        return "REMINDERS", ""
    if re.search(r"\b(note|note down|make a note)\b", t) and re.search(
            r"\b(note|down|that|save|log)\b", t):
        return "NOTE", re.sub(r"^(?:note(?:\s+(?:that|down|this))?\s+)", "", t, flags=re.I)[:150]
    if t.startswith("find "):
        return "FIND", t[5:100]
    if re.search(r"\bfind\b", t) and re.search(r"\b(file|document|pdf|folder|my)\b", t):
        return "FIND", re.sub(r"^find\s+", "", t, flags=re.I)[:100]
    if re.search(r"\bsummarize\b", t):
        return "SUMMARIZE", t[:200]
    if re.search(r"https?://\S+", t) and re.search(r"\b(summar|read|what does it say)\b", t):
        return "READ_URL", t[:200]
    if re.search(r"\b(weather|forecast)\b", t):
        return "WEATHER", re.sub(r"\b(the |weather|forecast|in|whats|what.s|like|today|tomorrow)\b", " ", t).strip() or ""
    if re.search(r"\b(search|google|look up)\b", t) and re.search(r"\b(web|internet|online|the web)\b", t):
        return "SEARCH_WEB", re.sub(r"\b(search|google|look up)( the web| online| for)?\b[: ]?", "", t, flags=re.I).strip()[:100]
    if re.search(r"\b(photos?|pictures?|screenshots?)\b", t) and re.search(r"\b(show|find|search|of|my)\b", t):
        return "PHOTOS", re.sub(r"\b(show|find|search|photos?|pictures?|of|my)\b", " ", t, flags=re.I).strip()[:80]
    if re.search(r"\b(warranty|manual|receipt|invoice|statement|paperless|document)\b", t) \
            and re.search(r"\b(what|say|cover|find|check|show|summar|look up|read)\b", t):
        return "PAPERLESS", t[:150]
    if re.search(r"\bwhere (?:is|are|did i (?:put|leave|hide))\b", t):
        return "MEMORY", re.sub(r"\bwhere (?:is|are|did i (?:put|leave|hide))\b", "", t, flags=re.I).strip(" ?")[:80]
    if re.search(r"\bwhat do you (?:remember|know) (?:about|of)\b", t):
        return "MEMORY", re.sub(r"\bwhat do you (?:remember|know) (?:about|of)\b", "", t, flags=re.I).strip()[:80]
    if re.search(r"\bforget\b", t):
        return "FORGET", re.sub(r"\bforget (?:what i said about|about|that)?\b", "", t, flags=re.I).strip()[:80]
    if re.search(r"\bremember (?:that )?\b", t) and not re.search(r"\bremind\b", t):
        return "REMEMBER", re.sub(r"\bremember (?:that )?\b", "", t, flags=re.I).strip()[:150]
    if re.search(r"\b(shopping|groceries?)\b", t):
        return "SHOPPING", t[:120]
    if re.search(r"\b(chores?|maintenance)\b", t) and re.search(r"\b(status|log|changed|cleaned|did|overdue)\b", t) \
            or (re.search(r"\b(changed|cleaned|replaced|fixed)\b", t) and re.search(r"\b(today|yesterday)\b", t)):
        return "CHORE", t[:120]
    if re.search(r"\b(log|logged)\b", t) and re.search(r"\b(water|oz|ounces|ml|reading|minutes|pages|steps|miles|exercise|meditation)\b", t):
        return "HABIT", re.sub(r"\b(log|logged|that|i)\b", "", t, flags=re.I).strip()[:120]
    if re.search(r"\bbatter(y|ies)\b", t) and re.search(r"\b(how|status|check|report|levels?)\b", t):
        return "BATTERIES", ""
    if re.search(r"\bvacuum\b", t) and re.search(r"\b(start|stop|dock|status|clean)\b", t):
        return "VACUUM", re.sub(r"\b(the|vacuum)\b", "", t, flags=re.I).strip()[:40]
    return None, None


def classify_llm(chat, t, history=None):
    """LLM classifier fallback: returns (action, arg)."""
    from . import llm
    out, _backend = llm.ask_llm(chat.cfg, CLASSIFIER_PROMPT + "Request: " + t,
                                history=history, temperature=0)
    if not out:
        return "CHAT", ""
    line = out.strip().splitlines()[0]
    m = re.search(
        r"\b(FIND|CHAT|WEATHER|SEARCH_WEB|SUMMARIZE|READ_URL|EVENT|TASK|AGENDA|"
        r"REMIND|NOTE|CONTACT|CONTACT_INFO|REMINDERS|COMPLETE|LOCATION|SHOPPING|"
        r"CHORE|HABIT|REMEMBER|FORGET|MEMORY|HID|PHOTOS|PAPERLESS|BATTERIES|"
        r"VACUUM)\b\s*[:]?(.*)$",
        line)
    if not m:
        return "CHAT", ""
    return m.group(1), m.group(2).strip()[:150]