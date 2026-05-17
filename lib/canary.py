"""Mike-canary metric — track whether META layer actually catches things Mike would have asked.

The whole point of the META layer build is to invert "Mike has to ask" into
"META surfaces it first." Without instrumentation, we can't tell if the
investment is paying off over time.

Each canary entry records:
  - When Mike asked something (or noted something)
  - What META layer SHOULD have surfaced first
  - Whether META actually did (Y / N / partial)
  - The class of detection that should have caught it

Pattern: when a session logs a canary, the META layer's miss rate is visible.
If we go 14 days with zero canaries → META is catching everything Mike cares
about. If canaries accumulate → tune detection thresholds or add classes.

v0.1: helper + backfill of May 16 session canaries (5 entries).
v0.2 (later): operator_v2 brief includes weekly canary summary.
v0.3 (later): auto-detect canary moments from session transcripts.
"""
from __future__ import annotations
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

LOG_PATH = Path(__file__).resolve().parent.parent / "state" / "canary.jsonl"


def log_canary(
    question: str,
    meta_caught: str,  # "yes" | "no" | "partial"
    detection_class: Optional[str] = None,
    why: str = "",
    bot: str = "",
    ts: Optional[str] = None,
) -> dict:
    """Record a canary moment.

    Args:
        question: what Mike asked or what came up (free text)
        meta_caught: "yes" (META surfaced it first), "no" (Mike asked first),
                     "partial" (META surfaced something related but not the actual thing)
        detection_class: which anomaly class WOULD have caught it
                         (BEHAVIOR_ANOMALY / DECISION_TRIGGER / CONFIG_DRIFT /
                         ROUTINE_FIRE_MISSED / POSITION_WITHOUT_STOP / COST_BASIS_DRIFT /
                         MANUAL_INTERVENTION_RECENT / META_SELF / ARCHITECTURAL)
        why: short note on why this was missed (or how META caught it)
        bot: kalshi-bot / smallcap-bot-agent / cross-bot / vault / meta
    """
    LOG_PATH.parent.mkdir(exist_ok=True)
    entry = {
        "ts": ts or datetime.now(timezone.utc).isoformat(),
        "question": question,
        "meta_caught": meta_caught,
        "detection_class": detection_class,
        "why": why,
        "bot": bot,
    }
    with open(LOG_PATH, "a") as f:
        f.write(json.dumps(entry) + "\n")
    return entry


def list_canaries(
    since_iso: Optional[str] = None,
    meta_caught: Optional[str] = None,
) -> list[dict]:
    """Walk canary log, filter, return matching entries."""
    if not LOG_PATH.exists():
        return []
    entries = []
    cutoff = datetime.fromisoformat(since_iso) if since_iso else None
    with open(LOG_PATH) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if cutoff:
                try:
                    ts = datetime.fromisoformat(e["ts"])
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    if ts < cutoff:
                        continue
                except (KeyError, ValueError):
                    continue
            if meta_caught and e.get("meta_caught") != meta_caught:
                continue
            entries.append(e)
    return entries


def summarize_canaries(window_days: int = 7) -> dict:
    """Aggregate canary metrics for the past window_days.

    Returns:
      total: int
      meta_caught_yes: int
      meta_caught_no: int
      meta_caught_partial: int
      miss_rate: float (0.0 - 1.0)
      top_missed_classes: list[(class, count)] — which detection classes missed most
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=window_days)).isoformat()
    entries = list_canaries(since_iso=cutoff)
    total = len(entries)
    yes = sum(1 for e in entries if e.get("meta_caught") == "yes")
    no_ = sum(1 for e in entries if e.get("meta_caught") == "no")
    partial = sum(1 for e in entries if e.get("meta_caught") == "partial")
    miss_rate = (no_ + 0.5 * partial) / total if total > 0 else 0.0

    missed_classes: dict[str, int] = {}
    for e in entries:
        if e.get("meta_caught") in ("no", "partial"):
            cls = e.get("detection_class") or "UNKNOWN"
            missed_classes[cls] = missed_classes.get(cls, 0) + 1
    top = sorted(missed_classes.items(), key=lambda x: -x[1])[:5]

    return {
        "window_days": window_days,
        "total": total,
        "meta_caught_yes": yes,
        "meta_caught_no": no_,
        "meta_caught_partial": partial,
        "miss_rate": round(miss_rate, 3),
        "top_missed_classes": top,
    }


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "summary":
        days = int(sys.argv[2]) if len(sys.argv) > 2 else 7
        s = summarize_canaries(window_days=days)
        print(f"=== Canary summary, last {s['window_days']} days ===")
        print(f"Total events:     {s['total']}")
        print(f"META caught:      {s['meta_caught_yes']}  ({100 * s['meta_caught_yes'] / max(s['total'], 1):.0f}%)")
        print(f"META missed:      {s['meta_caught_no']}")
        print(f"META partial:     {s['meta_caught_partial']}")
        print(f"Miss rate:        {s['miss_rate']:.1%}  (lower = better)")
        if s['top_missed_classes']:
            print(f"Top missed classes:")
            for cls, n in s['top_missed_classes']:
                print(f"  {cls}: {n}")
    else:
        for e in list_canaries():
            caught = e.get('meta_caught', '?')
            symbol = '✓' if caught == 'yes' else ('~' if caught == 'partial' else '✗')
            print(f"{e['ts'][:19]}  {symbol} [{caught}] {e.get('detection_class') or 'UNCLASSIFIED'}")
            print(f"   Q: {e['question']}")
            if e.get('why'):
                print(f"   why: {e['why']}")
