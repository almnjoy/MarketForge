"""Swing signal engine. PURE (no I/O) so the tests hammer it hard.

This is a deterministic, rules-only trend-pullback framework. The LLM never
invents a strategy (see IMPLEMENTATION-NOTES: assume no durable alpha); it only
annotates/gates candidates that THIS code has already flagged.

Framework (long-only, swing / multi-week horizon):
  Trend filter : close > SMA_SLOW  AND  SMA_FAST > SMA_SLOW
  Entry trigger: price pulled back to the PULLBACK_SMA and reclaimed it
                 (prev close below the fast pullback SMA, latest close back above)
  Initial stop : entry - ATR_STOP_MULT * ATR(ATR_PERIOD)
  Exit         : close < SMA_SLOW (trend break)  [managed elsewhere]

All prices are INTEGER CENTS. Bars are dicts {o,h,l,c,v} ascending by time.
"""
from __future__ import annotations

import config


def sma(values, n):
    if len(values) < n or n <= 0:
        return None
    return sum(values[-n:]) / n


def atr(bars, n=config.ATR_PERIOD):
    """Wilder-style ATR in cents. Needs n+1 bars (prev close for true range)."""
    if len(bars) < n + 1:
        return None
    trs = []
    for i in range(1, len(bars)):
        h, l, pc = bars[i]["h"], bars[i]["l"], bars[i - 1]["c"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    if len(trs) < n:
        return None
    return sum(trs[-n:]) / n


def trend_ok(closes, cfg=config):
    fast = sma(closes, cfg.SMA_FAST)
    slow = sma(closes, cfg.SMA_SLOW)
    if fast is None or slow is None:
        return False
    return closes[-1] > slow and fast > slow


def _reclaim(closes, cfg=config):
    """True on the bar where price crosses back UP through the pullback SMA:
    previous close below it, latest close at/above it. This keeps us from chasing
    extended names and gives a defined stop."""
    if len(closes) < cfg.PULLBACK_SMA + 1:
        return False
    prev_sma = sma(closes[:-1], cfg.PULLBACK_SMA)
    cur_sma = sma(closes, cfg.PULLBACK_SMA)
    if prev_sma is None or cur_sma is None:
        return False
    return closes[-2] < prev_sma and closes[-1] >= cur_sma


def signal(bars, cfg=config):
    """Return the entry decision for the LATEST bar.

    {action: 'buy'|'hold', entry_cents, stop_cents, atr_cents, trend, reason}
    """
    need = max(cfg.SMA_SLOW, cfg.ATR_PERIOD + 1, cfg.PULLBACK_SMA + 1)
    if len(bars) < need:
        return {"action": "hold", "reason": f"insufficient_history (<{need} bars)",
                "entry_cents": None, "stop_cents": None, "atr_cents": None, "trend": False}

    closes = [b["c"] for b in bars]
    t_ok = trend_ok(closes, cfg)
    a = atr(bars, cfg.ATR_PERIOD)
    entry = closes[-1]

    if not t_ok:
        return {"action": "hold", "reason": "trend_filter_failed", "entry_cents": entry,
                "stop_cents": None, "atr_cents": a, "trend": False}
    if not _reclaim(closes, cfg):
        return {"action": "hold", "reason": "no_pullback_reclaim", "entry_cents": entry,
                "stop_cents": None, "atr_cents": a, "trend": True}
    if a is None or a <= 0:
        return {"action": "hold", "reason": "atr_unavailable", "entry_cents": entry,
                "stop_cents": None, "atr_cents": a, "trend": True}

    stop = int(round(entry - cfg.ATR_STOP_MULT * a))
    if stop <= 0 or stop >= entry:
        return {"action": "hold", "reason": "bad_stop", "entry_cents": entry,
                "stop_cents": stop, "atr_cents": a, "trend": True}

    return {"action": "buy", "reason": "trend_pullback_reclaim", "entry_cents": entry,
            "stop_cents": stop, "atr_cents": round(a, 1), "trend": True}


def exit_signal(bars, cfg=config):
    """Trend-break exit for a held position: close below the slow SMA."""
    closes = [b["c"] for b in bars]
    slow = sma(closes, cfg.SMA_SLOW)
    if slow is None:
        return {"exit": False, "reason": "insufficient_history"}
    if closes[-1] < slow:
        return {"exit": True, "reason": "close_below_slow_sma"}
    return {"exit": False, "reason": "trend_intact"}


def avg_dollar_volume(bars, n=20):
    """Liquidity proxy in dollars: mean(close_cents/100 * volume) over last n bars."""
    if len(bars) < n:
        n = len(bars)
    if n == 0:
        return 0.0
    recent = bars[-n:]
    return sum((b["c"] / 100.0) * b["v"] for b in recent) / n
