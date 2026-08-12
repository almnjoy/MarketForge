"""Exit-guarantee tests. Pure, no network, no keys, no Flask.

Regression target: the paper lane shipped 2026-08-10 with NO exit guarantee.
It submitted the protective order in the same breath as the entry, so the broker
rejected it while the entry was still working:

    403 - cannot open a long buy while a short sell order is open

and the position then filled naked. Six paper longs were sitting unprotected when
Dustin found it. Live had three layers; paper had zero.

The FakeBroker below reproduces that 403 exactly. If anyone ever puts the arm
call back next to the entry, test_race_is_dead fails.

Run: python bot/src/test_exit_guarantee.py
"""
from __future__ import annotations

import types


from testkit import stub_flask_if_missing

stub_flask_if_missing()   # api.py imports Flask at module scope


class FakeBroker:
    """Minimal Alpaca stand-in that enforces the real broker's ordering rule."""

    def __init__(self, fill_after=1, trail_base="https://paper-api.alpaca.markets"):
        self.trade_base = trail_base
        self.fill_after = fill_after      # polls before the entry reports filled
        self.polls = 0
        self.orders = {}                  # id -> dict
        self.working = {}                 # symbol -> {side: qty}
        self.positions = []
        self.submitted = []               # every order, in order, for assertions
        self._n = 0

    # --- the rule that broke us -------------------------------------------
    def _guard(self, symbol, side):
        w = self.working.get(symbol, {})
        if side == "buy" and w.get("sell"):
            raise RuntimeError("403 - cannot open a long buy while a short sell "
                               "order is open")
        if side == "sell" and w.get("buy"):
            raise RuntimeError("403 - cannot open a short sell while a long buy "
                               "order is open")

    def submit_market_order(self, *, symbol, side, notional=None, qty=None,
                            client_order_id=None):
        self._guard(symbol, side)
        self._n += 1
        oid = f"o{self._n}"
        self.orders[oid] = {"id": oid, "symbol": symbol, "side": side,
                            "status": "new", "filled_qty": 0, "qty": qty}
        self.working.setdefault(symbol, {})[side] = abs(float(qty or 1))
        self.submitted.append(("entry", symbol, side))
        return {"id": oid, "status": "accepted"}

    def get_order(self, oid):
        o = self.orders[oid]
        self.polls += 1
        if o["status"] == "new" and self.polls >= self.fill_after:
            o["status"] = "filled"
            o["filled_qty"] = o["qty"] or 1
            # A filled entry is no longer WORKING - this is the state change the
            # old code never waited for.
            self.working.get(o["symbol"], {}).pop(o["side"], None)
            q = float(o["filled_qty"]) * (1 if o["side"] == "buy" else -1)
            self.positions.append({"symbol": o["symbol"], "qty": q,
                                   "avg_entry_cents": 1000,
                                   "current_price_cents": 1000,
                                   "market_value_cents": 1000,
                                   "unrealized_pl_cents": 0})
        return dict(o)

    def list_positions(self):
        return list(self.positions)

    def _req(self, method, base, path, params=None, json=None):
        if path == "/v2/orders":
            # Quantities, not just sides. This fixture used to return
            # {symbol, side} only, which encoded the OLD model - "does an exit
            # exist" - and so it kept passing after the code moved to "does the
            # exit COVER the position". A fixture that models the bug's
            # assumption cannot catch the bug.
            return [{"symbol": s, "side": sd, "qty": str(q), "filled_qty": "0"}
                    for s, sides in self.working.items()
                    for sd, q in sides.items()]
        return {}

    def _trail(self, symbol, qty, side):
        self._guard(symbol, side)
        self._n += 1
        self.working.setdefault(symbol, {})[side] = abs(float(qty or 1))
        self.submitted.append(("trail", symbol, side))
        return {"id": f"t{self._n}"}

    def submit_trailing_stop_sell(self, *, symbol, qty, trail_percent, **k):
        return self._trail(symbol, qty, "sell")

    def submit_trailing_stop_buy(self, *, symbol, qty, trail_percent, **k):
        return self._trail(symbol, qty, "buy")

    def get_latest_price(self, symbol):
        return 1000


def _api():
    import api
    return api


def test_race_is_dead_on_a_short():
    """The exact failure: short entry, protective BUY must wait for the fill.

    If arm_after_fill submitted the trail before polling, FakeBroker raises the
    403 and `armed` is False. It must be True.
    """
    api = _api()
    b = FakeBroker(fill_after=1)
    entry = b.submit_market_order(symbol="AXTI", side="sell", qty=10)
    res = api.arm_after_fill(b, "AXTI", 4.0, entry["id"], venue="paper",
                             poll=4, gap=0)
    assert res.get("armed") is True, res
    assert res["side"] == "buy", res            # a short is closed by BUYING
    kinds = [k for k, _s, _d in b.submitted]
    assert kinds == ["entry", "trail"], kinds   # entry strictly before trail


def test_race_is_dead_on_a_long():
    api = _api()
    b = FakeBroker(fill_after=1)
    entry = b.submit_market_order(symbol="NVDA", side="buy", qty=5)
    res = api.arm_after_fill(b, "NVDA", 10.0, entry["id"], venue="paper",
                             poll=4, gap=0)
    assert res.get("armed") is True, res
    assert res["side"] == "sell", res


def test_slow_fill_is_queued_not_dropped():
    """A fill that never lands inside the poll window must be handed to the
    durable watcher. Returning "arm it yourself" is what left VRM naked."""
    api = _api()
    b = FakeBroker(fill_after=999)
    entry = b.submit_market_order(symbol="SLOW", side="buy", qty=3)
    queued = {}
    orig = api._protect_queue_add
    api._protect_queue_add = lambda oid, sym, pct, venue=None: queued.update(
        {"oid": oid, "sym": sym, "pct": pct, "venue": venue})
    try:
        res = api.arm_after_fill(b, "SLOW", 5.0, entry["id"], venue="paper",
                                 poll=2, gap=0)
    finally:
        api._protect_queue_add = orig
    assert res.get("armed") is False and res.get("pending") is True, res
    assert queued.get("sym") == "SLOW" and queued.get("venue") == "paper", queued


def test_rejected_entry_protects_nothing():
    api = _api()
    b = FakeBroker(fill_after=999)
    entry = b.submit_market_order(symbol="DEAD", side="buy", qty=1)
    b.orders[entry["id"]]["status"] = "rejected"
    res = api.arm_after_fill(b, "DEAD", 5.0, entry["id"], venue="paper",
                             poll=3, gap=0)
    assert res["armed"] is False and "rejected" in res["error"], res
    assert [k for k, _s, _d in b.submitted] == ["entry"], b.submitted


def test_short_position_is_visible_to_the_sweep():
    """Negative qty must read as a short needing a BUY."""
    api = _api()
    b = FakeBroker()
    b.positions = [{"symbol": "AXTI", "qty": -10, "avg_entry_cents": 1000,
                    "current_price_cents": 1000, "market_value_cents": -10000,
                    "unrealized_pl_cents": 0}]
    naked = api.unprotected_positions(b)
    assert len(naked) == 1 and naked[0]["side"] == "short", naked
    assert naked[0]["needs"] == "buy", naked


def test_working_buy_counts_as_protection_for_a_short():
    api = _api()
    b = FakeBroker()
    b.positions = [{"symbol": "AXTI", "qty": -10, "avg_entry_cents": 1000,
                    "current_price_cents": 1000, "market_value_cents": -10000,
                    "unrealized_pl_cents": 0}]
    b.working["AXTI"] = {"buy": 10}
    assert api.unprotected_positions(b) == []


def test_sweep_counters_are_venue_scoped():
    """Live AXTI and paper AXTI must not share one attempt budget."""
    api = _api()
    api._sweep_attempts.clear()
    api._sweep_attempts[("live", "AXTI")] = 3
    assert api._sweep_attempts.get(("paper", "AXTI"), 0) == 0


def test_venue_client_pins_paper():
    api = _api()
    import config
    config.PAPER_KEY_ID, config.PAPER_SECRET = "PKTEST123", "s"
    saved = api.LIVE_VENUE
    api.LIVE_VENUE = "live"          # pretend the process is live
    try:
        c = api._venue_client("paper")
        assert c.trade_base == "https://paper-api.alpaca.markets", c.trade_base
    finally:
        api.LIVE_VENUE = saved


def test_process_venue_never_requires_paper_keys():
    """REGRESSION: routing the process's OWN venue through paper.client() made a
    working STOCK_ENV=paper desk fail its sweep whenever the key did not start
    with PK. The process venue must keep using the plain client."""
    api = _api()
    import config
    saved_key, saved_venue = config.PAPER_KEY_ID, api.LIVE_VENUE
    config.PAPER_KEY_ID = ""          # paper keys deliberately unusable
    api.LIVE_VENUE = "paper"          # ...and the process IS paper
    try:
        c = api._venue_client("paper")   # must not raise
        assert c is not None
    finally:
        config.PAPER_KEY_ID, api.LIVE_VENUE = saved_key, saved_venue


def test_venues_does_not_duplicate_paper():
    api = _api()
    saved = api.LIVE_VENUE
    api.LIVE_VENUE = "paper"
    try:
        assert api._venues() == ["paper"], api._venues()
    finally:
        api.LIVE_VENUE = saved


def test_protect_queue_rows_carry_a_venue():
    api = _api()
    rows = []
    api._protect_save(rows)
    api._protect_queue_add("oX", "AXTI", 4.0, venue="paper")
    got = [r for r in api._protect_load() if r["order_id"] == "oX"]
    assert got and got[0]["venue"] == "paper", got
    api._protect_save([r for r in api._protect_load() if r["order_id"] != "oX"])


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
