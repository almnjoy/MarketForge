# _tools/ - third-party tools, cloned not vendored

Things the desk can talk to but does not own. Cloned here so there is one place
to find them; each keeps its own git history and upstream remote.

Ignored by git on purpose - a clone of someone else's repo does not belong in
ours, and `build.py` must never ship it.

| Tool | What | Port |
|---|---|---|
| `tradingview-mcp/` | tradesdontlie/tradingview-mcp - drives TradingView **Desktop** over CDP | **9222** |

Setup: `docs/TRADINGVIEW-MCP-SETUP.md`.

**Port note:** TradingView Desktop owns **9222** because the MCP's launch scripts
hardcode it. Market Forge's own debug Chrome (`_app/tv.py`) moved to **9223** on
2026-08-16 to get out of its way.
