# BRAIN 2 - the short lane

**STATUS: PAPER ONLY. No real short capital until the gates at the bottom of
this file are all closed.** Decided 2026-08-06.

Structured off Ariel Hernandez's short approach. Where his method is documented I
say so; where it is not, I say that too rather than filling the gap.

---

## Why this lane exists at all

Not primarily to make money. Ariel's most useful idea about shorts is that they
are a **market diagnostic**:

> "I need longs to get stopped out to really know we're not in a good
> environment, because all of my longs are getting stopped out but all of my
> shorts are starting to work."

Two books running at once tell you what one cannot. When longs stop out and
shorts start paying, the tape has turned and you have hard evidence instead of a
feeling. When shorts start getting stopped and reclaiming, that is the signal to
rotate back to Brain 1's undercut-and-rally setups.

That feedback loop is the point. Profit is secondary and, at your size, mostly
theoretical.

---

## The core heuristic: invert the chart

His actual documented trick, and it is elegant:

> Put a minus sign in front of the ticker to flip the chart upside down. If it
> looks like a setup you would buy inverted, short it.

So there is no separate short playbook to learn. **The six long setups are the
short setups, upside down.** An inverted flat base is a distribution range about
to break down. An inverted undercut-and-rally is a failed rally into resistance.

Two short tactics he teaches by name:

- **Double top short sale** - price fails at a prior high a second time, entry on the break of the intervening low, stop above the second high
- **The "620 setup"** - he teaches this in his free Chart Academy masterclass. **I have not verified the exact definition and will not guess at it.** Commonly a 6/20 EMA relationship, but confirm from the source before trading it.

Beyond those two, his big shorts (SLV, NFLX) were described by others as
**parabolic exhaustion shorts**, not breakdowns. That is a different and much
harder animal - it means shorting strength, not weakness. I found no published
rule set from him for it. Treat it as undocumented.

---

## What to short, and when

**Regime gate, inverted.** Shorts are a RED and YELLOW tape tool.

| Regime | Short posture |
|---|---|
| **RED** | Primary environment. This is when the lane is live. |
| **YELLOW** | Selectively, small. |
| **GREEN** | Counter-trend. Expect to be wrong. Mostly stand down. |
| **UNKNOWN** | Stand down. |

**Selection is Brain 1's screen with the sign flipped.** Instead of the strongest
names in the strongest groups, you want the **weakest names in the weakest
groups** - specifically the ones that acted weak *even while the market was
strong*. Those are the first to get obliterated when the tape turns. That
asymmetry is the entire filter.

**Setups, in fit order:**

1. **Double top / failed second test** - clean, defined risk, entry known in advance
2. **Breakdown from a distribution range** (inverted flat base) - stop above the range high
3. **Failed breakout** - breaks out, immediately loses the level. Trapped longs are the fuel.
4. **Loss of the 50 DMA on volume** after an extended run

**Do not short:** parabolic moves into strength, low-float squeezers, anything
sub-$5, anything hard-to-borrow, anything into earnings.

---

## Short risk is not long risk. Read this part twice.

This is the whole reason the lane is paper-first.

| | Long | Short |
|---|---|---|
| Max loss | 100%, capped | **unbounded** |
| Position grows when | you are right | **you are wrong** |
| Gap risk | against you, capped | against you, uncapped |
| Can be force-closed | no | **yes - buy-in on recalled borrow** |
| Costs to hold | none | borrow fee, margin interest |

Three specific ways it kills accounts that longs never do:

1. **The position sizes up against you.** A short that doubles is now a 200% position you did not choose. Losses compound in the wrong direction.
2. **Buy-ins.** If the lender recalls the shares you are closed at market, at whatever price, without being asked.
3. **Gaps.** A stop is not a guarantee. A halt-then-reopen 40% higher fills your buy stop 40% higher.

**Your exit is a BUY, not a sell.** Everything in this desk's exit machinery
assumes the exit is a sell. That is the bug below.

---

## Margin mechanics you have to plan around

Verified from Alpaca docs, 2026-08-06:

- **$2,000 minimum equity** to short at all. Your Alpaca account is $1,000, which is exactly why it is cash-only. Your E*TRADE $3K clears it.
- **Maintenance on shorts:** price ≥ $5.00 -> greater of **$5.00/share** or 30%. Price < $5.00 -> greater of $2.50/share or 100%.

Run that: shorting 100 shares of a $9 stock is $900 exposure but ties up **$500**,
because the $5/share floor beats 30%. That is 55% of position value. On a $6
stock it is 83%.

**Consequence for your $3K:** you could carry roughly one 100-share short in a $9
name and almost nothing else. There is no diversification and no scaling. Any
single gap is an account-level event.

Also: the $25,000 pattern-day-trader minimum was eliminated effective **June 4,
2026**, replaced by per-broker intraday margin standards for accounts over
$2,000. So PDT is not the blocker it used to be. Capital and skill still are.

---

## ⚠ The desk cannot handle shorts yet - two real bugs

Found in `bot/src/api.py` on 2026-08-06. **Both must be fixed before any live
short touches Alpaca.**

1. **`unprotected_positions()` filters on `float(p.get("qty")) > 0`.** Alpaca
   reports shorts as *negative* qty. A short position is therefore **invisible to
   the safety sweep.** The red banner would never fire on it. This is the exact
   failure mode that cost real money on VRM, except worse, because on a short the
   downside has no floor.

2. **`arm_trail()` calls `submit_trailing_stop_sell()`.** On a short position a
   sell does not close anything - it **doubles the short.** Hitting Protect on a
   short would make the problem twice as large.

Fix required: detect side from qty sign, and on shorts arm a trailing stop *buy*
to cover. The sweep must count a working **buy** order as protection for a short,
and a working **sell** as protection for a long. Not started - flag it when you
want it done.

---

## Paper protocol

Alpaca paper account, `trades-paper.db`, already exists. Costs nothing.

**Rules for the paper phase:**

- Same $50 notional and same discipline as live. Paper trading at $10,000 a clip teaches nothing transferable.
- Log every trade to `journal.jsonl` with a `note`: setup, level, stop, regime read at entry, outcome.
- Record the trades you **skipped** and why. Ariel's point about watching when a room *avoids* a trade applies to your own log too.
- Judge it on process, not P&L. The question is "did I follow the rule", not "did I make money".

**Gates to close before one dollar of real short exposure:**

- [ ] 20+ paper shorts logged with setup, stop, and regime stamped
- [ ] Win rate and average R computed from your own log, not assumed
- [ ] The two desk bugs above fixed and tested
- [ ] Brain 1 profitable and boring over a full month
- [ ] You can state the buy-in and gap risk from memory without rereading this
- [ ] Decide deliberately: Alpaca funded past $2,000, or E*TRADE stays the short venue

Until every box is checked, this lane is a simulator and a diagnostic. Nothing
more.

---

## Meanwhile: E*TRADE

E*TRADE is already a margin account with $3K, so it is the only venue where you
*could* short today. That is a fact, not a recommendation. I cannot see it, I
have no connector for it, and I will not stage tickets into it while the paper
gates are open.

If you want to move the $3K to Alpaca, that puts both lanes behind one API and
one set of safety machinery - after the bugs are fixed. That is the cleaner
end state.

---

## What Brain 2 tells Brain 1

- Shorts working while longs stop out -> tape has turned, cut long exposure
- Shorts getting stopped and reclaiming -> bottoming, rotate to Brain 1's U&R setups
- Both sides chopping -> no edge, sit out

See `BRAIN-1-LONG.md`.
