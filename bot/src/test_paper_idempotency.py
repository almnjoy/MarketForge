"""Idempotency tests for the paper lane. Pure, no network, no keys.

Regression target, 2026-08-11: a duplicated bridge turn replayed the same paper
order three times. RPD ended at 108 shares instead of 36, and that 36 was the
control leg of a chase-versus-retest test. The experiment was corrupted before
it ran, and it only surfaced because a replay went looking.

The rule this encodes: an unwatched lane must be idempotent, because nobody is
there to notice the double.

Run: python bot/src/test_paper_idempotency.py
"""
from __future__ import annotations

import types

import config
import paper


class FakeClient:
    """Counts orders so a duplicate is measurable, not just reported."""

    def __init__(self, px=1377):
        self.px = px
        self.orders = []
        self.trade_base = "https://paper-api.alpaca.markets"

    def submit_market_order(self, *, symbol, side, notional=None, qty=None,
                            client_order_id=None):
        self.orders.append({"symbol": symbol, "side": side, "qty": qty,
                            "notional": notional})
        return {"id": f"o{len(self.orders)}", "status": "accepted"}

    def get_latest_price(self, symbol):
        return self.px

    def list_positions(self):
        return []


def setup():
    """Fresh fire log + a stubbed broker for every test."""
    config.PAPER_KEY_ID, config.PAPER_SECRET = "PKTEST", "s"
    if paper.FIRES_PATH.exists():
        paper.FIRES_PATH.unlink()
    fake = FakeClient()
    paper.client = lambda: fake            # noqa: E731
    return fake


def test_second_identical_fire_is_refused():
    """THE TEST. Same symbol, same side, same day - one order reaches the broker."""
    fake = setup()
    a = paper.place("RPD", "buy", qty=36)
    b = paper.place("RPD", "buy", qty=36)
    c = paper.place("RPD", "buy", qty=36)

    assert a["ok"] is True, a
    assert b.get("duplicate") is True and b["ok"] is False, b
    assert c.get("duplicate") is True, c
    assert len(fake.orders) == 1, f"broker saw {len(fake.orders)} orders, expected 1"
    assert fake.orders[0]["qty"] == 36


def test_the_refusal_says_what_the_prior_was():
    """A bare 'duplicate' is useless at 09:31. Name the time, size and order id."""
    setup()
    paper.place("RPD", "buy", qty=36)
    dup = paper.place("RPD", "buy", qty=36)
    for token in ("already fired", "36", "RPD"):
        assert token in dup["error"], dup["error"]
    assert dup["prior"]["qty"] == 36


def test_opposite_side_is_not_a_duplicate():
    """Buying then selling the same name in a day is a normal round trip."""
    fake = setup()
    assert paper.place("RPD", "buy", qty=36)["ok"] is True
    assert paper.place("RPD", "sell", qty=36)["ok"] is True
    assert len(fake.orders) == 2


def test_other_symbols_unaffected():
    fake = setup()
    paper.place("RPD", "buy", qty=36)
    assert paper.place("HQI", "buy", qty=29)["ok"] is True
    assert len(fake.orders) == 2


def test_allow_repeat_overrides():
    """The repair tool has to be able to sell a name that already fired."""
    fake = setup()
    paper.place("RPD", "buy", qty=36)
    again = paper.place("RPD", "buy", qty=36, allow_repeat=True)
    assert again["ok"] is True, again
    assert len(fake.orders) == 2


def test_a_different_day_is_allowed():
    setup()
    paper.place("RPD", "buy", qty=36)
    assert paper.already_fired("RPD", "buy") is not None
    assert paper.already_fired("RPD", "buy", day="2020-01-01") is None


def test_clear_fire_allows_a_refire():
    fake = setup()
    paper.place("RPD", "buy", qty=36)
    assert paper.place("RPD", "buy", qty=36).get("duplicate")
    paper.clear_fire("RPD", "buy")
    assert paper.place("RPD", "buy", qty=36)["ok"] is True
    assert len(fake.orders) == 2


def test_notional_is_converted_to_whole_shares():
    """A fractional position can never carry a trailing stop. $100 of a $13.77
    stock is 7 shares, not 7.26."""
    fake = setup()
    config.PAPER_WHOLE_SHARES_ONLY = True
    r = paper.place("RPD", "buy", notional=100)
    assert r["qty"] == 7, r
    assert r["notional"] is None
    assert fake.orders[0]["qty"] == 7 and fake.orders[0]["notional"] is None


def test_under_one_share_reports_the_amount_you_asked_for():
    """REGRESSION: the refusal cleared `notional` before formatting the message,
    so it always read '$0.00 is under one share'."""
    setup()
    config.PAPER_WHOLE_SHARES_ONLY = True
    try:
        paper.place("RPD", "buy", notional=5)     # $5 of a $13.77 stock
        raise AssertionError("should have refused")
    except paper.PaperUnavailable as e:
        assert "$5.00" in str(e), str(e)
        assert "$0.00" not in str(e), str(e)


def test_a_rejected_order_does_not_lock_the_symbol():
    """The fire is recorded AFTER the broker accepts. A broker error must not
    burn the day's only allowed entry."""
    fake = setup()

    def boom(**k):
        raise RuntimeError("broker said no")
    fake.submit_market_order = boom
    try:
        paper.place("RPD", "buy", qty=36)
    except Exception:
        pass
    assert paper.already_fired("RPD", "buy") is None, "a failed order locked the symbol"


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
    if paper.FIRES_PATH.exists():
        paper.FIRES_PATH.unlink()
    print(f"\n{passed} passed, {failed} failed")
    raise SystemExit(1 if failed else 0)
