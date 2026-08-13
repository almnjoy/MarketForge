# DayTrader - Brain 3, intraday momentum

Standalone. Shares nothing with MoneyTrader/PaperTrader except the stop idea.

## What this brain trades
Low-float small caps, intraday, in and out the same session. $0.50-$20.
**This deliberately reverses the $3.00 floor in RULES.md** - that rule keeps
halt-junk out of the swing lanes; this lane fishes there on purpose. Two lanes, two
universes, and they must never share a screen.

## The setup
Surge, pullback, then the FIRST candle to make a new high. Stop is the pullback low.
Not an ATR multiple, not a percentage - the structural low, because that is the price
at which the pattern stops being true. Code: `daily_play.find_pullback_entry()`.

## The universe gate
Relative volume against the symbol's OWN median day, not an absolute floor and not a
20-day average. A gapper's signature is that today does not look like its history;
an average test rejects it on the strength of its quiet days.
Code: `daily_play.screen()`.

## What this brain cannot do yet
Select across the market. That needs a real-time full-tape feed. Until then it can
only run the pattern over symbols already chosen, and over history.

## Honest limits
- Supply is shares OUTSTANDING from EDGAR, **not float**. Float is smaller and needs
  a paid source. The code says "supply" everywhere for this reason.
- Live venue is capped by T+1 settlement on a cash account: about one round trip per
  day on settled cash. Paper is where this brain can actually run.
