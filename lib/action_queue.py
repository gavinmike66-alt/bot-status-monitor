"""Append-only action queue for Operator v0.1.

Persists proposed Tier B actions across Cloud Routine fires so Mike can resolve
them via Jarvis reply ("go {action_id}" / "skip {action_id}").

v0.1: local file only. v0.2: two-phase git push to persist across ephemeral containers.
"""
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

QUEUE_PATH = Path(__file__).resolve().parent.parent / "state" / "pending_actions.jsonl"


def _ensure_state_dir() -> None:
    QUEUE_PATH.parent.mkdir(exist_ok=True)


def _stable_action_id(action_text: str) -> str:
    """Generate a stable, short, deterministic ID from action text.

    Same action text → same ID. Avoids duplicate queue entries across fires.
    """
    h = hashlib.sha256(action_text.encode()).hexdigest()
    return f"act_{h[:6]}"


def load_queue() -> List[dict]:
    """Read all queue entries (pending + resolved)."""
    _ensure_state_dir()
    if not QUEUE_PATH.exists():
        return []
    entries = []
    with open(QUEUE_PATH, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries


def list_pending() -> List[dict]:
    """Return only entries with status='pending'."""
    return [e for e in load_queue() if e.get("status") == "pending"]


def add_action(action: str, tier: str, rationale: str = "", rule_cited: str = "") -> Optional[dict]:
    """Add a new proposed action to the queue.

    Returns the new entry if added, or None if a pending entry with the same
    action text already exists (deduplication).
    """
    _ensure_state_dir()
    action_id = _stable_action_id(action)

    # Skip if already pending
    for existing in load_queue():
        if existing.get("id") == action_id and existing.get("status") == "pending":
            return None

    entry = {
        "id": action_id,
        "ts": datetime.now(timezone.utc).isoformat(),
        "tier": tier,
        "action": action,
        "rationale": rationale,
        "rule_cited": rule_cited,
        "status": "pending",
    }
    with open(QUEUE_PATH, "a") as f:
        f.write(json.dumps(entry) + "\n")
    return entry


def resolve_action(action_id: str, resolution: str, by: str = "mike") -> bool:
    """Mark an action as approved/skipped/expired by appending a resolution event.

    v0.1 keeps the file append-only — resolutions are events, not in-place edits.
    list_pending() filters out actions that have a later resolution event.

    resolution: "approved" / "skipped" / "expired"
    """
    _ensure_state_dir()
    if resolution not in ("approved", "skipped", "expired"):
        return False
    event = {
        "id": action_id,
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": "resolution",
        "resolution": resolution,
        "by": by,
    }
    with open(QUEUE_PATH, "a") as f:
        f.write(json.dumps(event) + "\n")
    return True


def list_pending_resolved() -> List[dict]:
    """Same as list_pending but filters out resolved (approved/skipped/expired) entries.

    Walks the JSONL once, building a status map, returns entries still pending.
    """
    entries = load_queue()
    latest_status = {}  # id -> status

    for e in entries:
        if "event" in e and e["event"] == "resolution":
            latest_status[e["id"]] = e["resolution"]
        elif "id" in e and "status" in e:
            latest_status.setdefault(e["id"], e["status"])

    pending_entries = []
    seen_ids = set()
    for e in entries:
        if "event" in e:
            continue  # resolution event, not a proposal
        eid = e.get("id")
        if eid in seen_ids:
            continue
        seen_ids.add(eid)
        if latest_status.get(eid, "pending") == "pending":
            pending_entries.append(e)
    return pending_entries
