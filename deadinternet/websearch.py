"""Web search for the topic queue.

DuckDuckGo's HTML endpoint, because it needs no API key and this runs on a
box with no search credentials configured. That also means it is scraping,
so it is written to fail soft: any parse or network problem yields no
results and the director falls back to another source rather than dying.
"""
import html
import re
import time
from typing import List, NamedTuple

import requests

ENDPOINT = "https://lite.duckduckgo.com/lite/"
# lite/ is a much flatter document than html/ -- fewer wrappers to break.
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept-Language": "en-US,en;q=0.9",
}
TIMEOUT = 15
# Be a polite scraper: never hammer the endpoint from the turn loop.
MIN_INTERVAL = 2.0

_TAG = re.compile(r"<[^>]+>")
# The endpoint quotes attributes with single quotes (class='result-link'),
# so the quote character has to be optional here -- matching only double
# quotes silently parses zero results off a perfectly good page.
_RESULT_LINK = re.compile(
    r'<a[^>]+class=[\'"]result-link[\'"][^>]*>(.*?)</a>', re.S | re.I)
_SNIPPET = re.compile(
    r'<td[^>]*class=[\'"]result-snippet[\'"][^>]*>(.*?)</td>', re.S | re.I)

_last_call = 0.0


class Result(NamedTuple):
    title: str
    snippet: str

    def as_topic(self) -> str:
        """One line worth talking about."""
        if self.snippet and len(self.snippet) > 30:
            return f"{self.title} - {self.snippet}"
        return self.title


def _clean(fragment: str) -> str:
    text = _TAG.sub(" ", fragment or "")
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def search(query: str, limit: int = 8, log=print) -> List[Result]:
    """Search the web. Returns [] on any failure -- never raises."""
    global _last_call
    query = (query or "").strip()
    if not query:
        return []

    wait = MIN_INTERVAL - (time.monotonic() - _last_call)
    if wait > 0:
        time.sleep(wait)
    _last_call = time.monotonic()

    try:
        r = requests.post(ENDPOINT, data={"q": query}, headers=HEADERS,
                          timeout=TIMEOUT)
        r.raise_for_status()
        body = r.text
    except Exception as e:
        log(f"[web] search failed for {query!r}: {e}")
        return []

    titles = [_clean(m) for m in _RESULT_LINK.findall(body)]
    snippets = [_clean(m) for m in _SNIPPET.findall(body)]

    out = []
    seen = set()
    for i, title in enumerate(titles):
        if not title or len(title) < 8:
            continue
        key = title.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(Result(title, snippets[i] if i < len(snippets) else ""))
        if len(out) >= limit:
            break

    if not out:
        # Layout changed, or we were served a consent/blocked page. Say so
        # once rather than silently returning nothing forever.
        log(f"[web] no results parsed for {query!r} "
            f"({len(body)} bytes) - the endpoint layout may have changed")
    return out
