"""Smallcap-bot-agent summary — reads Alpaca account state + local state files.

The Alpaca paper account is shared with retired stock-bot-agent (~$106K equity
in May 2026 because stock-bot accumulated there before retirement). Smallcap-bot
operates within this paper account but at $3K starting-bankroll scale.

State files this reads (lighter than the bot itself; read-only inspector):
  ~/smallcap-bot-agent/state/positions.json     — bot's view of open positions
  ~/smallcap-bot-agent/state/daily_snapshot.json — last EOD snapshot
  ~/smallcap-bot-agent/state/last_run_*.txt     — routine freshness timestamps
  ~/smallcap-bot-agent/state/manual_intervention_log.md — human-edited entries
  ~/smallcap-bot-agent/PAUSED                    — kill switch touch-file (if present)

Returns a dict that mirrors the alpaca_summary.py shape with smallcap-specific
additions. Designed to plug into operator_v2 alongside kalshi_summary and
alpaca_summary.
"""
from __future__ import annotations
import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

SMALLCAP_DIR = Path("/Users/terry/smallcap-bot-agent")


def _file_age_hours(path: Path) -> float | None:
    if not path.exists():
        return None
    age_sec = (datetime.now(timezone.utc)
               - datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)).total_seconds()
    return age_sec / 3600.0


def _read_json_safe(path: Path) -> dict | list | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def get_smallcap_bot_summary(bot_dir: Path = SMALLCAP_DIR) -> dict:
    """Pull smallcap-bot state for operator_v2 ingestion.

    Returns dict with keys:
      bot, equity, cash, positions, alpaca_open_stops, alpaca_open_orders_count,
      bot_positions, last_run_premarket_h_ago, last_run_post_open_h_ago,
      last_run_midday_h_ago, last_run_close_h_ago, paused, manual_intervention_recent

    Notes:
      - equity/cash/positions come from Alpaca via existing alpaca_summary helper
      - bot_positions = bot's own positions.json view (may diverge from Alpaca)
      - last_run_* fields = hours since last fire of each routine (None if missing)
      - alpaca_open_stops = count of open sell-stop orders (positions without stops are anomalies)
    """
    out: dict = {"bot": "smallcap-bot-agent"}

    # 1. Alpaca account view (re-use alpaca_summary logic — same creds, same account)
    try:
        from lib.alpaca_summary import get_stock_bot_summary  # alias by history
        alpaca = get_stock_bot_summary()
        if "error" in alpaca:
            out["error"] = f"alpaca: {alpaca['error']}"
            return out
        out["equity"] = alpaca.get("equity", 0)
        out["cash"] = alpaca.get("cash", 0)
        out["positions"] = alpaca.get("positions", [])
        out["alpaca_open_orders_count"] = alpaca.get("open_orders", 0)
        out["orders_today"] = alpaca.get("orders_today", 0)
    except Exception as e:
        out["error"] = f"alpaca pull failed: {e}"
        return out

    # 2. Bot's own view of positions (may diverge from Alpaca — drift is an anomaly)
    bot_positions = _read_json_safe(bot_dir / "state" / "positions.json")
    out["bot_positions"] = bot_positions if isinstance(bot_positions, dict) else {}

    # 3. Daily snapshot
    snap = _read_json_safe(bot_dir / "state" / "daily_snapshot.json")
    out["daily_snapshot"] = snap if isinstance(snap, dict) else {}

    # 4. Routine freshness — TWO signals per routine
    #    last_run_*_h_ago: state file modified = routine completed its work
    #    last_attempt_*_h_ago: log file modified = cron fired the script
    # The gap matters: post_open.py fires at 9:35 ET but if PAUSED is set
    # it exits silently before writing the state timestamp. last_attempt
    # catches "cron triggered" while last_run catches "work completed."
    # If last_attempt fresh but last_run stale → PAUSED-aborted (not anomaly).
    # If neither fresh → cron didn't fire (real anomaly).
    state_dir = bot_dir / "state"
    logs_dir = bot_dir / "logs"
    for routine in ("premarket", "post_open", "midday", "close"):
        out[f"last_run_{routine}_h_ago"] = _file_age_hours(state_dir / f"last_run_{routine}.txt")
        out[f"last_attempt_{routine}_h_ago"] = _file_age_hours(logs_dir / f"{routine}.log")

    # 5. PAUSED touch-file (kill switch)
    paused_file = bot_dir / "PAUSED"
    out["paused"] = paused_file.exists()
    out["paused_age_hours"] = _file_age_hours(paused_file) if paused_file.exists() else None

    # 6. Manual intervention log recency
    mil = bot_dir / "state" / "manual_intervention_log.md"
    mil_age = _file_age_hours(mil)
    out["manual_intervention_age_hours"] = mil_age
    out["manual_intervention_recent"] = mil_age is not None and mil_age < 24

    # 7. Bot version (read from RULEBOOK.md if present)
    rulebook = bot_dir / "RULEBOOK.md"
    if rulebook.exists():
        try:
            head = rulebook.read_text().splitlines()[:10]
            for line in head:
                if "v0." in line and "—" in line:
                    out["bot_version"] = line.strip().split("—")[0].strip().lstrip("*").strip()
                    break
        except OSError:
            pass

    return out


if __name__ == "__main__":
    import sys

    # Auto-source .env files for testing
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    from operator_v2 import _load_env_files
    _load_env_files()

    summary = get_smallcap_bot_summary()
    print(json.dumps(summary, indent=2, default=str))
