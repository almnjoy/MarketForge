"""Short signal engine. PURE (no I/O), mirror of signals.py.

Where this comes from
---------------------
Ariel Hernandez's short rules, taken from 35 transcripts of his daily recaps
(2026-05-30 to 2026-08-10, in research/ariel-hernandez/). His published playbook
does not mention shorting at all, so none of this is in the ChartFanatics
writeup. It is only on the tape.

The one rule he repeats more than any other:

    "Please do not short in the hole unless you absolutely hate your money."
    "Shorting in the hole does work [sometimes]. The problem is it'll teach you
     some bad habits that you'll take with you for later."

And the setup that replaces it:

    "If you want to get short some names, short against declining moving
     averages. Short against the declining 10, short against the declining 20,
     wait until the 50 comes into play."

    "Rallying up into declining moving averages. Take it down to a smaller time
     frame."

So the structure is his long process with the sign flipped. Longs buy strength
reclaiming a RISING moving average. Shorts sell weakness rallying INTO a FALLING
one. The moving average is both the trigger and the invalidation, which is what
makes the stop tight and definable.

The gate that does the real work
--------------------------------
`atr_extension()` is his own metric, stated on the XLK:

    "When we're 10 times its ATR from the 50... you just went from 10 back down
     to four. So you just went from very extended [to not]."

Distance from the 50 SMA measured in ATRs. A name 10 ATRs BELOW its 50 is
precisely the "in the hole" short he warns about. A name that has rallied back to
within a couple of ATRs of a declining 50 is the setup. That single number is the
difference between his rule and the exact opposite of his rule, so it is a hard
gate here, not a score.

Conventions match signals.py: all prices are INTEGER CENTS, bars are dicts
{o,h,l,c,v} ascending by time, and the module never does I/O.
"""
from __future__ import annotations

import config
from signals import atr, sma


def _cfg(cfg, name, default):
    """Read a knob with a fallback so this module imports cleanly against an
    older config.py (and so the unit tests can pass a bare namespace)."""
    return getattr(cfg, name, default)


def ma_slope(closes, period, lookback=5):
    """Change in the `period` SMA over the last `lookback` bars, in cents.

    Negative means the average is DECLINING, which is the only kind Ariel will
    short into. Returns None when there is not enough history to compute both
    ends of the comparison.
    """
    if len(closes) < period + lookback:
        return None
    now = sma(closes, period)
    then = sma(closes[:-lookback], period)
    if now is None or then is None:
        return None
    return now - then


def atr_extension(bars, period=50, atr_period=None):
    """How far price sits from the `period` SMA, measured in ATRs.

    Sign convention: POSITIVE means price is ABOVE the average (extended long,
    do not add new long exposure), NEGATIVE means BELOW (extended short, this is
    "in the hole"). Ariel reads it as a magnitude; the sign is what lets one
    function serve both lanes.

    Returns None when the SMA or ATR is unavailable, and the callers treat that
    as "stand down" rather than as zero.
    """
    atr_period = atr_period or config.ATR_PERIOD
    closes = [b["c"] for b in bars]
    ma = sma(closes, period)
    a = atr(bars, atr_period)
    if ma is None or a is None or a <= 0:
        return None
    return (closes[-1] - ma) / a


def _rallying_into(bars, ma_period, tolerance_pct):
    """True when the LAST bar is a failed rally into a moving average from below.

    Three things must all hold, and each one is doing a job:
      1. the bar TAGGED the average (high reached within `tolerance_pct` of it)
         - this is the "rallying up into" part
      2. it CLOSED back below the average
         - the rally failed. without this you are shorting into strength, which
           is the parabolic-exhaustion trade he has no published rules for
      3. price is CLOSER to the average than it was `lookback` bars ago
         - confirms it is rallying INTO resistance rather than just drifting

    Returns (ok, ma_cents, reason).
    """
    closes = [b["c"] for b in bars]
    ma = sma(closes, ma_period)
    if ma is None:
        return False, None, "ma_unavailable"

    last = bars[-1]
    if last["c"] >= ma:
        return False, ma, "closed_above_ma"

    if last["h"] < ma * (1.0 - tolerance_pct):
        return False, ma, "did_not_tag_ma"

    prior_ma = sma(closes[:-3], ma_period) if len(closes) > ma_period + 3 else None
    if prior_ma is not None:
        was = prior_ma - closes[-4]
        now = ma - closes[-1]
        if now >= was:
            return False, ma, "not_approaching_ma"

    return True, ma, "failed_rally_into_declining_ma"


def short_signal(bars, cfg=config):
    """Entry decision for the LATEST bar, short side only.

    {action: 'short'|'hold', entry_cents, stop_cents, atr_cents, extension,
     ma_cents, reason}

    Never returns 'short' on a missing input. Every unavailable value routes to
    'hold', same as the long engine and same as regime.read()'s 'unknown'.
    """
    trend_ma = _cfg(cfg, "SHORT_TREND_SMA", 50)
    entry_ma = _cfg(cfg, "SHORT_ENTRY_SMA", 20)
    slope_look = _cfg(cfg, "SHORT_SLOPE_LOOKBACK", 5)
    max_below = _cfg(cfg, "SHORT_MAX_ATR_BELOW", 4.0)
    tol = _cfg(cfg, "SHORT_MA_TAG_TOLERANCE", 0.01)
    stop_mult = _cfg(cfg, "SHORT_STOP_ATR_BUFFER", 0.25)
    atr_period = _cfg(cfg, "ATR_PERIOD", 14)

    need = max(trend_ma + slope_look, entry_ma + slope_look, atr_period + 1) + 1
    base = {"action": "hold", "entry_cents": None, "stop_cents": None,
            "atr_cents": None, "extension": None, "ma_cents": None}
    if len(bars) < need:
        return {**base, "reason": f"insufficient_history (<{need} bars)"}

    closes = [b["c"] for b in bars]
    entry = closes[-1]
    a = atr(bars, atr_period)
    ext = atr_extension(bars, trend_ma, atr_period)
    base = {**base, "entry_cents": entry,
            "atr_cents": round(a, 1) if a else None, "extension": ext}

    if a is None or a <= 0:
        return {**base, "reason": "atr_unavailable"}
    if ext is None:
        return {**base, "reason": "extension_unavailable"}

    # 1. Broken, not merely pulling back.
    trend_sma = sma(closes, trend_ma)
    if trend_sma is None or entry >= trend_sma:
        return {**base, "reason": "above_trend_sma"}

    # 2. The average has to be FALLING. A flat or rising 50 with price under it
    #    is a pullback in an uptrend, which is a long setup, not a short one.
    slope = ma_slope(closes, trend_ma, slope_look)
    if slope is None:
        return {**base, "reason": "slope_unavailable"}
    if slope >= 0:
        return {**base, "reason": "trend_sma_not_declining"}

    # 3. THE RULE. Do not short in the hole. `ext` is negative below the 50, so
    #    a name 10 ATRs down reads -10 and is rejected here.
    if ext < -abs(max_below):
        return {**base, "reason": f"in_the_hole ({ext:.1f} ATR below {trend_ma}sma)"}

    # 4. Failed rally into the faster declining average.
    ok, ma, why = _rallying_into(bars, entry_ma, tol)
    base = {**base, "ma_cents": int(round(ma)) if ma else None}
    if not ok:
        return {**base, "reason": why}

    entry_slope = ma_slope(closes, entry_ma, slope_look)
    if entry_slope is None or entry_slope >= 0:
        return {**base, "reason": "entry_sma_not_declining"}

    # 5. Stop goes above STRUCTURE, not a round number: the higher of the
    #    average it just failed at and the day's high, plus a small ATR buffer so
    #    a one-tick poke through does not take you out.
    stop = int(round(max(bars[-1]["h"], ma) + stop_mult * a))
    if stop <= entry:
        return {**base, "reason": "bad_stop"}

    return {**base, "action": "short", "stop_cents": stop,
            "reason": f"failed_rally_into_declining_{entry_ma}sma"}


def cover_signal(bars, cfg=config):
    """Trend-break exit for a held SHORT: close back above the trend SMA.

    Mirror of signals.exit_signal(). The trailing stop in api.arm_trail() is the
    mechanical guard; this is the discretionary one the manager can consult.
    """
    trend_ma = _cfg(cfg, "SHORT_TREND_SMA", 50)
    closes = [b["c"] for b in bars]
    ma = sma(closes, trend_ma)
    if ma is None:
        return {"exit": False, "reason": "insufficient_history"}
    if closes[-1] > ma:
        return {"exit": True, "reason": f"close_above_{trend_ma}sma"}
    return {"exit": False, "reason": "downtrend_intact"}
