#!/usr/bin/env python3
"""Local dry-run: exercises every module against live state.

Run with `python scripts/dry_run.py` from repo root.
Requires env vars set (or .env loaded) — same vars Cloud Routine uses.
Sends a Telegram test message to confirm wiring.
"""
import sys
from pathlib import Path

# Allow `from lib import ...` when running from repo root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.alpaca_summary import get_stock_bot_summary
from lib.git_state_reader import get_stock_bot_state
from lib.kalshi_summary import get_kalshi_summary
from lib.notify import send_jarvis


def synthesize_brief(stock_state: dict, alpaca: dict, kalshi: dict) -> str:
    """Format the 5-line brief."""
    from datetime import datetime
    fire_time = datetime.now().strftime("%H:%M ET")
    lines = [f"🤖 Bot Status — {fire_time}"]

    # Stock-bot line
    if "error" in alpaca:
        lines.append(f"Stock-bot: ⚠ Alpaca: {alpaca['error']}")
    else:
        eq = alpaca["equity"]
        n_pos = len(alpaca["positions"])
        last_premkt = stock_state.get("last_run_premarket", "?")
        last_council = stock_state.get("last_council_decision") or {}
        last_council_str = (
            f"{last_council.get('ticker','?')} {last_council.get('rating','?')}"
            if "error" not in last_council else "?"
        )
        n_refl = stock_state.get("last_reflection_count", 0)
        lines.append(
            f"Stock-bot: ${eq:,.0f} ({n_pos}/4 pos). Premarket: {last_premkt}. "
            f"Last council: {last_council_str}. Reflections: {n_refl}."
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
                flags.append(f"⚠ {p['symbol']} -{abs(p['unrealized_plpc'])*100:.1f}%")
        if alpaca.get("open_orders", 0) > 0:
            flags.append(f"{alpaca['open_orders']} open orders")
    if "last_fill_age_hours" in kalshi and kalshi["last_fill_age_hours"]:
        if kalshi["last_fill_age_hours"] > 12:
            flags.append(f"⚠ Kalshi silent {kalshi['last_fill_age_hours']:.0f}h")

    if flags:
        lines.append("Heads-up: " + " • ".join(flags))
    else:
        lines.append("Heads-up: nothing flagged.")

    return "\n".join(lines)


def main() -> int:
    print("=== bot-status-monitor dry-run ===\n")

    print("[1/3] Pulling stock-bot state from GitHub...")
    stock_state = get_stock_bot_state()
    print(f"  -> {stock_state}\n")

    print("[2/3] Pulling Alpaca summary...")
    alpaca = get_stock_bot_summary()
    print(f"  -> {alpaca}\n")

    print("[3/3] Pulling Kalshi summary...")
    kalshi = get_kalshi_summary()
    print(f"  -> {kalshi}\n")

    brief = synthesize_brief(stock_state, alpaca, kalshi)
    print("=== BRIEF ===")
    print(brief)
    print("=============\n")

    print("Sending to Telegram...")
    ok = send_jarvis(brief)
    print(f"Telegram send: {'OK' if ok else 'FAILED'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
