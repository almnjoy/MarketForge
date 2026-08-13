# Three-lane restructure - plan

Status: **DRAFT FOR DUSTIN'S REVIEW. Nothing built.** 2026-08-13.

Goal in his words: three lanes in the menu, three separate scanners that do not
depend on each other, one brain per lane, brain docs in `TheTradingBrains/`, and
the copilot as the orchestrator that knows which lane a question belongs to.

---

## 1. SETTLED - the lane model (Dustin, 2026-08-13)

**Two brains, three lanes.** My "two axes" objection was answered by a better model
than the one I proposed, so it is dropped.

| Lane | Brain | Venue | Behaviour |
|---|---|---|---|
| **Daily Trader** | Brain 3 - momentum | paper first, live when proven | its own everything |
| **Live Trader** | the swing brain | live | **stages**, waits for a click |
| **Paper Trader** | the SAME swing brain | paper | **auto-fires everything** |

His words: *"the paper lane and the live lane are the same brain in that regard, but
they still have slightly different purposes... we kinda duplicate what we need to in
the paper lane to understand and learn it and decide if we want to do it in the live
lane."*

So Paper is not a separate strategy - it is the **always-on shadow of Live**. Ask for
something in the Live lane and Paper fires it too, automatically. Shorting is a
capability of the shared swing brain that only the paper venue can currently execute,
because the live account is $1,023 of cash under the $2,000 Reg T minimum. When that
changes, nothing about the model has to change - the live venue just gains shorts.

This is already what `/api/plan` does (paper fires, live stages). The restructure
formalises it rather than inventing it.

### The one thing the mirror must fix

**Mirroring by SHARE COUNT produces a false record.** Today it took a human to notice:
1 live CSCO share was 11.4% of a $1,023 account, and the "matching" 99 paper shares
was 11.07% of $100k - close by luck. When live became 2 shares (22.09%), the correct
paper twin became 198, not 99. Nobody would have caught that in a month of journals.

The mirror must be **percentage-matched, computed, and automatic**:

```
paper_qty = round(live_notional / live_equity * paper_equity / price)
```

Same percentage, same R, so `sizing_math()` stays scale-invariant and the paper leg
is a record of the live trade rather than a different trade wearing the same ticker.

### Naming

Folders say `DayTrader / MoneyTrader / PaperTrader`; the menu says `Daily / Live /
Paper`. **Use the folder names everywhere** - they are already on disk and already
have his content in them.

---

## 2. Delete the monolith, but EXTRACT before deleting

`radar.py` is 531 lines and most of them are yesterday's bug fixes:

- `_ScanLock` - cross-process disk lock (fixed CWVX scoring twice)
- claim-before-scoring (the other half of that same fix)
- candidate dedupe (the movers feed handing the loop the same symbol twice)
- `_scanlog_write` - every decision including rejections, which is the Scoring tab
- leveraged-wrapper filter and company-over-ETP ranking

**Deleting radar.py wholesale re-opens all of it, three times over.** Splitting into
three scanners without extracting first means three copies of the same bugs, and
they will not be fixed in lockstep.

Plan: lift the mechanics into `bot/src/scanner_core.py`, leave behind only the
long-lane gates, then the three lane scanners are thin:

```
scanner_core.py      lock, claim, dedupe, scanlog, ranking  (ONE copy, shared)
  scan_daily.py      Brain 3 gates: $0.50-20, relative volume, minute pattern
  scan_live.py       Brain 1 gates: Ariel/O'Neil, daily bars, the $3 floor
  scan_paper.py      Brain 2 gates: declining 50, atr_extension, borrow check
```

The three never import each other. They all import `scanner_core`, `alpaca_client`,
`fundamentals`. That is the separation he asked for without three drifting copies of
the Alpaca client.

---

## 3. What is separate, what stays shared

**Separate per lane** - this is the point of the restructure:

- scanner module, its universe and its gates
- its own SQLite file (not a shared table with a lane column - separate files also
  dodge the SMB lock contention that already bit us)
- its own config block, its own scheduler job, its own Overview + Radar pages
- its own brain folder in `TheTradingBrains/`

**Shared, and must NOT be forked:**

- `alpaca_client`, bar fetching, cents math, `fundamentals`, `scanner_core`
- **the exit guarantee and the unprotected sweep.** This one is not negotiable.
  Three lanes each running their own protection watcher is three ways to end up
  with a naked position and three places to fix it. One sweep watches every
  position on both venues, exactly as it does today.

---

## 4. The live tap cannot be split, and its budget is already oversubscribed

Hard constraint: **the free Alpaca plan allows one concurrent websocket connection
per key.** Three lanes cannot have three taps. One connection, 30 subscriptions,
15 symbols across trades+quotes, shared.

Worse, the budget is already gone before any scanning:

```
live positions   4   (CSCO, NBIS, SMCI, VRM)
paper positions 10   (AAPL, CLW, CRWG, CRWV, FF, HQI, HURN, META, NBIS, SMCI)
indices          3   (SPY, QQQ, IWM)
                ---
                17   for 15 slots
```

So a priority order has to be written down and enforced:

1. **live positions** - real money, always
2. **indices** - the regime read
3. **Daily Trader's active candidates** - the lane that actually needs the second
4. **paper positions last** - paper is a record, not money; delayed is survivable

And the bug from this morning must be fixed as part of this: the tap picks its
symbols **once at startup and never refreshes**, so positions opened after launch
are silently absent while the chip reads a green 14/14.

---

## 4b. The data decision for the day lane - SETTLED ENOUGH TO ACT

He asked whether to get "another IEX account or broker account." Answering that
directly: **a second Alpaca key does not fix this.** It buys a second websocket
connection - 30 more subscriptions - but the feed is still IEX-only, still ~2% of
volume, and still cannot scan the market for today's gapper. It doubles how many
symbols you can watch and changes nothing about what you can see, or about finding
the name worth watching in the first place.

What Brain 3 actually needs is two different things, and conflating them is what
makes this look expensive:

1. **A market-wide SNAPSHOT every N seconds** - to find today's movers. This is a
   screen, not a stream. It does not require streaming 5,000 symbols.
2. **A real-time STREAM on the 5-10 that matter** - for the 1-minute pullback entry.

**Recommendation: Alpaca Algo Trader Plus, $99/mo, one month, then re-evaluate.**

Not because it is the cheapest - Massive (the July 2026 rebrand of Polygon.io) has
tiers from $29 - but because of something already true in this repo:

```
ALPACA_DATA_FEED is read in 7 places across alpaca_client, api, backtest,
config and stream. The whole desk already switches to SIP on one env var.
```

Upgrading the plan is a **one-line config change, not a build**. Full consolidated
tape, real-time, 10,000 rpm, and it simultaneously fixes the 15-minute REST blind
spot for the swing lanes, the tap's 2%-of-volume problem, and the snapshot scanner -
with zero integration work and cancel-anytime. Any other vendor means writing and
maintaining a second data client before knowing whether the pattern is worth trading.

**Sequencing that avoids paying to learn nothing:** the 15-minute restriction only
applies to RECENT data. Historical minute bars are already free and complete. So
build the backtest harness first, measure Brain 3's pattern over real sessions at
zero cost, and let that decide whether the $99 is buying an edge or a hobby.

## 5. One command runs everything

Today `run-portable.bat` starts the desk and `stream.py` is a separate window he
starts by hand - which is exactly how the stale-symbol bug happened.

app.py already supervises the engine as a subprocess with pid tracking, orphan-kill
on startup and crash-restart. **Reuse that machinery**, do not write new
supervision: add the tap and the three scan jobs as supervised children.

Keep `stop.bat`'s refuse-to-close guard. `run-tradingview.bat` stays separate - it
drives a real Chrome and has nothing to do with the desk lifecycle.

---

## 6. TheTradingBrains - and a leak to close first

**`TheTradingBrains/` is not in `.gitignore`.** `RULES.md` and `BRAIN-*.md` already
are, described as "personal trading playbooks - not product." These folders are the
same thing reorganised, and right now they would ship his strategy to every user on
the next push. Add the ignore line before writing anything into them.

Consequence: a fresh clone gets empty folders. Mirror the `RULES.template.md`
pattern - ship `TheTradingBrains/_templates/` with a skeleton per lane, seeded on
first run.

Because Live and Paper share a brain, the three folders are NOT three strategies:

```
TheTradingBrains/
  MoneyTrader/     THE SWING BRAIN - canonical. Setups, gates, rules.
    00-BRAIN.md    what this brain is, one page - the file the copilot reads
    10-SETUPS.md   the patterns, codeable, each naming the gate it maps to
    20-RULES.md    his rules for the swing lanes
    90-JOURNAL.md  what actually happened

  PaperTrader/     ONLY THE DIFFERENCES from MoneyTrader. Short file on purpose.
    00-BRAIN.md    "same brain as MoneyTrader, except:" auto-fire, shorts
                   enabled, percentage-matched sizing, and what paper is FOR
    90-JOURNAL.md  the paper record - the study log

  DayTrader/       BRAIN 3 - standalone, shares nothing with the other two
    00-BRAIN.md / 10-SETUPS.md / 20-RULES.md / 90-JOURNAL.md
```

Writing PaperTrader as a full second strategy would be the bug: two documents
describing the same brain drift, and then the mirror is not a mirror. It inherits,
and states only what it overrides.

The gates in code and the setups in the .md must reference each other by name, or
they drift and the .md becomes decoration.

---

## 7. Copilot as orchestrator

Give it a **routing index**, not three brains. Turns already hit 300s; loading every
brain every turn makes that worse for no gain.

```
"radar scan, live data today"  -> Daily Trader -> TheTradingBrains/DayTrader/
"short setups"                 -> Paper Trader -> TheTradingBrains/PaperTrader/
"my positions / swing"         -> Live Trader  -> TheTradingBrains/MoneyTrader/
```

It gets the index in its system context and reads the specific brain file on demand.
This is exactly the pattern `PRODUCTION-1/.claude/CLAUDE.md` already uses - an index
plus "working on X, read Y" - and he already thinks in it.

Cross-lane questions ("what is my total exposure") stay the copilot's job, which is
the argument for it being the only thing that spans lanes.

---

## 8. Order of work

1. `.gitignore` the brains folder **(do first, it is a leak)**
2. Decide naming: option A/B/C, and one set of lane names
3. Extract `scanner_core.py` from `radar.py`, tests green, no behaviour change
4. `scan_live.py` = today's long lane on the new core, prove parity, delete the
   monolith
5. Split the store; migrate or archive existing `radar_alerts`
6. `scan_paper.py` from `short_screen.py`; `scan_daily.py` from `daily_play.py`
   plus `get_intraday_bars()`
7. UI: three lanes, sub-tabs, per-lane Radar
8. Tap: priority order + the refresh fix
9. Supervise tap and scans from app.py
10. Copilot routing index

Steps 1-4 are the risky ones. Everything after is additive.

---

## 9. What this does not fix

Brain 3 still cannot select across the market - that needs a real-time full-tape
feed (~$99/mo). The restructure gives it a proper home; it does not give it eyes.

Brain 3's live venue is also capped by T+1 settlement on a cash account: about one
round trip a day on $394 of settled cash. Paper is where it can actually run.
