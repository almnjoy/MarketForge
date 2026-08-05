# Market Forge - Portable / Friend Edition

One folder = the whole desk: bot engine (scanner, gates, auto-trader), dashboard,
and the AI copilot seat. No Docker, no servers, your keys never leave the machine.

## Setup (10 minutes)
1. **Python 3.10+** on PATH (python.org, check "Add to PATH").
2. Copy `bot\.env.template` -> `bot\.env`, add your **Alpaca PAPER keys**
   (free at alpaca.markets). It STARTS in paper mode with auto-trading OFF.
3. Double-click **run-portable.bat** -> http://localhost:8410
4. Optional but worth it:
   - **Claude Code CLI** (or another coding agent) logged in -> the COPILOT tab
     comes alive: chat/voice answers, panel building, day replays.
   - **Ollama** (`ollama pull qwen2.5:3b`) -> flip `RADAR_USE_LLM=true` and the
     radar scores catalysts signal-vs-noise instead of alert-only.
   - **Voicebox** with a Kokoro voice -> spoken replies (browser voice otherwise).

## Make it YOURS
- **RULES tab** = the agent's plan: the whole pipeline explained + every knob
  clickable to discuss with the copilot. Applying = edit `bot\.env`, restart.
- **memory.md (🧠 drawer)** = your standing orders. Put YOUR plan here - max
  size, stop style, setups you trade, setups you skip. The copilot honors it
  on every turn.
- **RULES.md** = the training-wheels doc; rewrite it to your strategy.

## Going live (when YOU decide, not before)
1. Trade paper until the journal shows your process works.
2. Add your LIVE key pair in `bot\.env`, set `STOCK_ENV=live`.
3. Auto-trading live needs BOTH `RADAR_AUTO_EXECUTE=true` AND
   `LIVE_AUTO_ENABLED=true` - and starts hard-capped ($50/trade, 2/day, $150
   total, trailing stops always). Manual trades via the ticket work regardless.

## Known limits (v1)
- Market Brief needs the `claude` CLI on PATH (skipped gracefully without it).
- Scans run while the app is open (10a/12p/2p/4p ET weekdays) - it's a desk
  tool, not a headless server. Leave it running during market hours.
- No holiday calendar on the market clock; NYSE equities only.
