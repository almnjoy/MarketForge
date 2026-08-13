#!/usr/bin/env python3
"""BRAIN 3 - the daily play. Intraday momentum on low-float small caps.

Brain 1 (signals.py) is Ariel: multi-week swing on daily bars.
Brain 2 (short_signals.py) is the short lane: failed rallies under a declining 50.
Brain 3 is Ross Cameron: in and out the same day, often the same few minutes.

Almost nothing transfers between them except one idea, which all three independently
arrive at:

    "What's my max loss on this pattern? My max loss is the low of the pullback.
     And this is why it's so important to wait for a pullback."

THE PULLBACK IS THE STOP. Chasing the initial pop is not wrong because the
direction is wrong - it is wrong because there is no level behind you to size
against.

WHAT THIS MODULE IS AND IS NOT
------------------------------
A PATTERN ENGINE over minute bars: pure functions, no network, no orders.

It is NOT a scanner, and on the free data plan it cannot become one. Cameron is
explicit that SELECTION is the edge - "you trade the strongest stocks on any given
day" - which means scanning the whole market in real time. The free plan gives 15
real-time IEX symbols you pick in advance. You cannot find today's gapper by
watching fifteen names you already chose.

THE UNLOCK: the 15-minute restriction applies only to RECENT data. Historical
minute bars are free and complete, so the pattern can be measured on real sessions
at zero cost before anyone pays for a live feed.

DELIBERATELY ABSENT: position sizing, risk caps, portfolio rules. Those live in
advice.py. The only "risk" here is the geometry of the setup itself.

All prices are INTEGER CENTS, converted at the API boundary only.
"""
from __future__ import annotations

# --- gates -----------------------------------------------------------------
# Cameron's stated band is $2-$20. The floor is lower here because the sub-$2 tape
# is explicitly part of this lane. Note this REVERSES the $3.00 floor in RULES.md,
# which keeps halt-junk out of the SWING lanes. Two lanes, two universes.
PRICE_MIN_C = 50            # $0.50
PRICE_MAX_C = 2000          # $20.00

# UNITS PROBLEM: EDGAR gives shares OUTSTANDING, not FLOAT. For exactly these names
# the gap is worst - insider and restricted blocks mean outstanding can be several
# times the tradeable float. A weak upper bound, labelled as one, never as float.
LOW_SUPPLY_MAX = 20_000_000
MICRO_SUPPLY_MAX = 5_000_000

# A gapper's signature is that TODAY does not look like its own history. The radar's
# floor is a 20-DAY AVERAGE, which structurally rejects every one of these: a stock
# that traded 148 shares yesterday and 50M today fails an average test on the
# strength of the 148. This lane asks the INVERSE question.
MIN_RELATIVE_VOLUME = 5.0
MIN_SESSION_DOLLARS = 1_000_000

# A stop must sit OUTSIDE the noise band or the R it defines is fiction. Both floors
# exist because the first backtest (2026-08-13) produced entries like CRWV at 65.42
# with a stop at 65.41 - ONE CENT of risk on a $65 stock. Downstream everything
# looked fine: "2R" was two cents, hit inside the same minute, booked as a win. It
# was not a win, it was the bid-ask spread. A stop tighter than the spread is not a
# tight stop, it is no stop.
MIN_RISK_CENTS = 5          # roughly a spread on an active low-priced name
MIN_RISK_PCT = 0.5          # below this a "pullback" is a tick, not a consolidation


def relative_volume(session_volume, baseline_daily_volumes):
    """Today's volume as a multiple of a normal day. None when unknowable.

    MEDIAN, not mean: one previous spike in the baseline drags a mean up enough to
    hide the next spike, which is the failure this gate exists to catch.
    """
    vols = sorted(v for v in (baseline_daily_volumes or []) if v and v > 0)
    if not vols or not session_volume:
        return None
    n = len(vols)
    med = vols[n // 2] if n % 2 else (vols[n // 2 - 1] + vols[n // 2]) / 2
    return (session_volume / med) if med else None


def supply_class(shares_outstanding):
    """Weak proxy only. Says 'supply', never 'float', because it is not float."""
    if not shares_outstanding:
        return "unknown"
    if shares_outstanding <= MICRO_SUPPLY_MAX:
        return "micro_supply"
    if shares_outstanding <= LOW_SUPPLY_MAX:
        return "low_supply"
    return "heavy_supply"


# --- the pattern -----------------------------------------------------------
def find_surge(bars, lookback=None):
    """The leg up the pullback will pull back FROM. (start_idx, peak_idx) or None.

    The leg ending at the highest high, measured back to the lowest low before it.
    """
    if not bars or len(bars) < 3:
        return None
    end = len(bars) if lookback is None else min(len(bars), lookback)
    window = bars[:end]
    peak_idx = max(range(len(window)), key=lambda i: window[i]["h"])
    if peak_idx == 0:
        return None
    start_idx = min(range(peak_idx + 1), key=lambda i: window[i]["l"])
    if start_idx >= peak_idx:
        return None
    return (start_idx, peak_idx)


def find_pullback_entry(bars, max_retrace_pct=61.8, min_pullback_bars=2,
                        max_pullback_bars=12):
    """IS THE LAST BAR A TRIGGER? Returns a dict, always, with a reason when not.

    THE BUG THIS SHAPE FIXES (2026-08-13, found by the first backtest):
    it used to ask "is there a trigger anywhere in this window?", and that inverted
    the entire method. find_surge() takes the highest high in the window it is
    given - so the moment a trigger bar made a new session high, IT became the surge
    peak, there were no bars after it, and the setup was reported absent:

        breakout through the prior high  -> REJECTED   (the actual setup)
        bounce that stalls underneath it -> ACCEPTED   (the anti-setup)

    All 32 trades in that first run were the second kind. It measured a pattern
    nobody intended to trade and reported +0.371R for it. Scoping the surge to the
    bars BEFORE the candidate trigger is the fix, and it is also the only shape that
    makes sense live, where "the last bar" is now.

    ONE RETURN SHAPE, ALWAYS - no bare None. "No" and "no, because" are different
    answers and only one of them is useful.
    """
    if not bars or len(bars) < 5:
        return {"ok": False,
                "reason": f"need at least 5 bars (surge + {min_pullback_bars} pullback "
                          f"+ trigger), got {len(bars or [])}"}

    trigger = bars[-1]
    prior = bars[:-1]
    surge = find_surge(prior)
    if not surge:
        return {"ok": False,
                "reason": ("the highest high is the first bar - price only went down, "
                           "there is no surge to pull back from")}
    s_i, p_i = surge
    surge_low, surge_high = prior[s_i]["l"], prior[p_i]["h"]
    surge_size = surge_high - surge_low
    if surge_size <= 0:
        return {"ok": False, "reason": "surge has no height (flat bars)"}

    after = prior[p_i + 1:]
    ctx = {"surge": {"low_c": surge_low, "high_c": surge_high, "size_c": surge_size,
                     "start_idx": s_i, "peak_idx": p_i}}
    if len(after) < min_pullback_bars:
        return {"ok": False,
                "reason": (f"only {len(after)} bar(s) between the peak and this one; "
                           f"a {min_pullback_bars}-bar pullback has not formed yet"
                           if after else "the peak is the bar before this one - "
                                         "nothing has pulled back yet"),
                "pullback_low_c": min((b["l"] for b in after), default=None), **ctx}
    if len(after) > max_pullback_bars:
        return {"ok": False,
                "reason": f"pullback ran {len(after)} bars past the peak without a new "
                          f"high (limit {max_pullback_bars}); it is basing, not pulling back",
                "pullback_low_c": min(b["l"] for b in after), **ctx}

    pb_low = min(b["l"] for b in after)
    prior_high = after[-1]["h"]

    if trigger["h"] <= prior_high:
        return {"ok": False,
                "reason": "pullback still in progress - this bar did not take out the "
                          "previous bar's high",
                "pullback_low_c": pb_low, **ctx}

    entry_c = prior_high + 1        # trigger: through the prior bar's high
    stop_c = pb_low                 # THE PULLBACK IS THE STOP
    retrace = (surge_high - pb_low) / surge_size * 100
    if retrace > max_retrace_pct:
        return {"ok": False,
                "reason": f"gave back {retrace:.0f}% of the surge (limit "
                          f"{max_retrace_pct:.0f}%) - this is the move failing, not "
                          f"consolidating",
                "pullback_low_c": pb_low, **ctx}

    risk_c = entry_c - stop_c
    if risk_c <= 0:
        return {"ok": False,
                "reason": "trigger is at or below the pullback low; no stop distance "
                          "to size against", **ctx}
    if risk_c < MIN_RISK_CENTS:
        return {"ok": False,
                "reason": (f"stop is {risk_c}c from entry, under the {MIN_RISK_CENTS}c "
                           f"floor - that is inside the spread, so the 'R' it implies "
                           f"is not real"),
                "pullback_low_c": pb_low, **ctx}
    risk_pct_v = risk_c / entry_c * 100
    if risk_pct_v < MIN_RISK_PCT:
        return {"ok": False,
                "reason": (f"stop is {risk_pct_v:.3f}% from entry, under the "
                           f"{MIN_RISK_PCT}% floor - a pullback that shallow is a tick, "
                           f"not a consolidation"),
                "pullback_low_c": pb_low, **ctx}

    return {
        "ok": True,
        "entry_c": entry_c,
        "stop_c": stop_c,
        "risk_c": risk_c,
        "risk_pct": round(risk_pct_v, 3),
        "pullback_bars": len(after),
        "pullback_low_c": pb_low,
        "retrace_pct": round(retrace, 1),
        # R multiples, not dollar targets. What a trade pays is a function of size,
        # and size is not what this module decides.
        "target_1r_c": entry_c + risk_c,
        "target_2r_c": entry_c + 2 * risk_c,
        "note": "stop is the pullback low, per the method. Moving it lower does not "
                "make the trade safer, it makes the pattern absent.",
        **ctx,
    }


def screen(price_c, session_volume=None, baseline_daily_volumes=None,
           shares_outstanding=None):
    """Does this name belong in the daily-play universe at all?

    Returns (passes, findings). Findings come back either way, because "why was
    this rejected" is the question the radar could not answer until the scan log
    existed.
    """
    f = []
    ok = True
    if not price_c or not (PRICE_MIN_C <= price_c <= PRICE_MAX_C):
        ok = False
        f.append({"gate": "price_band", "pass": False,
                  "detail": f"${(price_c or 0)/100:.2f} outside "
                            f"${PRICE_MIN_C/100:.2f}-${PRICE_MAX_C/100:.2f}"})
    else:
        f.append({"gate": "price_band", "pass": True, "detail": f"${price_c/100:.2f}"})

    rvol = relative_volume(session_volume, baseline_daily_volumes)
    if rvol is None:
        ok = False
        f.append({"gate": "relative_volume", "pass": False,
                  "detail": "no baseline - unknown, which is not the same as normal"})
    elif rvol < MIN_RELATIVE_VOLUME:
        ok = False
        f.append({"gate": "relative_volume", "pass": False,
                  "detail": f"{rvol:.1f}x median day, needs {MIN_RELATIVE_VOLUME:.0f}x"})
    else:
        f.append({"gate": "relative_volume", "pass": True,
                  "detail": f"{rvol:.1f}x its own median day"})

    dollars = (price_c / 100) * (session_volume or 0)
    if dollars < MIN_SESSION_DOLLARS:
        ok = False
        f.append({"gate": "session_liquidity", "pass": False,
                  "detail": f"${dollars:,.0f} today, needs ${MIN_SESSION_DOLLARS:,.0f}. "
                            f"NOTE: measured on TODAY, not a 20-day average - an average "
                            f"test rejects every gapper on the strength of its quiet days"})
    else:
        f.append({"gate": "session_liquidity", "pass": True,
                  "detail": f"${dollars:,.0f} traded today"})

    cls = supply_class(shares_outstanding)
    f.append({"gate": "supply", "pass": True,   # never blocks: a proxy, not a fact
              "detail": f"{cls}"
                        + (f" ({shares_outstanding/1e6:.1f}M shares OUTSTANDING - "
                           f"NOT float; float is smaller and needs a paid source)"
                           if shares_outstanding else "")})
    return ok, f


if __name__ == "__main__":
    import json
    print(json.dumps({
        "module": "daily_play (Brain 3)",
        "price_band": f"${PRICE_MIN_C/100:.2f}-${PRICE_MAX_C/100:.2f}",
        "min_relative_volume": MIN_RELATIVE_VOLUME,
        "min_risk": f"{MIN_RISK_CENTS}c or {MIN_RISK_PCT}%, whichever is larger",
        "what_it_cannot_do": "select across the market - that needs a real-time "
                             "full-tape feed the free plan does not include",
        "what_it_can_do_today": "run the pattern over historical minute bars, free, "
                                "because only the LAST 15 MINUTES are withheld",
    }, indent=2))
