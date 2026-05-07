#!/usr/bin/env python3
"""Local dry-run: exercises every module against live state.

Run with `python scripts/dry_run.py` from repo root.
Requires env vars set — same vars Cloud Routine uses.
Sends a Telegram test message to confirm wiring.
"""
import sys
from datetime import datetime
from pathlib import Path

# Allow `from lib import ...` when running from repo root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.alpaca_summary import get_stock_bot_summary
from lib.kalshi_summary import get_kalshi_summary
from lib.notify import send_jarvis


def synthesize_brief(alpaca: dict, kalshi: dict) -> str:
    """Format the 5-line brief."""
    fire_time = datetime.now().strftime("%H:%M ET")
    lines = [f"🤖 Bot Status — {fire_time}"]

    # Stock-bot line
    if "error" in alpaca:
        lines.append(f"Stock-bot: ⚠ {alpaca['error']}")
    else:
        eq = alpaca["equity"]
        n_pos = len(alpaca["positions"])
        orders_today = alpaca.get("orders_today", 0)
        open_orders = alpaca.get("open_orders", 0)
        fire_status = "fired ✓" if orders_today > 0 else "no orders today"
        lines.append(
            f"Stock-bot: ${eq:,.0f} ({n_pos} pos, {open_orders} open). Premarket: {fire_status}."
        )

    # Kalshi line
    if "note" in kalshi:
        lines.append(f"Kalshi: {kalshi['note']}")
    elif "error" in kalshi:
        lines.append(f"Kalshi: ⚠ {kalshi['error']}")
    else:
        bal = kalshi.get("balance", 0)
        age = kalshi.get("last_fill_age_hours")
        n_fills = kalshi.get("fills_24h", 0)
        age_str = f"{age:.1f}h ago" if age is not None else "no recent fills"
        lines.append(f"Kalshi: ${bal:.2f} • Last fill {age_str} • Fills 24h: {n_fills}")

    # Heads-up line
    flags = []
    if "error" not in alpaca:
        for p in alpaca.get("positions", []):
            if p["unrealized_plpc"] < -0.05:
                flags.append(f"⚠ {p['symbol']} {p['unrealized_plpc']*100:.1f}%")
    if "last_fill_age_hours" in kalshi and kalshi["last_fill_age_hours"]:
        if kalshi["last_fill_age_hours"] > 12:
            flags.append(f"⚠ Kalshi silent {kalshi['last_fill_age_hours']:.0f}h")

    if flags:
        lines.append("Heads-up: " + " • ".join(flags))

    return "\n".join(lines)


def main() -> int:
    print("=== bot-status-monitor dry-run ===\n")

    print("[1/2] Pulling Alpaca summary...")
    alpaca = get_stock_bot_summary()
    print(f"  -> {alpaca}\n")

    print("[2/2] Pulling Kalshi summary...")
    kalshi = get_kalshi_summary()
    print(f"  -> {kalshi}\n")

    brief = synthesize_brief(alpaca, kalshi)
    print("=== BRIEF ===")
    print(brief)
    print("=============\n")

    print("Sending to Telegram...")
    ok = send_jarvis(brief)
    print(f"Telegram send: {'OK' if ok else 'FAILED'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
