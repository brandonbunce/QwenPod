"""Web topics come from what an article says, not from its search listing.

All offline: the parsers are pure functions over canned markup, and the model
is a stub. What is being pinned down is the set of things that were wrong --
adverts served as topics, snippets paired with the wrong title, a shop's
product grid read as prose -- none of which raise; they just make a bad show.

    .venv-app/bin/python deadinternet/tests/test_websearch.py
    .venv-app/bin/python -m pytest deadinternet/tests/
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _ROOT)

from deadinternet import websearch as w  # noqa: E402
from deadinternet.llm import BaseLLM  # noqa: E402

PARA = ("The council voted nine to two on Tuesday to ban leaf blowers inside "
        "the city limits, after a four hour hearing that ended in shouting. ")


def test_adverts_are_not_results_and_redirects_are_unwrapped():
    assert w._result_url("https://duckduckgo.com/y.js?ad_domain=shop.com&x=1") is None
    assert w._result_url(
        "//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fstory&amp;rut=abc"
    ) == "https://example.com/story"
    assert w._result_url("https://example.com/a") == "https://example.com/a"


def test_news_feed_and_news_page_both_parse():
    feed = (b'<?xml version="1.0"?><rss><channel>'
            b'<item><title>Council bans leaf blowers</title>'
            b'<link>http://www.bing.com/news/apiclick.aspx?url=https%3a%2f%2fexample.com%2fleaf&amp;c=1</link>'
            b'<description>Nine to two.</description></item>'
            b'<item><title>tiny</title><link>https://example.com/x</link></item>'
            b'</channel></rss>')
    got = w.parse_news(feed)
    assert got == [w.Result("Council bans leaf blowers", "Nine to two.",
                            "https://example.com/leaf")]

    page = (b'<html><a class="title" href="https://example.com/a?x=1&amp;y=2">'
            b'Mayor denies owning the goose</a></html>')
    assert w.parse_news(page) == [w.Result("Mayor denies owning the goose", "",
                                           "https://example.com/a?x=1&y=2")]
    assert w.parse_news(b"not markup at all") == []


def test_unreadable_hosts_only_pad_a_thin_list():
    item = ('<item><title>Story number {n} about it</title>'
            '<link>https://{host}/{n}</link></item>')
    def feed(hosts):
        body = "".join(item.format(n=i, host=h) for i, h in enumerate(hosts))
        return f"<rss><channel>{body}</channel></rss>".encode()
    plenty = w.parse_news(feed(["www.msn.com", "a.com", "b.com", "c.com"]))
    assert [r.url for r in plenty] == ["https://a.com/1", "https://b.com/2",
                                       "https://c.com/3"]
    thin = w.parse_news(feed(["www.msn.com", "a.com"]))
    assert [r.url for r in thin] == ["https://a.com/1", "https://www.msn.com/0"]


def test_article_body_is_found_and_furniture_is_not():
    markup = f"""<html><head><title>Site | Leaf blowers</title>
      <meta property="og:title" content="Council bans leaf blowers"></head><body>
      <nav><p>{PARA}</p></nav>
      <article><h1>Council bans leaf blowers</h1>
        <p>{PARA * 3}</p><p>By A. Reporter</p><p>{PARA * 3}second.</p>
        <aside><p>Subscribe to our newsletter for more stories like this one, every day.</p></aside>
      </article>
      <footer><p>{PARA}</p></footer></body></html>"""
    art = w.extract_article(markup)
    assert art.title == "Council bans leaf blowers"
    assert art.text.count("nine to two") == 6
    assert "Reporter" not in art.text and "Subscribe" not in art.text


def test_a_product_grid_is_not_prose():
    assert w._is_prose(PARA)
    assert not w._is_prose("Read more")
    assert not w._is_prose(
        "Meta Quest 3S 128GB Virtual Reality Headset Bundle 4.6 out of 5 stars. "
        "(6.5k) $349.99 $349.99 List: $399.00 Save $49.01 with coupon today.")


def test_only_public_http_is_fetched():
    dns = {"example.com": ["93.184.215.14"],
           "rebound.example": ["127.0.0.1"],
           "mixed.example": ["93.184.215.14", "192.168.1.10"]}
    def lookup(host):
        if host not in dns:
            raise OSError("Name or service not known")
        return dns[host]

    real, w._resolve = w._resolve, lookup
    try:
        assert w._public("https://example.com/a")
        # A public-looking name is only as public as what it resolves to.
        assert not w._public("http://rebound.example/")
        assert not w._public("http://mixed.example/")
        assert not w._public("http://no-such-name.example/")
    finally:
        w._resolve = real
    for bad in ("file:///etc/passwd", "http://localhost:8080/", "http://127.0.0.1/",
                "http://192.168.1.1/admin", "http://10.0.0.5/", "ftp://example.com/",
                "http://printer.local/", ""):
        assert not w._public(bad), bad
    assert w.read_article("http://127.0.0.1:11434/api/tags", log=lambda m: None) is None


def test_a_redirect_to_this_machine_is_never_requested():
    class Resp:
        def __init__(self, status, headers):
            self.status_code, self.headers = status, headers

        def close(self):
            pass

    asked = []

    def fake_get(url, **kw):
        asked.append(url)
        assert kw.get("allow_redirects") is False
        return Resp(302, {"Location": "http://127.0.0.1:11434/api/tags"})

    real_get, w.requests.get = w.requests.get, fake_get
    real_dns, w._resolve = w._resolve, lambda host: ["93.184.215.14"]
    try:
        assert w.read_article("https://example.com/a", log=lambda m: None) is None
    finally:
        w.requests.get, w._resolve = real_get, real_dns
    assert asked == ["https://example.com/a"]


class FakeLLM(BaseLLM):
    def __init__(self, answer):
        self.answer, self.calls = answer, []

    def chat(self, messages, **kw):
        self.calls.append((messages, kw))
        return self.answer


def test_brief_is_bounded_and_skip_means_no_story():
    llm = FakeLLM('"The council banned leaf blowers nine to two. See https://x.test/a"')
    out = llm.brief_article("local news", "Council bans leaf blowers", PARA)
    assert out == "The council banned leaf blowers nine to two. See"[:len(out)]
    assert "http" not in out
    messages, kw = llm.calls[0]
    assert kw["think"] is False and kw["label"] == "topic: local news"
    # The page's words are in the user turn, fenced -- never in the system one.
    assert PARA.strip() in messages[1]["content"] and "<article>" in messages[1]["content"]
    assert PARA.strip() not in messages[0]["content"]

    assert FakeLLM("They liked *Salt, Fat* and **Heat**.").brief_article(
        "s", "t", PARA) == "They liked Salt, Fat and Heat."
    assert FakeLLM("SKIP").brief_article("s", "t", PARA) == ""
    assert FakeLLM("Skip.").brief_article("s", "t", PARA) == ""
    long = FakeLLM("One sentence about it. " * 60).brief_article("s", "t", PARA)
    assert 0 < len(long) <= 420 and long.endswith(".")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
