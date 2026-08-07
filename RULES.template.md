# Agent Rules

**This is your trading plan, not the app's.** It ships as a starting point and it
is deliberately conservative, because the safest default for a document you have
not read yet is one that struggles to lose much money. Replace it with your own.

The copilot reads this file every turn and treats it as binding. So this is the
one place where writing down what you actually intend changes what the software
does.

Nothing here is financial advice. Trading involves substantial risk of loss.

---

## How to read this file

The **numbers below are documentation, not settings.** The live values come from
the engine and render on the left side of the Rules tab. If the two ever disagree,
the engine is right and this file is stale.

To change a value: edit `bot/.env`, then restart the desk. To change the *plan*,
edit this file (Admin → Files, or any text editor).

---

## Entry gates

Every gate must pass. They fail closed, so a gate that cannot be evaluated blocks
the entry rather than waving it through.

| Gate | Ships as | What it defends against | Lives in |
|---|---|---|---|
| Master switch | **off** | Everything. Nothing auto-trades until you deliberately turn it on | `LIVE_AUTO_ENABLED` |
| Quality | score ≥ 85 and verdict `signal` | Acting on a move with no real catalyst behind it | `RADAR_AUTO_MIN_SCORE` |
| Data trust | every % re-verified against the live tape | Screeners lie: stale prints, split artifacts, IPO first-day noise | built in, not tunable |
| Price floor | $3.00 | The sub-$3 halt-and-spike junk tier, where fills are unreliable | `RADAR_AUTO_MIN_PRICE_CENTS` |
| Size | $50 per trade, whole shares | One bad idea being able to matter | `RADAR_AUTO_NOTIONAL_CENTS` |
| Frequency | 2 entries per day | Machine-gunning a bad day into a worse one | `RADAR_AUTO_MAX_PER_DAY` |
| Total exposure | $150 | Correlated positions adding up to one big bet | `RADAR_AUTO_MAX_EXPOSURE_CENTS` |
| Kill switch | armed | Drawdown compounding while you are away | breaker, always on |
| Dedup | never adds to a held symbol | Averaging down by accident | built in |

**Why the shipped sizes are small.** Not because small is correct, but because
you have not chosen yet. $50 against a $1,000 account is a position you can be
wrong about eleven times and still be in the game. Size is the first thing an
experienced trader should change and the last thing a new one should.

## Exits

This is the part that decides whether a strategy survives contact with a bad week.

**Default: a GTC trailing stop, armed at fill.** The entry is a two-step: buy,
confirm the fill, then attach the stop. Every entry gets one. If the fill is slow,
the order goes on a disk-backed queue and a watcher arms the stop the moment it
lands.

Trailing stops ride winners and cut faders, at the cost of giving back some of the
peak. Know the tradeoffs before you widen or tighten:

- **Too tight** and normal volatility stops you out of a thesis that was working
- **Too wide** and you give back most of an unrealized gain before it triggers
- A trigger fires a **market** sell, so thin names can fill below your stop
- **Gaps gap.** A stop is not a floor. Overnight news does not respect it

**Alternative:** set `RADAR_AUTO_EXIT=bracket` for a fixed take-profit and
stop-loss attached at entry instead. Simpler to reason about, and it caps your
upside on purpose.

### The one rule worth keeping

**No naked entries.** Whatever else you change, do not remove the requirement that
every position carries an exit. The app enforces this in three places: at fill, on
a queue for slow fills, and on a 30-second sweep that arms anything it finds
uncovered. That redundancy exists because a position without a stop was the single
most expensive bug in this project's history.

## Know your worst realistic day

Work this out for your own settings and write it here. With the shipped defaults:

> 2 entries x $50, both stopping out around -6 to -10%, is about **-$10**, plus
> gap risk.

If you cannot state this number for your own configuration, your position sizing
is a guess.

## Manual lane

The copilot **stages** trades. You click. It is contractually forbidden from
placing an order or changing a risk setting without your explicit instruction in
that conversation, and "just do it" is not that instruction.

The trade ticket pre-fills a trailing stop before you can submit. A paper account
stays available (`trades-paper.db`) for testing an idea that you would not put
money behind yet.

---

## Your plan

*Replace everything below with how you actually trade. The copilot reads it and
will hold you to it, which is the entire point.*

**What I trade.** Which setups, which sectors, which market caps. What I
deliberately do not touch.

**What I need to see before entering.** The specific conditions. Be strict enough
that the copilot can tell you when you are about to break your own rule.

**How I size.** Fixed dollar, percent of account, volatility-scaled.

**How I exit.** Winners and losers, and what makes you override the stop.

**When I stop for the day.** A loss number, a number of trades, or a feeling you
have learned to recognise.

**What I am currently working on.** The copilot can hold you to one experiment at
a time instead of drifting between three.
