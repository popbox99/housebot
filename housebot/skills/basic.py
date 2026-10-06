"""Notes (markdown files) and file search skills."""

import os
import re
import time


class Notes:
    def __init__(self, cfg):
        self.dir = cfg["skills"].get("notes_dir") or os.path.join(cfg.data_dir, "notes")
        os.makedirs(self.dir, exist_ok=True)

    def add(self, text, folder=None, label="Notes"):
        if not text or not text.strip():
            return "What would you like me to note down?"
        folder = folder or self.dir
        os.makedirs(folder, exist_ok=True)
        now = time.localtime()
        stamp = time.strftime("%Y-%m-%d %H:%M", now)
        slug = re.sub(r"[^A-Za-z0-9 ]", "", text.strip())[:40].strip() or "note"
        path = os.path.join(folder, f"{time.strftime('%Y-%m-%d', now)} {slug}.md")
        n = 2
        while os.path.exists(path):
            path = os.path.join(folder, f"{time.strftime('%Y-%m-%d', now)} {slug} {n}.md")
            n += 1
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"# {label} — {stamp}\n\n{text.strip()}\n")
        return f"Noted: {os.path.basename(path)}"


class Find:
    def __init__(self, cfg):
        self.dirs = [os.path.expanduser(d) for d in cfg["skills"].get("search_dirs") or []]

    def search(self, query, k=5):
        if not self.dirs:
            return None  # skill disabled
        q = (query or "").lower().strip()
        hits = []
        skip = {".stfolder", ".git", "node_modules", "__pycache__", ".cache"}
        for base in self.dirs:
            for root, dirs, files in os.walk(base):
                dirs[:] = [d for d in dirs if d not in skip and not d.startswith(".")]
                for f in files:
                    if q and q in f.lower():
                        hits.append(os.path.join(root, f))
                        if len(hits) >= k:
                            break
                if len(hits) >= k:
                    break
            if len(hits) >= k:
                break
        return hits