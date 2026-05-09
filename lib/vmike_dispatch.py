"""Operator → vMike dispatch (Ship 1 per vMike's May 9 plan).

When Operator detects anomalies, dispatches the anomaly + relevant context to
Claude (with about_me.md as system prompt = vMike's voice + judgment) for
analysis. Returns formatted Jarvis-ready brief.

Reuses the lightweight messages.create() call rather than Managed Agents
sessions — no $0.08/hr fee, same voice profile, simpler runtime. The full
Virtual Mike Managed Agent (sync chat) is for Mike's interactive `vm` CLI
calls; this is the async equivalent inside the Operator routine.

Failure modes baked in (per vMike's success criteria):
- If anomaly list is empty, skip dispatch entirely
- If API call fails, return raw anomalies + error note (don't silent skip)
- Cap response length implicitly via system prompt (terse rules)
"""
import os
from pathlib import Path

try:
    import anthropic
except ImportError:
    anthropic = None

ABOUT_ME_PATH = Path(__file__).resolve().parent.parent / "about_me.md"
MODEL = "claude-opus-4-7"
MAX_TOKENS = 1500  # ~500-1000 words target


def dispatch_to_vmike(anomalies: list, pages: list, alpaca: dict, kalshi: dict) -> str:
    """Call Claude with vMike's voice/judgment; return analysis brief.

    Returns markdown string ready to append to the Telegram brief.
    Returns empty string if no anomalies + no pages (skip dispatch).
    Returns error note if API call fails (never silent skip).
    """
    if not anomalies and not pages:
        return ""

    if anthropic is None:
        return "\n⚠ vMike dispatch: anthropic SDK not installed."

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return "\n⚠ vMike dispatch: ANTHROPIC_API_KEY not set in env."

    try:
        system_prompt = ABOUT_ME_PATH.read_text()
    except Exception as e:
        return f"\n⚠ vMike dispatch: cannot read about_me.md ({e})."

    # Build the user message with anomaly payload
    user_lines = ["Operator detected the following anomalies during this fire. Apply your voice/judgment rules from system prompt — verify, recommend, cite. Output ONE concise brief (<300 words). Format:"]
    user_lines.append("**Anomalies:**")
    for a in anomalies:
        user_lines.append(f"- {a}")
    if pages:
        user_lines.append("\n**Tier D pages:**")
        for p in pages:
            user_lines.append(f"- {p}")

    user_lines.append("\n**Current bot state at fire time:**")
    if "error" not in alpaca:
        user_lines.append(f"Stock-bot: equity ${alpaca.get('equity', 0):,.0f}, {len(alpaca.get('positions', []))} positions, {alpaca.get('open_orders', 0)} open orders, orders_today={alpaca.get('orders_today', 0)}")
        for p in alpaca.get("positions", []):
            user_lines.append(f"  - {p['symbol']}: qty={p['qty']}, unrealized_plpc={p['unrealized_plpc']*100:+.1f}%")
    else:
        user_lines.append(f"Stock-bot: ⚠ {alpaca['error']}")

    if "note" in kalshi:
        user_lines.append(f"Kalshi: {kalshi['note']}")
    elif "error" in kalshi:
        user_lines.append(f"Kalshi: ⚠ {kalshi['error']}")
    else:
        user_lines.append(f"Kalshi: balance=${kalshi.get('balance', 0):.2f}, fills_24h={kalshi.get('fills_24h', 0)}, last_fill_age_h={kalshi.get('last_fill_age_hours', 'n/a')}")

    user_lines.append("\n**Your output format:** Brief markdown. State the ONE highest-priority anomaly + your verified read + recommended action (Tier A/B/C/D per spec_operator_authority_bounds.md). If multiple anomalies, rank them. If you'd need more data to decide, say what to verify and skip recommending. Concede uncertainty cleanly. <300 words.")

    user_msg = "\n".join(user_lines)

    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=system_prompt,
            messages=[{"role": "user", "content": user_msg}],
        )
        text_blocks = [b.text for b in response.content if hasattr(b, "text")]
        analysis = "\n".join(text_blocks).strip()
        return f"\n\n📊 **vMike analysis:**\n{analysis}"
    except Exception as e:
        return f"\n⚠ vMike dispatch failed: {e}\nAnomalies still surfaced above; investigate manually."
