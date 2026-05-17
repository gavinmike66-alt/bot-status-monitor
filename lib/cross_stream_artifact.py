"""Cross-stream queryable view artifact (Mike build directive May-17 2026).

Generates a single vault markdown file that:
  - Frames each bot's P&L lifetime / 7d / 24h / peak-to-current
  - Reports health (procs / last-fill-age / paused / tripwire / routine freshness)
  - Folds in active anomaly findings as a single "anything wrong / what
    needs Mike" synthesis line at the top
  - Is read-only — does not touch trading paths

Scope per Mike directive: Kalshi (15m + hourly) + smallcap-bot-agent +
biotech-research-bot ONLY. Roth, personal spec, BBG income explicitly OUT.

Output: ~/reference/dashboards/cross_stream_view.md (vault-tracked, agent-queryable)

Generated every 4h by operator_v2 alongside the Jarvis brief send.
"""
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

VAULT_DASHBOARD_DIR = Path("/Users/terry/reference/dashboards")
ARTIFACT_PATH = VAULT_DASHBOARD_DIR / "cross_stream_view.md"


def _fmt_money(n: float | None, default: str = "?") -> str:
    if n is None:
        return default
    sign = "+" if n >= 0 else ""
    return f"{sign}${n:,.2f}"


def _fmt_amount(n: float | None, default: str = "?") -> str:
    if n is None:
        return default
    return f"${n:,.2f}"


def _proc_status(uptime_hint: str | None) -> str:
    return "🟢" if uptime_hint else "❓"


def synthesize_findings_line(actions: list[tuple[str, str, str]]) -> str:
    """The single 'anything wrong / what needs Mike' synthesis line.

    Rules:
      - 0 actions → 'All bots healthy. No items requiring Mike.'
      - 1-3 high/medium → 'N items waiting on Mike: <ids>'
      - >3 → 'N items (top: <topic>)'
      - Watch-only (Tier C) → 'N watch items accumulating, no Mike action'
    """
    if not actions:
        return "All bots healthy. No items requiring Mike."
    b_count = sum(1 for _, t, _ in actions if t == "B")
    c_count = sum(1 for _, t, _ in actions if t == "C")
    if b_count == 0:
        return f"{c_count} watch item{'s' if c_count != 1 else ''} accumulating (no Mike action)."
    if b_count <= 3:
        return f"{b_count} item{'s' if b_count != 1 else ''} waiting on Mike{f' (+{c_count} watch)' if c_count else ''}."
    # Try to identify the most common topic
    return f"{b_count} items waiting on Mike{f' (+{c_count} watch)' if c_count else ''} — review Jarvis queue."


def render_artifact(kalshi_summary: dict,
                      smallcap_summary: dict,
                      biotech_summary: dict,
                      kalshi_pnl: dict,
                      smallcap_pnl: dict,
                      biotech_pnl: dict,
                      actions: list[tuple[str, str, str]],
                      pending_count: int) -> str:
    """Render the markdown artifact text. Caller writes to disk."""
    ny = ZoneInfo("America/New_York")
    now = datetime.now(ny)
    synthesis = synthesize_findings_line(actions)

    L = []
    L.append(f"# Cross-stream bot view")
    L.append(f"")
    L.append(f"_Generated {now.strftime('%Y-%m-%d %H:%M ET')} by operator_v2 every 4h. "
             f"Read-only surfacing layer — never touches trading paths._")
    L.append(f"")
    L.append(f"## Synthesis")
    L.append(f"")
    L.append(f"**{synthesis}**")
    L.append(f"")
    L.append(f"Pending action queue: {pending_count} item{'s' if pending_count != 1 else ''} "
             f"({'reply via Jarvis `go <id>` / `skip <id>`' if pending_count else 'queue clear'}).")
    L.append(f"")

    # ============== Kalshi ==============
    L.append(f"---")
    L.append(f"")
    L.append(f"## 🟢 Kalshi (15m + hourly)")
    L.append(f"")
    bal = kalshi_summary.get("balance") if "error" not in kalshi_summary else None
    fills = kalshi_summary.get("fills_24h", "?")
    last_fill = kalshi_summary.get("last_fill_age_hours")
    last_fill_str = f"{last_fill:.1f}h ago" if last_fill is not None else "no recent fills"

    L.append(f"**Live balance:** {_fmt_amount(bal)} | **Fills last 24h:** {fills} | **Last fill:** {last_fill_str}")
    L.append(f"")
    L.append(f"**P&L trajectory** (never anchor day-over-day; always peak-to-current):")
    L.append(f"- Lifetime: {_fmt_money(kalshi_pnl.get('lifetime_pnl'))} on {kalshi_pnl.get('lifetime_trades', '?')} live trades")
    L.append(f"- Last 7d:  {_fmt_money(kalshi_pnl.get('pnl_7d'))} on {kalshi_pnl.get('trades_7d', '?')} trades")
    L.append(f"- Last 24h: {_fmt_money(kalshi_pnl.get('pnl_24h'))} on {kalshi_pnl.get('trades_24h', '?')} trades")
    peak = kalshi_pnl.get('peak_balance')
    peak_date = kalshi_pnl.get('peak_date_iso', '?')
    dd = kalshi_pnl.get('drawdown_from_peak')
    L.append(f"- Peak balance: {_fmt_amount(peak)} on {peak_date}")
    L.append(f"- Drawdown from peak: {_fmt_money(dd)}")
    L.append(f"")

    # ============== Smallcap ==============
    L.append(f"---")
    L.append(f"")
    paused_tag = " 🔴 PAUSED" if smallcap_summary.get("paused") else ""
    ver = smallcap_summary.get("bot_version", "?")
    L.append(f"## 🟢 smallcap-bot-agent ({ver}){paused_tag}")
    L.append(f"")
    eq = smallcap_summary.get("equity")
    n_pos = len(smallcap_summary.get("positions", []))
    o_today = smallcap_summary.get("orders_today", 0)
    L.append(f"**Account equity:** {_fmt_amount(eq)} | **Open positions:** {n_pos} | **Orders today:** {o_today}")
    L.append(f"")
    L.append(f"**Routine freshness** (last fire age in hours):")
    for r in ("premarket", "post_open", "midday", "close"):
        age = smallcap_summary.get(f"last_run_{r}_h_ago")
        attempt = smallcap_summary.get(f"last_attempt_{r}_h_ago")
        if age is None and attempt is None:
            tag = "never fired"
        elif age is None:
            tag = f"attempted {attempt:.1f}h ago (state file missing — PAUSED-aborted?)"
        else:
            tag = f"{age:.1f}h ago"
        L.append(f"- {r}: {tag}")
    L.append(f"")
    sn = smallcap_pnl
    if "_note" in sn:
        L.append(f"> {sn['_note']}")
        L.append(f"")

    # ============== Biotech ==============
    L.append(f"---")
    L.append(f"")
    status_emoji = {"active": "🟢", "idle": "🟡", "dormant": "🔴", "never-run": "⚪"}.get(
        biotech_pnl.get("status", "active"), "🟢"
    )
    L.append(f"## {status_emoji} biotech-research-bot (on-demand, Anthropic MA)")
    L.append(f"")
    age = biotech_pnl.get('last_session_age_days')
    age_str = f"{age:.1f} days ago" if age is not None else "never"
    L.append(f"**Last session:** {age_str} | **Sessions (last 7d):** {biotech_pnl.get('sessions_last_7d', 0)} | "
             f"**Lifetime sessions:** {biotech_pnl.get('session_count', 0)}")
    L.append(f"")
    L.append(f"> P&L framing N/A — research agent (~$0.60/session). Memory store accumulates context.")
    L.append(f"")

    # ============== Pending actions (if any) ==============
    if actions:
        L.append(f"---")
        L.append(f"")
        L.append(f"## Pending actions ({len(actions)})")
        L.append(f"")
        for text, tier, _cite in actions[:10]:
            L.append(f"- [Tier {tier}] {text[:200]}")
        if len(actions) > 10:
            L.append(f"- _…and {len(actions)-10} more_")
        L.append(f"")
        L.append(f"Resolve via Jarvis: `go <id>` (approve) / `skip <id>` (dismiss).")
        L.append(f"")

    L.append(f"---")
    L.append(f"")
    L.append(f"_Out of scope per `investor_one_pager.md`: Roth portfolio, personal spec trades, BBG income. "
             f"This view covers bot income streams only._")
    L.append(f"")
    L.append(f"_Generated by `bot-status-monitor/lib/cross_stream_artifact.py`. "
             f"Source data refreshed at write time — no caching._")
    L.append(f"")

    return "\n".join(L)


def write_artifact(content: str) -> Path:
    VAULT_DASHBOARD_DIR.mkdir(parents=True, exist_ok=True)
    ARTIFACT_PATH.write_text(content)
    return ARTIFACT_PATH
