# TradingView MCP - the real one, and how to wire it

Rewritten 2026-08-16. The first version of this file walked through the WRONG
repo; that section is kept at the bottom so the distinction stays on record.

**The one you want: [`tradesdontlie/tradingview-mcp`](https://github.com/tradesdontlie/tradingview-mcp)**
MIT, node.js, 78 MCP tools + a `tv` CLI. This is the one from the Mind Math Money
course - it drives your TradingView **Desktop** app.

---

## THE ONE THING TO READ BEFORE INSTALLING: the port collision

**It connects over Chrome DevTools Protocol on port 9222. That is the exact port
Market Forge's own `tv.py` Chrome already used.**

Two CDP endpoints cannot share a port, and the failure mode is quiet rather than
loud: whichever process binds first owns it, and the second either fails to bind
or attaches to the *first one's* targets and starts driving the wrong application.
You would see the copilot "change the symbol" and nothing move.

**Already fixed in this repo, 2026-08-16:**

| | Port | Owner |
|---|---|---|
| TradingView **Desktop** (the MCP) | **9222** | its launch scripts hardcode this |
| Market Forge's debug **Chrome** (`tv.py`) | **9223** | changed, override with `MF_TV_CDP` |

Changed: `_app/run-tradingview.bat`, `_app/tv.py`, `AGENTS.md`, `CLAUDE.md`,
`DEPENDENCIES.md`, `docs/API.md`. Nothing else to do - just know that if you see a
stale `9222` anywhere, it means Chrome, and it is wrong now.

---

## Prerequisites

- TradingView **Desktop app**, installed and logged in
- **A paid TradingView subscription.** The tool does not bypass anything - it reads
  the app you are already paying for.
- **Node.js 18+** (`node --version`)
- Claude Code

---

## Install (Windows)

### 1. Clone and install

```powershell
cd <your-MarketForge-checkout>\_tools
git clone https://github.com/tradesdontlie/tradingview-mcp.git
cd tradingview-mcp
npm install
```

(Any folder works. Keeping it under the project means one place to find it, and
`_tools/` is not something the build ships.)

### 2. Launch TradingView Desktop with the debug port

The repo ships a Windows script:

```powershell
scripts\launch_tv_debug.bat
```

If auto-detection fails, launch it by hand:

```powershell
& "$env:LOCALAPPDATA\TradingView\TradingView.exe" --remote-debugging-port=9222
```

Verify the port is actually live before going further:

```powershell
curl http://localhost:9222/json/version
```

You want JSON back. If it hangs or refuses, nothing downstream will work and the
MCP will report a health-check failure that looks like a config problem.

> **The Microsoft Store trap - this is the one that bit us before.** The README
> lists two Windows locations: `%LOCALAPPDATA%\TradingView\TradingView.exe` (the
> **direct download** from tradingview.com/desktop) and
> `%PROGRAMFILES%\WindowsApps\TradingView*\TradingView.exe` (the **Store**
> package). `WindowsApps` is ACL-locked - you generally cannot launch that binary
> with custom flags, which is the reason `tv.py` went the Chrome route in the
> first place. **If `curl` on 9222 gives you nothing, check which one you have.**
> If it is the Store version, uninstall it and reinstall from
> tradingview.com/desktop.

### 3. Register the MCP with Claude Code

Add to `~/.claude/.mcp.json` (or a project-level `.mcp.json`). Merge into an
existing `mcpServers` block if you have one - do not paste a second one.

```json
{
  "mcpServers": {
    "tradingview": {
      "command": "node",
      "args": ["C:\\path\\to\\MarketForge\\_tools\\tradingview-mcp\\src\\server.js"]
    }
  }
}
```

Double backslashes - it is JSON.

### 4. Restart Claude Code, then verify

```
Use tv_health_check to verify TradingView is connected
```

Or from a terminal, which is the faster loop while debugging:

```powershell
node src\cli\index.js status
node src\cli\index.js quote
```

### If the launch script fails on a recent TradingView

There is a known launch bug on **TradingView Desktop v2.14+**. The fork
[`LewisWJackson/tradingview-mcp-jackson`](https://github.com/LewisWJackson/tradingview-mcp-jackson)
fixes it (same upstream, plus a morning-brief workflow and a rules config). Try
upstream first; switch if launching is what breaks.

---

## What it can actually do

78 MCP tools, and every one is also a `tv` CLI command with JSON output - which
matters, because it means the desk can call it from a script without going through
a model at all.

| Group | Highlights |
|---|---|
| Chart reading | `chart_get_state`, `data_get_study_values` (live RSI/MACD/BB/EMA), `quote_get`, `data_get_ohlcv` |
| **Pine indicator output** | `data_get_pine_lines` / `_labels` / `_tables` / `_boxes` - read the levels a Pine script DREW, not just its inputs |
| Chart control | symbol, timeframe, chart type, add/remove indicators, scroll to date, zoom to range |
| Pine development | `pine_set_source` -> `pine_smart_compile` -> `pine_get_errors` -> `pine_save` |
| **Replay** | `replay_start` / `_step` / `_trade` / `_status` - step historical bars and practice entries |
| Drawing | horizontal lines, trend lines, rectangles, text |
| Alerts | create, list, delete |
| Layout | 2x2 / 3x1 grids, per-pane symbols, tabs |
| Streaming | `tv stream quote|bars|values|lines|tables|all` - poll-and-diff JSONL to stdout |

**The two that matter most for the DayTrader lane:**

1. **`data_get_pine_lines` / `_labels` / `_tables`.** This reads the *output* of
   indicators you already trust on your own chart - session levels, bias labels,
   profiler tables. That is not something we can compute from Alpaca bars, and it
   is the genuinely new capability.
2. **`replay_*`.** Stepping historical bars with an agent watching is a real
   backtesting surface for a pattern that is hard to express as a gate.

`tv stream` is interesting and dangerous in the same breath - see the boundary.

---

## Where it belongs in the five layers

**DayTrader lane, plus Workshop for development. Behind a config flag, default OFF.**

```
TradingView Desktop --CDP:9222--> MCP --> layer 2 VALIDATOR --> layer 3
                                              |
   never bypasses ---------------------------/
```

Three boundaries, and they are the whole reason this stays safe:

1. **It is a data source, so it enters at the validator.** A price read off a chart
   is not broker truth. `risk.py` and the broker own sizing, stops and protection,
   unchanged. An MCP reading a chart must never decide whether a position is safe.
2. **It never touches the order path.** The tool itself cannot trade (chart
   interaction only) and that property should not be softened by wiring it to
   anything that can.
3. **`tv stream` is for a human watching, not for a loop.** The README is explicit
   that programmatic consumption of TradingView data may conflict with their Terms
   regardless of source, and that you carry that risk. A polling loop that runs all
   session is a different thing from asking for a read when you are looking at it.

**Do not replace `tv.py`.** It is 300 stdlib lines over a documented protocol with
one job. This MCP touches *undocumented internal Electron APIs* - the README says
outright they can break in any TradingView update and to pin your version if
stability matters. Keeping both means a TradingView release breaks the day lane's
nice-to-have, not the copilot's "pull up AMWL".

### Terms of Use - read it once, decide once

The repo is unusually honest about this and it deserves a straight summary rather
than being buried: it uses a standard Chromium debug interface, does not touch
TradingView's servers, and does not bypass any paywall. **But TradingView's Terms
restrict automated data collection and non-display usage, and the author says
plainly that this may conflict with them and that you assume all risk including
account action.** You have a paid account you care about. Occasional
agent-assisted reads while you work is a very different risk profile from an
all-day stream, and that distinction is yours to make deliberately.

---

## Sequencing

1. Confirm `curl http://localhost:9222/json/version` answers. If it does not, it is
   the Store-package problem - fix that first, everything else is downstream.
2. Install, register, `tv_health_check`.
3. Use it from the **Workshop** lane by hand for a week. No wiring, no flag, no
   code. Find out whether reading your own Pine levels is as useful as it sounds.
4. Only then wire it into DayTrader behind `tv_mcp: false` in config, entering at
   the validator.
5. Leave `tv.py` exactly where it is.

---

## Appendix: the other repo, and why it is not this

`atilaahmettaner/tradingview-mcp` is a **different project with a confusingly
similar name**. It does not touch the desktop app at all - no chart control, no
Pine, no drawing. It is a headless Python data server: `tradingview_ta` screener +
Yahoo Finance + Reddit sentiment + its own backtester. Its own README draws the
same distinction.

It is still worth having, separately, for one reason: **`walk_forward_backtest_strategy`**
returns an explicit ROBUST / MODERATE / WEAK / OVERFITTED verdict, and every
backtest reports vs buy-and-hold with commission *and* slippage. That is the
discipline the course video was missing entirely.

Install (Windows) if you want it - the trap is a 60-second MCP init timeout caused
by pandas building from source on Python 3.14:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
uv tool install --python 3.13 tradingview-mcp-server
```

```json
{ "mcpServers": { "tradingview-data": {
    "command": "uvx",
    "args": ["--python", "3.13", "--from", "tradingview-mcp-server", "tradingview-mcp"] } } }
```

Note the key is `tradingview-data`, not `tradingview` - if you run both, they need
different names.
