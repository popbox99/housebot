"""Weather and web search skills.

Weather uses Open-Meteo (no API key). Web search uses a self-hosted SearXNG
instance (config skills.searxng.base_url) — results are URLs + titles for the
LLM or the user.

read_url is SSRF-hardened: https only, and hostnames resolving to private,
loopback, link-local or reserved addresses are refused.
"""

import ipaddress
import json
import re
import socket
import urllib.parse
import urllib.request


class Web:
    def __init__(self, cfg):
        s = cfg["skills"]
        self.searxng = (s.get("searxng") or {}).get("base_url", "").rstrip("/")
        self.geocode_default = s.get("default_place", "")

    def weather(self, place):
        place = (place or "").strip() or self.geocode_default
        if not place:
            return "Weather needs a place (or skills.default_place in config)."
        try:
            geo = json.loads(urllib.request.urlopen(
                "https://geocoding-api.open-meteo.com/v1/search?name="
                + urllib.parse.quote(place) + "&count=1", timeout=15).read())
            if not geo.get("results"):
                return f"Couldn't find {place!r}."
            g = geo["results"][0]
            lat, lon, name = g["latitude"], g["longitude"], g.get("name", place)
            url = (f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
                   f"&current=temperature_2m,weather_code,wind_speed_10m"
                   f"&daily=temperature_2m_max,temperature_2m_min,weather_code"
                   f"&temperature_unit=fahrenheit&wind_speed_unit=mph&forecast_days=2")
            w = json.loads(urllib.request.urlopen(url, timeout=15).read())
            cur = w["current"]
            today = w["daily"]
            desc = _WMO.get(cur["weather_code"], "weather code " + str(cur["weather_code"]))
            lines = [f"{name}: {cur['temperature_2m']:.0f}°F, {desc}, "
                     f"wind {cur['wind_speed_10m']:.0f} mph",
                     f"Today: {today['temperature_2m_min'][0]:.0f}–"
                     f"{today['temperature_2m_max'][0]:.0f}°F, "
                     + _WMO.get(today["weather_code"][0], ""),
                     f"Tomorrow: {today['temperature_2m_min'][1]:.0f}–"
                     f"{today['temperature_2m_max'][1]:.0f}°F, "
                     + _WMO.get(today["weather_code"][1], "")]
            return "\n".join(lines)
        except Exception as e:
            return f"Weather lookup failed: {e}"

    def search(self, query, k=5):
        if not self.searxng:
            return None   # skill disabled
        try:
            url = f"{self.searxng}/search?q={urllib.parse.quote(query)}&format=json"
            req = urllib.request.Request(url, headers={"User-Agent": "housebot"})
            with urllib.request.urlopen(req, timeout=20) as r:
                results = json.loads(r.read()).get("results", [])
            return [{"title": r.get("title", ""), "url": r.get("url", ""),
                     "snippet": (r.get("content") or "")[:200]}
                    for r in results[:k]]
        except Exception as e:
            return None

    def search_reply(self, query):
        results = self.search(query)
        if results is None:
            return "Web search is not configured (skills.searxng.base_url)."
        if not results:
            return f"No results for {query!r}."
        return "Search results:\n" + "\n".join(
            f"  • {r['title']} — {r['url']}" for r in results)

    def _safe_url(self, url):
        """https-only, and refuse hosts that resolve to private/reserved space.
        (Best-effort SSRF guard: TOCTOU DNS rebinding is out of scope here.)"""
        try:
            parts = urllib.parse.urlsplit(url)
        except Exception:
            return None
        if parts.scheme != "https" or not parts.hostname:
            return None
        try:
            infos = socket.getaddrinfo(parts.hostname, 443)
        except Exception:
            return None
        for info in infos:
            try:
                ip = ipaddress.ip_address(info[4][0])
            except ValueError:
                return None
            if (ip.is_private or ip.is_loopback or ip.is_link_local
                    or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
                return None
        return url

    def read_url(self, url, summarize_fn):
        """Fetch a URL's text and summarize via the LLM."""
        url = self._safe_url(url)
        if not url:
            return "I can only fetch https:// pages on public hosts."
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "housebot"})
            with urllib.request.urlopen(req, timeout=30) as r:
                html = r.read().decode("utf-8", errors="replace")
        except Exception as e:
            return f"Couldn't fetch {url}: {e}"
        text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html,
                      flags=re.S | re.I)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s{2,}", " ", text)[:12000]
        if not text.strip():
            return "That page had no readable text."
        return summarize_fn(f"Summarize this page:\n\n{text}")


_WMO = {0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "overcast",
        45: "fog", 51: "light drizzle", 61: "light rain", 63: "rain",
        65: "heavy rain", 71: "light snow", 73: "snow", 75: "heavy snow",
        80: "rain showers", 95: "thunderstorm"}