"""Pull stock-bot account summary directly from Alpaca paper API."""
import os
from datetime import date, datetime, timezone
from typing import Optional

try:
    from alpaca.trading.client import TradingClient
    from alpaca.trading.requests import GetOrdersRequest
    from alpaca.trading.enums import QueryOrderStatus
except ImportError:
    TradingClient = None


def get_stock_bot_summary() -> dict:
    """Return a dict of key stock-bot state pulled live from Alpaca.

    Fields:
        - equity: current portfolio value
        - cash: available cash
        - positions: list of {symbol, qty, market_value, unrealized_pl, unrealized_plpc}
        - open_orders: count of pending orders
        - orders_today: count of orders submitted today (proxy for premarket fire)
        - error: present only if fetch failed

    Fail-soft: returns dict with 'error' key on any failure rather than raising.
    """
    if TradingClient is None:
        return {"error": "alpaca-py not installed"}

    api_key = os.environ.get("ALPACA_API_KEY")
    secret = os.environ.get("ALPACA_SECRET_KEY")
    if not api_key or not secret:
        return {"error": "missing ALPACA_API_KEY/SECRET"}

    try:
        client = TradingClient(api_key, secret, paper=True)
        acct = client.get_account()
        positions = client.get_all_positions()
        all_orders = client.get_orders()

        # Count today's orders (UTC date) — proxy for "did premarket fire today"
        today_utc = datetime.now(timezone.utc).date()
        orders_today = 0
        for o in all_orders:
            try:
                if o.submitted_at and o.submitted_at.date() == today_utc:
                    orders_today += 1
            except Exception:
                pass

        open_orders = sum(
            1 for o in all_orders
            if o.status in ("new", "accepted", "pending_new", "partially_filled")
        )

        return {
            "equity": float(acct.equity),
            "cash": float(acct.cash),
            "buying_power": float(acct.buying_power),
            "positions": [
                {
                    "symbol": p.symbol,
                    "qty": float(p.qty),
                    "market_value": float(p.market_value),
                    "unrealized_pl": float(p.unrealized_pl),
                    "unrealized_plpc": float(p.unrealized_plpc),
                }
                for p in positions
            ],
            "open_orders": open_orders,
            "orders_today": orders_today,
        }
    except Exception as e:
        return {"error": f"Alpaca fetch failed: {e}"}
