"""Photos skill: Immich-native search with a pluggable fallback service."""

import json
import urllib.parse
import urllib.request


class Photos:
    def __init__(self, cfg, log=lambda *a: None):
        s = cfg["skills"].get("immich") or {}
        self.enabled = bool(s.get("enabled") and s.get("base_url"))
        self.base = (s.get("base_url") or "").rstrip("/")
        self.key = self._read_key(s.get("api_key_file"))
        self.fallback_url = s.get("fallback_url") or ""   # e.g. a vision service
        self.log = log

    def _read_key(self, path):
        if path and os.path.exists(os.path.expanduser(path)):
            return open(os.path.expanduser(path)).read().strip()
        return ""

    def immich_search(self, q, k=5):
        try:
            req = urllib.request.Request(
                f"{self.base}/api/search/smart?query={urllib.parse.quote(q)}",
                headers={"x-api-key": self.key})
            with urllib.request.urlopen(req, timeout=30) as r:
                assets = json.loads(r.read())[:k]
            out = []
            for a in assets:
                name = (a.get("originalFileName") or a.get("originalPath")
                        or str(a.get("id") or "photo"))
                taken = (a.get("localDateTime") or a.get("fileCreatedAt") or "")
                out.append({"path": name, "taken": str(taken), "id": a.get("id")})
            return out
        except Exception as e:
            self.log(f"immich search failed: {e}")
            return None   # None = immich unavailable (vs. empty result)

    def search(self, q, k=5):
        imm = self.immich_search(q, k=k) if self.enabled else None
        if imm is not None:
            return imm
        if self.fallback_url:
            try:
                req = urllib.request.Request(
                    self.fallback_url + "/photos/search?q=" + urllib.parse.quote(q))
                with urllib.request.urlopen(req, timeout=60) as r:
                    return json.loads(r.read()).get("results", [])
            except Exception as e:
                self.log(f"photo fallback failed: {e}")
        return []

    def reply(self, q):
        q = (q or "").strip()
        if not q:
            return "Usage: photos <query> - e.g. photos dog at the park"
        photos = self.search(q)
        if not photos:
            return f"No photos matched {q!r}."
        lines = [f"Photos matching {q!r}:"]
        for p in photos[:5]:
            taken = (p.get("taken") or "")[:10]
            name = (p.get("path") or "photo").split("/")[-1]
            lines.append(f"  {name}" + (f" ({taken})" if taken else ""))
        return "\n".join(lines)


import os  # noqa: E402