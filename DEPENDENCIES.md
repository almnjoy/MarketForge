# Dependencies

Market Forge is deliberately light. Full version of this page with install links:
https://docs.madeformeai.com/marketforge/requirements

## Required

| What | Why | Get it |
|---|---|---|
| **Python 3.10+** | Runs everything. The dashboard is pure standard library. | [python.org](https://python.org) - on Windows tick **Add Python to PATH** |
| **git** | Install and update. | [git-scm.com](https://git-scm.com) |
| **Flask** | The bot engine's web layer. The *only* pip package. | `pip install -r requirements.txt` |
| **Alpaca account** | Your broker + market data. **Paper keys** to start (fake money, real data). | [alpaca.markets](https://alpaca.markets) |

No Docker. No database. No Node.js. No cloud account. Nothing phones home.

## Optional - each one unlocks a specific feature

| What | Unlocks | Notes |
|---|---|---|
| **AI coding agent** (Claude Code, Codex, ...) | The COPILOT tab: chat answers, live panel building, day replays, rule tuning | Without it the desk is still a full scanner + manual trading surface. Set the model in `config.json` (`bridge_model`); override the binary with `CLAUDE_BIN`. |
| **Ollama** | Catalyst **scoring** (signal vs noise, 0-100 + why) | `ollama pull qwen2.5:3b`, then `RADAR_USE_LLM=true` in `bot/.env`. Any OpenAI-compatible endpoint works. Without it: alerts, no scores - and auto-entries need scores. |
| **[Voicebox](https://voicebox.sh/)** (Kokoro voice) | A natural voice for spoken replies | Local TTS app. Point `voicebox_url` in `config.json` at it. Without it the browser voice speaks. |
| **Chrome or Edge** | Voice **input** (push-to-talk + hot mic) | Browser speech recognition. Everything else works in any modern browser. |
| **Chrome + a TradingView account** | Steering **TradingView** from the desk: change symbol and timeframe by voice, and capture the chart to a PNG an AI agent can read | Run `run-tradingview.bat`. It opens Chrome with the DevTools protocol on :9222 and its own profile - log in once. Without it, `/api/tv/*` just reports that no debug browser is running and nothing else is affected. |
| **Discord webhook** | Radar alerts pushed to your phone | `RADAR_DISCORD_WEBHOOK` in `bot/.env`. |
| **FRED API key** | Richer macro context in the Market Brief | Free from the St. Louis Fed. `FRED_API_KEY` in `bot/.env`. |

## Feature to dependency map

| You want... | You need |
|---|---|
| Radar, charts, manual trading | Python + Flask + Alpaca paper keys |
| Scored signal-vs-noise catalysts | Ollama (or any OpenAI-compatible LLM) |
| Copilot: chat, panels, replay | An AI coding agent |
| Talking to the desk | Chrome or Edge |
| A natural voice back | Voicebox with a Kokoro profile |
| Alerts on your phone | Discord webhook |
| Auto-entries (paper or live) | Scoring on, then the flags in `bot/.env` |

## Platform notes

- **Windows / macOS / Linux** all supported. Windows gets `run-portable.bat`; elsewhere
  run `MF_EMBEDDED=1 python app.py`.
- **Footprint:** a few hundred KB of code. Ollama's 3B model is ~2 GB if you use it.
- **Scans run while the app is open** (10am / noon / 2pm / 4pm ET weekdays). It is a
  desk tool, not a headless server.
