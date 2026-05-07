"""Telegram notify helpers — mirror of stock-bot-agent pattern."""
import os
import requests


def send_jarvis(text: str) -> bool:
    """Send a message to Mike's Jarvis Telegram bot.

    Returns True on success, False on any failure (logs but doesn't raise).
    """
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print(f"[notify] missing TELEGRAM creds — skipping. text was: {text[:120]}")
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        r = requests.post(
            url,
            data={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
            timeout=10,
        )
        if r.status_code != 200:
            print(f"[notify] HTTP {r.status_code}: {r.text[:200]}")
            return False
        return True
    except Exception as e:
        print(f"[notify] exception: {e}")
        return False
