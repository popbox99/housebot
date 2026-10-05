"""Markdown-backed list skills: shopping, chores, habits, HID (where-is-it), memory.

All state lives in plain files inside your notes dir — readable in Obsidian and
syncable. Shopping supports two-way use with any VTODO-syncing task app if you point
`shopping_note` at a file that your task app also manages.
"""

import json
import os
import re
import time


class Lists:
    def __init__(self, cfg):
        s = cfg["skills"]
        self.dir = s.get("notes_dir") or os.path.join(cfg.data_dir, "notes")
        os.makedirs(self.dir, exist_ok=True)
        self.shopping_note = os.path.join(self.dir, s.get("shopping_note", "Shopping List.md"))
        self.chores_note = os.path.join(self.dir, s.get("chores_note", "Chores.md"))
        self.habits_note = os.path.join(self.dir, s.get("habits_note", "Habits.md"))
        self.memory_file = os.path.join(cfg.data_dir, "memory.json")

    # -- shopping --------------------------------------------------------------

    def _read_note_lines(self, path):
        if not os.path.exists(path):
            return []
        return [l.rstrip() for l in open(path)]

    def _write_note_lines(self, path, lines):
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            f.write("\n".join(lines) + "\n")
        os.replace(tmp, path)

    def shopping_add(self, items, section="Groceries and Supplies"):
        lines = self._read_note_lines(self.shopping_note)
        if not lines:
            lines = [f"# Shopping List", "", f"## {section}", ""]
        # append under the first matching section header, else at end
        idx = next((i for i, l in enumerate(lines)
                    if l.strip().lower() == "## " + section.lower()), None)
        entry_lines = [f"- {it}" for it in items]
        if idx is not None:
            j = idx + 1
            while j < len(lines) and (not lines[j].startswith("## ")):
                j += 1
            insert_at = j
            while insert_at > idx + 1 and lines[insert_at - 1].strip().startswith("-"):
                insert_at -= 1
            lines[insert_at:insert_at] = entry_lines
        else:
            lines += [f"## {section}"] + entry_lines
        self._write_note_lines(self.shopping_note, lines)
        return "Added to shopping list: " + ", ".join(items)

    def shopping_remove(self, item):
        lines = self._read_note_lines(self.shopping_note)
        kept, removed = [], False
        for l in lines:
            if l.strip().startswith("- ") and item.lower() in l.lower() and not removed:
                removed = True
                continue
            kept.append(l)
        self._write_note_lines(self.shopping_note, kept)
        return (f"Removed {item!r}." if removed else
                f"Couldn't find {item!r} on the list.")

    def shopping_show(self):
        lines = self._read_note_lines(self.shopping_note)
        items = [l.strip()[2:] for l in lines if l.strip().startswith("- ")]
        return ("Shopping list:\n- " + "\n- ".join(items)) if items else "Shopping list is empty."

    # -- chores ------------------------------------------------------------------

    def chore_log(self, task):
        lines = self._read_note_lines(self.chores_note)
        if not lines:
            lines = ["# Chores & Maintenance Log", ""]
        today = time.strftime("%Y-%m-%d")
        lines.append(f"- {today} — {task}")
        self._write_note_lines(self.chores_note, lines)
        return f"Logged: {task} ({today})."

    def chore_status(self):
        lines = self._read_note_lines(self.chores_note)
        entries = [l.strip()[2:] for l in lines if l.strip().startswith("- ")]
        if not entries:
            return "No chores logged yet."
        return ("Last chores:\n" + "\n".join(f"  {e}" for e in entries[-10:]))

    # -- habits ------------------------------------------------------------------

    def habit_log(self, text):
        today = time.strftime("%Y-%m-%d")
        lines = self._read_note_lines(self.habits_note)
        if not lines:
            lines = ["# Habits", ""]
        lines.append(f"- {today} {time.strftime('%H:%M')} — {text}")
        self._write_note_lines(self.habits_note, lines)
        return f"Logged: {text}"

    # -- HID / memory --------------------------------------------------------------

    def remember(self, topic, fact):
        mem = self._load_memory()
        mem[topic.lower()] = {"fact": fact, "when": time.strftime("%Y-%m-%d")}
        self._save_memory(mem)
        return f"Noted: {topic} — {fact}"

    def forget(self, topic):
        mem = self._load_memory()
        key = topic.lower().strip()
        for k in list(mem):
            if key in k:
                del mem[k]
                self._save_memory(mem)
                return f"Forgot {k}."
        return f"I don't have anything about {topic!r}."

    def recall(self, topic):
        mem = self._load_memory()
        hits = {k: v for k, v in mem.items()
                if topic.lower() in k or topic.lower() in str(v.get("fact", "")).lower()}
        if not hits:
            return None
        return "\n".join(f"{k}: {v['fact']} ({v['when']})" for k, v in hits.items())

    def hide(self, item, location):
        return self.remember(f"hid: {item}", location)

    def where_hidden(self, item):
        r = self.recall(f"hid: {item}")
        return r or f"I don't have a hiding place for {item!r}."

    def _load_memory(self):
        try:
            return json.load(open(self.memory_file))
        except Exception:
            return {}

    def _save_memory(self, mem):
        tmp = self.memory_file + ".tmp"
        with open(tmp, "w") as f:
            json.dump(mem, f, indent=1)
        os.replace(tmp, self.memory_file)