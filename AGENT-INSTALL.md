# Install Market Forge with your AI agent

Copy the block below and paste it to your coding agent (Claude Code, Codex,
Cursor, whatever you run). It will set the whole desk up and configure it
WITH you.

```
Set up Market Forge - the open-source AI trading desk - on this machine.
Repo: https://github.com/almnjoy/MarketForge

1. Check for git and Python 3.10+; help me install whichever is missing.
2. Clone the repo to my home directory:
   git clone https://github.com/almnjoy/MarketForge  (~/MarketForge, or
   %USERPROFILE%\MarketForge on Windows)
3. Install the one dependency:  python -m pip install -r requirements.txt
4. Copy bot/.env.template to bot/.env. Then ask me for my Alpaca PAPER
   key id and secret (free at alpaca.markets -> Paper dashboard -> API keys)
   and fill in ALPACA_KEY_ID and ALPACA_SECRET_KEY. Leave STOCK_ENV=paper
   and leave every auto-trading flag false.
5. Launch it: run-portable.bat on Windows, or MF_EMBEDDED=1 python app.py
   elsewhere. Confirm http://localhost:8410 loads and the header shows PAPER.
6. Read PORTABLE.md and CLAUDE.md in the repo, then walk me through the
   RULES tab and the memory drawer (memory.md) so the desk learns MY plan:
   my max position size, my stop style, the setups I trade and the ones I skip.

Hard rules for you, the agent: never enable live trading, never place any
trade, and never change risk settings without my explicit instruction in
this conversation.
```

Why this works: Market Forge's copilot seat is plain files + localhost HTTP
(panels/, chat-*.jsonl, memory.md, journal.jsonl - see CLAUDE.md). Any capable
coding agent can drive the desk after setup: build boards on the Workbench,
answer in the chat, replay your trading day from the journal.
