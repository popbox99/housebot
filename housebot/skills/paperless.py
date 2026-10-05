"""Paperless-ngx skill: upload documents, OCR text retrieval, document Q&A."""

import json
import mimetypes
import os
import time
import urllib.request


class Paperless:
    def __init__(self, cfg, log=lambda *a: None):
        s = cfg["skills"].get("paperless") or {}
        self.enabled = bool(s.get("enabled") and s.get("base_url"))
        self.base = (s.get("base_url") or "").rstrip("/")
        self.token = self._read_token(s.get("token_file"))
        self.log = log

    def _read_token(self, path):
        if path and os.path.exists(os.path.expanduser(path)):
            return open(os.path.expanduser(path)).read().strip()
        return ""

    def _headers(self):
        return {"Authorization": "Token " + self.token}

    def upload(self, file_path, title=None, wait=90):
        """Upload and wait for OCR; returns (doc_id, text) or (None, None)."""
        if not self.enabled:
            return None, None
        boundary = "----housebot" + hex(int(time.time() * 1000))[2:]
        filename = os.path.basename(file_path)
        ctype = mimetypes.guess_type(file_path)[0] or "application/octet-stream"
        try:
            with open(file_path, "rb") as f:
                file_bytes = f.read()
            body = bytearray()
            body.extend(f"--{boundary}\r\n".encode())
            body.extend(f'Content-Disposition: form-data; name="document"; '
                        f'filename="{filename}"\r\n'.encode())
            body.extend(f"Content-Type: {ctype}\r\n\r\n".encode())
            body.extend(file_bytes)
            body.extend(b"\r\n")
            if title:
                body.extend(f"--{boundary}\r\n".encode())
                body.extend(b'Content-Disposition: form-data; name="title"\r\n\r\n')
                body.extend(f"{title}\r\n".encode())
            body.extend(f"--{boundary}--\r\n".encode())
            req = urllib.request.Request(
                f"{self.base}/api/documents/post_document/", data=bytes(body),
                headers={**self._headers(),
                         "Content-Type": f"multipart/form-data; boundary={boundary}"})
            with urllib.request.urlopen(req, timeout=30) as r:
                doc_ids = json.loads(r.read())
            doc_id = doc_ids[0] if isinstance(doc_ids, list) and doc_ids else None
            if not doc_id:
                return None, None
            deadline = time.time() + wait
            while time.time() < deadline:
                time.sleep(4)
                _id, text = self.fetch_text(doc_id)
                if text:
                    return doc_id, text
            return doc_id, ""
        except Exception as e:
            self.log(f"paperless upload failed: {e}")
            return None, None

    def fetch_text(self, doc_id):
        try:
            req = urllib.request.Request(
                f"{self.base}/api/documents/{doc_id}/", headers=self._headers())
            with urllib.request.urlopen(req, timeout=30) as r:
                full = json.loads(r.read())
            return doc_id, (full.get("content") or "").strip()
        except Exception as e:
            self.log(f"paperless fetch failed: {e}")
            return doc_id, ""

    def answer(self, query, summarize_fn):
        """Search documents and answer from their text via the LLM."""
        try:
            url = (f"{self.base}/api/documents/?query="
                   + urllib.request.quote(query) + "&page_size=3")
            req = urllib.request.Request(url, headers=self._headers())
            with urllib.request.urlopen(req, timeout=30) as r:
                results = json.loads(r.read()).get("results", [])
        except Exception as e:
            self.log(f"paperless search failed: {e}")
            return "Paperless search failed."
        if not results:
            return f"No documents matching {query!r}."
        chunks = []
        for doc in results[:3]:
            _id, text = self.fetch_text(doc["id"])
            if text:
                chunks.append(f"--- {doc.get('title', doc['id'])} ---\n{text[:2000]}")
        if not chunks:
            return "Documents matched but OCR text isn't ready yet - try again in a minute."
        return summarize_fn("Answer from these documents:\n\n" + "\n".join(chunks)
                            + f"\n\nQuestion: {query}")