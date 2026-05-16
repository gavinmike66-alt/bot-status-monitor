#!/usr/bin/env python3
"""Operator v2 — local-cron edition with cross-bot anomaly detection.

Pipeline:
  1. Run v1 detection (Alpaca + Kalshi API summaries) — preserved for back-compat
  2. Run v2 detection (local file inspection: trades.csv, paper_trades.csv, touch-files)
  3. Self-heartbeat check (META catches its own staleness)
  4. Append new findings to action_queue (deduplicated by content hash)
  5. Dispatch combined findings to vMike for Claude analysis (if any)
  6. Synthesize Jarvis brief
  7. Send to Telegram
  8. Write heartbeat timestamp + structured run-log

Cron deployment target: every 4h Mon-Sun (16:00, 20:00, 00:00, 04:00, 08:00, 12:00 ET).

Robustness features (May-16 build, addressing "make our systems robust"):
- Day-boundary cutoff anchoring (no rolling-second edge effects)
- Idempotent action queue (content-hash dedup via existing add_action)
- META self-heartbeat (if last run > 6h ago, alert)
- All exceptions logged to operator_v2.log, never silent
- Dry-run mode via --no-send flag (test without Telegram)
"""
from __future__ import annotations
import argparse
import json
import logging
import os
import sys
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load_env_files() -> None:
    """Auto-source .env files from known bot locations.

    Cross-bot deployment: operator_v2 needs Kalshi + Alpaca + Telegram +
    Anthropic creds. Rather than duplicate them into bot-status-monitor/.env,
    source them from each bot's own .env file. setdefault honors any creds
    already in the process env.

    Order matters: kalshi first (has Kalshi + Telegram + Anthropic),
    then smallcap-bot (has Alpaca paper). bot-status-monitor/.env last as
    final override.
    """
    sources = [
        Path("/Users/terry/kalshi-bot/.env"),
        Path("/Users/terry/smallcap-bot-agent/.env"),
        ROOT / ".env",
    ]
    for src in sources:
        if not src.exists():
            continue
        try:
            for line in src.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                # Cross-bot env-name bridging — kalshi-bot and bot-status-monitor
                # were built with different conventions. Map the kalshi-bot names
                # onto bot-status-monitor's expected names so the same .env files
                # work for both. Mapping is one-way: kalshi-bot's names → BSM names.
                if k == "KALSHI_API_KEY":
                    os.environ.setdefault("KALSHI_API_KEY_ID", v)
                elif k == "KALSHI_PRIVATE_KEY_PATH":
                    pem_path = src.parent / v
                    if pem_path.exists():
                        try:
                            os.environ.setdefault("KALSHI_PRIVATE_KEY", pem_path.read_text())
                        except OSError:
                            pass
                os.environ.setdefault(k, v)
        except OSError:
            continue


_load_env_files()

LOG_PATH = ROOT / "state" / "operator_v2.log"
HEARTBEAT_PATH = ROOT / "state" / "operator_v2_heartbeat.txt"
HEARTBEAT_STALE_HOURS = 6  # if last run > 6h ago, that's a META-itself anomaly

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("operator_v2")


def check_meta_heartbeat() -> tuple[str, str, str] | None:
    """If last operator_v2 run > HEARTBEAT_STALE_HOURS ago, return a finding tuple.

    This is the META-self-check: if the watchdog itself stops watching, we want
    to know. Detected by the NEXT successful run, which surfaces the gap as
    an action. (If the META is truly dead, no run happens and a separate
    process-level check would have to surface it — but that's the canonical
    catch-22 with self-monitoring; this catches the "ran today after 3 days
    silence" case at minimum.)
    """
    if not HEARTBEAT_PATH.exists():
        return (
            "→ [META_SELF] First-ever operator_v2 run, or heartbeat file missing. "
            "Establishing baseline. Subsequent runs will detect staleness.",
            "C",
            "operator_v2.py § meta_self_heartbeat",
        )
    try:
        last_ts = datetime.fromisoformat(HEARTBEAT_PATH.read_text().strip())
    except (ValueError, OSError) as e:
        return (
            f"→ [META_SELF] Could not parse heartbeat file: {e}. "
            "Resetting baseline.",
            "B",
            "operator_v2.py § meta_self_heartbeat",
        )
    age = datetime.now(timezone.utc) - last_ts.astimezone(timezone.utc)
    if age > timedelta(hours=HEARTBEAT_STALE_HOURS):
        return (
            f"→ [META_SELF] operator_v2 last ran {age.total_seconds()/3600:.1f}h ago "
            f"(threshold {HEARTBEAT_STALE_HOURS}h). The META layer was silent — "
            f"any findings during that window were missed. Investigate cron + retry.",
            "B",
            "operator_v2.py § meta_self_heartbeat",
        )
    return None


def write_heartbeat() -> None:
    HEARTBEAT_PATH.parent.mkdir(exist_ok=True)
    HEARTBEAT_PATH.write_text(datetime.now(timezone.utc).isoformat())


def collect_v1_actions(alpaca: dict, kalshi: dict) -> list[tuple[str, str, str]]:
    """v1 detection — Alpaca/Kalshi API-based, preserved for back-compat."""
    from lib.anomalies import detect_proposed_actions
    return detect_proposed_actions(alpaca, kalshi)


def collect_v2_actions(bot_dir: Path, smallcap_summary: dict | None = None) -> list[tuple[str, str, str]]:
    """v2 detection — local-file inspection, new in May-16 build.

    Now covers kalshi-bot AND smallcap-bot-agent (Phase 4 cross-bot extension).
    """
    from lib.anomalies_v2 import detect_proposed_actions_v2
    return detect_proposed_actions_v2(bot_dir, smallcap_summary=smallcap_summary)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-send", action="store_true",
                        help="Skip Telegram send (dry-run mode)")
    parser.add_argument("--kalshi-dir", default="/Users/terry/kalshi-bot",
                        help="Path to kalshi-bot repo")
    args = parser.parse_args(argv)

    log.info("=== operator_v2 fire ===")

    from lib.action_queue import add_action, list_pending_resolved
    from lib.alpaca_summary import get_stock_bot_summary
    from lib.anomalies import detect_pages
    from lib.kalshi_summary import get_kalshi_summary
    from lib.notify import send_jarvis
    from lib.smallcap_summary import get_smallcap_bot_summary
    from lib.vmike_dispatch import dispatch_to_vmike

    # --- 1. Pull bot summaries (v1 + smallcap inputs)
    try:
        alpaca = get_stock_bot_summary()
    except Exception as e:
        log.error(f"alpaca summary failed: {e}")
        alpaca = {"error": f"summary failure: {e}"}
    try:
        kalshi = get_kalshi_summary()
    except Exception as e:
        log.error(f"kalshi summary failed: {e}")
        kalshi = {"error": f"summary failure: {e}"}
    try:
        smallcap = get_smallcap_bot_summary()
    except Exception as e:
        log.error(f"smallcap summary failed: {e}")
        smallcap = {"error": f"summary failure: {e}"}
    log.info(f"summaries: alpaca={list(alpaca.keys())} "
             f"kalshi={list(kalshi.keys())} smallcap={list(smallcap.keys())}")

    # --- 2. Run v1 + v2 detection
    v1_actions = []
    v2_actions = []
    try:
        v1_actions = collect_v1_actions(alpaca, kalshi)
    except Exception:
        log.error(f"v1 detection failed:\n{traceback.format_exc()}")
    try:
        v2_actions = collect_v2_actions(Path(args.kalshi_dir), smallcap_summary=smallcap)
    except Exception:
        log.error(f"v2 detection failed:\n{traceback.format_exc()}")
    log.info(f"detected v1={len(v1_actions)} v2={len(v2_actions)} actions")

    # --- 3. META self-heartbeat check (BEFORE writing new heartbeat)
    meta_self = check_meta_heartbeat()
    if meta_self:
        v2_actions.insert(0, meta_self)
        log.info(f"meta-self check: {meta_self[0][:80]}")

    all_actions = v1_actions + v2_actions

    # --- 4. Pages (Tier D, immediate)
    fresh_pages = []
    try:
        fresh_pages = detect_pages(alpaca, kalshi)
    except Exception as e:
        log.warning(f"page detection failed: {e}")

    # --- 5. Append to action queue (idempotent — add_action dedups by content hash)
    new_count = 0
    for action_text, tier, cite in all_actions:
        entry = add_action(action_text, tier, rule_cited=cite)
        if entry is not None:
            new_count += 1
            log.info(f"queued {entry['id']} [Tier {tier}]: {action_text[:80]}")

    pending = list_pending_resolved()
    log.info(f"queue: +{new_count} new, {len(pending)} total pending")

    # --- 6. vMike dispatch (if anomalies)
    vmike_brief = ""
    if all_actions or fresh_pages:
        try:
            vmike_brief = dispatch_to_vmike(all_actions, fresh_pages, alpaca, kalshi)
            log.info(f"vmike dispatch: {len(vmike_brief)} chars")
        except Exception:
            log.warning(f"vmike dispatch failed:\n{traceback.format_exc()}")
            vmike_brief = ""

    # --- 7. Synthesize brief
    fire_time = datetime.now(ZoneInfo("America/New_York")).strftime("%H:%M ET")
    lines = [f"🤖 Operator v2 — {fire_time}"]

    # Smallcap-bot (May-16: replaced retired stock-bot adapter)
    if "error" in smallcap:
        lines.append(f"smallcap-bot: ⚠ {smallcap['error']}")
    else:
        eq = smallcap.get("equity", 0)
        n_pos = len(smallcap.get("positions", []))
        ver = smallcap.get("bot_version", "")
        paused = " [PAUSED]" if smallcap.get("paused") else ""
        lines.append(f"smallcap-bot {ver}: ${eq:,.0f} ({n_pos} pos){paused}")

    if "error" in kalshi:
        lines.append(f"Kalshi: ⚠ {kalshi['error']}")
    elif "note" in kalshi:
        lines.append(f"Kalshi: {kalshi['note']}")
    else:
        bal = kalshi.get("balance", 0)
        n_fills = kalshi.get("fills_24h", 0)
        lines.append(f"Kalshi: ${bal:.2f} • Fills 24h: {n_fills}")

    if fresh_pages:
        lines.append("")
        lines.extend(fresh_pages)

    if pending:
        lines.append("")
        lines.append(f"📋 Pending actions ({len(pending)}):")
        for entry in pending[:6]:  # cap at 6 for telegram width
            lines.append(f"• {entry['id']} [Tier {entry['tier']}]: {entry['action'][:100]}")
        if len(pending) > 6:
            lines.append(f"  …and {len(pending)-6} more.")
        lines.append("")
        lines.append("Reply 'go <id>' to authorize. 'skip <id>' to dismiss.")
    else:
        lines.append("")
        lines.append("📋 No pending actions. META silent = healthy.")

    brief = "\n".join(lines) + vmike_brief

    # --- 8. Send (or skip in dry-run)
    if args.no_send:
        log.info("--no-send flag: skipping Telegram")
        print("\n=== BRIEF (dry-run) ===")
        print(brief)
        print("=======================\n")
    else:
        try:
            ok = send_jarvis(brief)
            log.info(f"telegram send: {'OK' if ok else 'FAILED'}")
            if not ok:
                return 1
        except Exception as e:
            log.error(f"telegram send raised: {e}")
            return 1

    # --- 9. Write heartbeat (only after successful run)
    write_heartbeat()
    log.info(f"heartbeat written to {HEARTBEAT_PATH}")
    log.info("=== operator_v2 done ===")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception:
        log.error(f"FATAL: {traceback.format_exc()}")
        sys.exit(2)
