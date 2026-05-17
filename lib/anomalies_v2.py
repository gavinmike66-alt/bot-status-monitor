"""Anomaly detection v2 — extended classes that catch today's META gaps.

Prototype for the cross-bot META layer. Lives alongside anomalies.py (v1)
during the test period. If 14-day kalshi run validates the design, this
replaces v1 and ports to smallcap-bot via per-bot adapters.

The agent-design call: expected_state.json is the source of truth.
v2 implements that pattern. Hardcoded thresholds (v1) → declarative state.

Classes v2 catches that v1 misses:
  1. BEHAVIOR-ANOMALY: asset configured live, zero trades in window
  2. DECISION-TRIGGER: paper criteria cleared, propose live promotion
  3. CONFIG-DRIFT: stale touch-file (PAUSED, TRIPWIRE_BASELINE_TS) past TTL
  4. FRAMING-CONTEXT: balance delta vs realized-P&L-net-of-pending-escrow

Author: Terry, 2026-05-16 (in response to today's "you shouldnt have to ask" pattern).
"""
from __future__ import annotations
import csv
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Expected-state per bot (declarative source of truth)
# ---------------------------------------------------------------------------

@dataclass
class AssetExpectation:
    """What a single asset/mode should look like if healthy."""
    name: str                     # e.g. "15m-DOGE"
    enabled: bool                  # is this expected to be trading live?
    min_trades_per_7d: int = 0     # below this counts as "silent"
    grace_period_hours: int = 48   # ignore silence checks for N hours after promotion
    promoted_at: str | None = None # ISO timestamp of last promotion (for grace calc)
    paper_period_start_iso: str | None = None  # override: anchor for paper-perf eval
    paper_promotion_criteria: dict[str, Any] = field(default_factory=dict)


@dataclass
class BotExpectation:
    """Full expected-state for one bot."""
    bot_name: str                  # "kalshi-bot" / "smallcap-bot"
    assets: list[AssetExpectation]
    touch_files: dict[str, int]    # filename → max-age-hours (0 = no TTL)
    realized_pnl_anchor: str       # "peak" | "lifetime" | "session-base"
    paper_promotion_rules: dict[str, Any]  # global rules: e.g. min WR, min n


def _load_expected_state(bot_dir: Path) -> dict | None:
    """Load expected_state.json from a bot's repo directory.

    Returns the parsed dict, or None if the file doesn't exist or fails to
    parse. v0.10 — moved from hardcoded BotExpectation to data file so
    threshold tweaks don't require code changes.
    """
    path = bot_dir / "expected_state.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def kalshi_expected_state(bot_dir: Path = Path("/Users/terry/kalshi-bot")) -> BotExpectation:
    """Load kalshi-bot expected state from JSON file with code-default fallback.

    v0.10 (May-16): data-file-first. If file missing or malformed, fall back
    to the previous hardcoded defaults (defense-in-depth — META layer keeps
    working if the JSON file gets corrupted).
    """
    cfg = _load_expected_state(bot_dir)
    if cfg is None:
        # Hardcoded defaults — used only if JSON file missing/malformed
        ny = timezone(timedelta(hours=-4))
        today_iso = datetime(2026, 5, 16, 16, 50, tzinfo=ny).isoformat()
        return BotExpectation(
            bot_name="kalshi-bot",
            assets=[
                AssetExpectation("15m-DOGE", enabled=True, min_trades_per_7d=5, promoted_at=today_iso),
                AssetExpectation("15m-HYPE", enabled=True, min_trades_per_7d=5, promoted_at=today_iso),
                AssetExpectation("15m-XRP",  enabled=True, min_trades_per_7d=5, promoted_at=today_iso),
                AssetExpectation("15m-BTC",  enabled=True, min_trades_per_7d=5, promoted_at=today_iso),
                AssetExpectation("15m-ETH",  enabled=True, min_trades_per_7d=5, promoted_at=today_iso),
                AssetExpectation("15m-BNB",  enabled=False),
                AssetExpectation("15m-SOL",  enabled=False),
                AssetExpectation("hourly-BTC", enabled=True, min_trades_per_7d=20),
                AssetExpectation("hourly-ETH", enabled=True, min_trades_per_7d=10),
                AssetExpectation("hourly-XRP", enabled=True, min_trades_per_7d=0),
                AssetExpectation("hourly-DOGE",enabled=True, min_trades_per_7d=0),
                AssetExpectation("hourly-BNB", enabled=False),
                AssetExpectation("hourly-SOL", enabled=False),
            ],
            touch_files={"PAUSED": 24, "TRIPWIRE_BASELINE_TS": 0},
            realized_pnl_anchor="peak",
            paper_promotion_rules={"min_wr": 0.70, "min_n": 15, "min_days": 5, "min_ev_per_trade": 0.0},
        )

    # Parse from JSON
    assets = []
    for a in cfg.get("assets", []):
        assets.append(AssetExpectation(
            name=a["name"],
            enabled=a.get("enabled", False),
            min_trades_per_7d=a.get("min_trades_per_7d", 0),
            grace_period_hours=a.get("grace_period_hours", 48),
            promoted_at=a.get("promoted_at"),
            paper_period_start_iso=a.get("paper_period_start_iso"),
        ))

    # touch_files can be either {filename: hours} or {filename: {"max_age_hours": N}}
    touch_files: dict[str, int] = {}
    for fname, val in cfg.get("touch_files", {}).items():
        if isinstance(val, dict):
            touch_files[fname] = val.get("max_age_hours", 0)
        else:
            touch_files[fname] = val

    return BotExpectation(
        bot_name=cfg.get("bot_name", "kalshi-bot"),
        assets=assets,
        touch_files=touch_files,
        realized_pnl_anchor=cfg.get("realized_pnl_anchor", "peak"),
        paper_promotion_rules=cfg.get("paper_promotion_rules", {
            "min_wr": 0.70, "min_n": 15, "min_days": 5, "min_ev_per_trade": 0.0
        }),
    )


# ---------------------------------------------------------------------------
# Anomaly detection — diff actual against expected
# ---------------------------------------------------------------------------

def detect_behavior_anomalies(expected: BotExpectation, trades_csv: Path) -> list[dict]:
    """Class 1: asset configured live but zero/sub-threshold trades in 7d.

    Honors per-asset grace_period_hours (avoid alerting on newly-promoted assets).
    """
    ny = timezone(timedelta(hours=-4))
    now = datetime.now(ny)
    t7d = now - timedelta(days=7)

    counts: dict[str, int] = {}
    if trades_csv.exists():
        with open(trades_csv) as f:
            for r in csv.DictReader(f):
                try:
                    ts = datetime.fromisoformat(r["time"])
                except (ValueError, KeyError):
                    continue
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=ny)
                if ts < t7d:
                    continue
                counts[r.get("bot", "")] = counts.get(r.get("bot", ""), 0) + 1

    findings: list[dict] = []
    for a in expected.assets:
        if not a.enabled:
            continue
        # Grace period check — newly-promoted assets get a window
        if a.promoted_at:
            promoted = datetime.fromisoformat(a.promoted_at)
            if (now - promoted).total_seconds() < a.grace_period_hours * 3600:
                continue  # still in grace; suppress
        actual_n = counts.get(a.name, 0)
        if actual_n < a.min_trades_per_7d:
            findings.append({
                "class": "BEHAVIOR_ANOMALY",
                "asset": a.name,
                "expected_min": a.min_trades_per_7d,
                "actual_7d": actual_n,
                "severity": "high" if actual_n == 0 else "medium",
                "proposed_action": (
                    f"Investigate {a.name}: configured live, "
                    f"{actual_n} trades in 7d (expected ≥{a.min_trades_per_7d}). "
                    f"Likely cause: signal filter, Kalshi liquidity, or learner block."
                ),
            })
    return findings


def _wilson_lower(wins: int, n: int, z: float = 1.96) -> float:
    """Wilson score interval lower bound at 95% CI (z=1.96 default).

    More robust than point-estimate w/n on small samples. At n=15 with 12 wins:
      point estimate: 80.0%
      Wilson 95% lower: 54.8%
    Point estimate would flip on one trade; Wilson is stable.

    Args:
        wins: number of wins
        n: total trades (must be >0)
        z: standard normal critical value (1.96 = 95%, 1.645 = 90%)
    """
    if n <= 0:
        return 0.0
    p = wins / n
    denom = 1 + (z ** 2) / n
    center = p + (z ** 2) / (2 * n)
    spread = z * ((p * (1 - p) / n + (z ** 2) / (4 * n ** 2)) ** 0.5)
    return (center - spread) / denom


def detect_decision_triggers(expected: BotExpectation,
                               paper_trades_csv: Path,
                               actual_paper_status: dict[str, bool]) -> list[dict]:
    """Class 2: paper criteria cleared on an asset → propose live promotion.

    actual_paper_status: {asset_name: is_currently_paper} from config inspection.

    Cutoff anchoring (robustness, May-16 ETH-miss lesson):
      Rolling `now - N days` windows are brittle — they slide forward each
      hour, silently dropping early-window trades. Two fixes:
      1. Default global cutoff anchored to midnight 5d ago (day-boundary).
      2. Per-asset paper_period_start_iso override (set when an asset is
         demoted/promoted to reset its evaluation window cleanly).
    """
    ny = timezone(timedelta(hours=-4))
    now = datetime.now(ny)
    rules = expected.paper_promotion_rules
    # Day-boundary anchor: midnight, min_days ago. Stable across the day.
    default_cutoff_date = (now - timedelta(days=rules.get("min_days", 5))).date()
    default_cutoff = datetime.combine(default_cutoff_date, datetime.min.time(), tzinfo=ny)

    # Per-asset override map (built from expectation list)
    per_asset_cutoff: dict[str, datetime] = {}
    for ae in expected.assets:
        if ae.paper_period_start_iso:
            per_asset_cutoff[ae.name] = datetime.fromisoformat(ae.paper_period_start_iso)

    # Per-asset paper stats post-cutoff (asset-specific or default)
    stats: dict[str, dict] = {}
    if paper_trades_csv.exists():
        with open(paper_trades_csv) as f:
            for r in csv.DictReader(f):
                try:
                    ts = datetime.fromisoformat(r["time"])
                except (ValueError, KeyError):
                    continue
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=ny)
                bot = r.get("bot", "")
                # Use per-asset cutoff if set, else default
                this_cutoff = per_asset_cutoff.get(bot, default_cutoff)
                if ts < this_cutoff:
                    continue
                s = stats.setdefault(bot, {"w": 0, "l": 0, "pnl": 0.0})
                if r.get("result") == "WIN":
                    s["w"] += 1
                elif r.get("result") == "LOSS":
                    s["l"] += 1
                try:
                    s["pnl"] += float(r.get("profit") or 0)
                except ValueError:
                    pass

    # Gate config — supports both point-estimate (legacy) and Wilson CI lower-bound
    # for robustness. If wilson_lower_floor is set in rules, use that AS the gate
    # instead of min_wr point estimate. min_wr stays as a watch-tier threshold.
    wilson_floor = rules.get("wilson_lower_floor")  # e.g. 0.60 = "Wilson 95% lower bound ≥ 60%"

    findings: list[dict] = []
    for asset, s in stats.items():
        if not actual_paper_status.get(asset, False):
            continue  # asset is already live; nothing to propose
        n = s["w"] + s["l"]
        if n < 5:
            continue  # too small for any meaningful evaluation
        wr_point = s["w"] / n
        wr_wilson = _wilson_lower(s["w"], n)
        ev = s["pnl"] / n
        meets_size = n >= rules["min_n"]
        meets_ev = ev >= rules["min_ev_per_trade"]
        # Two ways to clear the gate:
        #   Wilson mode (preferred): wilson_lower ≥ wilson_floor + meets_size + meets_ev
        #   Point mode (legacy):     wr_point ≥ min_wr + meets_size + meets_ev
        gate_cleared_wilson = (
            wilson_floor is not None and wr_wilson >= wilson_floor
            and meets_size and meets_ev
        )
        gate_cleared_point = wr_point >= rules["min_wr"] and meets_size and meets_ev

        if gate_cleared_wilson or gate_cleared_point:
            gate_via = "Wilson 95% CI" if gate_cleared_wilson else "point estimate"
            findings.append({
                "class": "DECISION_TRIGGER",
                "asset": asset,
                "wr": round(wr_point * 100, 1),
                "wr_wilson_low": round(wr_wilson * 100, 1),
                "ev_per_trade": round(ev, 2),
                "n": n,
                "gate_via": gate_via,
                "severity": "medium",
                "proposed_action": (
                    f"Promote {asset} from paper → live: "
                    f"{wr_point*100:.1f}% WR (Wilson 95% lower {wr_wilson*100:.1f}%) / "
                    f"+${ev:.2f} EV / n={n} clears the gate ({gate_via}). "
                    f"Single-asset promotion, never-stack-compliant."
                ),
            })
        elif (wr_point >= rules["min_wr"] and meets_ev) or (n >= rules["min_n"] and wr_wilson >= 0.50):
            # Watch tier — approaching the gate but not crossed yet
            # Two paths: (a) WR good but n too small, (b) n good but WR borderline
            why = []
            if n < rules["min_n"]:
                why.append(f"n={n} (need ≥{rules['min_n']})")
            if wilson_floor is not None and wr_wilson < wilson_floor:
                why.append(f"Wilson low {wr_wilson*100:.1f}% (need ≥{wilson_floor*100:.0f}%)")
            elif wr_point < rules["min_wr"]:
                why.append(f"WR {wr_point*100:.1f}% (need ≥{rules['min_wr']*100:.0f}%)")
            findings.append({
                "class": "DECISION_WATCH",
                "asset": asset,
                "wr": round(wr_point * 100, 1),
                "wr_wilson_low": round(wr_wilson * 100, 1),
                "ev_per_trade": round(ev, 2),
                "n": n,
                "blockers": why,
                "severity": "watch",
                "proposed_action": (
                    f"Watch {asset} — approaching paper→live gate: "
                    f"{wr_point*100:.1f}% WR (Wilson {wr_wilson*100:.1f}%) / "
                    f"+${ev:.2f} EV / n={n}. Blockers: {', '.join(why)}. "
                    f"No action proposed yet."
                ),
            })
    return findings


def detect_config_drift(expected: BotExpectation, bot_dir: Path) -> list[dict]:
    """Class 3: stale touch files past TTL (PAUSED, etc.)."""
    findings: list[dict] = []
    now = datetime.now(timezone.utc)
    for fname, ttl_hours in expected.touch_files.items():
        path = bot_dir / fname
        if not path.exists():
            continue
        if ttl_hours == 0:
            continue
        age = now - datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        if age > timedelta(hours=ttl_hours):
            findings.append({
                "class": "CONFIG_DRIFT",
                "file": fname,
                "age_hours": round(age.total_seconds() / 3600, 1),
                "ttl_hours": ttl_hours,
                "severity": "high",
                "proposed_action": (
                    f"Touch-file {fname} is {age.total_seconds()/3600:.1f}h old "
                    f"(TTL {ttl_hours}h). Likely a protective gate that has "
                    f"outlived its triggering condition. Propose: investigate cause, "
                    f"lift if condition cleared."
                ),
            })
    return findings


def run_kalshi(bot_dir: Path = Path("/Users/terry/kalshi-bot")) -> dict:
    """One-shot diff for kalshi-bot. Returns all findings."""
    import sys
    sys.path.insert(0, str(bot_dir))
    import config

    expected = kalshi_expected_state()

    actual_paper_status = {}
    for n, a in config.ASSETS.items():
        if a.get("series_15m"):
            actual_paper_status[f"15m-{n}"] = (
                a.get("paper_only", False) or config.PAPER_MODE_15M
            )
        if a.get("series_hourly"):
            actual_paper_status[f"hourly-{n}"] = (
                a.get("paper_only", False)
                or a.get("hourly_paper_only", False)
                or config.PAPER_MODE
            )

    findings = (
        detect_behavior_anomalies(expected, bot_dir / "trades.csv")
        + detect_decision_triggers(expected, bot_dir / "paper_trades.csv", actual_paper_status)
        + detect_config_drift(expected, bot_dir)
    )
    return {
        "bot": expected.bot_name,
        "ts": datetime.now(timezone.utc).isoformat(),
        "findings": findings,
        "summary": {
            "total": len(findings),
            "behavior_anomaly": sum(1 for f in findings if f["class"] == "BEHAVIOR_ANOMALY"),
            "decision_trigger": sum(1 for f in findings if f["class"] == "DECISION_TRIGGER"),
            "config_drift": sum(1 for f in findings if f["class"] == "CONFIG_DRIFT"),
        },
    }


# ---------------------------------------------------------------------------
# Smallcap-bot-agent — cross-bot extension (May-16 Phase 4)
# ---------------------------------------------------------------------------

def _last_expected_weekday_fire(routine_hour_et: int, routine_min_et: int = 0) -> datetime:
    """Return the most recent weekday-at-routine-time in the past (ET).

    Used to detect missed routine fires while honoring weekend schedule.
    On Saturday/Sunday: walks back to Friday's fire. On Monday 11am if
    bot didn't fire at 8:30: returns Monday 8:30 ET.
    """
    ny = timezone(timedelta(hours=-4))
    now = datetime.now(ny)
    d = now
    while True:
        if d.weekday() < 5:
            fire = d.replace(hour=routine_hour_et, minute=routine_min_et,
                              second=0, microsecond=0)
            if fire < now:
                return fire
        d = d - timedelta(days=1)


def smallcap_expected_state(bot_dir: Path = Path("/Users/terry/smallcap-bot-agent")) -> dict:
    """Load smallcap-bot expected_state.json with code-default fallback.

    v0.10: data-driven thresholds. JSON file at bot_dir/expected_state.json
    is the source of truth; falls back to safe defaults if missing.
    """
    cfg = _load_expected_state(bot_dir) or {}
    # Defaults — used as base, overridden by JSON if present
    return {
        "routines": cfg.get("routines", [
            {"name": "premarket", "hour_et": 8,  "minute_et": 30, "grace_hours_after_fire": 1.5},
            {"name": "post_open", "hour_et": 9,  "minute_et": 35, "grace_hours_after_fire": 1.0},
            {"name": "midday",    "hour_et": 12, "minute_et": 0,  "grace_hours_after_fire": 1.0},
            {"name": "close",     "hour_et": 15, "minute_et": 45, "grace_hours_after_fire": 1.0},
        ]),
        "touch_files": cfg.get("touch_files", {"PAUSED": {"max_age_hours": 24}}),
        "cost_basis_drift_usd": cfg.get("thresholds", {}).get("cost_basis_drift_usd", 0.01),
        "manual_intervention_recent_hours": cfg.get("thresholds", {}).get("manual_intervention_recent_hours", 24),
    }


def detect_smallcap_anomalies(summary: dict, expected: dict | None = None) -> list[dict]:
    """Smallcap-bot-specific anomaly detection.

    Operates on the dict returned by smallcap_summary.get_smallcap_bot_summary().
    Thresholds come from expected_state.json (per-bot data-driven config).

    Classes:
      1. ROUTINE_FIRE_MISSED: weekday-aware check per routine in expected.routines
      2. POSITION_WITHOUT_STOP: May-13 QUBT/OTO incident class
      3. COST_BASIS_DRIFT: state.entry_price ≠ Alpaca avg_entry_price > threshold
      4. CONFIG_DRIFT: touch-file older than per-file TTL
      5. MANUAL_INTERVENTION_RECENT: human edited the log within recent window
    """
    if expected is None:
        expected = smallcap_expected_state()
    findings: list[dict] = []
    ny = timezone(timedelta(hours=-4))
    now = datetime.now(ny)

    if "error" in summary:
        findings.append({
            "class": "SMALLCAP_SUMMARY_ERROR",
            "severity": "high",
            "proposed_action": (
                f"smallcap-bot summary failed: {summary['error']}. "
                f"Investigate Alpaca creds + bot state file accessibility."
            ),
        })
        return findings

    # --- 1. Routine fire missed (data-driven from expected.routines)
    # Two-signal check: last_attempt (log file mtime = cron fired) vs
    # last_run (state file = work completed). If attempt is fresh but run is
    # stale, the cron fired but the routine exited via PAUSED — not anomaly.
    # If BOTH are stale past expected_age + grace, cron itself didn't fire — real anomaly.
    for r in expected.get("routines", []):
        name = r["name"]
        hh = r["hour_et"]
        mm = r.get("minute_et", 0)
        grace_hours = r.get("grace_hours_after_fire", 1.0)
        age_key = f"last_run_{name}_h_ago"
        attempt_key = f"last_attempt_{name}_h_ago"
        last_expected = _last_expected_weekday_fire(hh, mm)
        # Compute expected_age = how many hours ago the fire should have happened
        expected_age = (now - last_expected).total_seconds() / 3600
        actual_age = summary.get(age_key)
        # Only alert if (a) routine has never run OR (b) it was supposed to fire
        # at least `grace_hours` ago AND state shows it didn't.
        if expected_age < grace_hours:
            continue  # too soon to know — give the routine its grace window
        actual_age = summary.get(age_key)
        attempt_age = summary.get(attempt_key)
        # Two-signal check:
        #   attempt fresh (cron fired within last expected_age window) → no anomaly,
        #     even if last_run is stale (PAUSED-aborted is valid silent exit)
        #   attempt stale OR missing → cron didn't fire → real anomaly
        attempt_fresh = attempt_age is not None and attempt_age <= expected_age + 1.0
        if attempt_fresh:
            continue  # cron fired; if state file stale, bot exited via PAUSED — fine
        if actual_age is None and attempt_age is None:
            findings.append({
                "class": "ROUTINE_FIRE_MISSED",
                "routine": name,
                "expected_fire": last_expected.isoformat(),
                "severity": "high",
                "proposed_action": (
                    f"smallcap-bot {name} routine has NEVER fired (no log file, "
                    f"no state file). Expected last fire at "
                    f"{last_expected.strftime('%a %H:%M ET')}. "
                    f"Check cron + venv + smallcap-bot daemon."
                ),
            })
        elif actual_age is not None and actual_age > expected_age + 1.0:
            findings.append({
                "class": "ROUTINE_FIRE_MISSED",
                "routine": name,
                "actual_age_hours": round(actual_age, 1),
                "expected_age_hours": round(expected_age, 1),
                "severity": "high",
                "proposed_action": (
                    f"smallcap-bot {name} routine missed its last "
                    f"weekday fire: state is {actual_age:.1f}h old, expected "
                    f"≤{expected_age:.1f}h, log mtime {attempt_age:.1f}h ago. "
                    f"Cron may have fired but routine isn't writing state. "
                    f"Check cron + venv + daemon."
                ),
            })

    # --- 2. Position without stop (May-13 QUBT/OTO class — load-bearing)
    positions = summary.get("positions", [])
    open_orders_count = summary.get("alpaca_open_orders_count", 0)
    if positions and open_orders_count == 0:
        # Positions exist but ZERO open orders → no stops armed. Critical.
        for p in positions:
            findings.append({
                "class": "POSITION_WITHOUT_STOP",
                "symbol": p.get("symbol", "?"),
                "qty": p.get("qty", "?"),
                "severity": "high",
                "proposed_action": (
                    f"smallcap-bot has open {p.get('symbol', '?')} position "
                    f"({p.get('qty', '?')} sh) with NO open orders armed. "
                    f"This is the May-13 QUBT/OTO-silent-fail class. "
                    f"Arm GTC stop immediately or close position."
                ),
            })

    # --- 2b. Cost-basis drift (May-13 OTO root cause — load-bearing)
    # If state's entry_price diverges from Alpaca's avg_entry_price by >$0.01,
    # all downstream math is wrong (stops, rungs, time stop). The smallcap-bot's
    # midday/close routines now sync via update_entry_price (commit 75fc065),
    # but if post_open fails and midday/close haven't fired yet, drift sits
    # un-detected. This check catches the window in between routines.
    bot_positions = summary.get("bot_positions", {}) or {}
    for p in positions:
        symbol = p.get("symbol")
        if not symbol or symbol not in bot_positions:
            continue
        try:
            alpaca_entry = float(p.get("avg_entry_price", 0))
            bot_entry = float(bot_positions[symbol].get("entry_price", 0))
        except (TypeError, ValueError):
            continue
        if alpaca_entry <= 0 or bot_entry <= 0:
            continue
        drift = abs(alpaca_entry - bot_entry)
        drift_threshold = expected.get("cost_basis_drift_usd", 0.01)
        if drift > drift_threshold:
            findings.append({
                "class": "COST_BASIS_DRIFT",
                "symbol": symbol,
                "alpaca_entry": alpaca_entry,
                "bot_entry": bot_entry,
                "drift_usd": round(drift, 4),
                "severity": "high",
                "proposed_action": (
                    f"smallcap-bot {symbol}: state.entry_price=${bot_entry:.4f} "
                    f"diverges from Alpaca avg_entry_price=${alpaca_entry:.4f} "
                    f"by ${drift:.4f}. All downstream math (stops, rungs, time "
                    f"stop) is computed off the wrong anchor. This is the "
                    f"May-13 OTO root cause. Force routine sync via "
                    f"lib/state.update_entry_price() or restart post_open.py."
                ),
            })

    # --- 3. PAUSED stale (kill switch outlived its trigger condition)
    if summary.get("paused"):
        paused_age = summary.get("paused_age_hours", 0) or 0
        # touch_files config may be {filename: hours} or {filename: {max_age_hours: N}}
        paused_cfg = expected.get("touch_files", {}).get("PAUSED", 24)
        paused_ttl = paused_cfg.get("max_age_hours", 24) if isinstance(paused_cfg, dict) else paused_cfg
        if paused_age > paused_ttl:
            findings.append({
                "class": "CONFIG_DRIFT",
                "file": "PAUSED",
                "age_hours": round(paused_age, 1),
                "ttl_hours": paused_ttl,
                "severity": "high",
                "proposed_action": (
                    f"smallcap-bot PAUSED touch-file is {paused_age:.1f}h old (TTL {paused_ttl}h). "
                    f"Likely a protective gate that has outlived its trigger. "
                    f"Investigate cause, lift if condition cleared."
                ),
            })

    # --- 4. Manual intervention recent (FYI tier C — humans touched the bot)
    if summary.get("manual_intervention_recent"):
        findings.append({
            "class": "MANUAL_INTERVENTION_RECENT",
            "age_hours": round(summary.get("manual_intervention_age_hours", 0), 1),
            "severity": "medium",
            "proposed_action": (
                f"smallcap-bot manual_intervention_log.md was edited "
                f"{summary.get('manual_intervention_age_hours', 0):.1f}h ago. "
                f"Surface the entry for context — a human stepped in outside "
                f"the normal bot cadence."
            ),
        })

    return findings


# ---------------------------------------------------------------------------
# Kalshi 50¢-zone DECISION_TRIGGER (May-16 pre-flagged weak entry zone)
# ---------------------------------------------------------------------------

# v0.4 15m-live launch baseline — only trades after this point count for the
# 50¢ analysis (the pre-launch sample is a different regime).
KALSHI_15M_LIVE_LAUNCH_ISO = "2026-05-16T16:52:00-04:00"

# Mike-approved gate: n>=20 filled 15m-live trades before any filter call
KALSHI_50C_GATE_N = 20


def detect_kalshi_50c_zone(bot_dir: Path = Path("/Users/terry/kalshi-bot")) -> list[dict]:
    """50¢-zone DECISION_TRIGGER for Kalshi 15m live universe.

    Per Airy's May-17 proposal: every 4h, split filled live 15m trades since
    the v0.4 launch into buckets <0.50¢ entry vs >=0.50¢ entry. At n>=20,
    surface the split with recommend/skip.

    Reads kalshi-bot/trades.csv. The 'entry_price' column is column 7
    (0-indexed: col 6) — confirmed against May-16 fill records.
    """
    findings: list[dict] = []
    trades_csv = bot_dir / "trades.csv"
    if not trades_csv.exists():
        return findings

    ny = timezone(timedelta(hours=-4))
    launch = datetime.fromisoformat(KALSHI_15M_LIVE_LAUNCH_ISO)

    # Buckets: sub_50 (entry < 0.50), 50_plus (entry >= 0.50)
    buckets: dict[str, dict] = {
        "sub_50":  {"n": 0, "w": 0, "l": 0, "pnl": 0.0},
        "50_plus": {"n": 0, "w": 0, "l": 0, "pnl": 0.0},
    }

    with open(trades_csv) as f:
        for r in csv.DictReader(f):
            # Filter: live 15m only, post-launch
            bot = r.get("bot", "")
            if not bot.startswith("15m-"):
                continue
            try:
                ts = datetime.fromisoformat(r["time"])
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=ny)
            except (ValueError, KeyError):
                continue
            if ts < launch:
                continue
            result = r.get("result", "")
            if result not in ("WIN", "LOSS"):
                continue  # skip pending / unfilled / errors
            try:
                entry = float(r.get("entry_price") or 0)
                pnl = float(r.get("profit") or 0)
            except (ValueError, TypeError):
                continue
            bkey = "sub_50" if entry < 0.50 else "50_plus"
            buckets[bkey]["n"] += 1
            if result == "WIN":
                buckets[bkey]["w"] += 1
            else:
                buckets[bkey]["l"] += 1
            buckets[bkey]["pnl"] += pnl

    total_n = buckets["sub_50"]["n"] + buckets["50_plus"]["n"]

    if total_n == 0:
        return findings  # no live 15m trades yet — nothing to surface

    if total_n < KALSHI_50C_GATE_N:
        # Pre-gate: watch-tier informational only
        findings.append({
            "class": "DECISION_WATCH",
            "topic": "kalshi-15m-50c-zone",
            "n_total": total_n,
            "gate_n": KALSHI_50C_GATE_N,
            "sub_50":  {**buckets["sub_50"],  "pnl": round(buckets["sub_50"]["pnl"], 2)},
            "50_plus": {**buckets["50_plus"], "pnl": round(buckets["50_plus"]["pnl"], 2)},
            "severity": "watch",
            "proposed_action": (
                f"Kalshi 15m-live 50¢-zone watch (n={total_n}, gate={KALSHI_50C_GATE_N}). "
                f"Pre-gate split: sub-50 W/L {buckets['sub_50']['w']}/{buckets['sub_50']['l']} "
                f"pnl ${buckets['sub_50']['pnl']:+.2f} | 50+ "
                f"W/L {buckets['50_plus']['w']}/{buckets['50_plus']['l']} "
                f"pnl ${buckets['50_plus']['pnl']:+.2f}. No filter decision yet — accumulating."
            ),
        })
        return findings

    # Gate hit (n>=20): produce a recommend/skip on the 50¢ bucket
    sub = buckets["sub_50"]
    plus = buckets["50_plus"]
    sub_ev = sub["pnl"] / sub["n"] if sub["n"] else 0
    plus_ev = plus["pnl"] / plus["n"] if plus["n"] else 0
    # Recommend EXCLUSION of 50¢-bucket if plus_ev < 0 AND sub_ev > plus_ev
    recommend_exclude = plus_ev < 0 and sub_ev > plus_ev
    rec_text = "RECOMMEND: add 50¢-exclusion filter (live data shows clear divergence)" if recommend_exclude else \
               "RECOMMEND: skip filter — 50¢-zone not yet underperforming clearly"
    findings.append({
        "class": "DECISION_TRIGGER",
        "topic": "kalshi-15m-50c-zone",
        "n_total": total_n,
        "sub_50":  {**sub,  "pnl": round(sub["pnl"], 2),  "ev": round(sub_ev, 2)},
        "50_plus": {**plus, "pnl": round(plus["pnl"], 2), "ev": round(plus_ev, 2)},
        "recommend_exclude": recommend_exclude,
        "severity": "medium",
        "proposed_action": (
            f"Kalshi 15m-live 50¢-zone: n={total_n} (gate cleared). "
            f"sub-50: W/L {sub['w']}/{sub['l']} pnl ${sub['pnl']:+.2f} ev ${sub_ev:+.2f}. "
            f"50+: W/L {plus['w']}/{plus['l']} pnl ${plus['pnl']:+.2f} ev ${plus_ev:+.2f}. "
            f"{rec_text}."
        ),
    })
    return findings


def run_smallcap(summary: dict) -> dict:
    """Smallcap-bot anomaly run — parallel structure to run_kalshi().

    Caller passes in the smallcap_summary dict (avoids re-pulling Alpaca twice).
    """
    findings = detect_smallcap_anomalies(summary)
    return {
        "bot": "smallcap-bot-agent",
        "ts": datetime.now(timezone.utc).isoformat(),
        "findings": findings,
        "summary": {
            "total": len(findings),
            "routine_fire_missed": sum(1 for f in findings if f["class"] == "ROUTINE_FIRE_MISSED"),
            "position_without_stop": sum(1 for f in findings if f["class"] == "POSITION_WITHOUT_STOP"),
            "config_drift": sum(1 for f in findings if f["class"] == "CONFIG_DRIFT"),
            "manual_intervention": sum(1 for f in findings if f["class"] == "MANUAL_INTERVENTION_RECENT"),
        },
    }


def detect_proposed_actions_v2(bot_dir: Path = Path("/Users/terry/kalshi-bot"),
                                smallcap_summary: dict | None = None) -> list[tuple[str, str, str]]:
    """v2 wrapper returning the same tuple shape as v1 anomalies.detect_proposed_actions().

    Returns list of (action_text, tier, rule_cited) tuples ready to plug into
    operator_dry_run pipeline (add_action + vMike dispatch + Jarvis brief).

    Now covers BOTH kalshi-bot and smallcap-bot-agent (May-16 Phase 4 cross-bot).

    Severity → Tier mapping:
      high   → "B" (Tier B: Mike-approval-via-go/skip)
      medium → "B"
      watch  → "C" (Tier C: informational only, no action queued)
    """
    actions: list[tuple[str, str, str]] = []
    cite_v2 = "anomalies_v2.py § detection (May-16 META-extension)"

    # Kalshi
    kalshi_result = run_kalshi(bot_dir)
    for finding in kalshi_result["findings"]:
        cls = finding["class"]
        tier = "B" if finding.get("severity") in ("high", "medium") else "C"
        text = f"→ [kalshi/{cls}] {finding['proposed_action']}"
        actions.append((text, tier, cite_v2))

    # Kalshi 50¢-zone DECISION_TRIGGER / WATCH (Airy proposal May-17)
    for finding in detect_kalshi_50c_zone(bot_dir):
        cls = finding["class"]
        tier = "B" if finding.get("severity") in ("high", "medium") else "C"
        text = f"→ [kalshi/{cls}] {finding['proposed_action']}"
        actions.append((text, tier, cite_v2))

    # Smallcap-bot
    if smallcap_summary is not None:
        smallcap_result = run_smallcap(smallcap_summary)
        for finding in smallcap_result["findings"]:
            cls = finding["class"]
            tier = "B" if finding.get("severity") in ("high", "medium") else "C"
            text = f"→ [smallcap/{cls}] {finding['proposed_action']}"
            actions.append((text, tier, cite_v2))

    return actions


if __name__ == "__main__":
    import json as _json
    result = run_kalshi()
    print(_json.dumps(result, indent=2, default=str))
    print()
    print("=== TUPLE-SHAPE (for operator_dry_run integration) ===")
    for text, tier, cite in detect_proposed_actions_v2():
        print(f"[Tier {tier}] {text}")
