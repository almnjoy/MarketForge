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


def test_symbol_budget_is_the_free_plan_cap():
    assert stream.MAX_SYMBOLS == 30


def test_positions_are_never_evicted_by_radar():
    """Held names are the highest-value subscriptions. They go first and a long
    radar must not push them past the 30 cap."""
    class C:
        def list_positions(self):
            return [{"symbol": f"POS{i}"} for i in range(5)]
    (config.DATA_DIR).mkdir(parents=True, exist_ok=True)
    (config.DATA_DIR / "radar.json").write_text(json.dumps(
        [{"symbol": f"RAD{i}", "score": 90 - i} for i in range(60)]))
    syms = stream.default_symbols(C())
    assert len(syms) == 30
    for i in range(5):
        assert f"POS{i}" in syms, syms[:8]
    assert syms[:5] == [f"POS{i}" for i in range(5)]


def test_indices_are_requested():
    class C:
        def list_positions(self):
            return []
    (config.DATA_DIR / "radar.json").write_text("[]")
    syms = stream.default_symbols(C())
    for s in ("SPY", "QQQ", "IWM"):
        assert s in syms


# --- fundamentals -----------------------------------------------------------
def test_supply_class_buckets():
    assert fundamentals.supply_class(5_000_000) == "micro"
    assert fundamentals.supply_class(67_400_000) == "small"
    assert fundamentals.supply_class(306_000_000) == "large"
    assert fundamentals.supply_class(24_200_000_000) == "mega"
    assert fundamentals.supply_class(None) == "unknown"


def test_annotate_degrades_to_unknown_not_an_exception():
    """A scan must never die because a government website is down."""
    orig = fundamentals.shares_outstanding
    fundamentals.shares_outstanding = lambda *a, **k: None
    try:
        a = fundamentals.annotate("NOPE")
        assert a["supply_class"] == "unknown"
        assert a["shares_outstanding"] is None
        assert "no SEC share count" in a["note"]
    finally:
        fundamentals.shares_outstanding = orig


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
    print(f"\n{passed} passed, {failed} failed")
    raise SystemExit(1 if failed else 0)
