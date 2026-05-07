"""Read stock-bot-agent state files directly from GitHub raw."""
import json
import os
from typing import Optional

import requests

REPO = "gavinmike66-alt/stock-bot-agent"
RAW_BASE = f"https://raw.githubusercontent.com/{REPO}"


def _fetch(path: str, branch: str = "main") -> Optional[str]:
    """Fetch a single file from GitHub raw. Returns None on failure."""
    url = f"{RAW_BASE}/{branch}/{path}"
    try:
        r = requests.get(url, timeout=10)
        if r.status_code != 200:
            return None
        return r.text
    except Exception:
        return None


def get_stock_bot_state() -> dict:
    """Pull state files from stock-bot-agent repo and summarize."""
    out = {}

    # Council decisions — last entry
    cd = _fetch("state/council_decisions.jsonl", branch="main")
    if cd:
        lines = [l for l in cd.strip().splitlines() if l.strip()]
        if lines:
            try:
                last = json.loads(lines[-1])
                out["last_council_decision"] = {
                    "ticker": last.get("ticker", "?"),
                    "rating": last.get("rating", "?"),
                    "timestamp": last.get("timestamp", "?"),
                }
                out["council_decisions_count"] = len(lines)
            except Exception:
                out["last_council_decision"] = {"error": "parse failed"}
        else:
            out["council_decisions_count"] = 0
            out["last_council_decision"] = None
    else:
        out["last_council_decision"] = {"error": "fetch failed (file may not exist)"}

    # Last run timestamps
    for routine in ("premarket", "midday", "close"):
        ts = _fetch(f"state/last_run_{routine}.txt", branch="claude/watchdog-state")
        if ts:
            out[f"last_run_{routine}"] = ts.strip()
        else:
            out[f"last_run_{routine}"] = None

    # Reflection log — last 3 entries
    refl = _fetch("state/reflection_log.md", branch="main")
    if refl:
        # Split on H2 headers (## YYYY-MM-DD)
        entries = [e.strip() for e in refl.split("\n## ") if e.strip()]
        if entries:
            out["last_reflection_count"] = len(entries) - 1  # first entry is header
            out["last_reflection_3"] = entries[-3:] if len(entries) >= 3 else entries[1:]
    else:
        out["last_reflection_3"] = []

    # Daily snapshot
    snap = _fetch("state/daily_snapshot.json", branch="main")
    if snap:
        try:
            out["daily_snapshot"] = json.loads(snap)
        except Exception:
            out["daily_snapshot"] = {"error": "parse failed"}

    return out
