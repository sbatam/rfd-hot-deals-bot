import json, sys, tempfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rfd_bot  # noqa: E402

rfd_bot.time.sleep = lambda s: None

HTML = """
<ul class="topiclist">
 <li class="row topic sticky"><h3 class="topictitle"><a class="topic_title_link" href="/hot-deals-rules-1000001/">Rules</a></h3></li>
 <li class="row topic"><h3 class="topictitle"><a class="topictitle_retailer" href="/x">Costco</a>
   <a class="topic_title_link" href="/kirkland-coffee-2712346/">Kirkland Coffee $9.99 &amp; up</a></h3></li>
 <li class="row topic"><h3 class="topictitle"><a class="topic_title_link" href="/airpods-pro-2712345/?page=2">AirPods Pro $199</a></h3></li>
</ul>"""


def test_html_parse():
    deals = rfd_bot.parse_html(HTML)
    ids = {d.id: d for d in deals}
    assert 1000001 not in ids, "sticky should be skipped"
    assert ids[2712346].title == "[Costco] Kirkland Coffee $9.99 & up"
    assert ids[2712345].url == "https://forums.redflagdeals.com/airpods-pro-2712345/"
    # Fallback when row classes don't exist
    bare = '<div><a href="/foo-bar-2700001/">Foo bar</a><a href="/about/">About</a></div>'
    assert [d.id for d in rfd_bot.parse_html(bare)] == [2700001]


class Resp:
    def __init__(self, payload=None, text="", status=200):
        self.payload, self.text, self.status_code = payload, text, status
    def json(self):
        if self.payload is None:
            raise ValueError("not json")
        return self.payload
    def raise_for_status(self):
        if self.status_code >= 400:
            raise rfd_bot.requests.HTTPError(str(self.status_code))


def make_session(topics, fail_send_ids=()):
    s = mock.Mock()
    sent = []
    s.get.side_effect = lambda url, **kw: Resp({"topics": topics})
    def post(url, json=None, **kw):
        if any(str(i) in json["text"] for i in fail_send_ids):
            return Resp({"ok": False, "error_code": 400, "description": "boom"})
        sent.append(json["text"])
        return Resp({"ok": True, "result": {}})
    s.post.side_effect = post
    return s, sent


def topic(i, title, dealer=None):
    t = {"topic_id": i, "title": title, "web_path": f"/deal-{i}/"}
    if dealer:
        t["offer"] = {"dealer_name": dealer}
    return t


def test_flow():
    with tempfile.TemporaryDirectory() as d:
        cfg = {"token": "T", "chat_id": "-100", "state_file": Path(d) / "s.json",
               "post_on_first_run": False, "max_posts": 15}
        base = [topic(2700001, "Old deal"), topic(2700002, "Old deal 2")]

        s, sent = make_session(base)
        assert rfd_bot.check_once(s, cfg) == 0 and sent == []          # first run seeds only

        s, sent = make_session(base)
        assert rfd_bot.check_once(s, cfg) == 0                          # nothing new

        new = [topic(2700004, "TV <4K>", "Best Buy"), topic(2700003, "Socks")] + base
        s, sent = make_session(new)
        assert rfd_bot.check_once(s, cfg) == 2
        assert "Socks" in sent[0] and "[Best Buy] TV &lt;4K&gt;" in sent[1]   # oldest first, escaped
        assert "https://forums.redflagdeals.com/deal-2700004/" in sent[1]

        s, sent = make_session(new)
        assert rfd_bot.check_once(s, cfg) == 0                          # no duplicates

        # Telegram failure: deal stays unseen and is retried next time
        newer = [topic(2700005, "Retry me")] + new
        s, sent = make_session(newer, fail_send_ids=[2700005])
        assert rfd_bot.check_once(s, cfg) == 0
        s, sent = make_session(newer)
        assert rfd_bot.check_once(s, cfg) == 1 and "Retry me" in sent[0]

        # Burst over the cap: only newest N posted, rest marked seen
        cfg["max_posts"] = 2
        burst = [topic(2710000 + i, f"B{i}") for i in range(5)] + newer
        s, sent = make_session(burst)
        assert rfd_bot.check_once(s, cfg) == 2 and "B3" in sent[0] and "B4" in sent[1]
        s, sent = make_session(burst)
        assert rfd_bot.check_once(s, cfg) == 0


def test_api_falls_back_to_html():
    s = mock.Mock()
    s.get.side_effect = [Resp(None, status=404), Resp(text=HTML)]
    deals = rfd_bot.fetch_deals(s)
    assert {d.id for d in deals} == {2712345, 2712346}


if __name__ == "__main__":
    test_html_parse(); test_flow(); test_api_falls_back_to_html()
    print("all tests passed")
