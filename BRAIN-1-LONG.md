# BRAIN 1 - the long lane

The desk's existing plan, plus the parts of Ariel Hernandez's method that
actually survive a $1,000 cash account. Written 2026-08-06.

This does not replace `RULES.md`. RULES.md is the account-level cap sheet and the
bot's auto gates. This is the human playbook that decides *whether to act* on
what the radar surfaces.

---

## The one structural change: regime comes first

The radar is unchanged. It still scans catalysts on the 14/16/18/20 UTC schedule
and still scores them. What changed is that a score is no longer sufficient
reason to buy.

New read: `GET /api/bot/regime` -> `green` / `yellow` / `red` / `unknown`.
It scores SPY, QQQ and IWM against their 10/20/50/200 day averages plus the
golden-cross state, 15 checks total.

| Regime | What it means | What you may do |
|---|---|---|
| **GREEN** | ≥80% of trend checks pass | Full playbook. Both entries/day available. |
| **YELLOW** | 45-80% | One position, half size ($25). Breakouts fail here most. |
| **RED** | <45% | **No new longs.** This is Brain 2's environment. |
| **UNKNOWN** | Could not read the tape | Stand down. Do not guess. |

Why this matters more than any setup: Ariel's own account of 2022 is that his
strategy did not stop working, the environment stopped being conducive to it. A
score-90 earnings gap in a bleeding tape is still a bad long. The radar could not
tell the difference before. Now it can.

**The radar keeps its job.** Regime does not filter alerts out of the list. Every
catalyst still shows. Regime tells you which of them are actionable today.

---

## What to trade

Four setups, in order of how well they fit a cash account. All long only, all
with the stop defined before entry.

### 1. Flat Base Breakout *(best fit)*
Weeks of tight sideways after a prior advance, 10 and 20 DMA converging up into
price, daily ranges contracting near the highs.

- **Entry:** above prior day's high, which also clears the base
- **Stop:** prior day's low if the breakout candle is tight; low of the breakout day if it is wide or wicky
- **Why it fits:** no gap, no chase, entry level known the night before

### 2. Undercut & Rally *(best fit)*
Price knifes below an obvious prior low, runs the stops, then reclaims it.

- **Entry:** on the reclaim of that prior low
- **Stop:** the new swing low made during the undercut
- **Why it fits:** you are buying weakness into strength, the opposite of chasing. Works best when the market is stabilising off a low.

### 3. MA Undercut & Rally
Same shape, but the level is the 10/20/50 DMA instead of a swing low.

- **Entry:** reclaim of the moving average, ideally on expanding volume
- **Stop:** low of the reclaim day

### 4. Delayed High Volume Close *(use instead of chasing the gap)*
A stock gapped on earnings days ago on multiples of average volume. Draw a line
at that gap day's *close*. Wait for price to consolidate near it and then push
back through.

- **Entry:** on the break back through the high-volume close
- **Stop:** low of the entry day
- **Why it fits:** this is the anti-chase entry. Same institutional accumulation, without paying the gap.

### Not for this account: the Episodic Pivot
The EP is a 5-minute opening-range break on a fresh gapper. It is his highest
tempo entry and it requires being at the screen at 9:30 with a hard stop already
placed. **It is also the exact shape of the VRM trade:** alert at 8.66, fill at
9.36, 8% chased. His version has a defined low-of-day stop; the one you took did
not. Revisit this only after the other three are boring.

---

## Sizing, and what progressive exposure means at $1,000

Ariel scales into winners across a seven-figure book. You cannot do that inside a
$50 cap. The idea still translates, just across *positions* instead of within
one.

**The exposure ladder:**

1. Start with **one** position. That is it.
2. A second position only unlocks once the first is green and trailing.
3. Third only if the second is green. Hard ceiling stays $150 total.
4. Any stop-out drops you back a rung. Two stop-outs in a day, you are done for the day.

That is progressive exposure at small scale: you add risk only after the market
has paid you, never before.

**Risk per trade.** His rule is 0.5-1% of equity. On $1,000 that is $5-10.

Your $50 cap already enforces this. A $50 position with a 10% stop risks $5,
which is 0.5%. Even a wide 15% structural stop only risks $7.50. **The cap is
doing your risk management for you** - the structural stop only changes *where*
you get out, not how much you can lose.

Whole shares only. $50 buys 5 shares of a $10 name, 16 of a $3 name.

---

## Stops: structural, not blanket

Current default is a 10% trailing stop on everything. Keep it as the *floor*, but
prefer the structural level when the setup gives you one:

| Setup | Stop goes at |
|---|---|
| Flat base, tight candle | prior day's low |
| Flat base, wide candle | low of the breakout day |
| U&R | the undercut swing low |
| MA U&R | low of the reclaim day |
| Delayed HVC | low of the entry day |

If the structural stop is more than ~15% away, the setup is too loose. Skip it
rather than widening risk.

Once it is working: trim into strength after 3-5 strong days, trail the rest on
the rising 10 or 20 DMA. Exit without argument if it closes below the trailing MA
on heavy volume.

---

## Selection, before the radar even fires

His nightly process, which costs nothing and is the part he says anyone can do:

1. FinViz industry-group relative strength over **1, 3 and 6 months**. Where is money flowing?
2. Inside the leading groups, filter for price above the **50 and 200 DMA**
3. Add earnings and sales growth quarter over quarter
4. Of what is left, which held up best on the market's last down day?

That last filter is the whole thesis: **the best stocks go down least when the
market pulls back, and the weakest act weak even when the market is strong.**

Your radar finds catalysts. This finds candidates. They are complementary - a
catalyst on a name already in a leading group is worth far more than a catalyst
in isolation.

---

## Expectations, so you do not quit at the wrong moment

- He is wrong roughly **60%** of the time. Some months his win rate is 20%.
- The common outcomes are **small green, small red, flat.** There is no big red, because the stop was set before entry.
- There will be stretches with no setups. That is the strategy working, not failing.
- "Paper cuts" are the cost of tight stops. Expect a run of them.

On a $1,000 account with live P&L on screen, this is a psychology problem before
it is a math problem. Two of three trades losing is normal and expected.

---

## Standing limits (unchanged, from RULES.md)

- $50 per trade, whole shares
- $3.00 price floor. No sub-$3 low-float spikers, ever.
- Max 2 entries per day
- $150 total exposure ceiling
- Every entry carries an exit. No naked positions, no exceptions.
- Never add to a held symbol
- Long only in this account. Cash account, no borrow, no shorting. Shorts live in Brain 2.

## What Brain 1 tells Brain 2

Stop-outs are information, not just losses. If your longs keep getting stopped
while the regime read is still green, the tape is lying to the indicators and you
should size down. If longs stop out *and* regime flips red, that is the handoff
signal to Brain 2.

See `BRAIN-2-SHORT.md`.
