"""Partial exit coverage, wrapper filtering, candidate dedupe. No network.

THE BUG THAT MATTERS (2026-08-12): paper held 58 HQI with a trailing stop for 29,
and the sweep reported it protected. The check asked "does any working exit order
exist?" when the question that protects money is "does the exit cover the WHOLE
position?" 29 shares were uncovered AND invisible.

Same shape as the original naked-position bug, one level down: a partial answer
that reads as a complete one.

Run: python bot/src/test_coverage_gaps.py
"""
from __future__ import annotations

import types

from testkit import stub_flask_if_missing

stub_flask_if_missing()          # api.py imports Flask at module scope
import api  # noqa: E402


class Broker:
    """Positions plus working orders, shaped like Alpaca's."""

    def __init__(self, positions, orders, assets=None):
        self.trade_base = "https://paper-api.alpaca.markets"
        self._pos, self._orders = positions, orders
        self._assets = assets or {}
        self.submitted = []

    def list_positions(self):
        return self._pos

    def _req(self, method, base, path, params=None, json=None):
        if path == "/v2/orders":
            return self._orders
        if path.startswith("/v2/assets/"):
            return self._assets.get(path.rsplit("/", 1)[-1], {})
        return {}

    def get_asset(self, symbol):
        return self._assets.get(symbol, {})

    def submit_trailing_stop_sell(self, *, symbol, qty, trail_percent, **k):
        self.submitted.append(("sell", symbol, qty))
        return {"id": "t1"}

    def submit_trailing_stop_buy(self, *, symbol, qty, trail_percent, **k):
        self.submitted.append(("buy", symbol, qty))
        return {"id": "t2"}


def pos(sym, qty):
    return {"symbol": sym, "qty": qty, "avg_entry_cents": 1600,
            "current_price_cents": 1700, "market_value_cents": 98600,
            "unrealized_pl_cents": 3480}


def order(sym, side, qty, filled=0):
    return {"symbol": sym, "side": side, "qty": str(qty), "filled_qty": str(filled)}


# --- partial coverage -------------------------------------------------------
def test_hqi_58_behind_a_stop_for_29_is_reported():
    """THE TEST. The exact position that was called protected."""
    b = Broker([pos("HQI", 58.0)], [order("HQI", "sell", 29)])
    naked = api.unprotected_positions(b)
    assert len(naked) == 1, naked
    n = naked[0]
    assert n["partial"] is True
    assert n["covered"] == 29.0 and n["uncovered"] == 29.0, n
    assert "29 of 58" in n["why"], n["why"]


def test_full_coverage_is_not_reported():
    b = Broker([pos("HQI", 58.0)], [order("HQI", "sell", 58)])
    assert api.unprotected_positions(b) == []


def test_two_partial_orders_can_add_up_to_full_coverage():
    """Legitimate: scaling out in pieces still protects the whole position."""
    b = Broker([pos("HQI", 58.0)],
               [order("HQI", "sell", 30), order("HQI", "sell", 28)])
    assert api.unprotected_positions(b) == []


def test_a_filled_portion_no_longer_protects_what_is_left():
    """A 58-share stop that has already filled 40 covers 18, not 58."""
    b = Broker([pos("HQI", 58.0)], [order("HQI", "sell", 58, filled=40)])
    naked = api.unprotected_positions(b)
    assert naked and naked[0]["covered"] == 18.0, naked


def test_wrong_side_never_counts_as_coverage():
    """A working BUY does not protect a LONG. This was already right; keeping it
    right while the shape changed is the point of the test."""
    b = Broker([pos("HQI", 58.0)], [order("HQI", "buy", 58)])
    naked = api.unprotected_positions(b)
    assert naked and naked[0]["covered"] == 0.0 and naked[0]["needs"] == "sell"


def test_short_needs_a_buy_and_partial_still_counts():
    b = Broker([pos("META", -10.0)], [order("META", "buy", 4)])
    naked = api.unprotected_positions(b)
    assert naked and naked[0]["side"] == "short"
    assert naked[0]["needs"] == "buy" and naked[0]["uncovered"] == 6.0


def test_arm_trail_tops_up_only_the_uncovered_remainder():
    """It used to refuse outright whenever ANY working exit existed, so a
    half-covered position could only be fixed by cancelling the good order."""
    b = Broker([pos("HQI", 58.0)], [order("HQI", "sell", 29)])
    res = api.arm_trail(b, "HQI", 10)
    assert res.get("ok") is True, res
    assert b.submitted == [("sell", "HQI", 29)], b.submitted


def test_arm_trail_still_refuses_when_fully_covered():
    b = Broker([pos("HQI", 58.0)], [order("HQI", "sell", 58)])
    res = api.arm_trail(b, "HQI", 10)
    assert res.get("ok") is False and "already has a working" in res["error"]
    assert b.submitted == []


def test_unreadable_orders_fail_closed():
    """Unknown must mean unguarded, never guarded."""
    class Bad(Broker):
        def _req(self, *a, **k):
            raise RuntimeError("api down")
    b = Bad([pos("HQI", 58.0)], [])
    naked = api.unprotected_positions(b)
    assert len(naked) == 1 and naked[0]["covered"] == 0.0


# --- leveraged wrapper filter ----------------------------------------------
WRAPPERS = {
    "CWVX": {"name": "GraniteShares 2x Long CRWV Daily ETF"},
    "NBIL": {"name": "Direxion Daily NBIS Bull 2X Shares"},
    "SMCL": {"name": "Defiance Daily Target 2X Long SMCI ETF"},
    "CRWV": {"name": "CoreWeave, Inc. Class A Common Stock"},
    "NBIS": {"name": "Nebius Group N.V."},
    "HQI": {"name": "HireQuest, Inc."},
}


def _is_wrapper(name):
    nm = (name or "").upper()
    lev = [w for w in ("2X", "3X", "-1X", "LEVERAGED", "INVERSE",
                       "BULL ", "BEAR ", " ETN") if w in nm]
    return bool(lev) and ("ETF" in nm or "ETN" in nm or "DAILY" in nm)


def test_wrappers_are_identified():
    for s in ("CWVX", "NBIL", "SMCL"):
        assert _is_wrapper(WRAPPERS[s]["name"]), s


def test_real_companies_are_not():
    for s in ("CRWV", "NBIS", "HQI"):
        assert not _is_wrapper(WRAPPERS[s]["name"]), s


def test_a_company_with_2x_in_its_name_survives():
    """Requiring BOTH a leverage word and a fund word is what stops this."""
    assert not _is_wrapper("2X Genomics Holdings Inc")
    assert not _is_wrapper("Bull Run Capital Corp")


def test_get_asset_returns_empty_not_an_exception():
    class Bad:
        trade_base = "x"

        def _req(self, *a, **k):
            raise RuntimeError("nope")
    from alpaca_client import AlpacaClient
    c = AlpacaClient.__new__(AlpacaClient)
    c.trade_base = "x"
    c._req = Bad()._req
    assert c.get_asset("ANY") == {}


# --- candidate dedupe -------------------------------------------------------
def test_duplicate_movers_are_collapsed_before_the_loop():
    """OFAL and SMCL were scored twice, one second apart, inside ONE scan - so
    neither the scan lock nor the claim could help. Both live inside the loop,
    and the loop was handed the same symbol twice."""
    feed = [{"symbol": "SMCL"}, {"symbol": "OFAL"}, {"symbol": "SMCL"},
            {"symbol": "ofal"}, {"symbol": "HQI"}]
    seen, out = set(), []
    for g in feed:
        s = str(g.get("symbol") or "").upper()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(g)
    assert [g["symbol"] for g in out] == ["SMCL", "OFAL", "HQI"], out


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
