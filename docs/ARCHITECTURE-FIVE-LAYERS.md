# Market Forge - the five layers

Status: **PLAN. Layer boundaries are real as of 2026-08-16; the validator, the
watchdog and the TradingView MCP lane are not built.**

Dustin's framing, 2026-08-16: the app is really five parts. Python scanners that
pull market data and do the numbers. A validator between the numbers and the AI.
The AI layer that reviews results. A minimal always-on brain that just checks the
scripts are running and files errors. The UI. Plus TradingView desktop + MCP as
the sixth piece feeding the day lane.

This document is the map. It is deliberately about **boundaries**, not features -
the value is that each layer can be wrong on its own without taking the others
down, and that you can point an agent at exactly one of them.

---

## The layers

```
  1  NUMBERS        bot/src/*.py          pure Python. fetch, compute, rank.
     |                                    NO model call. NO prose. Deterministic.
     |                                    Output: JSON artifacts + sqlite
     v
  2  VALIDATOR      bot/src/validate.py   the gate. Every number crossing into
     |              (NOT BUILT)           layer 3 is checked first. Refuses on
     |                                    stale, absent, out-of-range, or
     |                                    self-inconsistent data.
     v
  3  AI REVIEW      TheTradingBrains/     plans + prompts. The model PHRASES
     |              _data/chat-*.jsonl    facts it was handed. It never computes
     |                                    a number and never places an order.
     v
  4  UI             static/ + app.py      where you look, chat, and confirm.
                                          The only place a human decides.

  0  WATCHDOG       bot/src/watch.py      runs beside 1-3. Minimal. Confirms the
                    _data/errors.jsonl    scripts actually ran and the output is
     (NOT BUILT)                          shaped right. Writes failures to a file
                                          that gets read and fixed.

  5  CHART          _app/tv.py (now)      TradingView. Currently Chrome + CDP.
                    MCP lane (proposed)   Candidate: desktop app + MCP for the
                                          day lane. See the section below.
```

**The one rule that makes this worth doing:** *a number is computed in layer 1,
proved in layer 2, and only ever quoted in layer 3.* Every recurring bug in this
repo's history has been a violation of that line - a stop for 29 shares on a
58-share position reported as "protected", a tap reading a file that did not
exist under a bare `except: pass` while printing success, three June dashboards
whose KPIs were literal numbers typed into a seed.

---

## Layer 1 - the numbers

Already the strongest layer. What it needs is not new code, it is **legibility**:
one file per job, each with a stated input, output and owner lane.

Current state after the 2026-08-16 clean:

| File | Job | Lane |
|---|---|---|
| `alpaca_client.py` | broker + bars. THE single client. Never forked. | shared |
| `scanner_core.py` | lock, claim, dedupe, scanlog, ranking | shared |
| `scan_live.py` | Brain 1 gates - Ariel/O'Neil, daily bars, $3 floor | MoneyTrader |
| `short_screen.py` / `short_signals.py` | Brain 2 gates - declining 50, atr_extension, borrow | PaperTrader |
| `daily_play.py` | Brain 3 pattern engine | DayTrader |
| `fundamentals.py` | EDGAR supply annotation | shared |
| `regime.py` | the gate that decides which brain may speak | shared |
| `risk.py` | sizing + caps | shared |
| `stream.py` | the live IEX tap | shared, 30-subscription budget |
| `brief.py` | facts computed in code, model only phrases | boundary to L3 |

**Still owed here:** `scan_paper.py` and `scan_daily.py` do not exist as their own
scanners (the lanes borrow). That was step 6 of the three-lane plan and is
unchanged by today's work.

## Layer 2 - the validator (NOT BUILT - this is the new piece)

A single `validate.py` that everything in layer 1 writes *through*, not around.
Modelled on the one good idea in the Investment Council repo, hardened with what
this desk has already learned the hard way.

It should refuse, not warn, on:

- **Staleness.** Every artifact carries a `run_id` and a UTC timestamp. A consumer
  whose input `run_id` differs from the current run stops and names the stale
  stage. *This is already live in the Consensus panel - it caught five empty
  stage dirs reporting as "current" the first time it ran.*
- **Absence read as zero.** A missing field is `None`, never `0`. The tap reading
  a nonexistent `data/radar.json` and printing success is this bug.
- **Range.** A price of 0, a negative quantity, a percentage over 1000, a
  volatility of NaN. Cheap, and it catches feed corruption.
- **Unit mismatch.** `/api/bot/bars` is DOLLARS and radar rows are CENTS. That
  is written in `docs/API.md` and enforced nowhere.
- **Self-consistency.** Stop-covered shares must equal position shares. Notional
  must equal qty x price. Percentages in a group must sum. The 29-vs-58 bug dies
  here permanently.
- **Freshness of quotes.** A print older than N seconds is not "the price".

Contract: `validate(artifact) -> (ok, [findings])`. On failure the caller writes to
`_data/errors.jsonl` and **does not hand the data to layer 3**. A layer-3 turn
that never happened is always better than one built on a wrong number.

## Layer 3 - AI review

Two distinct things, and they should not share a file:

- **Plans** - `TheTradingBrains/<Lane>/` - what a lane trades, its setups, its
  rules, its journal. Loaded on demand via the routing index, never all at once.
- **Turns** - `_data/chat-inbox.jsonl` / `chat-outbox.jsonl` - the copilot bus.

Standing rules, unchanged and non-negotiable: the model never computes a number,
never places an order, and stages everything for a human click.

Two things worth adopting from the AI-trading course reviewed today:

1. **"Ask me questions before you start"** as a preamble on any open-ended
   analysis turn. Cheap, and it measurably tightened his multi-timeframe output.
2. **Every backtest prints its benchmark next to it.** A net-profit number with no
   buy-and-hold or SPY comparison beside it is a partial answer reading as
   complete - the exact failure class this whole architecture is arranged against.

## Layer 0 - the watchdog (NOT BUILT)

Deliberately the dumbest component in the system. It does **not** analyse the
market. It answers one question every N minutes: *did the scripts run, and does
their output look like output?*

- Did each scheduled job run within its window?
- Did it write its artifact, and is the artifact non-empty and parseable?
- Did `validate.py` pass it?
- Is the tap connected and are its subscriptions the CURRENT positions rather
  than the ones it picked at startup?

Anything that fails becomes one line in `_data/errors.jsonl`:

```json
{"ts":"...","layer":1,"source":"scan_live.py","kind":"empty_artifact",
 "detail":"radar.json 0 rows after a 12-symbol scan","run_id":"..."}
```

That file is the input to a fix. You are already chatting to Claude in the app,
and there is no reason it cannot read `errors.jsonl`, propose the patch, and push
it - that loop is the point of the file existing.

**Build it with a model call as the LAST step, not the first.** Nearly all of this
is a shape check, and a shape check written in Python cannot hallucinate that a
file is fine.

---

## Layer 5 - TradingView: Chrome/CDP now, desktop+MCP proposed

### What we run today

`_app/tv.py` drives a debug Chrome over the Chrome DevTools Protocol. Symbol and
interval travel in the URL; screenshots come from CDP. Stdlib only, ~70-line
WebSocket client. Endpoints `/api/tv/open`, `/api/tv/shot`, `/api/tv/status`,
captures land in `tv-shots/`.

Its scope is narrow **on purpose**: no drawing tools, no indicator manipulation,
no reading TradingView's internal chart state.

### The recorded reason we are not on the desktop app - and why it may not apply

From `tv.py`'s own docstring:

> TradingView Desktop ships from the Microsoft Store as a packaged app, so you
> cannot pass it `--remote-debugging-port` and its install dir is ACL-locked.

**Read that carefully: the blocker is the MICROSOFT STORE PACKAGE, not the
desktop app.** The course installs from `tradingview.com/desktop`, which is a
direct installer, not the Store package - a different install with different
ACLs. So the recorded blocker may simply not apply to the download the MCP
expects. **That is a 20-minute test, and it is the first thing to do here.**

### Honest comparison

| | Chrome + CDP (today) | Desktop + MCP (proposed) |
|---|---|---|
| Read live chart state | screenshot only | yes, structured |
| Draw on the chart | no | yes |
| Add/configure indicators | no | yes |
| Switch timeframes | URL | yes, and it can sweep them |
| Pine / strategy tester | no | yes - this is the big one |
| Install friction | a .bat and a Chrome flag | node.js + MCP + debug-mode desktop |
| Paid TradingView plan | not required | **required** |
| Stability | documented protocol, survives TV updates | unofficial, unaffiliated with TV, breaks on their schedule |
| Blast radius if it breaks | screenshots stop | the day lane stops |

### Recommendation

**Add it for the DayTrader lane. Do not replace `tv.py`.**

The day lane is the one that actually needs what the MCP adds - reading bars,
timing a 1-minute pullback, sweeping timeframes, and driving the strategy tester.
The swing lanes need a picture of a chart, which CDP already delivers with far
less to go wrong.

Keeping both is not indecision, it is the same reasoning as one shared exit
guarantee: **the fragile capability rides in the lane that needs it, and the
lane that does not need it cannot be taken down by it.**

Sequencing:

1. Install TradingView desktop from the **direct download**, confirm it takes the
   debug flag. If it does not, the whole question closes and CDP stays.
2. Read the MCP repo before installing anything - the course itself says to, and
   it is unaffiliated with TradingView.
3. Wire it to **DayTrader only**, behind a config flag, default OFF.
4. Everything it produces enters at layer 2. **An MCP reading a chart is a data
   source, and a data source that skips the validator is how a wrong number gets
   quoted with confidence.**

### The trap to avoid

The course's best demo was Claude generating Pine and running the strategy
tester. It is genuinely impressive and it has no overfitting discipline
whatsoever - no sample independence, no walk-forward, no slippage, and a BTC
backtest over the exact window BTC performed in. If the strategy tester comes
into this desk, **the benchmark rule from layer 3 comes with it or it does not
come at all.**
