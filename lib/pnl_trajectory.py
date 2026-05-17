"""Per-bot P&L trajectory framing — lifetime / 7d / 24h / peak-to-current.

The memory `feedback_kalshi_pnl_trajectory_framing.md` (May 16) named this
as a load-bearing META failure: day-over-day balance deltas are meaningless
without trajectory context. Mike anchors on peak-to-current, not yesterday
vs today.

This module computes the trajectory for the cross-stream view artifact.
Different shapes per bot:
  - kalshi-bot: read trades.csv (live trades, profit column) + balance_log.csv
                + live API balance. Compute realized P&L lifetime/7d/24h +
                balance peak from balance_log.
  - smallcap-bot: read trades.csv (likely sparse, just opened May 18) +
                  daily_snapshot.json history + Alpaca account equity. The
                  peak is shared with stock-bot history; current equity is
                  the Alpaca value.
  - biotech: P&L not applicable (on-demand research). Returns counts only.

All numbers are computed at read time from source — no caching, per the
`feedback_regrep_dont_reassert.md` rule.
"""
from __future__ import annotations
import csv
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path


def kalshi_trajectory(bot_dir: Path = Path("/Users/terry/kalshi-bot"),
                       current_balance: float | None = None) -> dict:
    """Compute Kalshi P&L trajectory: lifetime / 7d / 24h / peak-to-current.

    Args:
        bot_dir: kalshi-bot repo path
        current_balance: live API balance in USD; if None, falls back to last
                         balance_log entry. Caller should pass live value when
                         possible (avoids stale read).

    Returns dict with:
        lifetime_pnl, lifetime_trades
        pnl_7d, trades_7d
        pnl_24h, trades_24h
        peak_balance, peak_date_iso
        current_balance
        drawdown_from_peak (current - peak)
    """
    ny = timezone(timedelta(hours=-4))
    now = datetime.now(ny)
    t24 = now - timedelta(hours=24)
    t7d = now - timedelta(days=7)

    out = {"bot": "kalshi-bot"}

    # 1. Realized P&L from trades.csv (LIVE trades only — paper_trades.csv separate)
    trades_csv = bot_dir / "trades.csv"
    if not trades_csv.exists():
        out["error"] = "trades.csv not found"
        return out

    lifetime_pnl = 0.0
    lifetime_n = 0
    pnl_7d = 0.0
    n_7d = 0
    pnl_24h = 0.0
    n_24h = 0

    with open(trades_csv) as f:
        for r in csv.DictReader(f):
            try:
                ts = datetime.fromisoformat(r["time"])
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=ny)
            except (ValueError, KeyError):
                continue
            try:
                pnl = float(r.get("profit") or 0)
            except (ValueError, TypeError):
                continue
            lifetime_pnl += pnl
            lifetime_n += 1
            if ts >= t7d:
                pnl_7d += pnl
                n_7d += 1
            if ts >= t24:
                pnl_24h += pnl
                n_24h += 1

    out["lifetime_pnl"] = round(lifetime_pnl, 2)
    out["lifetime_trades"] = lifetime_n
    out["pnl_7d"] = round(pnl_7d, 2)
    out["trades_7d"] = n_7d
    out["pnl_24h"] = round(pnl_24h, 2)
    out["trades_24h"] = n_24h

    # 2. Peak balance from balance_log.csv (EOD records)
    balance_log = bot_dir / "balance_log.csv"
    peak = 0.0
    peak_date = ""
    if balance_log.exists():
        with open(balance_log) as f:
            for line in f:
                parts = line.strip().split(",")
                if len(parts) < 3:
                    continue
                try:
                    bal = float(parts[2])
                except ValueError:
                    continue
                if bal > peak:
                    peak = bal
                    peak_date = parts[0]
    # Include current_balance in peak comparison so today's intraday peak counts
    if current_balance is not None:
        if current_balance > peak:
            peak = current_balance
            peak_date = now.date().isoformat() + " (intraday)"
        out["current_balance"] = round(current_balance, 2)
    else:
        out["current_balance"] = None
    out["peak_balance"] = round(peak, 2)
    out["peak_date_iso"] = peak_date
    if current_balance is not None and peak > 0:
        out["drawdown_from_peak"] = round(current_balance - peak, 2)
    else:
        out["drawdown_from_peak"] = None

    return out


def smallcap_trajectory(bot_dir: Path = Path("/Users/terry/smallcap-bot-agent"),
                         current_equity: float | None = None) -> dict:
    """Compute smallcap-bot P&L trajectory.

    Smallcap-bot is paper-only (May 17). Trades reside on Alpaca. Account-level
    equity history is in daily_snapshot.json files (if rotated) or the current
    snapshot. For MVP this returns:
      - current_equity (from caller; live Alpaca pull)
      - last_snapshot_equity (from daily_snapshot.json)
      - bot v0.4 just shipped, expects first trades Monday — lifetime/peak
        framing is mostly informational until then.
    """
    out = {"bot": "smallcap-bot-agent"}
    snap_path = bot_dir / "state" / "daily_snapshot.json"
    if snap_path.exists():
        try:
            snap = json.loads(snap_path.read_text())
            out["last_snapshot_equity"] = snap.get("equity")
            out["last_snapshot_ts"] = snap.get("ts")
            out["last_snapshot_positions"] = len(snap.get("positions", []))
        except (json.JSONDecodeError, OSError):
            pass
    if current_equity is not None:
        out["current_equity"] = round(current_equity, 2)
        # Drawdown vs last snapshot (intra-period if same day)
        if "last_snapshot_equity" in out:
            out["change_since_last_snapshot"] = round(current_equity - out["last_snapshot_equity"], 2)
    out["_note"] = "v0.4 first fire Monday May 18; lifetime trajectory framing becomes meaningful once trades accumulate"
    return out


def biotech_trajectory() -> dict:
    """Biotech-research-bot — P&L not applicable. Returns activity metrics."""
    from lib.biotech_summary import get_biotech_summary
    s = get_biotech_summary()
    return {
        "bot": "biotech-research-bot",
        "mode": "on-demand",
        "session_count": s.get("session_count", 0),
        "sessions_last_7d": s.get("sessions_last_7d", 0),
        "last_session_age_days": s.get("last_session_age_days"),
        "status": s.get("status"),
        "_note": "P&L not applicable — on-demand research agent (~$0.60/session)",
    }
