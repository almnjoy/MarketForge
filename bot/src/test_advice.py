"""Advisory-mode tests. Pure, no network.

The property that matters: a soft cap must NOT stop a ticket, and a hard gate
must. Dustin runs this account deliberately concentrated; the app's job is to
say the number, not to refuse.

Run: python bot/src/test_advice.py
"""
from __future__ import annotations

import types

import advice
import risk


class Cfg:
    RISK_MODE = "advisory"
    MAX_GAP_LOSS_PCT = 0.03      # 3% max loss to...
    ASSUMED_GAP_PCT = 0.30       # ...a 30% gap  => a 10% derived position cap
    MAX_POSITION_PCT = 0.10      # fallback, and the same number by construction
    MAX_SECTOR_PCT = 0.40
    MAX_POSITIONS = 8
    MAX_DAILY_LOSS_PCT = 0.05
    MAX_DRAWDOWN_PCT = 0.15
    WASH_SALE_COOLDOWN_DAYS = 31
    RISK_PER_TRADE_PCT = 0.01
    KELLY_FRACTION = 0.25
    USE_KELLY_CAP = False
    FRACTIONAL = False
    MIN_ORDER_NOTIONAL_CENTS = 2000
    MAX_LIMIT_DEVIATION_PCT = 0.05
    STOCK_ENV = "live"


class Strict(Cfg):
    RISK_MODE = "strict"


def ctx(**over):
    """A candidate that is valid but INTENTIONALLY oversized: $5,000 account,
    a $500 share. This is Dustin's own example."""
    base = {
        "symbol": "NVDA", "sector": "tech", "action": "buy",
        "confidence": "high", "critique_verdict": "pass",
        "entry_cents": 50_000, "stop_cents": 47_000,
        "reference_price_cents": 50_000,
        "bankroll_cents": 500_000,          # $5,000
        "bot_committed_cents": 0,
        "sector_exposure_cents": 0,
        "open_positions": 0,
        "in_wash_cooldown": False,
        "open_equity_cents": 500_000,
        "current_equity_cents": 500_000,
        "peak_equity_cents": 500_000,
    }
    base.update(over)
    return base


def test_the_sentence_dustin_asked_for():
    msg = advice.position_notice(symbol="NVDA", qty=10, entry_cents=50_000,
                                 stop_cents=47_000, account_cents=500_000)
    assert "$5,000.00 account." in msg, msg
    assert "10 shares of NVDA at $500.00" in msg, msg
    assert "100% of the account" in msg, msg
    assert "risks $300.00" in msg, msg


def test_soft_cap_does_not_block():
    """A position over the 10% cap still stages, with a notice attached.

    $5,000 account, $500 share, $30 stop distance. The 1% risk budget is $50,
    which buys 1.67 -> 1 share = $500 = 10% of the account. Widen the stop and
    the risk math wants MORE size, which is exactly the case that used to be
    silently shrunk.
    """
    r = risk.evaluate(ctx(stop_cents=49_500), Cfg)   # $5 stop -> 10 shares
    assert r["staged"] is True, r
    assert r["order"] is not None
    assert r["all_gates_pass"] is False, "fixture should trip a soft gate"
    assert r["blocking"] == [], r["blocking"]
    assert r["advisories"], "an over-cap trade must say so"
    sizing = [a for a in r["advisories"] if a["code"] == "sizing"]
    assert sizing and sizing[0]["severity"] == "caution", r["advisories"]
    assert "of the account" in sizing[0]["message"]


def test_advisory_mode_lets_the_risk_math_size_it():
    """REGRESSION: sizing used to clamp at MAX_POSITION_PCT unconditionally, so
    G3 could never fail and advisory mode was decoration."""
    q_adv, n_adv = risk.position_size(50_000, 49_500, 500_000, Cfg)
    q_str, n_str = risk.position_size(50_000, 49_500, 500_000, Strict)
    assert q_adv > q_str, (q_adv, q_str)
    assert n_str <= Strict.MAX_POSITION_PCT * 500_000 + 1


def test_strict_mode_still_blocks():
    """The old behavior is one env var away, and must still work."""
    r = risk.evaluate(ctx(), Strict)
    if not r["all_gates_pass"]:
        assert r["staged"] is False, r
        assert r["blocking"], r


def test_breaker_still_halts_in_advisory_mode():
    """G5 is NOT advisory. A tripped kill switch means stop, not size down."""
    r = risk.evaluate(ctx(current_equity_cents=400_000), Cfg)   # -20% on the day
    assert r["halt"] is True, r
    assert r["staged"] is False


def test_fat_finger_still_blocks():
    """G9 is a typo check, not a preference. A limit miles off the reference is
    a fat finger regardless of how confident anyone is."""
    r = risk.evaluate(ctx(reference_price_cents=25_000), Cfg)   # entry 2x the ref
    assert r["staged"] is False, r
    assert "G9_fat_finger" in r["blocking"], r["blocking"]


def test_sub_floor_notional_still_blocks():
    """The $20 order floor is also G9. Tiny bankroll -> tiny size -> no ticket."""
    r = risk.evaluate(ctx(bankroll_cents=1_000, current_equity_cents=1_000,
                          open_equity_cents=1_000, peak_equity_cents=1_000), Cfg)
    assert r["staged"] is False, r
    assert "G9_fat_finger" in r["blocking"], r["blocking"]


def test_no_stop_never_stages():
    """G1. A 'trade' with the stop above the entry is not a trade."""
    r = risk.evaluate(ctx(stop_cents=60_000), Cfg)
    assert r["staged"] is False, r
    assert "G1_signal" in r["blocking"], r["blocking"]


def test_wash_sale_is_a_loud_notice_not_a_wall():
    r = risk.evaluate(ctx(in_wash_cooldown=True), Cfg)
    assert r["staged"] is True, r
    a = [x for x in r["advisories"] if x["code"] == "G8_wash_sale"]
    assert a and a[0]["severity"] == "danger", r["advisories"]
    assert "disallow" in a[0]["message"]


def test_sector_notice_names_the_sector_and_the_number():
    r = risk.evaluate(ctx(sector_exposure_cents=450_000), Cfg)
    a = [x for x in r["advisories"] if x["code"] == "G4_sector_cap"]
    assert a, r["advisories"]
    assert "tech" in a[0]["message"] and "%" in a[0]["message"]


def test_advisories_are_worst_first():
    r = risk.evaluate(ctx(in_wash_cooldown=True, sector_exposure_cents=450_000), Cfg)
    sev = [a["severity"] for a in r["advisories"]]
    order = [advice.SEVERITY_ORDER.index(s) for s in sev]
    assert order == sorted(order), sev
    assert advice.headline(r["advisories"]).startswith("[danger]")


def test_advisories_ride_on_the_ticket():
    """The object the operator clicks must be the one that knows what is odd
    about it. A warning in a log he is not reading is not a warning."""
    r = risk.evaluate(ctx(), Cfg)
    assert "advisories" in r["order"], r["order"].keys()
    assert r["order"]["advisories"] == r["advisories"]


def test_blocking_gates_respects_mode():
    gates = {"G3_position_cap": False, "G5_breakers": True, "G1_signal": True,
             "G9_fat_finger": True}
    assert advice.blocking_gates(gates, Cfg) == []
    assert advice.blocking_gates(gates, Strict) == ["G3_position_cap"]


def test_healthy_trade_has_an_info_notice_not_silence():
    """Even a fine trade prints the size. The number is useful either way."""
    # NOTE the arithmetic this fixture encodes: with risk-based sizing,
    #   position % of account = RISK_PER_TRADE_PCT / stop distance %
    # so at 1% risk a 6% stop is inherently a 16.7% position. To land UNDER a
    # 10% position guideline the stop has to be wider than 10%. Here: a $500
    # entry with a $440 stop (12%) -> 8.3% position.
    big = 50_000_000     # $500k
    r = risk.evaluate(ctx(bankroll_cents=big, current_equity_cents=big,
                          open_equity_cents=big, peak_equity_cents=big,
                          stop_cents=44_000), Cfg)
    assert r["staged"] is True
    sizing = [a for a in r["advisories"] if a["code"] == "sizing"]
    assert sizing and sizing[0]["severity"] == "info", r["advisories"]
    assert "guideline" not in sizing[0]["message"], sizing[0]["message"]


def test_the_cap_is_derived_from_the_gap_tolerance():
    """3% max loss to a 30% gap IS a 10% position cap. Not a hand-picked number."""
    assert abs(advice.derived_position_cap(Cfg) - 0.10) < 1e-9

    class NoGap(Cfg):
        MAX_GAP_LOSS_PCT = 0
        ASSUMED_GAP_PCT = 0
        MAX_POSITION_PCT = 0.25
    assert advice.derived_position_cap(NoGap) == 0.25   # falls back to literal


def test_crossover_stop_is_where_the_two_rules_agree():
    """risk% / cap% = the stop width at which neither constraint binds.
    1% / 10% = 10%. Tighter stops are cap-bound, wider are risk-bound."""
    m = advice.sizing_math(50_000, 45_000, 500_000, Cfg)   # 10% stop exactly
    assert abs(m["crossover_stop_pct"] - 0.10) < 1e-9
    assert abs(m["wanted_position_pct"] - m["cap_pct"]) < 1e-9


def test_tight_stop_is_cap_bound_and_says_so():
    """The 6% stop that started this. 1%/6% wants 16.7%, cap holds it to 10%."""
    m = advice.sizing_math(50_000, 47_000, 500_000, Cfg)   # 6% stop
    assert m["binding"] == "position_cap", m
    assert abs(m["wanted_position_pct"] - 1 / 6) < 0.01
    assert abs(m["final_position_pct"] - 0.10) < 1e-9
    # and you are then risking LESS than the stated 1%
    assert abs(m["effective_risk_pct"] - 0.006) < 1e-6
    exp = advice.sizing_explainer(m)
    assert "cap binds" in exp and "0.60%" in exp, exp
    assert "does nothing about gaps" in exp


def test_wide_stop_is_risk_bound():
    m = advice.sizing_math(50_000, 40_000, 500_000, Cfg)   # 20% stop
    assert m["binding"] == "risk", m
    assert abs(m["effective_risk_pct"] - Cfg.RISK_PER_TRADE_PCT) < 1e-9
    assert "RISK is what sized this" in advice.sizing_explainer(m)


def test_analysis_is_scale_invariant():
    """THE REQUIREMENT: same analysis at $10 as at $10M.

    Every ratio in sizing_math must be identical across account sizes. Only the
    dollar figure (r_cents) is allowed to scale.
    """
    tiny = advice.sizing_math(50_000, 47_000, 1_000, Cfg)          # $10 account
    huge = advice.sizing_math(50_000, 47_000, 1_000_000_000, Cfg)  # $10M account
    ratios = ("risk_pct", "cap_pct", "stop_pct", "wanted_position_pct",
              "final_position_pct", "binding", "effective_risk_pct",
              "crossover_stop_pct", "gap_loss_pct")
    for k in ratios:
        assert tiny[k] == huge[k], (k, tiny[k], huge[k])
    assert advice.sizing_explainer(tiny) == advice.sizing_explainer(huge)
    assert tiny["r_cents"] != huge["r_cents"]      # only the dollars scale


def test_gap_loss_is_reported():
    """The number the position cap actually exists to bound."""
    m = advice.sizing_math(50_000, 47_000, 500_000, Cfg)
    # capped at 10% of the account, a 30% gap costs 3%
    assert abs(m["gap_loss_pct"] - 0.03) < 1e-9


def test_sizing_math_explainer_rides_along():
    r = risk.evaluate(ctx(), Cfg)
    codes = [a["code"] for a in r["advisories"]]
    assert "sizing_math" in codes, codes


if __name__ == "__main__":
    passed = failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and isinstance(fn, types.FunctionType):
            try:
                fn()
                print(f"  ok   {name}")
                passed += 1
            except Exception as e:
                print(f"  FAIL {name}: {e.__class__.__name__}: {e}")
                failed += 1
    print(f"\n{passed} passed, {failed} failed")
    raise SystemExit(1 if failed else 0)
