"""Pull kalshi-bot state directly from Kalshi REST API.

Optional in v0.1 — if KALSHI_API_KEY_ID / KALSHI_PRIVATE_KEY not in env,
this module returns a fail-soft 'kalshi: not provisioned' state.
Mike provisions when ready.
"""
import base64
import os
import time
from datetime import datetime, timezone
from typing import Optional

import requests

KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"


def _signed_headers(method: str, path: str) -> Optional[dict]:
    """Build Kalshi API signed headers. Returns None if creds missing."""
    key_id = os.environ.get("KALSHI_API_KEY_ID")
    private_key_pem = os.environ.get("KALSHI_PRIVATE_KEY")
    if not key_id or not private_key_pem:
        return None

    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding

        ts_ms = str(int(time.time() * 1000))
        msg = f"{ts_ms}{method}{path}".encode()

        key = serialization.load_pem_private_key(
            private_key_pem.encode(), password=None
        )
        sig = key.sign(
            msg,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.DIGEST_LENGTH,
            ),
            hashes.SHA256(),
        )
        sig_b64 = base64.b64encode(sig).decode()
        return {
            "KALSHI-ACCESS-KEY": key_id,
            "KALSHI-ACCESS-TIMESTAMP": ts_ms,
            "KALSHI-ACCESS-SIGNATURE": sig_b64,
            "Content-Type": "application/json",
        }
    except Exception as e:
        print(f"[kalshi] signing failed: {e}")
        return None


def get_kalshi_summary() -> dict:
    """Return Kalshi bot snapshot. Fail-soft: returns dict with 'note' if not provisioned."""
    headers = _signed_headers("GET", "/trade-api/v2/portfolio/balance")
    if headers is None:
        return {"note": "kalshi creds not provisioned (v0.2)"}

    try:
        r = requests.get(f"{KALSHI_BASE}/portfolio/balance", headers=headers, timeout=10)
        if r.status_code != 200:
            return {"error": f"balance HTTP {r.status_code}"}
        balance_cents = r.json().get("balance", 0)
        balance = balance_cents / 100.0
    except Exception as e:
        return {"error": f"balance fetch: {e}"}

    # Recent fills (last 24h)
    try:
        cutoff_ts = int(time.time()) - 86400
        headers2 = _signed_headers("GET", "/trade-api/v2/portfolio/fills")
        r = requests.get(
            f"{KALSHI_BASE}/portfolio/fills",
            headers=headers2,
            params={"min_ts": cutoff_ts, "limit": 100},
            timeout=10,
        )
        if r.status_code != 200:
            fills = []
        else:
            fills = r.json().get("fills", [])
    except Exception:
        fills = []

    last_fill_iso = None
    last_fill_age_hours = None
    if fills:
        try:
            last_ts = max(f.get("created_time", 0) for f in fills)
            if isinstance(last_ts, str):
                last_dt = datetime.fromisoformat(last_ts.replace("Z", "+00:00"))
            else:
                last_dt = datetime.fromtimestamp(last_ts, tz=timezone.utc)
            now = datetime.now(timezone.utc)
            last_fill_age_hours = (now - last_dt).total_seconds() / 3600
            last_fill_iso = last_dt.isoformat()
        except Exception:
            pass

    return {
        "balance": balance,
        "fills_24h": len(fills),
        "last_fill_iso": last_fill_iso,
        "last_fill_age_hours": last_fill_age_hours,
    }
