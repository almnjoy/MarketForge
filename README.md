# MARKET FORGE - agentic trading cockpit

(drop your logo at `static/logo.png` and it appears in the top bar)
Ctrl+K opens the command palette - commands first, anything else goes straight to the copilot.

Two ways to run:
- **run.bat** - Dustin's desk: dashboard talks to the bot on rocker (Docker, cron).
- **run-portable.bat** - the FRIEND edition: bot engine EMBEDDED (bot/), one window,
  no Docker, own Alpaca keys in bot/.env. See PORTABLE.md for the 10-minute setup.

Local live dashboard designed to run three windows side by side (the 6-monitor
energy on one desk):

1. **This dashboard** - `run.bat` (or `python app.py`) -> http://localhost:8410
2. **Claude Code CLI** - `claude` in this folder (reads CLAUDE.md, becomes the copilot)
3. **Voice** - built into the dashboard's COPILOT tab (Chrome mic + TTS), no extra app

## Tabs
- **OVERVIEW** - live account, positions, orders, big candlestick chart + headlines
- **CATALYST_RADAR** - the bot's scored movers, live "now vs alert", trade from the card
- **RETAIL_RADAR** - what Reddit's hot pages are pushing
- **WORKBENCH** - Claude Code's canvas: every `panels/*.html` file renders live.
  Ask CC to build/transform panels while you talk - the page morphs in real time.
- **RULES** - live bot knobs (from rocker) + RULES.md side by side for tuning talks
- **COPILOT** - chat + voice to CC via `chat-inbox.jsonl` / `chat-outbox.jsonl`

## Trade ticket
Any Trade button opens the ticket: $ or shares, trailing-stop % (default 10), type
`live` to arm. Buys with a trail are placed by the bot as buy -> confirm fill ->
GTC trailing stop (whole shares auto-computed).

## Workflow loop
Talk (voice or type) in COPILOT -> CC reads chat-inbox.jsonl -> answers in
chat-outbox.jsonl (spoken aloud) and/or builds WORKBENCH panels -> you eyeball, then
pull the trigger on the ticket. CC never trades without an explicit instruction.

## Requirements
Python 3.9+ on PATH. Chrome/Edge for voice. Bot reachable at the LAN address in
config.json (rocker 10.20.20.100:8796).
