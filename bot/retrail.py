"""One-off maintenance: re-leash an EXISTING long position at a different trail width.

Why this exists: arm_trail() deliberately refuses to double up on a position that
already has a working exit, and there is no cancel endpoint. So tightening a trail
by hand meant cancelling at the broker, which drops the position into the desk's
30s sweep - and the sweep re-arms at the DEFAULT width, which is exactly the 10%
we were trying to get away from. That race is what put a 10% stop back on CSCO
after it was cancelled in Alpaca by hand.

This does the whole swap in one process: cancel every working exit on the symbol,
arm the new width immediately, then verify and repair if the sweep beat us to it.
The position is naked for about a second, and is never left that way.

Deliberately does NOT import bot.src.api - that module starts the protect worker
thread at import time, so importing it here would run a SECOND sweep alongside
the live desk's. It talks to the broker client directly instead.

    python bot/retrail.py CSCO 5
    python bot/retrail.py CSCO 5 --paper
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from alpaca_client import AlpacaClient          # noqa: E402


def working_exits(client, symbol):
    raw = client._req("GET", client.trade_base, "/v2/orders",
                      params={"status": "open", "limit": 100}) or []
    return [o for o in raw
            if o.get("symbol") == symbol
            and str(o.get("side")) == "sell"
            and str(o.get("status")) in ("new", "accepted", "held",
                                         "partially_filled", "pending_new")]


def covered_qty(orders):
    return sum(float(o.get("qty") or 0) for o in orders)


def main(symbol: str, trail_pct: float, venue: str = "live") -> int:
    if venue == "paper":
        import paper
        client = paper.client()
    else:
        client = AlpacaClient()
    print(f"venue: {venue}")
    pos = {p.get("symbol"): p for p in (client.list_positions() or [])}
    if symbol not in pos:
        print(f"no open position in {symbol}")
        return 1
    raw_qty = float(pos[symbol].get("qty") or 0)
    if raw_qty <= 0:
        print(f"{symbol} is flat or short - this script only handles longs")
        return 1
    want = int(abs(raw_qty))
    print(f"{symbol}: {raw_qty} shares @ {pos[symbol].get('avg_entry')}, "
          f"last {pos[symbol].get('price')}")

    for attempt in range(1, 5):
        cur = working_exits(client, symbol)
        stale = [o for o in cur
                 if abs(float(o.get("trail_percent") or 0) - trail_pct) > 1e-6]
        for o in stale:
            client.cancel_order(o["id"])
            print(f"cancelled {o.get('type')} {o.get('qty')}sh trail "
                  f"{o.get('trail_percent')}% stop {o.get('stop_price')} ({o['id'][:8]})")
        if stale:
            time.sleep(1.0)                       # let the broker release the shares

        good = covered_qty([o for o in working_exits(client, symbol)
                            if abs(float(o.get("trail_percent") or 0) - trail_pct) <= 1e-6])
        need = want - int(good)
        if need < 1:
            print(f"{symbol} fully covered at {trail_pct}%")
            break
        t = client.submit_trailing_stop_sell(symbol=symbol, qty=need,
                                             trail_percent=trail_pct)
        print(f"armed {need}sh trailing stop {trail_pct}% -> {t.get('id')} "
              f"status {t.get('status')}")
        time.sleep(1.0)
        if covered_qty(working_exits(client, symbol)) >= want:
            break
        print(f"still short of full coverage, retrying (attempt {attempt})")

    time.sleep(1.5)
    print("--- final state ---")
    for o in working_exits(client, symbol):
        print(f"  {o.get('qty')}sh {o.get('type')} trail {o.get('trail_percent')}% "
              f"stop {o.get('stop_price')} status {o.get('status')}")
    return 0


if __name__ == "__main__":
    argv = [a for a in sys.argv[1:] if a != "--paper"]
    venue = "paper" if "--paper" in sys.argv else "live"
    sym = (argv[0] if argv else "").upper().strip()
    pct = float(argv[1]) if len(argv) > 1 else 5.0
    if not sym:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(sym, pct, venue))
