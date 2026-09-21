"""Web search for the topic queue.

DuckDuckGo's HTML endpoint, because it needs no API key and this runs on a
box with no search credentials configured. That also means it is scraping,
so it is written to fail soft: any parse or network problem yields no
results and the director falls back to another source rather than dying.

Two halves. search() finds pages; read_article() opens one and pulls the
prose out of it. The second half exists because a result's title and snippet
are not a topic -- they are a headline written for a search page, and for any
commercial subject the first several are adverts. A cast handed "5 Best VR
Headsets 2025 - Read our Expert Reviews!" has nothing to talk about. What is
actually *in* the article is the topic; the director reads it and has the
model brief it (llm.brief_article) before anyone is asked to discuss it.
"""
import html
import ipaddress
import re
import socket
import time
from html.parser import HTMLParser
from typing import List, NamedTuple, Optional
from urllib.parse import parse_qs, urljoin, urlparse
from xml.etree import ElementTree

import requests

# Asked first. A news feed returns *stories* -- one URL per article, dated,
# no adverts -- where a web search for the same subject returns storefronts
# and topic hubs ("vr" -> Meta, Amazon, Walmart). Also keyless.
NEWS_ENDPOINT = "https://www.bing.com/news/search"
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
    r'<a([^>]+class=[\'"]result-link[\'"][^>]*)>(.*?)</a>', re.S | re.I)
_HREF = re.compile(r'href=[\'"]([^\'"]+)[\'"]', re.I)
_SNIPPET = re.compile(
    r'<td[^>]*class=[\'"]result-snippet[\'"][^>]*>(.*?)</td>', re.S | re.I)

_last_call = 0.0
# Set when the web endpoint answers with a bot challenge instead of results.
# There is nothing to do about that from here except stop asking for a while:
# retrying is what earned the challenge, and it is not this program's place to
# answer one.
_challenged_until = 0.0
CHALLENGE_BACKOFF = 1800.0


# An article is read up to here and no further. Enough for the substance of
# any news story; a bound on what a 12B model is asked to digest, and on what
# a hostile or merely enormous page can make this process download.
MAX_PAGE_BYTES = 1_500_000
# TIMEOUT is per read, so a server that sends a byte every few seconds could
# otherwise hold an executor thread for as long as it liked.
MAX_PAGE_SECONDS = 30.0
MAX_REDIRECTS = 5
MAX_ARTICLE_CHARS = 6000
# Below this much prose a page is not an article: a shop front, a video
# player, a cookie wall, a list of links. This is what filters those out --
# by what the page contains rather than by a list of domains to keep current.
MIN_ARTICLE_CHARS = 600


class Result(NamedTuple):
    title: str
    snippet: str
    url: str = ""

    def as_topic(self) -> str:
        """One line worth talking about."""
        if self.snippet and len(self.snippet) > 30:
            return f"{self.title} - {self.snippet}"
        return self.title


def _clean(fragment: str) -> str:
    text = _TAG.sub(" ", fragment or "")
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _pace():
    global _last_call
    wait = MIN_INTERVAL - (time.monotonic() - _last_call)
    if wait > 0:
        time.sleep(wait)
    _last_call = time.monotonic()


# The news endpoint's other answer. Asked for RSS it sometimes serves its
# ordinary results page instead -- reliably so for broad queries ("politics")
# -- and that page has the same stories on it as cards.
_NEWS_CARD = re.compile(
    r'<a[^>]+class="title"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S | re.I)
# Hosts that answer with an empty shell and draw the story in with script, so
# there is never anything to read. Not a quality judgement and not a blocklist
# to grow: one aggregator that happens to be a third of this feed, where every
# attempt is a wasted fetch.
_SHELL_HOSTS = ("msn.com",)


def parse_news(payload: bytes, limit: int = 8) -> List[Result]:
    """The stories in a news response, feed or page. Pure; [] if neither."""
    found = []
    try:
        for item in ElementTree.fromstring(payload).iter("item"):
            link = (item.findtext("link") or "").strip()
            # Links are click-trackers carrying the story's address in ?url=.
            inner = parse_qs(urlparse(link).query).get("url")
            found.append((item.findtext("title") or "",
                          item.findtext("description") or "",
                          inner[0] if inner else link))
    except ElementTree.ParseError:
        pass
    if not found:
        page = payload.decode("utf-8", errors="replace")
        found = [(title, "", html.unescape(href))
                 for href, title in _NEWS_CARD.findall(page)]

    out, shells, seen = [], [], set()
    for title, snippet, url in found:
        title = _clean(title)
        if len(title) < 8 or not url.startswith("http") or title.lower() in seen:
            continue
        seen.add(title.lower())
        host = (urlparse(url).hostname or "").lower()
        result = Result(title, _clean(snippet), url)
        (shells if host.endswith(_SHELL_HOSTS) else out).append(result)
    # Unreadable hosts only make up the numbers when there is little else.
    return (out + shells)[:limit] if len(out) < 3 else out[:limit]


def news(query: str, limit: int = 8, log=print) -> List[Result]:
    """Recent stories about `query`. Returns [] on any failure."""
    query = (query or "").strip()
    if not query:
        return []
    out = []
    # The feed first; then the ordinary results page, which for some queries
    # ("trump": two items in the feed, a full page of cards) has far more.
    for params in ({"q": query, "format": "rss"}, {"q": query}):
        _pace()
        try:
            r = requests.get(NEWS_ENDPOINT, params=params, headers=HEADERS,
                             timeout=TIMEOUT)
            r.raise_for_status()
        except Exception as e:
            log(f"[web] news search failed for {query!r}: {e}")
            break
        have = {x.url for x in out}
        out += [x for x in parse_news(r.content, limit) if x.url not in have]
        if len(out) >= 3:
            break
    # Stable, so each response's own ranking survives; it only moves the
    # unreadable hosts behind everything that can actually be opened.
    out.sort(key=lambda x: (urlparse(x.url).hostname or "").endswith(_SHELL_HOSTS))
    return out[:limit]


def find(query: str, limit: int = 8, log=print) -> List[Result]:
    """Things to read about `query`: news stories, else web results."""
    found = news(query, limit, log)
    if len(found) < 3:
        have = {r.url for r in found}
        found += [r for r in search(query, limit, log) if r.url not in have]
    return found[:limit]


def _result_url(href: str) -> Optional[str]:
    """A result link's real destination. None for an advert.

    Organic links are wrapped as //duckduckgo.com/l/?uddg=<the url>; adverts
    go through y.js with an ad_domain. Sponsored results are dropped outright:
    they are the top of the page for anything anyone sells, and an advert is
    never an article.
    """
    href = html.unescape(href or "").strip()
    if not href:
        return ""
    if href.startswith("//"):
        href = "https:" + href
    parts = urlparse(href)
    if parts.netloc.endswith("duckduckgo.com"):
        if "y.js" in parts.path or "ad_domain" in parts.query:
            return None
        inner = parse_qs(parts.query).get("uddg")
        return inner[0] if inner else ""
    return href


def search(query: str, limit: int = 8, log=print) -> List[Result]:
    """Search the web. Returns [] on any failure -- never raises."""
    global _challenged_until
    query = (query or "").strip()
    if not query:
        return []
    if time.monotonic() < _challenged_until:
        return []

    _pace()
    try:
        r = requests.post(ENDPOINT, data={"q": query}, headers=HEADERS,
                          timeout=TIMEOUT)
        r.raise_for_status()
        body = r.text
    except Exception as e:
        log(f"[web] search failed for {query!r}: {e}")
        return []

    if r.status_code == 202 or "confirm this search was made by a human" in body:
        _challenged_until = time.monotonic() + CHALLENGE_BACKOFF
        log(f"[web] DuckDuckGo asked for a CAPTCHA - not searching it again for "
            f"{CHALLENGE_BACKOFF / 60:.0f} minutes (news search is unaffected)")
        return []

    links = list(_RESULT_LINK.finditer(body))
    out = []
    seen = set()
    ads = 0
    for i, m in enumerate(links):
        href = _HREF.search(m.group(1))
        url = _result_url(href.group(1) if href else "")
        if not url:
            # None is a recognised advert. "" is a link with no destination
            # that could be recovered, which in practice is another advert
            # shape ("more info") -- and is unreadable either way.
            ads += 1
            continue
        title = _clean(m.group(2))
        if not title or len(title) < 8:
            continue
        key = title.lower()
        if key in seen:
            continue
        seen.add(key)
        # The snippet belonging to a link is the one between it and the next
        # link. Pairing the two lists by index, as this used to, slides every
        # snippet onto the wrong title as soon as one result is skipped.
        end = links[i + 1].start() if i + 1 < len(links) else len(body)
        snip = _SNIPPET.search(body, m.end(), end)
        out.append(Result(title, _clean(snip.group(1)) if snip else "", url))
        if len(out) >= limit:
            break
    if ads:
        log(f"[web] {query!r}: skipped {ads} sponsored result(s)")

    if not out:
        # Layout changed, or we were served a consent/blocked page. Say so
        # once rather than silently returning nothing forever.
        log(f"[web] no results parsed for {query!r} "
            f"({len(body)} bytes) - the endpoint layout may have changed")
    return out


# ---- reading a result ------------------------------------------------------
class Article(NamedTuple):
    title: str
    text: str


# Never prose, wherever they appear.
_SKIP = {"script", "style", "noscript", "template", "svg", "nav", "footer",
         "header", "aside", "form", "button", "figure", "figcaption", "iframe",
         "select"}
_BLOCK = {"p", "li", "blockquote", "h2", "h3"}
_VOID = {"br", "img", "meta", "link", "input", "hr", "source", "wbr", "area",
         "base", "col", "embed", "param", "track"}


class _Prose(HTMLParser):
    """Collects paragraph text, preferring what is inside <article>/<main>.

    Deliberately not a readability port. News pages put the story in <p> tags
    inside an <article>, and everything that is not the story -- menus,
    related links, sign-up forms -- in the tags skipped above. That covers
    most of the web that is worth discussing, in the standard library.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.og_title = ""
        self.inside = []      # blocks found within <article>/<main>
        self.anywhere = []    # every block, for pages with neither
        self._skip = 0
        self._main = 0
        self._in_title = False
        self._block = None

    def handle_starttag(self, tag, attrs):
        if tag in _VOID:
            if tag == "meta":
                a = dict(attrs)
                if a.get("property") == "og:title" and a.get("content"):
                    self.og_title = a["content"].strip()
            return
        if tag in _SKIP:
            self._skip += 1
        elif tag in ("article", "main"):
            self._main += 1
        elif tag == "title":
            self._in_title = True
        elif tag in _BLOCK and not self._skip and self._block is None:
            self._block = []

    def handle_endtag(self, tag):
        if tag in _SKIP:
            self._skip = max(0, self._skip - 1)
        elif tag in ("article", "main"):
            self._main = max(0, self._main - 1)
        elif tag == "title":
            self._in_title = False
        elif tag in _BLOCK and self._block is not None:
            text = re.sub(r"\s+", " ", "".join(self._block)).strip()
            self._block = None
            if _is_prose(text):
                self.anywhere.append(text)
                if self._main:
                    self.inside.append(text)

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif self._block is not None and not self._skip:
            self._block.append(data)


def _is_prose(text: str) -> bool:
    """A sentence or more of writing, as opposed to page furniture.

    Short blocks are bylines, captions and "Read more"; a block with no
    sentence in it is a menu item; and one that is mostly digits and currency
    is a product listing, which otherwise reads as 1700 characters of "prose"
    on a shop's search page.
    """
    if len(text) < 60 or len(text.split()) < 10:
        return False
    if not re.search(r"[.!?][\"'\u201d\u2019)]?(\s|$)", text):
        return False
    digits = sum(c.isdigit() for c in text)
    if digits > len(text) * 0.12 or len(re.findall(r"[$\u00a3\u20ac]\s?\d", text)) > 2:
        return False
    return True


def _resolve(host: str) -> List[str]:
    """Every address `host` resolves to. Its own function so tests can stand
    in for DNS."""
    return [info[4][0] for info in socket.getaddrinfo(host, None)]


def _public(url: str) -> bool:
    """http(s) to somewhere that is not this machine or this network. The URL
    came off a search page, so it is somebody else's string.

    A name is looked up and every address it has must be public: anyone can
    point a DNS record at 127.0.0.1, and Ollama, tts-server and this app's own
    UI are all listening there without a password. What this cannot close is
    the gap between this lookup and the one the request makes -- a record that
    changes in between still gets through -- but that takes a hostile page
    ranking in the results *and* a rebinding nameserver, for a blind GET.
    """
    parts = urlparse(url or "")
    host = (parts.hostname or "").lower()
    if parts.scheme not in ("http", "https") or not host:
        return False
    if host == "localhost" or host.endswith((".local", ".internal", ".lan")):
        return False
    try:
        return ipaddress.ip_address(host).is_global
    except ValueError:
        pass   # a name, not an address literal
    try:
        found = _resolve(host)
        # "fe80::1%eth0": the scope is not part of the address.
        return bool(found) and all(
            ipaddress.ip_address(a.split("%")[0]).is_global for a in found)
    except (OSError, ValueError):
        return False


def _open(url: str):
    """GET `url`, following redirects by hand. -> an open response, or None.

    By hand because every hop has to pass _public *before* it is requested.
    Left to requests, a public page that redirects to http://127.0.0.1:11434/
    has already made this process send that request by the time anyone looks
    at where it ended up.
    """
    for _ in range(MAX_REDIRECTS + 1):
        if not _public(url):
            return None
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT, stream=True,
                         allow_redirects=False)
        where = r.headers.get("Location")
        if r.status_code in (301, 302, 303, 307, 308) and where:
            r.close()
            url = urljoin(url, where)
            continue
        return r
    return None


def extract_article(markup: str) -> Article:
    """HTML -> the prose in it. Pure, so it can be tested without a network."""
    p = _Prose()
    try:
        p.feed(markup or "")
        p.close()
    except Exception:
        pass   # whatever was collected before a malformed tag is still good
    # <article>/<main> when it holds the bulk of the page's prose; otherwise
    # the page is using them for something else (a teaser, a hero block).
    inside, anywhere = " ".join(p.inside), " ".join(p.anywhere)
    body = inside if len(inside) >= max(MIN_ARTICLE_CHARS, len(anywhere) // 3) \
        else anywhere
    blocks = p.inside if body is inside else p.anywhere
    text = "\n".join(dict.fromkeys(blocks))   # order kept, repeats dropped
    if len(text) > MAX_ARTICLE_CHARS:
        cut = text.rfind(". ", 0, MAX_ARTICLE_CHARS)
        text = text[: cut + 1] if cut > MAX_ARTICLE_CHARS // 2 \
            else text[:MAX_ARTICLE_CHARS]
    title = re.sub(r"\s+", " ", p.og_title or p.title).strip()
    return Article(title, text.strip())


def read_article(url: str, log=print) -> Optional[Article]:
    """Open a result and return what it says. None when there is no article
    there -- unreachable, not HTML, or not enough prose to be one."""
    try:
        r = _open(url)
        if r is None:
            return None
        with r:
            r.raise_for_status()
            kind = r.headers.get("Content-Type", "").lower()
            if "html" not in kind:
                log(f"[web] not a page ({kind or 'no content type'}): {url}")
                return None
            raw, deadline = b"", time.monotonic() + MAX_PAGE_SECONDS
            for chunk in r.iter_content(65536):
                raw += chunk
                if len(raw) >= MAX_PAGE_BYTES or time.monotonic() > deadline:
                    break
            # requests assumes ISO-8859-1 for text/* with no charset, which
            # is what the RFC says and almost never what the page is.
            enc = r.encoding if "charset" in kind and r.encoding else "utf-8"
            markup = raw.decode(enc, errors="replace")
    except Exception as e:
        log(f"[web] could not read {url}: {e}")
        return None
    art = extract_article(markup)
    if len(art.text) < MIN_ARTICLE_CHARS:
        log(f"[web] no article at {url} ({len(art.text)} chars of prose)")
        return None
    return art
