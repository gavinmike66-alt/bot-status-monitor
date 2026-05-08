# Deployment Runbook — bot-status-monitor + Operator

Step-by-step UI deployment. Total time: ~10 minutes once GitHub repo exists.

## Phase 0a — One-time GitHub setup (5 min, ONLY if not done)

1. **Create the GitHub repo** at https://github.com/new
   - Owner: `gavinmike66-alt`
   - Name: `bot-status-monitor`
   - Visibility: Private
   - DO NOT initialize with README/license (we have files locally already)
   - Click **Create repository**

2. **Push from local machine:**
   ```bash
   cd ~/bot-status-monitor
   git remote add origin https://github.com/gavinmike66-alt/bot-status-monitor.git
   git branch -M main
   git push -u origin main
   ```

3. **(Optional) Set up branch protection** at github.com/gavinmike66-alt/bot-status-monitor/settings/branches
   - Branch name pattern: `main`
   - Require status checks before merging (if CI is added later)
   - Skip for v0.1; not blocking.

## Phase 0b — Create the bot-status-env environment in Anthropic Cloud Routines

Source: copy from existing `stock-bot-agent-env`. Same Alpaca paper + Telegram creds.

1. Open https://claude.ai/code/routines
2. Sidebar → **Customize** → **Environments** (or wherever environments live in current UI)
3. **+ New environment** → name: `bot-status-env`
4. **Environment variables** — paste these 5 (copy real values from your `stock-bot-agent-env`):
   ```
   ALPACA_API_KEY=<your paper key>
   ALPACA_SECRET_KEY=<your paper secret>
   ALPACA_BASE_URL=https://paper-api.alpaca.markets
   TELEGRAM_BOT_TOKEN=<your Jarvis bot token>
   TELEGRAM_CHAT_ID=<your chat id>
   ```
5. **Network access:** add hostnames:
   - `paper-api.alpaca.markets`
   - `api.telegram.org`
   - `api.elections.kalshi.com` (for v0.2 Kalshi support)
   - `github.com` (for v0.3 state push)
   - `raw.githubusercontent.com`
6. **Setup script** — paste:
   ```bash
   #!/bin/bash
   pip install -q alpaca-py cryptography requests
   ```
7. **Save**

## Phase 0c — Create 4 status-check routines (~3 min)

Schedule the read-only status brief 4× per workday.

For EACH of the 4 schedule times below, repeat:

1. **+ New routine**
2. Name: `bot-status-monitor-{time}` (e.g. `bot-status-monitor-09`, `-12`, `-15`, `-1730`)
3. **Repository:** `gavinmike66-alt/bot-status-monitor`
4. **Environment:** `bot-status-env`
5. **Schedule:** Weekdays at the specified time ET:
   - 09:00 AM ET
   - 12:00 PM ET
   - 03:00 PM ET
   - 05:30 PM ET
6. **Routine prompt** — copy contents of `routines/status_check.md` from the repo (paste into the prompt field)
7. **Save**

After saving the first one, hit **Run now** to verify it works. Should:
- Complete in ~30 sec
- Land a `🤖 Bot Status — HH:MM ET` message in your Jarvis Telegram chat
- Show stock-bot equity, positions, premarket fire status, kalshi status (note: not provisioned)

If it fails, check logs in the routine page; common issues:
- Missing env var → re-check bot-status-env
- Network 403 → add the missing hostname to allowlist
- pip install fails → setup script error

## Phase 0d — Create the Operator routine (1 min)

Operator runs at the same 4 schedules but with anomaly detection + action-queue persistence. v0.1 = read + propose, doesn't yet execute actions.

For now, **deploy ONE Operator routine to see if anomaly detection produces useful signal:**

1. **+ New routine**
2. Name: `operator-09` (start with one, expand later)
3. Repository: `gavinmike66-alt/bot-status-monitor`
4. Environment: `bot-status-env`
5. Schedule: Weekdays at 9:00 AM ET (start with one fire, evaluate)
6. Routine prompt: contents of `routines/operator.md`
7. **Save** + **Run now**

Expected output: similar to status_check brief but with `📋 Pending actions: none.` (or actions if any anomaly thresholds hit).

If happy with first run, expand operator schedule to 12:00, 15:00, 17:30 by duplicating the routine (same pattern as status_check).

## Acceptance criteria

**Phase 0 is done when:**
- GitHub repo `gavinmike66-alt/bot-status-monitor` exists with all files pushed
- `bot-status-env` environment exists with 5 env vars + network allowlist + setup script
- 4 `bot-status-monitor-*` routines firing at 09:00, 12:00, 15:00, 17:30 ET on weekdays
- 1+ `operator-*` routine firing at least once daily
- Each fire produces exactly one Telegram message in your Jarvis chat with current bot state
- "Run now" tested at least once for status_check and operator

**Falsifiable failure mode:**
- If by Mon 5/12 EOD you haven't received 4+ Telegram briefs/day from the schedule, deployment failed and we diagnose.

## v0.2 — when ready to enable action execution

Pre-requisites:
- Phase 0 stable for ~1 week
- Terry's `jarvis_listener.py` extended to handle `go {action_id}` / `skip {action_id}` Telegram replies → updates `state/pending_actions.jsonl` resolution events
- `lib/operator_actions.py` stubs filled in
- GitHub PAT on Cloud Routine env (for state two-phase push)

Then update operator routine prompt to enable execution.

## v0.3 — when Kalshi support added

Add to bot-status-env:
- `KALSHI_API_KEY_ID=<your kalshi key id>`
- `KALSHI_PRIVATE_KEY=<your kalshi private key PEM>`

`lib/kalshi_summary.py` will switch from "not provisioned" note to live balance + recent fills.

## Troubleshooting

**Routine fires but no Telegram brief lands:**
- Check Telegram bot token + chat ID in env
- Test locally: `cd ~/bot-status-monitor && python3 scripts/dry_run.py`

**Routine fails with "Host not in allowlist":**
- Add the missing host to bot-status-env network allowlist
- Re-run

**Routine succeeds but brief shows "missing ALPACA_API_KEY":**
- Env var not set in bot-status-env, OR routine pointing at wrong environment

**No proposed actions ever surface:**
- Could be correct (no anomalies hitting thresholds)
- Verify by SSH to Terry: `cd ~/bot-status-monitor && python3 scripts/operator_dry_run.py`
- If detection thresholds need tuning, edit `lib/anomalies.py` and re-push

**Pending actions queue grows without resolution:**
- Expected in v0.1 — Mike-reply integration is v0.2
- For now, queue is informational only
