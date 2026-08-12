"""Tests for the live price tap and the supply annotation. No network, no keys.

The property that matters for stream.py: **a stale price must never be handed
back as a current one.** The whole reason the tap exists is that REST is 15
minutes blind; a tap that silently returns old prints just moves the same lie to
a new file.

For fundamentals.py: the number is shares OUTSTANDING, not float, and nothing may
gate a scan on it.

Run: python bot/src/test_stream_supply.py
"""
from __future__ import annotations

import json
import time
import types

import config
import db
import fundamentals
import stream


def _write(prices, stale_after=90):
    stream.OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    stream.OUT_PATH.write_text(json.dumps(
        {"ts": "x", "stale_after_s": stale_after, "prices": prices}), encoding="utf-8")


# --- stream -----------------------------------------------------------------
def test_fresh_price_is_returned():
    _write({"NVDA": {"price": 220.5, "epoch_ts": time.time() - 5, "kind": "trade"}})
    r = stream.read_live("NVDA")
    assert r and r["price"] == 220.5 and r["age_s"] < 10, r


def test_stale_price_is_withheld():
    """THE TEST. A 20-minute-old print must come back as None, not as a price."""
    _write({"NVDA": {"price": 220.5, "epoch_ts": time.time() - 1200, "kind": "trade"}})
    assert stream.read_live("NVDA") is None
    assert stream.read_live() == {}


def test_a_row_with_no_timestamp_is_never_trusted():
    _write({"NVDA": {"price": 220.5, "kind": "trade"}})
    assert stream.read_live("NVDA") is None


def test_caller_can_demand_a_tighter_window():
    _write({"NVDA": {"price": 1, "epoch_ts": time.time() - 45, "kind": "trade"}})
    assert stream.read_live("NVDA") is not None          # default 90s
    assert stream.read_live("NVDA", max_age_s=10) is None


def test_missing_file_is_not_an_error():
    if stream.OUT_PATH.exists():
        stream.OUT_PATH.unlink()
    assert stream.read_live("NVDA") is None
    assert stream.read_live() is None


def test_symbol_budget_is_derived_from_the_subscription_cap():
    """Do NOT restate the number. The cap is on SUBSCRIPTIONS and each symbol
    costs one per channel; hard-coding 30 here is what let 16 symbols x 2
    channels go out against a 30 cap and get rejected wholesale."""
    assert stream.MAX_SYMBOLS * len(stream.CHANNELS) <= stream.MAX_SUBSCRIPTIONS


TEST_PREFIX = "ZTEST"


def _clear_seeded():
    """Remove ONLY this file's fixtures from the shared store."""
    conn = db.connect()
    db.init_db(conn)
    conn.execute("DELETE FROM radar_alerts WHERE symbol LIKE ?", (TEST_PREFIX + "%",))
    conn.commit()
    return conn


def _seed_alerts(n):
    """Put n alerts in the REAL store the radar uses.

    REGRESSION 1: these tests used to write data/radar.json, which does not exist
    and never did - radar.py calls db.record_alert(). So they passed against a
    file the production path never reads, while the live tap silently fell back
    to 5 symbols. A fixture that invents its own data source proves nothing.

    REGRESSION 2 (worse): having moved to the real store, they then SEEDED IT AND
    LEFT THE ROWS THERE. Running the suite put 60 fake alerts on the actual radar
    board, and the leftovers made the next run's assertions fail for reasons that
    had nothing to do with the code. Tests that share production storage must
    clean up before AND after, and must be identifiable as fixtures.
    """
    conn = _clear_seeded()
    for i in range(n):
        db.record_alert(conn, symbol=f"{TEST_PREFIX}{i}", kind="gainer", pct=10.0,
                        price_cents=1000, score=90 - i)
    return conn


def test_positions_are_never_evicted_by_radar():
    """Held names are the highest-value subscriptions. They go first and a long
    radar must not push them past the 30 cap."""
    class C:
        def list_positions(self):
            return [{"symbol": f"POS{i}"} for i in range(5)]
    _seed_alerts(60)
    try:
        syms = stream.default_symbols(C())
        assert len(syms) == stream.MAX_SYMBOLS, len(syms)
        for i in range(5):
            assert f"POS{i}" in syms, syms[:8]
        assert syms[:5] == [f"POS{i}" for i in range(5)]
        # and the budget actually got spent on radar names, not left idle
        assert any(s.startswith(TEST_PREFIX) for s in syms), syms
    finally:
        _clear_seeded()


def test_indices_survive_a_busy_radar():
    """REAL BUG this caught: indices were appended LAST, so a 60-alert day
    evicted SPY/QQQ/IWM and the regime gate's own symbols went untapped without
    anything saying so. They are reserved now, right after positions.

    Deliberately does NOT clear the store first - a full store is the condition
    the bug needed."""
    _seed_alerts(60)
    try:
        class C:
            def list_positions(self):
                return [{"symbol": "SMCI"}]
        syms = stream.default_symbols(C())
        assert len(syms) == stream.MAX_SYMBOLS
        assert syms[0] == "SMCI", syms[:5]
        for s in stream.INDEXES:
            assert s in syms, f"{s} evicted by a busy radar: {syms}"
        assert any(x.startswith(TEST_PREFIX) for x in syms), "radar got no slots"
    finally:
        _clear_seeded()


def test_a_missing_alert_store_still_gives_indices():
    """The failure that hid the bug: an unreadable source must not silently
    produce a 5-symbol subscription that looks like success."""
    orig = db.recent_alerts
    db.recent_alerts = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    try:
        class C:
            def list_positions(self):
                return [{"symbol": "SMCI"}]
        syms = stream.default_symbols(C())
        assert syms[0] == "SMCI"
        for s in ("SPY", "QQQ", "IWM"):
            assert s in syms, syms
    finally:
        db.recent_alerts = orig


# --- fundamentals -----------------------------------------------------------
def test_supply_class_buckets():
    assert fundamentals.supply_class(5_000_000) == "micro"
    assert fundamentals.supply_class(67_400_000) == "small"
    assert fundamentals.supply_class(306_000_000) == "large"
    assert fundamentals.supply_class(24_200_000_000) == "mega"
    assert fundamentals.supply_class(None) == "unknown"


def test_annotate_degrades_gracefully_not_by_exception():
    """A scan must never die because a government website is down.

    Two distinct no-data cases, and conflating them threw away a real signal:
      not_a_filer = not in SEC's company map at all -> almost certainly an
                    ETF/ETP. CWVX, NBIL, CRWG, SMCL and NBIG all land here, and
                    that is WHY every card read "unknown" on 2026-08-12.
      unknown     = it IS a filer, but the share count did not come back.
    """
    o_shares, o_cik = fundamentals.shares_outstanding, fundamentals.cik_for
    fundamentals.shares_outstanding = lambda *a, **k: None
    try:
        fundamentals.cik_for = lambda s: None            # not in the SEC map
        a = fundamentals.annotate("CWVX")
        assert a["supply_class"] == "not_a_filer", a
        assert a["shares_outstanding"] is None
        assert "ETF/ETP" in a["note"], a["note"]

        fundamentals.cik_for = lambda s: "0000320193"    # a real filer
        b = fundamentals.annotate("AAPL")
        assert b["supply_class"] == "unknown", b
        assert "no share count retrieved" in b["note"], b["note"]
    finally:
        fundamentals.shares_outstanding, fundamentals.cik_for = o_shares, o_cik


def test_annotation_says_outstanding_not_float():
    """Naming this 'float' would be a quiet lie. Keep it honest in the payload."""
    orig = fundamentals.shares_outstanding
    fundamentals.shares_outstanding = lambda *a, **k: {
        "shares": 13_900_000, "as_of": "2026-08-10", "source": "SEC EDGAR"}
    try:
        a = fundamentals.annotate("HQI")
        assert a["supply_class"] == "micro"
        assert a["shares_millions"] == 13.9
        assert "not free float" in a["note"], a["note"]
        assert "float" not in [k for k in a if k != "note"], list(a)
    finally:
        fundamentals.shares_outstanding = orig


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
    if stream.OUT_PATH.exists():
        stream.OUT_PATH.unlink()
    try:
        _clear_seeded()          # never leave fixtures in the real radar store
    except Exception:
        pass
    print(f"\n{passed} passed, {failed} failed")
    raise SystemExit(1 if failed else 0)
