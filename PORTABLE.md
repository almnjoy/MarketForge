# Market Forge - Portable / Friend Edition

One folder = the whole desk: bot engine (scanner, gates, auto-trader), dashboard,
and the AI copilot seat. No Docker, no servers, your keys never leave the machine.

## Zero-install option: MarketForge.exe
If you got the **desktop build** (`MarketForge-win64.zip`), there is no step 1:
unzip somewhere SHORT (`C:\MarketForge` beats ten nested folders - Windows
path-length limits are real), double-click `MarketForge.exe`, and the setup
wizard walks you through your Alpaca keys in a native window. Everything below
still applies - same folder, same files, same knobs. Build it yourself with
`python build.py` (see `docs/PACKAGING.md`).

## Setup from source (10 minutes)
1. **Python 3.10+** on PATH (python.org, check "Add to PATH").
2. Copy `bot\.env.template` -> `bot\.env`, add your **Alpaca PAPER keys**
   (free at alpaca.markets). It STARTS in paper mode with auto-trading OFF.
   Or skip this: the dashboard serves the same setup wizard on first run.
3. Double-click **run-portable.bat** -> http://localhost:8410
   (one window runs the dashboard AND the engine. **stop.bat** stops everything
   and verifies nothing is left listening - worth using, because closing a
   Windows console does not reliably kill child processes.)
4. Optional but worth it:
   - **Claude Code CLI** (or another coding agent) logged in -> the COPILOT tab
     comes alive: chat/voice answers, panel building, day replays.
   - With the agent installed the radar also SCORES catalysts signal-vs-noise
     (0-100) - no Ollama, no extra install. Model knob: `RADAR_AGENT_MODEL`.
   - **[Voicebox](https://voicebox.sh/)** with a Kokoro voice -> spoken replies (browser voice otherwise).

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
