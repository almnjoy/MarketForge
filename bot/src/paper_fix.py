#!/usr/bin/env python3
"""Repair the paper book after the 2026-08-11 duplicate-fire incident.

What happened: a duplicated bridge turn replayed the same paper order with no
idempotency check. RPD ended at 108 shares from three identical fires, HQI at 58
from two. RPD's 36-share chase entry was the CONTROL LEG of the
chase-versus-retest test, and a control at 3x size is not a control.

READ THIS BEFORE RUNNING --trim
-------------------------------
**You cannot restore an average entry price by trading.** Selling the excess is
the only honest repair, and here is exactly what it does and does not do:

  - Partial sales do NOT change average cost. Sell 72 of 108 and the remaining
    36 still carry the BLENDED average (13.76), not the original first fill
    (13.77). Those are one cent apart, which is $0.36 on 36 shares.
  - Closing all 108 and re-buying 36 is WORSE: you would refill at tomorrow's
    price, which has nothing to do with the entry the experiment is testing.

So: trim to size, then record the intended control price in the journal and use
THAT number in the analysis. The position is the instrument; the journal is the
record. Do not let the position pretend to be the record.

Usage:
    python bot/src/paper_fix.py --status
    python bot/src/paper_fix.py --trim RPD --to 36
    python bot/src/paper_fix.py --trim RPD --to 36 --yes     # skip the prompt
    python bot/src/paper_fix.py --close-fractions            # the 6 naked fractions
"""
from __future__ import annotations

import argparse
import sys

import paper


def _positions():
    c = paper.client()
    return c, (c.list_positions() or [])


def _working(c):
    """{symbol: {side: qty_covered}} of orders that could CLOSE something.

    Quantities, not sides. A trailing stop for 29 shares of a 58-share position
    is not protection for 58, and reporting it as such is how 29 shares end up
    uncovered and invisible.
    """
    try:
        raw = c._req("GET", c.trade_base, "/v2/orders",
                     params={"status": "open", "limit": 200}) or []
    except Exception:
        return {}          # fail closed: unknown means "assume unguarded"
    out = {}
    for o in raw:
        sym = o.get("symbol")
        if not sym:
            continue
        try:
            q = abs(float(o.get("qty") or 0)) - abs(float(o.get("filled_qty") or 0))
        except Exception:
            q = 0.0
        out.setdefault(sym, {}).setdefault(str(o.get("side")), 0.0)
        out[sym][str(o.get("side"))] += max(0.0, q)
    return out


def status():
    c, pos = _positions()
    acct = c.get_account()
    print(f"PAPER account  equity ${acct['equity_cents']/100:,.2f}  "
          f"cash ${acct['cash_cents']/100:,.2f}")
    if not pos:
        print("no open paper positions")
        return
    guarded = _working(c)
    naked, shorts = [], []
    print(f"\n{'sym':<7}{'qty':>10}{'entry':>10}{'now':>10}{'value':>11}{'P/L':>10}  flag")
    for p in sorted(pos, key=lambda x: x["symbol"]):
        q = float(p["qty"])
        frac = abs(q) - int(abs(q)) > 1e-9
        # A long is closed by SELLING, a short by BUYING. Getting this backwards
        # doubles the position instead of failing loudly.
        need = "sell" if q > 0 else "buy"
        size = abs(q)
        covered = float(guarded.get(p["symbol"], {}).get(need, 0.0))
        has_exit = covered + 1e-6 >= size
        flags = []
        if frac:
            flags.append("FRACTIONAL - cannot be trailed")
        if q < 0:
            flags.append("SHORT")
            shorts.append(p["symbol"])
        if not has_exit and not frac:
            flags.append(f"{'PARTIAL' if covered else 'NO'} EXIT "
                         f"({covered:g}/{size:g} covered, needs a {need})")
            naked.append((p["symbol"], q, need, covered, size))
        print(f"{p['symbol']:<7}{q:>10g}{p['avg_entry_cents']/100:>10.2f}"
              f"{p['current_price_cents']/100:>10.2f}"
              f"{p['market_value_cents']/100:>11.2f}"
              f"{p['unrealized_pl_cents']/100:>10.2f}  {' | '.join(flags)}")

    if naked:
        print(f"\n!! {len(naked)} position(s) with NO working exit order:")
        for sym, q, need, covered, size in naked:
            extra = ("  <-- SHORT, and a short's downside has no floor"
                     if q < 0 else "")
            gap = (f"{size - covered:g} of {size:g} uncovered"
                   if covered else f"{size:g} uncovered")
            print(f"   {sym} ({gap}, needs a {need}){extra}")
        print("   Arm one with:  python bot/src/paper_fix.py --protect SYM --trail 10")
        print("   The 30s sweep only runs while the desk is UP. It is not "
              "watching these right now.")

    fires = paper._fires_load()
    if fires:
        print(f"\nfire log ({paper.FIRES_PATH.name}) - one entry per symbol/side/day:")
        for k, v in sorted(fires.items()):
            print(f"  {k:<26} {v.get('at')}  qty {v.get('qty')}  order {v.get('order_id')}")


def trim(symbol, to_qty, assume_yes=False):
    symbol = symbol.upper()
    c, pos = _positions()
    p = next((x for x in pos if x["symbol"] == symbol), None)
    if not p:
        sys.exit(f"no paper position in {symbol}")

    have = float(p["qty"])
    if have < 0:
        sys.exit(f"{symbol} is SHORT ({have}). This tool only trims longs.")
    excess = int(have - to_qty)
    if excess <= 0:
        print(f"{symbol} already at {have:g} shares, target {to_qty}. Nothing to do.")
        return

    avg = p["avg_entry_cents"] / 100
    print(f"{symbol}: {have:g} shares @ ${avg:.2f} blended")
    print(f"  -> SELL {excess} shares, leaving {to_qty}")
    print(f"  -> the remaining {to_qty} keep the BLENDED ${avg:.2f} average.")
    print(f"     A partial sale cannot restore the original fill price. Put the")
    print(f"     intended control price in the journal and analyse against that.")
    if not assume_yes:
        if input("\nproceed? [y/N] ").strip().lower() != "y":
            print("aborted")
            return

    # allow_repeat: this is a deliberate correction, not a duplicated signal, and
    # it must not be blocked by (or consume) today's dedupe key.
    res = paper.place(symbol, "sell", qty=excess, allow_repeat=True,
                      note=f"trim duplicate fires to {to_qty}")
    print(f"\n{res.get('status')}  order {res.get('order_id')}")
    print("Re-run --status once it fills to confirm the size.")


def close_fractions(assume_yes=False):
    """Close every fractional paper position. They can never carry a trailing
    stop, so they are permanently unprotectable and pollute the record."""
    c, pos = _positions()
    fracs = [p for p in pos if abs(float(p["qty"])) - int(abs(float(p["qty"]))) > 1e-9]
    if not fracs:
        print("no fractional paper positions")
        return
    print("fractional positions (unprotectable):")
    for p in fracs:
        print(f"  {p['symbol']:<7}{float(p['qty']):>10g} @ ${p['avg_entry_cents']/100:.2f}")
    if not assume_yes:
        if input("\nclose all of these? [y/N] ").strip().lower() != "y":
            print("aborted")
            return
    for p in fracs:
        try:
            c._req("DELETE", c.trade_base, f"/v2/positions/{p['symbol']}")
            print(f"  closed {p['symbol']}")
        except Exception as e:
            print(f"  FAILED {p['symbol']}: {str(e)[:140]}")


def protect(symbol, trail):
    """Arm a trailing stop on an existing paper position, correct side.

    Works with the desk DOWN, which is the point: the 30s sweep only runs while
    app.py is up, so an overnight naked position has nothing watching it.
    """
    symbol = symbol.upper()
    c, pos = _positions()
    p = next((x for x in pos if x["symbol"] == symbol), None)
    if not p:
        sys.exit(f"no paper position in {symbol}")
    q = float(p["qty"])
    if abs(q) - int(abs(q)) > 1e-9:
        sys.exit(f"{symbol} is FRACTIONAL ({q:g}). Alpaca cannot trail it. "
                 f"Close it instead: --close-fractions")
    side = "sell" if q > 0 else "buy"
    size = abs(q)
    covered = float(_working(c).get(symbol, {}).get(side, 0.0))
    if covered + 1e-6 >= size:
        print(f"{symbol} already fully covered ({covered:g}/{size:g}). Nothing to do.")
        return
    need_qty = int(size - covered)      # top up ONLY the uncovered remainder
    if need_qty < 1:
        print(f"{symbol}: {size - covered:g} uncovered is under one whole share; "
              f"a trailing stop cannot cover it. Close by hand.")
        return
    fn = (c.submit_trailing_stop_sell if side == "sell"
          else c.submit_trailing_stop_buy)
    t = fn(symbol=symbol, qty=need_qty, trail_percent=float(trail))
    print(f"{symbol}: {side} trailing stop armed at {trail}% on {need_qty} "
          f"share(s){' (topping up ' + str(covered) + ' already covered)' if covered else ''} "
          f"(order {t.get('id')})")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--trim", metavar="SYMBOL")
    ap.add_argument("--to", type=int, help="target share count for --trim")
    ap.add_argument("--protect", metavar="SYMBOL", help="arm a trailing stop")
    ap.add_argument("--trail", type=float, default=10.0, help="trail %% for --protect")
    ap.add_argument("--close-fractions", action="store_true")
    ap.add_argument("--yes", action="store_true", help="skip confirmation prompts")
    args = ap.parse_args()

    ok, why = paper.configured()
    if not ok:
        sys.exit(f"paper account not usable: {why}")

    if args.trim:
        if args.to is None:
            sys.exit("--trim needs --to N")
        trim(args.trim, args.to, args.yes)
    elif args.protect:
        protect(args.protect, args.trail)
    elif args.close_fractions:
        close_fractions(args.yes)
    else:
        status()


if __name__ == "__main__":
    main()
