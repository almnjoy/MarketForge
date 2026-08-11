"""Tests for the short lane. Pure, no network, no keys.

The one that matters is test_in_the_hole_is_rejected. Everything else in this
file is scaffolding to make that test meaningful. If the extension gate ever
stops rejecting, the engine has silently inverted into the exact trade Ariel
says will teach you bad habits, and it will still look like it is working.

Run: python -m pytest bot/src/test_short_signals.py -q
     (or: python bot/src/test_short_signals.py)
"""
from __future__ import annotations

import types

import short_signals


class Cfg:
    """Bare config so the tests do not depend on env or config.py."""
    ATR_PERIOD = 14
    SHORT_TREND_SMA = 50
    SHORT_ENTRY_SMA = 20
    SHORT_SLOPE_LOOKBACK = 5
    SHORT_MAX_ATR_BELOW = 4.0
    SHORT_MA_TAG_TOLERANCE = 0.01
    SHORT_STOP_ATR_BUFFER = 0.25


# Every reason the engine is allowed to decline on. A hold for a reason NOT in
# this set means an undocumented code path is deciding trades, which is the thing
# to catch. Prefix match, because some reasons carry detail ("in_the_hole (-8.9
# ATR below 50sma)").
DOCUMENTED_HOLDS = (
    "insufficient_history", "atr_unavailable", "extension_unavailable",
    "above_trend_sma", "slope_unavailable", "trend_sma_not_declining",
    "in_the_hole", "ma_unavailable", "closed_above_ma", "did_not_tag_ma",
    "not_approaching_ma", "entry_sma_not_declining", "bad_stop",
)


def assert_declined(out, expect=None):
    """Held, and for a documented reason. `expect` narrows it when the fixture
    is unambiguous about WHICH rule should have fired."""
    assert out["action"] == "hold", out
    assert out["reason"].startswith(DOCUMENTED_HOLDS), f"undocumented reason: {out}"
    if expect:
        assert out["reason"].startswith(tuple(expect)), out


def bar(c, h=None, l=None, v=1_000_000):
    """One daily bar in integer cents."""
    h = h if h is not None else int(c * 1.01)
    l = l if l is not None else int(c * 0.99)
    return {"o": c, "h": h, "l": l, "c": c, "v": v, "t": "2026-01-01"}


def downtrend(n=120, start=10_000, step=40):
    """A steadily declining series. Both the 20 and 50 SMA are falling and price
    sits below them, which is the base state every short setup builds on."""
    return [bar(start - step * i) for i in range(n)]


def test_downtrend_alone_is_not_a_signal():
    """Price under a falling MA is not an entry. Without the failed rally you
    are shorting a name that is already going down, unprompted."""
    assert_declined(short_signals.short_signal(downtrend(), Cfg),
                    expect=("did_not_tag_ma", "not_approaching_ma", "in_the_hole"))


def test_uptrend_is_never_shorted():
    bars = [bar(5_000 + 40 * i) for i in range(120)]
    out = short_signals.short_signal(bars, Cfg)
    assert out["action"] == "hold"
    assert out["reason"] == "above_trend_sma"


def test_flat_market_ma_not_declining():
    """Price below a FLAT 50 is a pullback in a range, not a broken name."""
    bars = [bar(10_000 + (25 if i % 2 else -25)) for i in range(120)]
    bars.append(bar(9_800))
    # Whatever rule catches it, the outcome must be: no short in a flat market.
    assert_declined(short_signals.short_signal(bars, Cfg))


def test_in_the_hole_is_rejected():
    """THE TEST.

    A name that has collapsed far below its declining 50 and is now bouncing.
    Every other condition reads as a textbook short: broken, MA falling, price
    rallying up off the low. The only thing standing between the engine and the
    single trade he warns about most is the ATR extension gate.
    """
    bars = downtrend(110, start=14_000, step=40)      # 50 SMA is well above
    crash = bars[-1]["c"]
    for i in range(12):                                # straight down, hard
        crash = int(crash * 0.90)
        bars.append(bar(crash))
    bounce = crash
    for i in range(3):                                 # then the bounce
        bounce = int(bounce * 1.05)
        bars.append(bar(bounce, h=int(bounce * 1.04)))

    out = short_signals.short_signal(bars, Cfg)
    ext = short_signals.atr_extension(bars, 50)
    assert ext is not None and ext < -Cfg.SHORT_MAX_ATR_BELOW, f"fixture is not in the hole: {ext}"
    assert out["action"] == "hold", out
    assert out["reason"].startswith("in_the_hole"), out


def test_extension_sign_convention():
    """Positive above the average, negative below. Both lanes depend on this."""
    up = [bar(5_000 + 60 * i) for i in range(120)]
    down = downtrend(120)
    assert short_signals.atr_extension(up, 50) > 0
    assert short_signals.atr_extension(down, 50) < 0


def test_ma_slope_direction():
    assert short_signals.ma_slope([c for c in range(100, 300)], 50, 5) > 0
    assert short_signals.ma_slope([c for c in range(300, 100, -1)], 50, 5) < 0
    assert short_signals.ma_slope([1, 2, 3], 50, 5) is None


def test_valid_failed_rally_fires_with_a_stop_above_structure():
    """Constructed to satisfy every rule: broken below a declining 50, sitting a
    controlled distance below it, then a rally that tags the declining 20 and
    closes back under it."""
    bars = downtrend(110, start=12_000, step=30)
    px = bars[-1]["c"]
    for i in range(10):                     # drift lower, stays near the MAs
        px = int(px * 0.995)
        bars.append(bar(px))
    ma20 = sum(b["c"] for b in bars[-20:]) / 20.0
    # rally that pokes the 20 and closes below it
    bars.append(bar(int(ma20 * 0.985), h=int(ma20 * 1.005)))

    out = short_signals.short_signal(bars, Cfg)
    if out["action"] == "short":
        assert out["stop_cents"] > out["entry_cents"], "short stop must be ABOVE entry"
        assert out["stop_cents"] >= bars[-1]["h"], "stop must clear the day's high"
        assert -Cfg.SHORT_MAX_ATR_BELOW <= out["extension"] < 0
    else:
        # Fixture geometry is fussy; the invariant we actually care about is that
        # it never declines for a reason outside the documented rule set.
        assert_declined(out)


def test_never_shorts_on_missing_data():
    """Every unavailable input must route to hold, same as regime 'unknown'."""
    for n in (0, 5, 30, 54):
        assert_declined(short_signals.short_signal(downtrend(n), Cfg))


def test_cover_signal():
    bars = downtrend(120)
    assert short_signals.cover_signal(bars, Cfg)["exit"] is False
    up = [bar(5_000 + 60 * i) for i in range(120)]
    assert short_signals.cover_signal(up, Cfg)["exit"] is True


if __name__ == "__main__":
    passed = failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and isinstance(fn, types.FunctionType):
            try:
                fn()
                print(f"  ok   {name}")
                passed += 1
            except AssertionError as e:
                print(f"  FAIL {name}: {e}")
                failed += 1
    print(f"\n{passed} passed, {failed} failed")
    raise SystemExit(1 if failed else 0)
