#!/usr/bin/env python3
"""
notify_telegram.py — send a planned workout to the athlete via a Telegram bot.

Self-contained (stdlib only) so it works in the headless nightly job. Reads bot
credentials from ~/.claude/skills/fitness-copilot/profile/.telegram (gitignored),
a JSON file: {"token": "...", "chat_id": "..."}.

One-time setup:
  1. In Telegram, open @BotFather -> /newbot -> follow prompts -> copy the bot TOKEN.
  2. Open your new bot and send it any message (e.g. "hi") so it has an update to read.
  3. Run:  python3 notify_telegram.py --setup "<TOKEN>"
     This saves the token, auto-detects your chat_id from that message, and saves both.
     (If it can't find a chat_id, message the bot again and re-run --setup.)

Usage:
  python3 notify_telegram.py --html <file.html> --caption "Push day is ready 💪"
  python3 notify_telegram.py --message "text only"
  python3 notify_telegram.py --latest --caption "..."   # send newest file in workouts/
The token/chat_id are never printed.
"""

import argparse
import glob
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

CRED_FILE = os.path.expanduser("~/.claude/skills/fitness-copilot/profile/.telegram")
WORKOUTS_DIR = os.path.expanduser("~/.claude/skills/fitness-copilot/workouts")
API = "https://api.telegram.org/bot{token}/{method}"


def load_creds():
    if not os.path.exists(CRED_FILE):
        sys.exit(f"ERROR: no Telegram creds at {CRED_FILE}. Run --setup \"<TOKEN>\" first.")
    with open(CRED_FILE) as f:
        c = json.load(f)
    if not c.get("token") or not c.get("chat_id"):
        sys.exit("ERROR: Telegram creds missing token or chat_id. Re-run --setup.")
    return c


def tg_get(token, method, params=None):
    url = API.format(token=token, method=method)
    if params:
        url += "?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        sys.exit(f"ERROR: Telegram {method} {e.code}: {e.read().decode()[:300]}")
    except urllib.error.URLError as e:
        sys.exit(f"ERROR: could not reach Telegram ({e.reason}).")


def send_message(creds, text):
    res = tg_get(creds["token"], "sendMessage", {
        "chat_id": creds["chat_id"], "text": text[:4096],
        "disable_web_page_preview": "true",
    })
    if not res.get("ok"):
        sys.exit(f"ERROR: sendMessage failed: {res}")
    print("✓ sent message")


def send_document(creds, path, caption=""):
    if not os.path.exists(path):
        sys.exit(f"ERROR: file not found: {path}")
    with open(path, "rb") as f:
        file_bytes = f.read()
    filename = os.path.basename(path)
    boundary = "----fitnesscopilotTGboundary7MA4YWxk"
    crlf = b"\r\n"
    parts = []

    def field(name, value):
        parts.append(b"--" + boundary.encode() + crlf)
        parts.append(f'Content-Disposition: form-data; name="{name}"'.encode() + crlf + crlf)
        parts.append(str(value).encode() + crlf)

    field("chat_id", creds["chat_id"])
    if caption:
        field("caption", caption[:1024])
    parts.append(b"--" + boundary.encode() + crlf)
    parts.append(
        f'Content-Disposition: form-data; name="document"; filename="{filename}"'.encode()
        + crlf
    )
    parts.append(b"Content-Type: text/html" + crlf + crlf)
    parts.append(file_bytes + crlf)
    parts.append(b"--" + boundary.encode() + b"--" + crlf)
    body = b"".join(parts)

    url = API.format(token=creds["token"], method="sendDocument")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            res = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        sys.exit(f"ERROR: sendDocument {e.code}: {e.read().decode()[:300]}")
    except urllib.error.URLError as e:
        sys.exit(f"ERROR: could not reach Telegram ({e.reason}).")
    if not res.get("ok"):
        sys.exit(f"ERROR: sendDocument failed: {res}")
    print(f"✓ sent {filename}")


def setup(token):
    token = token.strip()
    res = tg_get(token, "getUpdates")
    if not res.get("ok"):
        sys.exit(f"ERROR: token rejected by Telegram: {res}")
    # Bind ONLY to a private (1:1) chat. Binding to the "most recent chat" could
    # latch onto a group the bot was added to — then everyone in that group receives
    # your plans and can drive the bot. Require a private DM.
    chat_id = None
    saw_group = False
    for upd in reversed(res.get("result", [])):
        msg = upd.get("message") or upd.get("edited_message") or {}
        chat = msg.get("chat") or {}
        if chat.get("id") is None:
            continue
        if chat.get("type") == "private":
            chat_id = chat["id"]
            break
        saw_group = True
    if chat_id is None:
        if saw_group:
            sys.exit("ERROR: only saw group chats. Message the bot in a DIRECT (1:1) chat, "
                     "not a group, then re-run --setup.")
        sys.exit("ERROR: no chat found. Open your bot in Telegram and send it a direct "
                 "message (e.g. 'hi'), then re-run --setup.")
    with open(CRED_FILE, "w") as f:
        json.dump({"token": token, "chat_id": str(chat_id)}, f)
    os.chmod(CRED_FILE, 0o600)
    print(f"✓ Telegram configured (chat_id saved, perms 600). Sending a test message…")
    send_message({"token": token, "chat_id": str(chat_id)},
                 "✅ Fitness Copilot is connected. Your planned workouts will arrive here.")


def latest_html():
    files = glob.glob(os.path.join(WORKOUTS_DIR, "*.html"))
    if not files:
        sys.exit("ERROR: no workout HTML found in workouts/.")
    return max(files, key=os.path.getmtime)


def main():
    p = argparse.ArgumentParser(description="Send a workout to Telegram.")
    p.add_argument("--setup", metavar="TOKEN", help="one-time setup with a bot token")
    p.add_argument("--html", help="path to an HTML workout to send as a document")
    p.add_argument("--latest", action="store_true", help="send the newest file in workouts/")
    p.add_argument("--message", help="send a plain text message")
    p.add_argument("--caption", default="", help="caption for the document")
    args = p.parse_args()

    if args.setup:
        setup(args.setup)
        return
    creds = load_creds()
    if args.message:
        send_message(creds, args.message)
    path = args.html or (latest_html() if args.latest else None)
    if path:
        send_document(creds, path, args.caption or "Your workout is ready.")
    if not args.message and not path:
        p.error("nothing to send: use --html, --latest, or --message")


if __name__ == "__main__":
    main()
