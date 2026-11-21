#!/usr/bin/env python3
"""One-time backfill: post the Nov 17, 2026 RFD Hot Deals to Telegram.

Reads nov17_backfill.json (54 deals still in the Hot Deals forum on
Nov 20, 2026; 2 of the original 56 had been moved out of Hot Deals to
forum 68, so they are not included). Posts oldest first, 3 seconds
apart. This is a one-off, workflow_dispatch only — it is NOT scheduled
and does not touch the normal bot's state.json.
"""
import html
import json
import os
import sys
import time
from pathlib import Path

import requests

SCRIPT_DIR = Path(__file__).resolve().parent


def main() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        sys.exit("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID")
    deals = json.loads((SCRIPT_DIR / "nov17_backfill.json").read_text())
    print(f"Backfill: posting {len(deals)} deals, oldest first", flush=True)
    session = requests.Session()
    posted = 0
    for i, deal in enumerate(deals, 1):
        text = f"<b>{html.escape(deal['title'])}</b>\n{html.escape(deal['url'])}"
        r = session.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML",
                  "disable_web_page_preview": False},
            timeout=20,
        )
        body = r.json()
        if not body.get("ok"):
            print(f"FAILED {i}/{len(deals)} {deal['id']} {deal['title']}: {body}", flush=True)
            raise SystemExit(f"Telegram sendMessage failed: {body}")
        posted += 1
        print(f"Posted {i}/{len(deals)} {deal['id']} | {deal['title']}", flush=True)
        if i < len(deals):
            time.sleep(3)
    print(f"Backfill done: posted {posted}/{len(deals)}", flush=True)


if __name__ == "__main__":
    main()
