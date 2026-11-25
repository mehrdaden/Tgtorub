#!/usr/bin/env python3
"""Telegram connectivity check.  Usage:
   sudo systemctl stop tg2rub
   sudo -u tg2rub /opt/tg2rub/app/venv/bin/python /opt/tg2rub/app/diag.py [OTHER_BOT_TOKEN]
Tests (1) the bot token over HTTPS and (2) the MTProto bot login the program really uses."""
import asyncio
import json
import os
import sys
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

BASE = Path(__file__).resolve().parent
os.chdir(BASE)
os.umask(0o077)
load_dotenv(BASE / ".env")
token = sys.argv[1] if len(sys.argv) > 1 else os.environ["BOT_TOKEN"]
api_id, api_hash = int(os.environ["API_ID"]), os.environ["API_HASH"]


def clean(e):
    return ("%s: %s" % (type(e).__name__, e)).replace(token, "<TOKEN>")[:300]


print("1) HTTPS getMe (token valid + api.telegram.org reachable)")
try:
    r = json.load(urllib.request.urlopen("https://api.telegram.org/bot%s/getMe" % token, timeout=20))
    print("   ->", ("OK @" + r["result"]["username"]) if r.get("ok") else r)
except Exception as e:
    print("   -> FAILED", clean(e))

print("2) MTProto bot login (Telethon)")


async def main():
    from telethon import TelegramClient
    c = TelegramClient(str(BASE / "tg_diag"), api_id, api_hash, request_retries=2, connection_retries=2,
                       raise_last_call_error=True)
    try:
        await asyncio.wait_for(c.start(bot_token=token), 120)
        print("   -> OK @" + (await c.get_me()).username)
    except Exception as e:
        print("   -> FAILED", clean(e))
    finally:
        await c.disconnect()

asyncio.run(main())
for f in BASE.glob("tg_diag*"):
    f.unlink()
