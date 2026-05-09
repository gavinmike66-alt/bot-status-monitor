#!/usr/bin/env python3
"""Operator v0.1 dry-run: detect anomalies, persist proposed actions to queue,
emit brief with action IDs.

Run with `python scripts/operator_dry_run.py` from repo root.
Same env vars as status_check.

Difference from dry_run.py:
- Persists proposed actions to state/pending_actions.jsonl (deduplicates by content hash)
- Includes pending action IDs in the brief so Mike can reference them
- Updates ~/reference/heartbeats/operator.txt (if path exists)
"""
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.action_queue import add_action, list_pending_resolved
from lib.alpaca_summary import get_stock_bot_summary
from lib.anomalies import detect_pages, detect_proposed_actions
from lib.kalshi_summary import get_kalshi_summary
from lib.notify import send_jarvis
from lib.vmike_dispatch import dispatch_to_vmike


def synthesize_operator_brief(alpaca: dict, kalshi: dict, pending: list, fresh_pages: list) -> str:
    fire_time = datetime.now(ZoneInfo("America/New_York")).strftime("%H:%M ET")
    lines = [f"🤖 Operator — {fire_time}"]

    # Stock-bot status
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

    # Kalshi status
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

    # Tier D pages (real-money emergencies — surface immediately)
    if fresh_pages:
        lines.append("")
        for p in fresh_pages:
            lines.append(p)

    # Pending actions queue
    if pending:
        lines.append("")
        lines.append(f"📋 Pending actions ({len(pending)}):")
        for entry in pending:
            lines.append(f"• {entry['id']} [Tier {entry['tier']}]: {entry['action'][:120]}")
        lines.append("")
        lines.append("Reply 'go <id>' to authorize. 'skip <id>' to dismiss.")
    else:
        lines.append("")
        lines.append("📋 Pending actions: none.")

    return "\n".join(lines)


def main() -> int:
    print("=== Operator v0.1 dry-run ===\n")

    print("[1/3] Pulling Alpaca summary...")
    alpaca = get_stock_bot_summary()
    print(f"  -> {alpaca}\n")

    print("[2/3] Pulling Kalshi summary...")
    kalshi = get_kalshi_summary()
    print(f"  -> {kalshi}\n")

    print("[3/3] Detecting anomalies + persisting to queue...")
    proposed_actions = detect_proposed_actions(alpaca, kalshi)
    pages = detect_pages(alpaca, kalshi)

    new_count = 0
    for action_text in proposed_actions:
        # action_text from anomalies.py is already a formatted line; pull tier from it heuristically
        tier = "B"
        if "Tier D" in action_text or "🚨" in action_text:
            tier = "D"
        rule_cited = ""
        if "Cite:" in action_text:
            rule_cited = action_text.split("Cite:", 1)[1].strip()
        entry = add_action(action_text, tier, rule_cited=rule_cited)
        if entry is not None:
            new_count += 1
            print(f"  + queued {entry['id']}: {action_text[:80]}")

    print(f"  -> {new_count} new pending actions added to queue\n")

    pending = list_pending_resolved()
    print(f"[STATE] {len(pending)} actions pending in queue\n")

    print("[4/4] Dispatching anomalies to vMike for analysis (if any)...")
    vmike_brief = dispatch_to_vmike(proposed_actions, pages, alpaca, kalshi)
    if vmike_brief.strip():
        print(f"  -> vMike returned {len(vmike_brief)} chars\n")
    else:
        print("  -> No anomalies, no dispatch\n")

    brief = synthesize_operator_brief(alpaca, kalshi, pending, pages) + vmike_brief
    print("=== BRIEF ===")
    print(brief)
    print("=============\n")

    print("Sending to Telegram...")
    ok = send_jarvis(brief)
    print(f"Telegram send: {'OK' if ok else 'FAILED'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
