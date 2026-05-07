# bot-status-monitor

Cloud-side monitoring for Mike's bot stack. Runs as Anthropic Cloud Routines on a schedule during workday, sends Telegram briefs to Mike's Jarvis bot so he has visibility into kalshi-bot + stock-bot state without asking.

**Goal:** AI works while Mike works.

**Schedule (initial):** weekdays at 09:00, 12:00, 15:00, 17:30 ET.

**What each fire does:**
1. Pull stock-bot-agent main from GitHub, read state files (council decisions, reflection log, run timestamps)
2. Hit Alpaca paper API for stock-bot account / positions / orders snapshot
3. Hit Kalshi REST API for kalshi-bot balance + recent trades + selectivity state (if creds provisioned)
4. Synthesize 5-line brief
5. Send to Jarvis Telegram (env vars TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)

**Scope:**
- READ ONLY — never sends orders, never writes state
- Single-source-of-truth: broker APIs + GitHub state files
- No vault dependency — works without ~/reference/

**Setup steps (Mike, one-time):**
1. Create GitHub repo `gavinmike66-alt/bot-status-monitor`, push this codebase
2. In Anthropic Cloud Routines, create new routine `bot-status-monitor`
3. Point at the GitHub repo
4. Create environment `bot-status-env` with these vars (copy from `stock-bot-agent-env`):
   - `ALPACA_API_KEY` (paper)
   - `ALPACA_SECRET_KEY` (paper)
   - `ALPACA_BASE_URL=https://paper-api.alpaca.markets`
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
   - `KALSHI_API_KEY_ID` (optional v0.2 — provision when ready)
   - `KALSHI_PRIVATE_KEY` (optional v0.2 — provision when ready)
5. Schedule: weekdays at 09:00, 12:00, 15:00, 17:30 ET
6. Network access: github.com, paper-api.alpaca.markets, api.telegram.org, api.kalshi.com
7. Hit "Run now" to test — should produce a Telegram brief

**Falsifiable success criterion:** Mike receives 4 Telegram briefs during a workday with real bot state. If any fire produces no message or wrong data, the routine failed and we diagnose.
