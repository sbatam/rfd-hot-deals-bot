# RFD Hot Deals to Telegram

Checks the RedFlagDeals Hot Deals forum (newest threads first) and posts every new thread to a Telegram channel (or group), like this:

> **[Costco] Kirkland Coffee $9.99**
> https://forums.redflagdeals.com/kirkland-coffee-2712346/

It reads RFD's JSON topic feed and falls back to parsing the forum page if the feed isn't available. It remembers which threads it has posted (`state.json`), so nothing goes out twice. The first run only records what's already on the page and posts nothing, so the group isn't flooded with 40 old deals.

## 1. Create the bot and the channel

1. In Telegram, message **@BotFather**, send `/newbot`, and copy the token.
2. Create the channel: pencil/new message icon > **New Channel**. Choose **Public** (gets an @handle, anyone can find it) or **Private** (invite link only).
3. Channel > **Manage Channel > Administrators > Add Admin**, search your bot's @username, and make sure **Post Messages** is on. Bots can only post in channels where they are admins.
4. Set `TELEGRAM_CHAT_ID`:
   - **Public channel:** just use the handle, e.g. `@rfdhotdeals`
   - **Private channel:** post any message in the channel, then run `TELEGRAM_BOT_TOKEN=<token> python rfd_bot.py --chat-id` and copy the ID (starts with `-100`)

Posting to a group works the same way: add the bot to the group and use the group's ID.

## 2. Choose where it runs

### Option A: GitHub Actions (free, checks every ~5 minutes)

1. Push this folder to a new **private** GitHub repo.
2. Repo **Settings > Secrets and variables > Actions**, add `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.
3. **Actions** tab > enable workflows > run "RFD Hot Deals to Telegram" once manually to seed it.

Five minutes is GitHub's minimum interval and runs can lag at busy times. GitHub also pauses scheduled workflows after 60 days with no repo activity; re-enable from the Actions tab if that happens.

### Option B: Any always-on machine (near real time, checks every 60 seconds)

A Raspberry Pi, a home server, or a small VPS.

```bash
pip install -r requirements.txt
cp .env.example .env      # fill in token and chat ID
python rfd_bot.py --loop
```

To keep it running after reboots on Linux, create `/etc/systemd/system/rfd-bot.service`:

```ini
[Unit]
Description=RFD Hot Deals to Telegram
After=network-online.target

[Service]
WorkingDirectory=/home/pi/rfd-bot
ExecStart=/usr/bin/python3 rfd_bot.py --loop
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
```

Then `sudo systemctl enable --now rfd-bot` and watch logs with `journalctl -u rfd-bot -f`.

## Settings

| Variable | Default | What it does |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | required | From @BotFather |
| `TELEGRAM_CHAT_ID` | required | The group's ID |
| `POLL_SECONDS` | 60 | Loop interval (minimum 30) |
| `POST_ON_FIRST_RUN` | 0 | Set to 1 to post the current page on the first run |
| `MAX_POSTS_PER_RUN` | 15 | Safety cap if many threads appear at once (the newest are posted) |
| `STATE_FILE` | state.json | Where posted thread IDs are stored |

## Troubleshooting

- **403 or empty results from RFD:** RFD may block some cloud server IPs. If GitHub Actions gets blocked, run it on a home machine (Option B) instead.
- **"No threads found in forum HTML":** RFD changed its page layout. The selectors are in `parse_html()` in `rfd_bot.py`.
- **Telegram "chat not found":** the chat ID or @handle is wrong, or the bot isn't in the channel.
- **Telegram "not enough rights":** the bot isn't an admin with Post Messages turned on.
