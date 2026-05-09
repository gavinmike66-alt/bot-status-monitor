"""Anomaly detection + proposed-action surfacing.

Read-only Operator-precursor: detects state deviations against authority bounds
(spec_operator_authority_bounds.md) and proposes actions Mike can ack via Jarvis.

v0.1: surfaces proposed actions in the brief. Does NOT execute.
v0.2 (later): execution layer for Tier A/B actions.
"""
from datetime import datetime, timedelta, timezone
from typing import List, Tuple

# Authority-bounds thresholds (per spec_operator_authority_bounds.md v0.1)
KALSHI_WR_DEMOTE_THRESHOLD = 0.55          # Tier B: trailing-50 WR < 55% → propose demote
KALSHI_SILENT_HOURS_FLAG = 12               # Heads-up only
KALSHI_SILENT_HOURS_PROPOSE = 24            # Tier B: propose investigation
STOCK_BOT_SILENT_DAYS_PROPOSE = 3           # Tier B: propose investigation
STOCK_BOT_POSITION_DRAWDOWN_FLAG = -0.05    # Heads-up only
STOCK_BOT_POSITION_DRAWDOWN_PROPOSE = -0.08 # Tier B: propose stop tightening
STOCK_BOT_DAILY_DRAWDOWN_PAGE = -0.05       # Tier D: page Mike (5% daily drawdown from peak)


def detect_proposed_actions(alpaca: dict, kalshi: dict) -> List[Tuple[str, str, str]]:
    """Return list of (text, tier, cite) tuples for proposed actions.

    text: formatted action line for the brief
    tier: "A" | "B" | "C" — explicit, no substring guessing downstream
    cite: rule reference (e.g. "spec_operator_authority_bounds.md §Tier B")

    Mike can ack proposed actions via Jarvis reply (handled by future extension).
    """
    actions = []
    cite_b = "spec_operator_authority_bounds.md §Tier B"

    # --- Stock-bot ---
    if "error" not in alpaca:
        # Stock-bot silent for too long
        # NOTE: orders_today=0 once isn't a problem; persistent silence is.
        # v0.1 detection: just flag if today's fire produced no orders AND it's mid-day or later.
        # v0.2 will track silence streak across fires via state file.
        now_utc = datetime.now(timezone.utc)
        is_market_hours = 13 <= now_utc.hour <= 21  # rough US market hours UTC
        if alpaca.get("orders_today", 0) == 0 and is_market_hours:
            actions.append((
                "→ Stock-bot 0 orders today during market hours. "
                "If pattern persists 3+ days, propose: review selectivity gate (Tier B).",
                "B",
                f"{cite_b} + Wed transcript flag",
            ))

        # Position drawdown deep enough to propose stop tightening
        for p in alpaca.get("positions", []):
            if p["unrealized_plpc"] < STOCK_BOT_POSITION_DRAWDOWN_PROPOSE:
                actions.append((
                    f"→ {p['symbol']} at {p['unrealized_plpc']*100:.1f}% unrealized. "
                    f"Propose: review stop placement (Tier B).",
                    "B",
                    cite_b,
                ))

    # --- Kalshi ---
    if "error" not in kalshi and "note" not in kalshi:
        age = kalshi.get("last_fill_age_hours")
        if age is not None and age > KALSHI_SILENT_HOURS_PROPOSE:
            actions.append((
                f"→ Kalshi silent {age:.0f}h. "
                f"Propose: check selectivity gate, verify bot health, consider asset rotation (Tier B).",
                "B",
                f"{cite_b} + project_kalshi_live_universe_may2.md",
            ))

        # Per-asset WR demotion check would go here once Kalshi creds + per-asset stats are wired.
        # v0.2 candidate.

    return actions


def detect_pages(alpaca: dict, kalshi: dict) -> List[str]:
    """Return list of Tier D page-Mike-immediately items.

    These are real-money emergencies. Operator does NOT act on its own; Mike must intervene.
    Surfacing in the brief with 🚨 prefix; future Operator extension will send dedicated PAGE alert.
    """
    pages = []

    # Stock-bot 5% daily drawdown from peak — needs daily-snapshot peak tracking, deferred to v0.2

    # Kalshi balance crash
    # Need balance-history tracking; deferred to v0.2

    # Auth errors persistent
    if "error" in alpaca and "auth" in alpaca["error"].lower():
        pages.append(f"🚨 Alpaca auth error: {alpaca['error']}. Page Mike (Tier D).")
    if "error" in kalshi and "auth" in str(kalshi.get("error", "")).lower():
        pages.append(f"🚨 Kalshi auth error: {kalshi['error']}. Page Mike (Tier D).")

    return pages
