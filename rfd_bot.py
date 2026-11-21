#!/usr/bin/env python3
"""
RFD Hot Deals -> Telegram

Watches the RedFlagDeals Hot Deals forum (newest threads first) and posts each
new thread's title and link to a Telegram group.

Modes
  python rfd_bot.py            run one check, then exit (for cron / GitHub Actions)
  python rfd_bot.py --loop     check forever every POLL_SECONDS (for a VPS / Pi)
  python rfd_bot.py --chat-id  print chat IDs the bot has seen (setup helper)
  python rfd_bot.py --test     post the newest deal once, to check setup

Config (environment variables or a .env file next to this script)
  TELEGRAM_BOT_TOKEN   required, from @BotFather
  TELEGRAM_CHAT_ID     required, @channelhandle (public) or -100... ID (private channel or group)
  POLL_SECONDS         loop interval, default 60
  STATE_FILE           where seen thread IDs are stored, default state.json
  POST_ON_FIRST_RUN    "1" to post the current deals on the very first run,
                       default "0" (first run only records what's there)
  MAX_POSTS_PER_RUN    max posts per check, default 40 (extras go out next check)
"""

from __future__ import annotations

import html
import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE = "https://forums.redflagdeals.com"
FORUM_ID = 9  # Hot Deals
API_URL = f"{BASE}/api/topics?forum_id={FORUM_ID}&per_page=40&page=1&sort=tt"
HTML_URL = f"{BASE}/hot-deals-f9/?sk=tt&rfd_sk=tt"  # page 1, sorted by newest thread

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
    ),
    "Accept-Language": "en-CA,en;q=0.9",
}

# RFD thread URLs end in "-<numeric id>/", e.g. /costco-some-deal-2712345/
THREAD_HREF = re.compile(r"^(?:https?://forums\.redflagdeals\.com)?/([a-z0-9-]+)-(\d{6,})/?$")

log = logging.getLogger("rfd")
SCRIPT_DIR = Path(__file__).resolve().parent


@dataclass
class Deal:
    id: int
    title: str
    url: str


# ---------------------------------------------------------------- config

def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def env(name: str, default: str | None = None) -> str:
    val = os.environ.get(name, default)
    if val is None or val == "":
        sys.exit(f"Missing required setting: {name}")
    return val


# ---------------------------------------------------------------- fetching

def fetch_from_api(session: requests.Session) -> list[Deal]:
    r = session.get(API_URL, headers={**HEADERS, "Accept": "application/json"}, timeout=20)
    r.raise_for_status()
    data = r.json()
    deals = []
    for t in data.get("topics", []):
        tid = t.get("topic_id") or t.get("id")
        title = (t.get("title") or "").strip()
        path = t.get("web_path") or ""
        if not tid or not title:
            continue
        if t.get("is_sticky") or t.get("sticky") or t.get("is_announcement"):
            continue
        dealer = ((t.get("offer") or {}).get("dealer_name") or "").strip()
        if dealer and not title.lower().startswith(("[" + dealer.lower(), dealer.lower())):
            title = f"[{dealer}] {title}"
        url = path if path.startswith("http") else f"{BASE}{path or f'/-{tid}/'}"
        deals.append(Deal(int(tid), title, url))
    if not deals:
        raise ValueError("API returned no topics")
    return deals


def parse_html(page: str) -> list[Deal]:
    soup = BeautifulSoup(page, "html.parser")
    deals: dict[int, Deal] = {}

    # Thread rows. Skip stickies / announcements, which sit at the top of the list.
    rows = soup.select("li.topic, li.row, .thread_list li, .topiclist li") or [soup]
    for row in rows:
        classes = " ".join(row.get("class", [])) if row is not soup else ""
        if re.search(r"sticky|announce", classes, re.I):
            continue
        link = row.select_one("a.topic_title_link") or row.select_one("h3 a, .topictitle a")
        candidates = [link] if link else row.find_all("a", href=True)
        for a in candidates:
            if not a or not a.get("href"):
                continue
            m = THREAD_HREF.match(a["href"].split("?")[0].split("#")[0])
            if not m:
                continue
            tid = int(m.group(2))
            title = " ".join(a.get_text(" ", strip=True).split())
            if not title or tid in deals:
                continue
            retailer = row.select_one(".topictitle_retailer, .topic_title_retailer") if row is not soup else None
            if retailer:
                r = retailer.get_text(" ", strip=True).strip("[] ")
                if r and r.lower() not in title.lower():
                    title = f"[{r}] {title}"
            deals[tid] = Deal(tid, title, f"{BASE}/{m.group(1)}-{tid}/")
            if link:
                break
    if not deals and rows != [soup]:
        # Row selectors matched the wrong things; scan every link on the page instead
        return parse_html_links(soup)
    return list(deals.values())


def parse_html_links(soup: BeautifulSoup) -> list[Deal]:
    deals: dict[int, Deal] = {}
    for a in soup.find_all("a", href=True):
        m = THREAD_HREF.match(a["href"].split("?")[0].split("#")[0])
        title = " ".join(a.get_text(" ", strip=True).split())
        if m and title and int(m.group(2)) not in deals:
            tid = int(m.group(2))
            deals[tid] = Deal(tid, title, f"{BASE}/{m.group(1)}-{tid}/")
    return list(deals.values())


def fetch_from_html(session: requests.Session) -> list[Deal]:
    r = session.get(HTML_URL, headers={**HEADERS, "Accept": "text/html"}, timeout=20)
    r.raise_for_status()
    deals = parse_html(r.text)
    if not deals:
        raise ValueError("No threads found in forum HTML (layout may have changed)")
    return deals


def fetch_deals(session: requests.Session) -> list[Deal]:
    try:
        return fetch_from_api(session)
    except Exception as e:  # noqa: BLE001
        log.info("JSON feed unavailable (%s), falling back to HTML", e)
    return fetch_from_html(session)


# ---------------------------------------------------------------- telegram

class TelegramError(Exception):
    def __init__(self, code: int, description: str):
        super().__init__(f"Telegram error {code}: {description}")
        self.code, self.description = code, description


def tg(session: requests.Session, token: str, method: str, **params):
    r = session.post(f"https://api.telegram.org/bot{token}/{method}", json=params, timeout=20)
    body = r.json()
    if not body.get("ok"):
        if body.get("error_code") == 429:  # rate limited
            wait = body.get("parameters", {}).get("retry_after", 5)
            log.warning("Telegram rate limit, waiting %ss", wait)
            time.sleep(wait + 1)
            return tg(session, token, method, **params)
        raise TelegramError(int(body.get("error_code") or 0), str(body.get("description") or body))
    return body["result"]


def post_deal(session: requests.Session, token: str, chat_id: str, deal: Deal) -> None:
    text = f"<b>{html.escape(deal.title)}</b>\n{html.escape(deal.url)}"
    tg(session, token, "sendMessage", chat_id=chat_id, text=text,
       parse_mode="HTML", disable_web_page_preview=False)


# ---------------------------------------------------------------- state

def load_state(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError:
            log.warning("State file unreadable, starting fresh")
    return {"seen": [], "initialized": False}


def save_state(path: Path, state: dict) -> None:
    state["seen"] = sorted(set(state["seen"]))[-1000:]  # keep it small
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state))
    tmp.replace(path)


# ---------------------------------------------------------------- main

def check_once(session: requests.Session, cfg: dict) -> int:
    state = load_state(cfg["state_file"])
    seen = set(state["seen"])
    deals = fetch_deals(session)

    new = sorted((d for d in deals if d.id not in seen), key=lambda d: d.id)  # oldest first

    if not state.get("initialized") and not cfg["post_on_first_run"]:
        log.info("First run: recorded %d current threads, nothing posted", len(deals))
        seen.update(d.id for d in deals)
        new_to_post = []
    else:
        # Oldest first. Anything over the cap stays unseen and goes out next run,
        # so no deal is ever skipped.
        new_to_post = new[:cfg["max_posts"]]
        if len(new) > len(new_to_post):
            log.warning("%d new threads, posting the oldest %d now, the rest next run",
                        len(new), len(new_to_post))

    posted = 0
    for d in new_to_post:
        try:
            post_deal(session, cfg["token"], cfg["chat_id"], d)
            posted += 1
            seen.add(d.id)
            log.info("Posted %s | %s", d.id, d.title)
            time.sleep(3)  # stay well under Telegram's limit of 20 posts a minute per chat
        except TelegramError as e:
            if e.code == 400 and "chat" not in e.description.lower():
                # This one message is unpostable (bad formatting etc.). Skip it so it
                # can't block every later deal.
                log.error("Skipping %s, Telegram rejected it: %s", d.id, e.description)
                seen.add(d.id)
                continue
            log.error("Could not post %s: %s", d.id, e)
            break  # channel/token problem: retry this and later ones next check
        except Exception as e:  # noqa: BLE001
            log.error("Could not post %s: %s", d.id, e)
            break  # network problem: retry next check

    log.info("Check done: %d new, %d posted", len(new), posted)
    state["seen"] = list(seen)
    state["initialized"] = True
    save_state(cfg["state_file"], state)
    return posted


def show_chat_ids(session: requests.Session, token: str) -> None:
    updates = tg(session, token, "getUpdates")
    chats = {}
    for u in updates:
        msg = u.get("message") or u.get("my_chat_member") or u.get("channel_post") or {}
        chat = msg.get("chat")
        if chat:
            chats[chat["id"]] = chat.get("title") or chat.get("username") or chat.get("first_name")
    if not chats:
        print("No chats yet. Add the bot to your group, send any message in the group, then run this again.")
    for cid, name in chats.items():
        print(f"{cid}\t{name}")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    load_dotenv(SCRIPT_DIR / ".env")
    session = requests.Session()
    token = env("TELEGRAM_BOT_TOKEN")

    if "--chat-id" in sys.argv:
        show_chat_ids(session, token)
        return

    cfg = {
        "token": token,
        "chat_id": env("TELEGRAM_CHAT_ID"),
        "state_file": Path(os.environ.get("STATE_FILE") or SCRIPT_DIR / "state.json"),
        "post_on_first_run": os.environ.get("POST_ON_FIRST_RUN", "0") == "1",
        "max_posts": int(os.environ.get("MAX_POSTS_PER_RUN", "40")),
    }

    if "--test" in sys.argv:
        # Post the newest deal once to confirm everything works. State is not touched.
        deal = max(fetch_deals(session), key=lambda d: d.id)
        post_deal(session, cfg["token"], cfg["chat_id"], deal)
        log.info("Test post sent: %s", deal.title)
        return

    if "--loop" not in sys.argv:
        check_once(session, cfg)
        return

    interval = max(30, int(os.environ.get("POLL_SECONDS", "60")))
    log.info("Watching RFD Hot Deals every %ss", interval)
    while True:
        try:
            check_once(session, cfg)
        except Exception as e:  # noqa: BLE001
            log.error("Check failed: %s", e)
        time.sleep(interval)


if __name__ == "__main__":
    main()
