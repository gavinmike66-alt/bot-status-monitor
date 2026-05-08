"""Operator action handlers — v0.1 stubs.

When Operator's pending_actions queue contains an entry that Mike has approved
(via Jarvis "go {id}" reply, wired in v0.2), the next routine fire dispatches to
the handler here.

**Architecture decision:**
Operator does NOT directly mutate state on Terry's bots (kalshi-bot, stock-bot-agent).
Direct mutation across machines is brittle (auth, state drift, halt-over-bypass concerns).
Instead, Operator either:

1. **Direct API actions** for things it owns natively (Alpaca order cancel, Kalshi REST
   order cancel, Cloud Routines toggle if API exists). These execute immediately.

2. **PR-propose actions** for things that touch bot config / RULEBOOK / strategy. Operator
   opens a C2 PR to the relevant bot repo with the proposed change. Per
   `spec_auto_merge_classes.md`, C2 is Mike-gated — so Mike merges via web UI to
   authorize the operational change. This respects the existing gating model.

Pattern family with `feedback_dont_relax_invariants.md`: invariants stay; never
silence the alarm. The PR-propose pattern preserves Mike's C2 gate as a hard
invariant even when Operator is acting on Mike-pre-approved Tier B authority.

v0.1 = stubs that raise NotImplementedError. v0.2 = filled implementations.
"""
from typing import Optional


class OperatorActionError(Exception):
    """Raised when an action handler can't execute."""


def _verify_action_in_bounds(action_entry: dict, current_state: dict) -> None:
    """Re-pull current state and re-verify the proposed action is still within
    Tier B bounds before executing. Bounds may have shifted between proposal
    and approval (e.g., Kalshi WR recovered above 55% threshold).

    Raises OperatorActionError if action is no longer applicable.
    """
    # v0.2: implement per-action-type bound re-checks
    raise NotImplementedError("v0.2: re-check action against current state before executing")


# ============================================================================
# Kalshi handlers
# ============================================================================

def kalshi_demote_asset_to_paper(action_entry: dict, asset: str) -> dict:
    """Tier B: trailing-50 WR < 55% on a live asset.

    v0.1 stub. v0.2 path: open C2 PR to kalshi-bot repo flipping `paper_only: True`
    on the asset's config entry. Mike merges via GitHub UI. Terry's watchdog
    cron picks up the change on next bot restart.

    NOT a direct kalshi-bot file edit — that would require SSH from Cloud Routine
    to Terry, which we don't allow.
    """
    raise NotImplementedError(
        "v0.2: open C2 PR to gavinmike66-alt/kalshi-bot flipping config.py "
        f"asset={asset} to paper_only=True. Body cites action_entry['rule_cited']."
    )


def kalshi_asset_cooldown(action_entry: dict, asset: str, hours: int = 24) -> dict:
    """Tier B: per-asset 24h cool-down after a $15+ single-trade loss.

    v0.1 stub. v0.2 path: write a cooldown marker file to a vault location
    Terry's bot reads at entry-decision time. OR open C2 PR to kalshi-bot
    config with a dated cooldown entry. Pattern TBD — depends on whether
    bot has runtime config-reload capability.
    """
    raise NotImplementedError(
        f"v0.2: apply {hours}h cooldown to {asset}. Mechanism TBD: vault marker "
        "file vs C2 PR vs Telegram-to-Terry command."
    )


def kalshi_tighten_selectivity_after_losses(action_entry: dict, asset: str) -> dict:
    """Tier B: 3 consecutive losses on a single asset → auto-tighten selectivity gate.

    Per feedback_dont_relax_invariants.md — tighter, not looser. The gate is the
    safety; tightening preserves it.

    v0.1 stub. v0.2 path: write asset-specific selectivity-tighten marker.
    """
    raise NotImplementedError(
        "v0.2: tighten selectivity gate for asset. Mechanism: vault marker file "
        "Terry's bot reads at strategy-evaluation time."
    )


def kalshi_watchdog_restart() -> dict:
    """Tier B: kill + restart wedged bot.

    v0.1 stub. v0.2 path: Terry's existing watchdog already does this on its own
    cadence. Operator's role is to surface a "watchdog should restart soon"
    advisory; not direct execution. Raise here.
    """
    raise NotImplementedError(
        "v0.2: surface advisory to Telegram. Terry's watchdog cron handles "
        "the actual restart. Operator does NOT directly kill bot processes."
    )


# ============================================================================
# Stock-bot handlers
# ============================================================================

def stockbot_tighten_selectivity_consecutive_losses(action_entry: dict) -> dict:
    """Tier B: 3 consecutive 7-day-window losses → tighten selectivity.

    Currently blocked on RULEBOOK ambiguity flagged in Wed transcript: "stop-outs
    in a row" vs "any loss" criterion. Per spec_operator_authority_bounds.md,
    default to RULEBOOK literal until Terry's PR 2 disambiguates.

    v0.1 stub. v0.2 path: open C2 PR to stock-bot-agent repo updating selectivity
    threshold. Cite RULEBOOK section + recent loss data.
    """
    raise NotImplementedError(
        "v0.2: open C2 PR to gavinmike66-alt/stock-bot-agent. Awaits RULEBOOK "
        "criterion disambiguation (Terry's PR 2 from inbox_terry.md)."
    )


def stockbot_park_stalled_scaffold(action_entry: dict, scaffold_name: str) -> dict:
    """Tier B: 30-day no-evidence auto-archive per Phase 3 v2.

    Updates scaffold_backlog.md (which Terry will create per spec_phase_3_signal_and_template.md).
    Markdown edit, not code change — could be a C1 PR (touches docs only).

    v0.1 stub. v0.2 path: edit scaffold_backlog.md, move entry to Killed section,
    push as C1 PR (auto-merge after CI + 1 vote per auto-merge spec v1.1).
    """
    raise NotImplementedError(
        f"v0.2: archive {scaffold_name} in scaffold_backlog.md. C1 PR (auto-mergeable)."
    )


# ============================================================================
# Cross-bot handlers
# ============================================================================

def toggle_cloud_routine(action_entry: dict, routine_name: str, target_state: str) -> dict:
    """Tier B: toggle a Cloud Routine to active/inactive after 2 hung-yellow fires.

    v0.1 stub. v0.2 path: requires Anthropic Cloud Routines API (may not exist yet).
    If API not available, fall back to Telegram alert with explicit UI-toggle request
    to Mike, marked Tier C (Mike must execute UI step manually).
    """
    raise NotImplementedError(
        f"v0.2: toggle {routine_name} → {target_state}. Requires Cloud Routines API "
        "(verify availability) or fallback to Tier C escalation if API absent."
    )


def early_wr_review(action_entry: dict, drawdown_pct: float) -> dict:
    """Tier B: trigger per-asset WR review out-of-band on 10%+ 24h drawdown.

    Pure analysis action — no state change. Operator pulls trades.csv (via Terry-side
    sync), runs the analysis, surfaces conclusions. No PR, no broker call.

    v0.1 stub. v0.2 path: pull last 100 trades per asset, compute trailing WR,
    alpha-vs-strategy-spec, generate Telegram brief.
    """
    raise NotImplementedError(
        f"v0.2: run WR review on {drawdown_pct*100:.0f}% drawdown trigger. "
        "Pull trades.csv, compute per-asset stats, surface to Telegram."
    )


# ============================================================================
# Direct API actions (no PR needed — Operator owns these natively)
# ============================================================================

def alpaca_cancel_stale_order(action_entry: dict, order_id: str) -> dict:
    """Tier A-ish: cancel an Alpaca order older than threshold.

    Direct API call. Operator owns this — Alpaca paper API access is in env.
    No PR, no Mike-gate (this is operational maintenance per spec Tier A).

    v0.1 stub. v0.2 path: TradingClient.cancel_order_by_id(order_id).
    """
    raise NotImplementedError("v0.2: TradingClient.cancel_order_by_id wrapper.")


def kalshi_cancel_resting_order(action_entry: dict, order_id: str) -> dict:
    """Tier A-ish: cancel a Kalshi resting order older than 2 min.

    Terry's existing watchdog already does this in `_cancel_in_csv`. Operator's
    role here is redundancy-as-belt-and-suspenders for cases where watchdog
    is wedged. Direct Kalshi REST call, requires KALSHI_PRIVATE_KEY env.

    v0.1 stub. v0.2 path: signed DELETE to /trade-api/v2/portfolio/orders/{id}.
    """
    raise NotImplementedError("v0.2: signed Kalshi DELETE order request.")


# ============================================================================
# Dispatcher (v0.2)
# ============================================================================

# Mapping action types (extracted from action_entry['action'] text or v0.2 schema field)
# to their handlers. v0.2 will add a structured action-type field to action_queue.add_action()
# so dispatcher doesn't have to parse free text.

ACTION_HANDLERS = {
    "kalshi_demote_asset": kalshi_demote_asset_to_paper,
    "kalshi_cooldown": kalshi_asset_cooldown,
    "kalshi_tighten": kalshi_tighten_selectivity_after_losses,
    "kalshi_watchdog_restart": kalshi_watchdog_restart,
    "stockbot_tighten": stockbot_tighten_selectivity_consecutive_losses,
    "stockbot_park_scaffold": stockbot_park_stalled_scaffold,
    "toggle_routine": toggle_cloud_routine,
    "early_wr_review": early_wr_review,
    "alpaca_cancel_order": alpaca_cancel_stale_order,
    "kalshi_cancel_order": kalshi_cancel_resting_order,
}


def dispatch(action_entry: dict, **kwargs) -> dict:
    """Dispatch an approved action to its handler.

    v0.1: raises NotImplementedError (handlers are stubs).
    v0.2: full dispatcher with bound re-checks + handler invocation + result logging.
    """
    raise NotImplementedError(
        "v0.2: dispatcher with action-type extraction + bound re-check + handler call."
    )
