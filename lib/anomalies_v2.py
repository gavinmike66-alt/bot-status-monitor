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


def kalshi_expected_state() -> BotExpectation:
    """Hardcoded for now; will move to ~/kalshi-bot/expected_state.json in v1.5."""
    ny = timezone(timedelta(hours=-4))
    today_iso = datetime(2026, 5, 16, 16, 50, tzinfo=ny).isoformat()  # launch time
    return BotExpectation(
        bot_name="kalshi-bot",
        assets=[
            # 15m universe (post May 16 launch)
            AssetExpectation("15m-DOGE", enabled=True, min_trades_per_7d=5, promoted_at=today_iso),
            AssetExpectation("15m-HYPE", enabled=True, min_trades_per_7d=5, promoted_at=today_iso),
            AssetExpectation("15m-XRP",  enabled=True, min_trades_per_7d=5, promoted_at=today_iso),
            AssetExpectation("15m-BTC",  enabled=True, min_trades_per_7d=5, promoted_at=today_iso),
            AssetExpectation("15m-ETH",  enabled=True, min_trades_per_7d=5, promoted_at=today_iso),
            AssetExpectation("15m-BNB",  enabled=False),  # paper-locked
            AssetExpectation("15m-SOL",  enabled=False),
            # Hourly
            AssetExpectation("hourly-BTC", enabled=True, min_trades_per_7d=20),
            AssetExpectation("hourly-ETH", enabled=True, min_trades_per_7d=10),
            AssetExpectation("hourly-XRP", enabled=True, min_trades_per_7d=0),  # liquidity-limited
            AssetExpectation("hourly-DOGE",enabled=True, min_trades_per_7d=0),  # liquidity-limited
            AssetExpectation("hourly-BNB", enabled=False),
            AssetExpectation("hourly-SOL", enabled=False),
        ],
        touch_files={
            "PAUSED": 24,                  # if PAUSED touch-file >24h old, propose lift
            "TRIPWIRE_BASELINE_TS": 0,     # no TTL — anchor file
        },
        realized_pnl_anchor="peak",
        paper_promotion_rules={
            "min_wr": 0.70,
            "min_n": 15,
            "min_days": 5,
            "min_ev_per_trade": 0.0,
        },
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

    findings: list[dict] = []
    for asset, s in stats.items():
        if not actual_paper_status.get(asset, False):
            continue  # asset is already live; nothing to propose
        n = s["w"] + s["l"]
        if n < rules["min_n"]:
            continue
        wr = s["w"] / n
        ev = s["pnl"] / n
        if wr >= rules["min_wr"] and ev >= rules["min_ev_per_trade"]:
            findings.append({
                "class": "DECISION_TRIGGER",
                "asset": asset,
                "wr": round(wr * 100, 1),
                "ev_per_trade": round(ev, 2),
                "n": n,
                "severity": "medium",
                "proposed_action": (
                    f"Promote {asset} from paper → live: "
                    f"{wr*100:.1f}% WR / +${ev:.2f} EV / n={n} clears "
                    f"the {rules['min_wr']*100:.0f}% WR + positive-EV gate. "
                    f"Single-asset promotion, never-stack-compliant."
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


def detect_smallcap_anomalies(summary: dict) -> list[dict]:
    """Smallcap-bot-specific anomaly detection.

    Operates on the dict returned by smallcap_summary.get_smallcap_bot_summary().
    Three classes for MVP:
      1. ROUTINE_FIRE_MISSED: weekday-aware check (premarket/midday/close)
      2. POSITION_WITHOUT_STOP: Alpaca has positions but no sell-stop orders
         (the May-13 QUBT/OTO incident class)
      3. PAUSED_STALE: kill-switch touch file older than 24h

    Returns list of finding dicts in the same shape as detect_behavior_anomalies.
    """
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

    # --- 1. Routine fire missed (premarket, midday, close)
    # Premarket fires at 8:30 ET; alert if it didn't fire by 10:00 ET on the
    # current/most-recent weekday.
    routines = [
        ("premarket", 8, 30, "last_run_premarket_h_ago", 1.5),  # 1.5h grace after fire
        ("midday",    12, 0, "last_run_midday_h_ago", 1.0),
        ("close",     15, 45, "last_run_close_h_ago", 1.0),
    ]
    for name, hh, mm, age_key, grace_hours in routines:
        last_expected = _last_expected_weekday_fire(hh, mm)
        # Compute expected_age = how many hours ago the fire should have happened
        expected_age = (now - last_expected).total_seconds() / 3600
        actual_age = summary.get(age_key)
        # Only alert if (a) routine has never run OR (b) it was supposed to fire
        # at least `grace_hours` ago AND state shows it didn't.
        if expected_age < grace_hours:
            continue  # too soon to know — give the routine its grace window
        if actual_age is None:
            findings.append({
                "class": "ROUTINE_FIRE_MISSED",
                "routine": name,
                "expected_fire": last_expected.isoformat(),
                "severity": "high",
                "proposed_action": (
                    f"smallcap-bot {name} routine has NEVER fired (no "
                    f"last_run_{name}.txt). Expected last fire at "
                    f"{last_expected.strftime('%a %H:%M ET')}. "
                    f"Check cron + venv + smallcap-bot daemon."
                ),
            })
        elif actual_age > expected_age + 1.0:  # > 1h beyond expected fire
            findings.append({
                "class": "ROUTINE_FIRE_MISSED",
                "routine": name,
                "actual_age_hours": round(actual_age, 1),
                "expected_age_hours": round(expected_age, 1),
                "severity": "high",
                "proposed_action": (
                    f"smallcap-bot {name} routine missed its last "
                    f"weekday fire: state is {actual_age:.1f}h old, expected "
                    f"≤{expected_age:.1f}h. Check cron + venv + daemon."
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

    # --- 3. PAUSED stale (kill switch outlived its trigger condition)
    if summary.get("paused"):
        paused_age = summary.get("paused_age_hours", 0) or 0
        if paused_age > 24:
            findings.append({
                "class": "CONFIG_DRIFT",
                "file": "PAUSED",
                "age_hours": round(paused_age, 1),
                "severity": "high",
                "proposed_action": (
                    f"smallcap-bot PAUSED touch-file is {paused_age:.1f}h old. "
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
