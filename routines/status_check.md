# Routine: Bot Status Check

Schedule: weekdays at 09:00, 12:00, 15:00, 17:30 ET.

You are the cross-bot status monitor for Mike's stack. Your one job: pull current state for kalshi-bot + stock-bot-agent, synthesize a 5-line brief, send to Mike's Jarvis Telegram.

## First: re-read the README
Read `README.md` for context on what this routine does and what's read-only.

## Available
- Env vars: `ALPACA_API_KEY`, `ALPACA_SECRET_KEY`, `ALPACA_BASE_URL`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`. Optionally: `KALSHI_API_KEY_ID`, `KALSHI_PRIVATE_KEY` (v0.2 — fail-soft if absent).
- Python helpers: `lib/notify.py`, `lib/alpaca_summary.py`, `lib/git_state_reader.py`, `lib/kalshi_summary.py`
- Network: github.com (read), paper-api.alpaca.markets, api.telegram.org, api.elections.kalshi.com

## Steps

1. **Pull stock-bot state** via `lib.git_state_reader.get_stock_bot_state()`. Reads council_decisions.jsonl, last_run_*.txt, reflection_log.md, daily_snapshot.json from GitHub raw.

2. **Pull stock-bot live state** via `lib.alpaca_summary.get_stock_bot_summary()`. Hits Alpaca paper API for equity, cash, positions, open orders.

3. **Pull kalshi state** via `lib.kalshi_summary.get_kalshi_summary()`. Returns `note: not provisioned` if KALSHI_* env vars absent (v0.1 reality) — that's fine, surface in brief.

4. **Synthesize a 5-line brief** in this format (keep TIGHT — Mike reads on phone):

```
🤖 Bot Status — {fire_time_ET}

Stock-bot: ${equity_total} ({n_positions}/4 positions). Premarket {fire_status}. Last council: {ticker} {rating} ({age}). Reflections: {n_reflections} entries.

Kalshi: ${kalshi_balance} • Last fill {age_hours}h ago • Fills 24h: {n}.
[Or if Kalshi not provisioned: "Kalshi: API creds not yet provisioned"]

Heads-up: {anything anomalous — silent bot, missing fires, errors fetched}.
```

5. **Send via `notify.send_jarvis(text)`**. Single Telegram message. Markdown ok.

6. **Log success/failure to stdout** for debugging from Cloud Routines log.

## Anomaly detection (what counts as "heads-up")

- Stock-bot: if `last_run_premarket` older than 24h on a weekday, flag
- Stock-bot: if council_decisions.jsonl is empty after expected fire, flag
- Stock-bot: if any open position has `unrealized_plpc < -0.05` (5% loss), flag
- Stock-bot: if `last_council_decision` parse failed, flag
- Kalshi: if balance dropped >$10 since prior fire (state in this fire is fire-time; comparison would need prior file but skip for v0.1 — flag absolute number only)
- Kalshi: if `last_fill_age_hours > 12` AND it's during US trading hours, flag (bot is silent)
- Kalshi: if `note: not provisioned` — surface as setup-pending, not error
- Any module returns `error` key — flag with the error message

## Error handling

- Any single source failure → continue with what works, mention failure in heads-up line
- If ALL sources fail → send "🤖 Bot Status: all sources unreachable — investigate" to Jarvis. Do not fail silently.
- Never silent skip. Every fire produces exactly one Telegram message, even if all data is errors.

## Success criterion

Mike receives a Telegram message at every scheduled fire time during weekdays. The message reflects current bot state. If Mike doesn't get the message or it's wrong, this routine failed and we diagnose.
