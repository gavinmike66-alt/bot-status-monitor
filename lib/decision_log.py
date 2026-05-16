"""Decision log — append-only structured record of every promotion/demotion/config flip.

Closes the "wait why is BNB locked again" auditability question. Every time
a bot's state changes (asset promoted, demoted, config flipped, PAUSED lifted,
strategy version bumped), append a structured entry to this log with rationale
+ evidence pointer + timestamp.

The log is queryable: "show me the history of BNB promotions" walks the
JSONL and filters by (bot, asset, kind).

Why a separate file from the action queue? Action queue is forward-looking
(proposed actions awaiting Mike's approval). Decision log is backward-looking
(record of what HAS happened, regardless of how it happened — Mike directive,
auto-resolution, or council vote). Different purposes, different shapes.

v0.1: helper + manual backfill of May 16 decisions.
v0.2: integrate with action_queue.resolve_action when resolution='approved'.
v0.3: scrape recent commit messages for decision patterns (auto-capture).
"""
from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

LOG_PATH = Path(__file__).resolve().parent.parent / "state" / "decisions.jsonl"


def log_decision(
    bot: str,
    kind: str,
    asset: Optional[str] = None,
    from_state: Optional[str] = None,
    to_state: Optional[str] = None,
    rationale: str = "",
    evidence: str = "",
    by: str = "mike",
    ts: Optional[str] = None,
) -> dict:
    """Append a decision event.

    Args:
        bot: "kalshi-bot" / "smallcap-bot-agent" / cross-bot
        kind: "promotion" / "demotion" / "config_flip" / "paused_lift" /
              "paused_set" / "strategy_version" / "manual_intervention"
        asset: ticker or asset name (e.g. "15m-DOGE", "BNB"). Optional for
               cross-asset decisions (e.g. global PAPER_MODE flip).
        from_state: prior state ("paper", "live", "v0.3", "PAUSED", etc.)
        to_state: new state
        rationale: why — the actual reason. Free text.
        evidence: pointer to data — commit hash, gate_tracker output, paper
                  performance line, etc.
        by: who decided ("mike", "auto-expiry", "council", "terry", "airy")
        ts: ISO timestamp; defaults to now UTC.
    """
    LOG_PATH.parent.mkdir(exist_ok=True)
    entry = {
        "ts": ts or datetime.now(timezone.utc).isoformat(),
        "bot": bot,
        "kind": kind,
        "asset": asset,
        "from_state": from_state,
        "to_state": to_state,
        "rationale": rationale,
        "evidence": evidence,
        "by": by,
    }
    with open(LOG_PATH, "a") as f:
        f.write(json.dumps(entry) + "\n")
    return entry


def list_decisions(
    bot: Optional[str] = None,
    asset: Optional[str] = None,
    kind: Optional[str] = None,
    limit: int = 50,
) -> list[dict]:
    """Walk the JSONL, filter, return most recent N entries matching."""
    if not LOG_PATH.exists():
        return []
    entries = []
    with open(LOG_PATH) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if bot and e.get("bot") != bot:
                continue
            if asset and e.get("asset") != asset:
                continue
            if kind and e.get("kind") != kind:
                continue
            entries.append(e)
    return entries[-limit:]


if __name__ == "__main__":
    import sys
    # CLI: python -m lib.decision_log [--bot X] [--asset Y] [--kind Z]
    # Pretty-print recent decisions.
    args = {}
    i = 1
    while i < len(sys.argv):
        if sys.argv[i].startswith("--") and i + 1 < len(sys.argv):
            args[sys.argv[i][2:]] = sys.argv[i + 1]
            i += 2
        else:
            i += 1
    entries = list_decisions(**args)
    if not entries:
        print("No decisions logged matching filter.")
    else:
        for e in entries:
            asset_str = f"/{e['asset']}" if e.get('asset') else ""
            print(f"{e['ts'][:19]}  [{e['bot']}{asset_str}] {e['kind']}: "
                  f"{e.get('from_state') or '?'} → {e.get('to_state') or '?'}  "
                  f"by={e['by']}")
            if e.get('rationale'):
                print(f"  why: {e['rationale']}")
            if e.get('evidence'):
                print(f"  evidence: {e['evidence']}")
