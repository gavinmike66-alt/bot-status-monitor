"""Biotech-research-bot summary — on-demand Anthropic Managed Agent.

Different shape from Kalshi (continuous) or smallcap-bot (cron-driven):
biotech runs on-demand only, ~$0.60/session. The "health" question is
different: "how recently was it used + what's in the memory store."

State files this reads (read-only):
  ~/biotech-research-bot/state/agents.json     — agent_id, memstore_id, dreams
  ~/biotech-research-bot/state/sessions/       — per-session directories
  ~/biotech-research-bot/logs/session_*.log    — session output logs

Returns a dict that fits the cross-stream artifact / operator_v2 brief.
"""
from __future__ import annotations
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

BIOTECH_DIR = Path("/Users/terry/biotech-research-bot")


def get_biotech_summary(bot_dir: Path = BIOTECH_DIR) -> dict:
    """Pull biotech-research-bot state for cross-stream view."""
    out: dict = {"bot": "biotech-research-bot", "mode": "on-demand"}

    # 1. Agents config (agent_id, memstore_id, dreams)
    agents_path = bot_dir / "state" / "agents.json"
    if not agents_path.exists():
        out["error"] = "agents.json not found — bot may not be deployed"
        return out
    try:
        agents = json.loads(agents_path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        out["error"] = f"agents.json parse failed: {e}"
        return out

    out["agent_id"] = agents.get("biotech_researcher_v0")
    out["memstore_id"] = agents.get("biotech_memory_store")
    out["dreams_count"] = len(agents.get("dreams", []))
    if agents.get("dreams"):
        out["last_dream"] = agents["dreams"][-1].get("created_at", "")

    # 2. Sessions — recency + count
    sessions_dir = bot_dir / "state" / "sessions"
    sessions = []
    if sessions_dir.exists():
        for d in sessions_dir.iterdir():
            if d.is_dir() and d.name.startswith("sesn_"):
                mtime = datetime.fromtimestamp(d.stat().st_mtime, tz=timezone.utc)
                sessions.append({"id": d.name, "mtime": mtime})
    sessions.sort(key=lambda s: s["mtime"], reverse=True)
    out["session_count"] = len(sessions)

    now = datetime.now(timezone.utc)
    if sessions:
        last = sessions[0]
        out["last_session_id"] = last["id"]
        out["last_session_age_days"] = round((now - last["mtime"]).total_seconds() / 86400, 1)
        # Cumulative counts
        out["sessions_last_7d"] = sum(1 for s in sessions if (now - s["mtime"]) < timedelta(days=7))
        out["sessions_last_14d"] = sum(1 for s in sessions if (now - s["mtime"]) < timedelta(days=14))
    else:
        out["last_session_age_days"] = None
        out["sessions_last_7d"] = 0
        out["sessions_last_14d"] = 0

    # 3. Status interpretation
    age = out.get("last_session_age_days")
    if age is None:
        out["status"] = "never-run"
    elif age < 7:
        out["status"] = "active"
    elif age < 30:
        out["status"] = "idle"
    else:
        out["status"] = "dormant"

    return out


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    try:
        from operator_v2 import _load_env_files
        _load_env_files()
    except ImportError:
        pass
    print(json.dumps(get_biotech_summary(), indent=2, default=str))
