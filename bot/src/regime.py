"""Market regime read. ADDITIVE + READ-ONLY.

Why this exists
---------------
The radar is a CATALYST finder. It answers "what just happened?" It does not
answer "is this a tape where acting on that catalyst pays?" Those are different
questions and the radar was silently conflating them: a score-90 earnings gap in
a market that is bleeding is still a bad long.

This module answers the second question only. It computes nothing about any
individual name. It touches no order path, no positions, no exit logic. It is a
pure function of index bars, and the worst it can do on failure is return
"unknown", which the playbooks treat as "stand down".

Method (deliberately boring, no magic numbers beyond the classic MAs):
  - pull daily bars for SPY / QQQ / IWM
  - for each: price vs 10 / 20 / 50 / 200 SMA, and whether 50 > 200
  - score each index 0-5, average, bucket into green / yellow / red

Cents trap: get_daily_bars returns CENTS. Everything here stays in cents until
the very end, where the JSON payload converts once. Do not mix.
"""
from __future__ import annotations


TRACK = ("SPY", "QQQ", "IWM")
_PERIODS = (10, 20, 50, 200)

# --- Ariel's tape language, added 2026-08-10 -------------------------------
# The MA score above says WHERE we are. These say what the tape is DOING, which
# is the part he actually talks about. All three are additive: read() gains keys,
# no existing key changes meaning, so nothing downstream breaks.
_DIST_WINDOW = 25          # IBD convention: distribution days over ~5 weeks
_DIST_MIN_DROP = 0.002     # a 0.2% decline counts, smaller is noise
_FTD_MIN_GAIN = 0.012      # follow-through day: 1.2%+ on higher volume
_FTD_MIN_DAY = 4           # and no earlier than day 4 of the attempted rally
_FTD_WINDOW = 15


def _sma(closes, n):
    """Simple moving average of the last n closes. None if not enough history."""
    if len(closes) < n:
        return None
    return sum(closes[-n:]) / float(n)


def _score_index(bars):
    """0-5 trend score for one index from its daily bars (closes in cents).

    5 points available: above each of the 4 MAs (4 pts) + 50 above 200 (1 pt).
    Returns (score, detail) or (None, reason) when there is not enough data.
    """
    closes = [b["c"] for b in bars if b.get("c") is not None]
    if len(closes) < 60:
        return None, {"error": f"only {len(closes)} bars, need 60+"}

    last = closes[-1]
    mas = {n: _sma(closes, n) for n in _PERIODS}

    score = 0
    above = {}
    for n in _PERIODS:
        ma = mas[n]
        if ma is None:
            above[n] = None
            continue
        above[n] = last > ma
        if above[n]:
            score += 1

    golden = None
    if mas[50] is not None and mas[200] is not None:
        golden = mas[50] > mas[200]
        if golden:
            score += 1

    # How many of the 5 checks could actually be evaluated. With <200 bars the
    # 200 SMA and the golden-cross check are both unavailable, so a raw score of
    # 3 means something different than it does on a full series. Say so rather
    # than pretending.
    available = sum(1 for n in _PERIODS if mas[n] is not None) + (1 if golden is not None else 0)

    return score, {
        "last": last,
        "sma": {str(n): mas[n] for n in _PERIODS},
        "above": {str(n): above[n] for n in _PERIODS},
        "golden_cross": golden,
        "available_checks": available,
    }


def distribution_days(bars, window=_DIST_WINDOW):
    """Count institutional selling days in the recent window.

    A distribution day is a close DOWN at least 0.2% on HIGHER volume than the
    prior session. One is noise. Four or five clustered inside five weeks is the
    classic "the tape has turned" tell, and it is what Ariel is describing when
    he stops taking new long exposure while the indexes still look fine.

    Returns (count, dates) or (None, []) without enough bars.
    """
    if len(bars) < window + 2:
        return None, []
    hits = []
    for i in range(len(bars) - window, len(bars)):
        prev, cur = bars[i - 1], bars[i]
        if not prev.get("c") or not cur.get("c"):
            continue
        change = (cur["c"] - prev["c"]) / float(prev["c"])
        if change <= -_DIST_MIN_DROP and (cur.get("v") or 0) > (prev.get("v") or 0):
            hits.append(cur.get("t"))
    return len(hits), hits


def follow_through_day(bars, window=_FTD_WINDOW):
    """Find the most recent follow-through day, and the low that invalidates it.

    Ariel on why the level matters more than the event:

        "What people have to remember is the follow-through day's low is the
         very important low. That's going to be 707.53, and you don't
         necessarily want to lose it."

    So this returns the LOW as well as the date. A confirmed rally that loses
    that low is a failed rally, and that is a concrete, non-discretionary flip
    from "longs are live" back to "stand down".

    Definition used: a gain of 1.2%+ on higher volume, occurring on day 4 or
    later of an attempted rally measured from the lowest low in the window.
    """
    if len(bars) < window + 2:
        return None
    recent = bars[-window:]
    lows = [b["l"] for b in recent if b.get("l") is not None]
    if not lows:
        return None
    trough = min(range(len(recent)), key=lambda i: recent[i]["l"])

    for i in range(len(recent) - 1, trough, -1):
        day_n = i - trough + 1
        if day_n < _FTD_MIN_DAY:
            continue
        prev, cur = recent[i - 1], recent[i]
        if not prev.get("c"):
            continue
        gain = (cur["c"] - prev["c"]) / float(prev["c"])
        if gain >= _FTD_MIN_GAIN and (cur.get("v") or 0) > (prev.get("v") or 0):
            return {
                "date": cur.get("t"),
                "day_of_rally": day_n,
                "gain_pct": round(gain * 100, 2),
                # the level that invalidates the whole thing
                "ftd_low_cents": cur["l"],
                "still_valid": bars[-1]["c"] > cur["l"],
            }
    return None


def atr_extension(bars, period=50, atr_period=14):
    """Distance from the `period` SMA in ATRs. His own extension metric.

        "When we're 10 times its ATR from the 50... you just went from 10 back
         down to four."

    Positive = above the average (extended, do not pile on new exposure).
    Negative = below (this is the "in the hole" reading the short lane rejects).
    Duplicated from short_signals.atr_extension deliberately: regime.py is a
    pure, dependency-free read of index bars and importing the trading engine
    into it would couple the safe module to the risky one.
    """
    closes = [b["c"] for b in bars if b.get("c") is not None]
    if len(closes) < period or len(bars) < atr_period + 1:
        return None
    ma = _sma(closes, period)
    trs = []
    for i in range(1, len(bars)):
        h, l, pc = bars[i]["h"], bars[i]["l"], bars[i - 1]["c"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    if ma is None or len(trs) < atr_period:
        return None
    a = sum(trs[-atr_period:]) / float(atr_period)
    if a <= 0:
        return None
    return (closes[-1] - ma) / a


def short_posture(regime):
    """Translate the long-side regime bucket into a short-side instruction.

    Straight from BRAIN-2-SHORT.md. Kept here so both lanes read one source of
    truth rather than each hard-coding its own table.
    """
    return {
        "red":     ("primary",     "Downtrend. This is the environment the short lane exists for."),
        "yellow":  ("selective",   "Mixed tape. Shorts selectively, small."),
        "green":   ("stand_down",  "Trend intact. Shorts are counter-trend. Expect to be wrong."),
        "unknown": ("stand_down",  "Could not read the tape. Do not guess."),
    }.get(regime, ("stand_down", "Unrecognized regime. Stand down."))


def _bucket(pct):
    """pct is 0.0-1.0 of available trend checks passed."""
    if pct is None:
        return "unknown"
    if pct >= 0.80:
        return "green"
    if pct >= 0.45:
        return "yellow"
    return "red"


def read(client, limit=220):
    """Compute the current regime. Never raises - returns 'unknown' on failure.

    Returns a dict shaped for both the API and the panels:
      {regime, label, pct, note, indexes: {SPY: {...}, ...}, errors: [...]}
    """
    indexes, errors = {}, []
    total, possible = 0, 0

    for sym in TRACK:
        try:
            bars = client.get_daily_bars(sym, limit=limit)
        except Exception as e:
            errors.append(f"{sym}: {str(e)[:120]}")
            indexes[sym] = {"error": str(e)[:120]}
            continue

        score, detail = _score_index(bars or [])
        if score is None:
            errors.append(f"{sym}: {detail.get('error')}")
            indexes[sym] = detail
            continue

        avail = detail["available_checks"]
        total += score
        possible += avail

        # Additive tape reads. Each is wrapped because a malformed bar must not
        # be able to take down the whole regime call - the MA score is the load
        # bearing part and these are commentary on top of it.
        try:
            dist_n, dist_dates = distribution_days(bars)
        except Exception:
            dist_n, dist_dates = None, []
        try:
            ftd = follow_through_day(bars)
        except Exception:
            ftd = None
        try:
            ext = atr_extension(bars, 50)
        except Exception:
            ext = None

        indexes[sym] = {
            "score": score,
            "of": avail,
            # convert cents -> dollars exactly once, here
            "last": round(detail["last"] / 100.0, 2),
            "sma": {k: (round(v / 100.0, 2) if v is not None else None)
                    for k, v in detail["sma"].items()},
            "above": detail["above"],
            "golden_cross": detail["golden_cross"],
            "distribution_days": dist_n,
            "distribution_dates": dist_dates[-6:],
            "follow_through": (
                {**ftd, "ftd_low": round(ftd["ftd_low_cents"] / 100.0, 2)}
                if ftd else None
            ),
            "atr_extension_50": round(ext, 2) if ext is not None else None,
        }

    pct = (total / float(possible)) if possible else None
    regime = _bucket(pct)

    note = {
        "green":   "Trend intact. Long setups are live. Shorts are counter-trend, expect chop.",
        "yellow":  "Mixed tape. Half size at most, or stand down. This is where breakouts fail.",
        "red":     "Downtrend. No new longs. This is the environment the short lane exists for.",
        "unknown": "Could not read the tape. Treat as stand down - do not guess.",
    }[regime]

    posture, posture_note = short_posture(regime)

    # Fleet-level roll-up of the tape reads, so callers do not have to know
    # which index to look at. Worst case (most distribution, any broken FTD) is
    # the one that matters, so take the max / the failure.
    dists = [v.get("distribution_days") for v in indexes.values()
             if isinstance(v.get("distribution_days"), int)]
    ftds = [v.get("follow_through") for v in indexes.values() if v.get("follow_through")]
    ftd_broken = [f for f in ftds if not f.get("still_valid")]

    return {
        "regime": regime,
        "label": regime.upper(),
        "pct": round(pct, 3) if pct is not None else None,
        "score": total,
        "of": possible,
        "note": note,
        "indexes": indexes,
        "errors": errors,
        # --- additive, 2026-08-10 ---
        "short_posture": posture,
        "short_note": posture_note,
        "max_distribution_days": max(dists) if dists else None,
        "follow_through_active": bool(ftds) and not ftd_broken,
        "follow_through_broken": [f.get("date") for f in ftd_broken],
    }
