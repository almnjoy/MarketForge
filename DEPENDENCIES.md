# Dependencies

Market Forge is deliberately light. Full version of this page with install links:
https://docs.madeformeai.com/marketforge/requirements

## Required

| What | Why | Get it |
|---|---|---|
| **Python 3.10+** | Runs everything. The dashboard is pure standard library. (Not needed for the `MarketForge.exe` desktop build - it carries its own.) | [python.org](https://python.org) - on Windows tick **Add Python to PATH** |
| **git** | Install and update. | [git-scm.com](https://git-scm.com) |
| **Flask + requests + tzdata** | The engine's web layer, its Alpaca client, and the timezone database Windows doesn't have (without it the ET scan schedule silently runs on local time). | `pip install -r requirements.txt` |
| **Alpaca account** | Your broker + market data. **Paper keys** to start (fake money, real data). | [alpaca.markets](https://alpaca.markets) |

No Docker. No database. No Node.js. No cloud account. Nothing phones home.

## Optional - each one unlocks a specific feature

| What | Unlocks | Notes |
|---|---|---|
| **AI coding agent** (Claude Code, Codex, ...) | The COPILOT tab (chat answers, live panel building, day replays, rule tuning) AND catalyst **scoring** (signal vs noise, 0-100 + why) | Without it the desk is still a full scanner + manual trading surface - alerts, no scores, and auto-entries need scores. Set the copilot model in `config.json` (`bridge_model`), the scoring model with `RADAR_AGENT_MODEL` in `bot/.env` (default haiku); override the binary with `CLAUDE_BIN`. Prefer an OpenAI-compatible endpoint (Ollama, OpenRouter) instead? `RADAR_LLM_PROVIDER=openai` + `RADAR_LLM_BASE_URL`. |
| **[Voicebox](https://voicebox.sh/)** (Kokoro voice) | A natural voice for spoken replies, AND offline voice **input** (push-to-talk + hot mic transcribe through its whisper models) | Local TTS/STT app. Point `voicebox_url` in `config.json` at it. Without it: browser voice out, and voice in needs Chrome/Edge. |
| **Chrome or Edge** | Voice **input** fallback when Voicebox is absent | Browser speech recognition (needs Google). With Voicebox running, voice input works in any browser and in the desktop exe. |
| **Chrome + a TradingView account** | Steering **TradingView** from the desk: change symbol and timeframe by voice, and capture the chart to a PNG an AI agent can read | Run `run-tradingview.bat`. It opens Chrome with the DevTools protocol on :9222 and its own profile - log in once. Without it, `/api/tv/*` just reports that no debug browser is running and nothing else is affected. |
| **Discord webhook** | Radar alerts pushed to your phone | `RADAR_DISCORD_WEBHOOK` in `bot/.env`. |
| **FRED API key** | Richer macro context in the Market Brief | Free from the St. Louis Fed. `FRED_API_KEY` in `bot/.env`. |

| **websocket-client** | The **live price tap** (`bot/src/stream.py`). Alpaca's free plan cannot return the latest 15 minutes over REST, but its websocket is real time. This subscribes to 30 symbols and writes `data/live-prices.json`. Without it the desk runs exactly as before, on delayed REST. | `pip install websocket-client` |
## Feature to dependency map

| You want... | You need |
|---|---|
| Radar, charts, manual trading | Python + `pip install -r requirements.txt` + Alpaca paper keys (or just `MarketForge.exe`) |
| Scored signal-vs-noise catalysts | your AI coding agent (or any OpenAI-compatible LLM via `RADAR_LLM_PROVIDER=openai`) |
| Copilot: chat, panels, replay | An AI coding agent |
| Talking to the desk | Voicebox (any browser / the exe), or Chrome/Edge without it |
| A natural voice back | Voicebox with a Kokoro profile |
| Alerts on your phone | Discord webhook |
| Auto-entries (paper or live) | Scoring on, then the flags in `bot/.env` |

## Platform notes

- **Windows / macOS / Linux** all supported. Windows gets `run-portable.bat`; elsewhere
  run `MF_EMBEDDED=1 python app.py`.
- **Footprint:** a few hundred KB of code. Everything heavier is optional.
- **Scans run while the app is open** (10am / noon / 2pm / 4pm ET weekdays). It is a
  desk tool, not a headless server.
