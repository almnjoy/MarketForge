#!/usr/bin/env python3
"""Brain 3 over real historical sessions. Answers one question: is the pattern worth
paying for a live feed?

    python bot/src/backtest_daily.py --symbols SMCI,NBIS,CRWV --days 20
    python bot/src/backtest_daily.py --symbols OFAL --date 2026-08-12 --verbose

WHY THIS EXISTS BEFORE THE $99
------------------------------
The free plan cannot return the last 15 minutes, which is why the day lane cannot
TRADE on it. But history is not restricted - minute bars from yesterday and before
come back complete and free. So the pattern can be measured on real sessions at
zero cost, and the subscription becomes a decision with evidence behind it instead
of a bet on a transcript.

WHAT IT MEASURES, AND WHAT IT REFUSES TO
----------------------------------------
Every trade is measured in **R** - multiples of the distance from entry to the
pullback low. Not dollars. Dollars are a statement about position sizing, and
sizing is not what is being tested here. A 60% win rate at 0.5R is a losing system
and a 35% win rate at 3R is a good one; only R can tell those apart.

It does NOT report a total return, an equity curve, or a percentage gain. Those
numbers require sizing assumptions that would be invented, and an invented number
that looks like a result is worse than no result. This session has produced enough
readouts that were confident and wrong.

HONEST LIMITS - read before believing any number this prints
------------------------------------------------------------
1. **Survivorship by construction.** You supply the symbols. Choosing names you
   remember moving is choosing the winners in advance, and it will inflate every
   statistic here. The real system has to FIND these at 09:25, and that is the part
   the free plan cannot do at all.
2. **Fills are assumed.** Entry at the trigger price, exit at the stop. Real
   low-float names gap through stops; a stop at 1.00 can fill at 0.94. Slippage is
   worst in exactly the names this brain trades.
3. **IEX bars are a sample.** On the free feed these minute bars are a few percent
   of the tape, so highs and lows are understated and some bars are missing. The
   pattern may look cleaner here than it was.
4. **No costs.** No commission (Alpaca is zero) but also no borrow, no halts, and
   no account for the fact that a halted stock cannot be exited at all.

Every one of those pushes results in the SAME direction: optimistic.
"""
from __future__ import annotations

import argparse
import statistics
import sys
from datetime import datetime, timedelta, timezone

import daily_play as dp


def simulate_session(bars, verbose=False):
    """Walk one session bar by bar. Returns a trade dict or a reason it stood down.

    Walk-forward on purpose: at each step the pattern only sees bars up to now, so
    it cannot use a high that has not printed yet. Handing the detector the whole
    session at once and calling the result a backtest is the classic way to measure
    hindsight instead of a method.
    """
    # range(3, len(bars) + 1), NOT len(bars). `bars[:i]` is exclusive, so stopping
    # at len(bars) means the final bar is never part of any slice - a setup that
    # triggers on the last bar of the session is invisible, and every session is
    # silently evaluated one bar short of what actually happened.
    setup = {}
    for i in range(3, len(bars) + 1):
        so_far = bars[:i]
        setup = dp.find_pullback_entry(so_far)
        if not setup.get("ok"):
            continue
        entry, stop = setup["entry_c"], setup["stop_c"]
        risk = setup["risk_c"]
        # Resolve forward from the NEXT bar.
        for j in range(i, len(bars)):
            b = bars[j]
            # Stop first when a single bar covers both. A bar that touches the stop
            # and the target cannot be assumed to have paid: assuming the good half
            # of an ambiguous bar is how a backtest lies.
            if b["l"] <= stop:
                return {"outcome": "stop", "r": -1.0, "entry_c": entry, "stop_c": stop,
                        "exit_c": stop, "bars_held": j - i + 1, "setup": setup}
            mfe = (b["h"] - entry) / risk if risk else 0
            if mfe >= 2.0:
                return {"outcome": "target_2r", "r": 2.0, "entry_c": entry, "stop_c": stop,
                        "exit_c": entry + 2 * risk, "bars_held": j - i + 1, "setup": setup}
        close = bars[-1]["c"]
        return {"outcome": "eod", "r": round((close - entry) / risk, 2) if risk else 0,
                "entry_c": entry, "stop_c": stop, "exit_c": close,
                "bars_held": len(bars) - i, "setup": setup}
    return {"outcome": "no_setup",
            "reason": (setup.get("reason") or "no qualifying pullback")
                      if bars else "no bars"}


def summarize(trades):
    taken = [t for t in trades if t.get("r") is not None and t["outcome"] != "no_setup"]
    if not taken:
        return {"trades": 0, "note": "no setups triggered in the sample"}
    rs = [t["r"] for t in taken]
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    return {
        "trades": len(taken),
        "win_rate_pct": round(len(wins) / len(taken) * 100, 1),
        "expectancy_r": round(statistics.fmean(rs), 3),
        "total_r": round(sum(rs), 2),
        "avg_win_r": round(statistics.fmean(wins), 2) if wins else 0.0,
        "avg_loss_r": round(statistics.fmean(losses), 2) if losses else 0.0,
        "best_r": round(max(rs), 2), "worst_r": round(min(rs), 2),
        "stopped": sum(1 for t in taken if t["outcome"] == "stop"),
        "hit_2r": sum(1 for t in taken if t["outcome"] == "target_2r"),
        "held_to_close": sum(1 for t in taken if t["outcome"] == "eod"),
    }


def verdict(s):
    """State plainly whether the sample can support a conclusion. Usually it cannot."""
    n = s.get("trades", 0)
    if n == 0:
        return ("NO SETUPS. That is a finding, not a failure - it means the gates "
                "did not fire on these names. Widen the symbol list or the date "
                "range before concluding anything about the pattern.")
    if n < 30:
        return (f"{n} trades is TOO FEW TO CONCLUDE ANYTHING. At this sample size the "
                f"expectancy swings wildly on one or two trades. Treat the number as "
                f"a smoke test that the machinery runs, not as evidence of an edge.")
    e = s["expectancy_r"]
    if e > 0.2:
        return (f"Positive expectancy ({e}R) over {n} trades. Worth testing forward on "
                f"paper. Remember every bias in this harness points optimistic - you "
                f"picked the symbols, and the real system has to find them itself.")
    if e > -0.05:
        return (f"Roughly break-even ({e}R) over {n} trades, BEFORE slippage. In these "
                f"names slippage is not a rounding error. This does not support paying "
                f"for a live feed yet.")
    return (f"Negative expectancy ({e}R) over {n} trades, and this harness is "
            f"optimistic by construction. The pattern as coded does not work on this "
            f"sample. Fix the pattern before buying data for it.")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbols", required=True, help="comma list")
    ap.add_argument("--days", type=int, default=10, help="sessions back from yesterday")
    ap.add_argument("--date", help="a single session, YYYY-MM-DD")
    ap.add_argument("--timeframe", default="1Min")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--min-risk-pct", type=float,
                    help="override the stop-width floor (default %(default)s); "
                         "the sensitivity table below shows what it costs")
    ap.add_argument("--min-risk-cents", type=int)
    args = ap.parse_args()

    from alpaca_client import AlpacaClient
    import config
    if args.min_risk_pct is not None:
        dp.MIN_RISK_PCT = args.min_risk_pct
    if args.min_risk_cents is not None:
        dp.MIN_RISK_CENTS = args.min_risk_cents
    client = AlpacaClient()
    print(config.feed_banner())

    syms = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    if args.date:
        dates = [args.date]
    else:
        today = datetime.now(timezone.utc).date()
        dates = [(today - timedelta(days=d)).isoformat() for d in range(1, args.days + 1)]

    trades, skipped = [], 0
    for sym in syms:
        for d in dates:
            try:
                bars = client.get_intraday_bars(sym, timeframe=args.timeframe,
                                                session_date=d)
            except Exception as e:
                print(f"  {sym} {d}: bars unavailable ({str(e)[:80]})")
                continue
            if len(bars) < 10:
                skipped += 1
                continue          # weekend, holiday, or a name that did not trade
            t = simulate_session(bars)
            t["symbol"], t["date"] = sym, d
            trades.append(t)
            if args.verbose or t.get("outcome") not in ("no_setup",):
                if t.get("outcome") == "no_setup":
                    print(f"  {sym} {d}: no setup - {t.get('reason','')[:70]}")
                else:
                    print(f"  {sym} {d}: {t['outcome']:<9} {t['r']:+.2f}R  "
                          f"entry ${t['entry_c']/100:.2f} stop ${t['stop_c']/100:.2f} "
                          f"({t['bars_held']} bars)")

    # THE UNIVERSE GATE, WHICH THE FIRST VERSION NEVER APPLIED.
    # It called find_pullback_entry() directly and never screen(), so the first run
    # measured the day-trading pattern on NBIS at $226 and CRWV at $90 - names this
    # brain would never look at. 28 of 32 trades were outside its own price band.
    # A result computed on the wrong universe is not a weak result, it is a
    # different question answered confidently.
    taken = [t for t in trades if t.get("outcome") not in (None, "no_setup")]
    in_band = [t for t in taken
               if dp.PRICE_MIN_C <= t["entry_c"] <= dp.PRICE_MAX_C]
    out_band = [t for t in taken if t not in in_band]

    s = summarize(trades)
    print(f"\n{'=' * 62}\nBRAIN 3 - {len(syms)} symbol(s) x {len(dates)} session(s)"
          f"   [{skipped} non-sessions skipped]\n{'=' * 62}")
    for k, v in s.items():
        print(f"  {k:<16} {v}")

    if out_band:
        s_in = summarize(in_band)
        print(f"\n  {'-' * 58}\n  UNIVERSE SPLIT - this brain trades "
              f"${dp.PRICE_MIN_C/100:.2f}-${dp.PRICE_MAX_C/100:.2f}")
        print(f"  {len(out_band)} of {len(taken)} trades were OUTSIDE that band and do "
              f"not belong\n  in this measurement at all.")
        print(f"  In-band only: {s_in.get('trades', 0)} trades, "
              f"expectancy {s_in.get('expectancy_r', 'n/a')}R, "
              f"win rate {s_in.get('win_rate_pct', 'n/a')}%")
        print(f"  {'-' * 58}")
        s = s_in          # judge on the real universe, not the flattering one

    # SENSITIVITY, because 0.5% is a number I chose and not one I measured.
    # A filter that only looks right at the value its author picked is a filter
    # tuned to a result. Showing the curve lets the data argue instead.
    if taken:
        print(f"\n  {'-' * 58}\n  STOP-WIDTH SENSITIVITY (in-band trades only)")
        print(f"  {'floor':>8}  {'trades':>7}  {'expectancy':>11}  {'win rate':>9}")
        pool = in_band if out_band else taken
        for floor in (0.0, 0.1, 0.25, 0.5, 1.0, 1.5):
            keep = [t for t in pool
                    if t["setup"]["risk_c"] / t["entry_c"] * 100 >= floor]
            ss = summarize(keep)
            print(f"  {floor:>7.2f}%  {ss.get('trades', 0):>7}  "
                  f"{ss.get('expectancy_r', 0):>10}R  {ss.get('win_rate_pct', 0):>8}%")
        print(f"  {'-' * 58}")
        print("  If expectancy only survives at ONE floor, that is curve-fitting,")
        print("  not a threshold. Look for a plateau, not a peak.")

    print(f"\n{verdict(s)}\n")
    print("Every bias in this harness points the same way - optimistic. You chose the "
          "symbols,\nfills are assumed, IEX bars understate the range, and halts do "
          "not exist here.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
