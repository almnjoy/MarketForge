#!/usr/bin/env python3
"""Brain 3 pattern tests. Run: python bot/src/test_daily_play.py

Written against the DISTINCTION in each rule, not the numbers, because the numbers
are guesses and the distinctions are the method. A test that only pins constants
passes forever and tells you nothing when the constant was wrong.
"""
from __future__ import annotations

import sys

import daily_play as dp

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'ok  ' if cond else 'FAIL'} {name}" + (f"\n       {detail}" if detail and not cond else ""))


def bar(o, h, l, c, v=100000):
    return {"o": o, "h": h, "l": l, "c": c, "v": v}


print("relative volume")

check("a gapper is caught by comparing today to its OWN median",
      (dp.relative_volume(50_000_000, [148, 900, 1200, 800, 1000]) or 0) > 5)

check("ONE past spike in the baseline does not hide the next one (median, not mean)",
      (dp.relative_volume(10_000_000, [1000, 1000, 900_000_000, 1000, 1000]) or 0) > 5,
      "a mean baseline would be ~180M and this would read as 0.06x - invisible")

check("a normal day on a normal stock is not a signal",
      (dp.relative_volume(1_050_000, [1_000_000] * 5) or 0) < dp.MIN_RELATIVE_VOLUME)

check("no baseline returns None, not a number",
      dp.relative_volume(5_000_000, []) is None,
      "unknown must not be able to masquerade as a pass")

print("\nsupply classification")

check("supply is called supply, never float", "float" not in dp.supply_class(3_000_000))
check("3M shares is micro", dp.supply_class(3_000_000) == "micro_supply")
check("a mega cap is not low supply", dp.supply_class(3_940_000_000) == "heavy_supply")
check("missing data is 'unknown', not 'low'", dp.supply_class(None) == "unknown")

print("\nthe pattern: surge, pullback, first new high")

# surge 100 -> 150, pullback to 130, then a bar taking out the prior bar's high
bars = [bar(100, 105, 99, 104), bar(104, 120, 103, 119), bar(119, 150, 118, 148),
        bar(148, 149, 138, 140), bar(140, 141, 130, 132), bar(132, 145, 131, 144)]
r = dp.find_pullback_entry(bars)

check("a clean pullback produces a trade", r and r.get("ok"), str(r))
check("THE STOP IS THE PULLBACK LOW, not the surge low",
      r["stop_c"] == 130, f"got {r.get('stop_c')}, pullback low was 130, surge low was 99")
check("entry triggers through the prior bar's high, not at the peak",
      r["entry_c"] == 142, f"got {r.get('entry_c')}")
check("risk is entry minus stop and nothing else",
      r["risk_c"] == r["entry_c"] - r["stop_c"])
check("targets are stated in R, not invented dollars",
      r["target_1r_c"] == r["entry_c"] + r["risk_c"]
      and r["target_2r_c"] == r["entry_c"] + 2 * r["risk_c"])

print("\nthe cases that look identical and are not")

# Chasing: no pullback at all, straight up. There is no level behind you.
straight = [bar(100, 110, 99, 109), bar(109, 125, 108, 124), bar(124, 140, 123, 139),
            bar(139, 150, 138, 149), bar(149, 165, 148, 164)]
r2 = dp.find_pullback_entry(straight)
check("straight up with no pullback gives NO trade",
      not (r2 or {}).get("ok"),
      "this is the chase - the whole point is that there is no stop to size against")

# A pullback that gives back nearly everything is the move failing.
failing = [bar(100, 105, 99, 104), bar(104, 150, 103, 148),
           bar(148, 149, 120, 122), bar(122, 123, 105, 108), bar(108, 130, 107, 129)]
r3 = dp.find_pullback_entry(failing)
check("giving back most of the surge is rejected as failure, not consolidation",
      not r3.get("ok") and "failing" in r3.get("reason", ""),
      str(r3.get("reason")))

# Still consolidating, no trigger yet.
pending = [bar(100, 105, 99, 104), bar(104, 150, 103, 148),
           bar(148, 149, 140, 142), bar(142, 143, 138, 139), bar(139, 141, 137, 138)]
r4 = dp.find_pullback_entry(pending)
check("a pullback with no new high yet is 'not yet', not 'no'",
      not r4.get("ok") and "in progress" in r4.get("reason", ""),
      str(r4.get("reason")))

# The bar right after a high: the commonest live state, and the one the old
# message described wrongly as "no bars after the peak".
r5 = dp.find_pullback_entry([bar(100, 105, 99, 104), bar(104, 120, 103, 119),
                             bar(119, 150, 118, 148), bar(148, 149, 140, 142),
                             bar(142, 152, 141, 151)])
check("'not enough bars yet' COUNTS them instead of claiming there are none",
      not r5.get("ok") and "only 1 bar(s)" in r5.get("reason", ""),
      str(r5.get("reason")))

r6 = dp.find_pullback_entry([bar(100, 105, 99, 104), bar(104, 120, 103, 119),
                             bar(119, 130, 118, 129), bar(129, 150, 128, 148),
                             bar(148, 160, 147, 159)])
check("a peak immediately before this bar says exactly that",
      not r6.get("ok") and "nothing has pulled back yet" in r6.get("reason", ""),
      str(r6.get("reason")))

check("too few bars says how many it needed and why",
      "5 bars" in dp.find_pullback_entry([bar(1, 2, 1, 2)] * 3).get("reason", ""))

check("every no-trade path returns a dict with a reason, never a bare None",
      all(isinstance(x, dict) and not x.get("ok") and x.get("reason")
          for x in (dp.find_pullback_entry([]),
                    dp.find_pullback_entry([bar(100, 105, 99, 104)]),
                    dp.find_pullback_entry([bar(150, 150, 100, 101), bar(101, 102, 90, 91),
                                            bar(91, 92, 80, 81)]))),
      "two return shapes made callers handle None and threw away the explanation")

check("an immediate bounce with no real pullback is not a setup",
      not dp.find_pullback_entry(
          [bar(100, 105, 99, 104), bar(104, 150, 103, 148), bar(148, 149, 140, 142),
           bar(142, 155, 141, 154)]).get("ok"),
      "only ONE bar pulled back - there is no consolidation low to stop against")

# THE INVERSION. The whole method turns on this: a trigger that BREAKS OUT above
# the prior high is the setup, and the old code rejected exactly that case while
# accepting bounces that stalled underneath.
breakout = [bar(100, 105, 99, 104), bar(104, 150, 103, 148),
            bar(148, 149, 138, 140), bar(140, 141, 130, 132),
            bar(132, 155, 131, 154)]
r_break = dp.find_pullback_entry(breakout)
check("A BREAKOUT TO A NEW SESSION HIGH IS A TRADE (was rejected before)",
      r_break.get("ok"), str(r_break)[:200])
check("the breakout's stop is still the pullback low",
      r_break.get("stop_c") == 130, str(r_break.get("stop_c")))

print("\nthe universe gate")

ok, f = dp.screen(price_c=180, session_volume=50_000_000,
                  baseline_daily_volumes=[900, 1200, 148, 1000, 800],
                  shares_outstanding=4_000_000)
check("a $1.80 micro-supply gapper passes", ok, str(f))

ok2, f2 = dp.screen(price_c=11500, session_volume=50_000_000,
                    baseline_daily_volumes=[30_000_000] * 5, shares_outstanding=3_940_000_000)
check("CSCO-class mega cap is rejected from THIS lane", not ok2)

ok3, f3 = dp.screen(price_c=180, session_volume=50_000_000,
                    baseline_daily_volumes=[45_000_000] * 5)
check("a stock that is always busy is not a gapper", not ok3,
      "relative volume, not absolute, is what makes it a setup")

check("rejections always explain themselves",
      all("detail" in x and x["detail"] for x in f2),
      "the radar could not answer 'why was this rejected' until the scoring tab existed")

check("supply never blocks, because outstanding is not float",
      all(x["pass"] for x in f3 if x["gate"] == "supply"))

print("\nlane separation")

check("this lane's floor is BELOW the swing lanes' $3.00 rule, deliberately",
      dp.PRICE_MIN_C < 300,
      "RULES.md keeps halt-junk out of the swing lanes; this lane fishes there on purpose")

check("session liquidity is measured on TODAY, not a 20-day average",
      any("not a 20-day average" in x["detail"]
          for x in dp.screen(price_c=100, session_volume=1, baseline_daily_volumes=[1])[1]
          if x["gate"] == "session_liquidity"))

print("\nthe stop must be outside the noise band (2026-08-13 backtest finding)")

# CRWV: entry 6542, stop 6541. One cent of risk on a $65 stock. The first backtest
# scored the resulting two-cent "2R" move as a win.
penny = [bar(6500, 6510, 6499, 6505), bar(6505, 6545, 6504, 6543),
         bar(6543, 6544, 6541, 6542), bar(6542, 6543, 6540, 6541),
         bar(6541, 6550, 6540, 6549)]   # triggers at 6544, stop 6540 = 4c
r_penny = dp.find_pullback_entry(penny)
check("a one-cent stop on a $65 stock is refused",
      not r_penny.get("ok"), str(r_penny)[:160])
check("...and says the R it implies is not real",
      "not real" in r_penny.get("reason", "") or "tick" in r_penny.get("reason", ""),
      r_penny.get("reason", ""))

check("the cent floor is a spread, not a guess", dp.MIN_RISK_CENTS >= 3)
check("there is a PERCENTAGE floor too, so a $500 stock cannot sneak a 5c stop through",
      dp.MIN_RISK_PCT > 0)

# The good SMCI trade from the same run: 3597/3561 = 36c = 1.0%. Must survive.
ok_bars = [bar(3500, 3520, 3495, 3515), bar(3515, 3600, 3510, 3595),
           bar(3595, 3596, 3561, 3570), bar(3570, 3580, 3560, 3568),
           bar(3568, 3620, 3565, 3618)]   # triggers at 3581, stop 3560 = 21c = 0.59%
r_ok = dp.find_pullback_entry(ok_bars)
check("a real 0.6%-wide stop still produces a trade",
      r_ok.get("ok"), str(r_ok)[:200])
check("the floors reject noise WITHOUT rejecting structure",
      r_ok.get("ok") and not r_penny.get("ok"),
      "a filter that kills both is just a smaller sample")

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
