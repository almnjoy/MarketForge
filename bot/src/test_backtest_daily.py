#!/usr/bin/env python3
"""Backtest harness tests. Run: python bot/src/test_backtest_daily.py

A backtest is the easiest thing in this repo to make lie, and the lies are the
comfortable kind: peeking at bars that had not printed, taking the good half of an
ambiguous bar, reporting a return that came from invented position sizing. These
tests are aimed at the lies, not at the arithmetic.
"""
from __future__ import annotations

import sys

import backtest_daily as bt

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'ok  ' if cond else 'FAIL'} {name}" + (f"\n       {detail}" if detail and not cond else ""))


def bar(o, h, l, c, v=100000):
    return {"o": o, "h": h, "l": l, "c": c, "v": v}


# surge 100->150, pullback to 130, trigger through 141 -> entry 142, stop 130, risk 12
SETUP = [bar(100, 105, 99, 104), bar(104, 120, 103, 119), bar(119, 150, 118, 148),
         bar(148, 149, 138, 140), bar(140, 141, 130, 132), bar(132, 145, 131, 144)]

print("the harness does not peek")

t = bt.simulate_session(SETUP + [bar(144, 170, 143, 168)])
check("a setup is found and resolved", t["outcome"] != "no_setup", str(t))
check("entry and stop come from the pattern, not from hindsight",
      t["entry_c"] == 142 and t["stop_c"] == 130, str(t))

# The detector must never see a bar that has not happened. If it peeked, this
# session - whose huge bar arrives AFTER the trigger - would produce a different
# entry than the same session truncated at the trigger.
truncated = bt.simulate_session(SETUP)
check("the entry is identical with and without future bars",
      truncated["entry_c"] == t["entry_c"],
      "if these differ, the detector is reading bars that had not printed")

print("\nambiguous bars resolve AGAINST the trade")

# One bar that touches both the stop (130) and 2R (166). Assuming the good half is
# the single most common way a backtest invents an edge.
both = bt.simulate_session(SETUP + [bar(144, 170, 125, 160)])
check("a bar hitting stop AND target is counted as a STOP",
      both["outcome"] == "stop" and both["r"] == -1.0,
      f"got {both['outcome']} {both.get('r')}")

print("\noutcomes")

stopped = bt.simulate_session(SETUP + [bar(144, 145, 120, 122)])
check("a stop is exactly -1R, by definition", stopped["r"] == -1.0)

target = bt.simulate_session(SETUP + [bar(144, 168, 143, 166)])
check("2R target pays exactly 2R", target["outcome"] == "target_2r" and target["r"] == 2.0)

eod = bt.simulate_session(SETUP + [bar(144, 150, 143, 148)])
check("an unresolved trade is marked end-of-day, not silently dropped",
      eod["outcome"] == "eod")
check("the end-of-day R is measured, not assumed",
      abs(eod["r"] - (148 - 142) / 12) < 0.01, str(eod["r"]))

flat = bt.simulate_session([bar(100, 101, 99, 100)] * 8)
check("no setup is reported as a finding with a reason",
      flat["outcome"] == "no_setup" and flat.get("reason"))

print("\nthe summary refuses to invent numbers")

s = bt.summarize([{"outcome": "stop", "r": -1.0}, {"outcome": "target_2r", "r": 2.0},
                  {"outcome": "target_2r", "r": 2.0}, {"outcome": "stop", "r": -1.0}])
check("expectancy is the mean R", abs(s["expectancy_r"] - 0.5) < 1e-9, str(s))
check("win rate counts trades, not dollars", s["win_rate_pct"] == 50.0)
check("no total return, no equity curve, no percentage gain",
      not any(k in s for k in ("total_return", "return_pct", "equity", "pnl", "dollars")),
      "those require sizing assumptions that would be invented")
check("no-setup rows are excluded from the stats, not counted as flat trades",
      bt.summarize([{"outcome": "no_setup"}])["trades"] == 0,
      "counting a no-trade as a 0R trade would dilute expectancy toward zero")

print("\nthe verdict tells the truth about the sample")

check("zero trades says so plainly, and calls it a finding",
      "NO SETUPS" in bt.verdict(bt.summarize([])))

small = bt.verdict({"trades": 6, "expectancy_r": 1.4})
check("a tiny sample is refused even when the number looks great",
      "TOO FEW" in small, small)
check("...and it is not sold as an edge", "edge" not in small.lower().split("evidence")[0])

good = bt.verdict({"trades": 50, "expectancy_r": 0.6})
check("a real positive sample still names the optimism bias",
      "optimistic" in good and "you picked the symbols" in good.lower(), good)

flat_v = bt.verdict({"trades": 50, "expectancy_r": 0.0})
check("break-even says NOT YET on the subscription",
      "does not support paying" in flat_v, flat_v)

bad = bt.verdict({"trades": 50, "expectancy_r": -0.4})
check("negative says fix the pattern before buying data for it",
      "before buying data" in bad, bad)

print("\nthe honest-limits block is part of the product, not decoration")

doc = bt.__doc__ or ""
for phrase, why in [("Survivorship", "you choose the symbols"),
                    ("Fills are assumed", "stops gap in these names"),
                    ("sample", "IEX understates the range"),
                    ("No costs", "halts mean you cannot exit at all")]:
    check(f"limits mention: {why}", phrase in doc)
check("and states they all point the same way",
      "SAME direction: optimistic" in doc)

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
