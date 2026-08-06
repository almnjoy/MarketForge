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
        indexes[sym] = {
            "score": score,
            "of": avail,
            # convert cents -> dollars exactly once, here
            "last": round(detail["last"] / 100.0, 2),
            "sma": {k: (round(v / 100.0, 2) if v is not None else None)
                    for k, v in detail["sma"].items()},
            "above": detail["above"],
            "golden_cross": detail["golden_cross"],
        }

    pct = (total / float(possible)) if possible else None
    regime = _bucket(pct)

    note = {
        "green":   "Trend intact. Long setups are live. Shorts are counter-trend, expect chop.",
        "yellow":  "Mixed tape. Half size at most, or stand down. This is where breakouts fail.",
        "red":     "Downtrend. No new longs. This is the environment the short lane exists for.",
        "unknown": "Could not read the tape. Treat as stand down - do not guess.",
    }[regime]

    return {
        "regime": regime,
        "label": regime.upper(),
        "pct": round(pct, 3) if pct is not None else None,
        "score": total,
        "of": possible,
        "note": note,
        "indexes": indexes,
        "errors": errors,
    }
