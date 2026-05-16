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
    # Try Markdown first (preserves vMike's formatting). On 400 parse error
    # (unbalanced ** from LLM output is common), retry as plain text.
    # Robust: NEVER let a formatting issue block an alert from reaching Mike.
    for parse_mode in ("Markdown", None):
        try:
            payload = {"chat_id": chat_id, "text": text}
            if parse_mode:
                payload["parse_mode"] = parse_mode
            r = requests.post(url, data=payload, timeout=10)
            if r.status_code == 200:
                return True
            # 400 with parse error → fallback to plain text on next iteration
            if r.status_code == 400 and parse_mode and "parse" in r.text.lower():
                print(f"[notify] Markdown parse failed, retrying plain text")
                continue
            print(f"[notify] HTTP {r.status_code}: {r.text[:200]}")
            return False
        except Exception as e:
            print(f"[notify] exception (parse_mode={parse_mode}): {e}")
            if parse_mode is None:
                return False
    return False
