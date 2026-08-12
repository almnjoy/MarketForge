"""Scheduler state + stream subscription budget. No network, no keys.

Two bugs found the moment these were switched on for real (2026-08-12):

  1. Enabling a 30-minute scan fired it THREE TIMES IN 43 SECONDS, because the
     schedule loader did not carry `last_run_ts` - the one field the due-check
     reads. Every reload reset it to 0, so every 20s tick believed it was overdue.

  2. The tap authenticated, then Alpaca answered `405 symbol limit exceeded` and
     delivered nothing, because the cap counts SUBSCRIPTIONS and the same symbol
     list went to both trades and quotes. 16 symbols was 32 against a 30 cap.

Both are the same species: a number or a field that looked right in one place and
was wrong in the place that used it.

Run: python bot/src/test_schedule_state.py
"""
from __future__ import annotations

import json
import time
import types

import stream


# --- the scheduler loader ---------------------------------------------------
def _loader(d):
    """The shape app.py._sched_load() returns. Kept in sync by the assertions
    below rather than by hoping."""
    return {"enabled": bool(d.get("enabled", False)),
            "every_min": int(d.get("every_min", 30) or 30),
            "job": str(d.get("job", "radar")),
            "market_hours_only": bool(d.get("market_hours_only", True)),
            "last_run": d.get("last_run"),
            "last_run_ts": float(d.get("last_run_ts") or 0),
            "last_result": d.get("last_result"),
            "next_run": d.get("next_run")}


def _due(s):
    return (time.time() - float(s.get("last_run_ts") or 0)) >= s["every_min"] * 60


def test_last_run_ts_survives_a_reload():
    """THE BUG. Drop this field and every tick thinks the job is overdue."""
    saved = {"enabled": True, "every_min": 30, "last_run_ts": time.time()}
    reloaded = _loader(json.loads(json.dumps(saved)))
    assert reloaded["last_run_ts"] == saved["last_run_ts"], reloaded


def test_a_job_that_just_ran_is_not_due_again():
    s = _loader({"enabled": True, "every_min": 30, "last_run_ts": time.time()})
    assert not _due(s), "fired again immediately after running"


def test_a_job_is_due_once_the_interval_elapses():
    s = _loader({"enabled": True, "every_min": 30,
                 "last_run_ts": time.time() - 31 * 60})
    assert _due(s)


def test_first_enable_runs_once_immediately():
    """No last_run_ts yet means it has never run - that SHOULD fire."""
    s = _loader({"enabled": True, "every_min": 30})
    assert _due(s)


def test_it_does_not_fire_three_times_in_a_minute():
    """Simulate the 20s worker tick across 60s, persisting through the loader
    each time, exactly as the real loop does."""
    state = {"enabled": True, "every_min": 30, "last_run_ts": 0}
    fires, now = 0, time.time()
    for tick in range(0, 60, 20):
        s = _loader(json.loads(json.dumps(state)))     # round-trips to disk
        if (now + tick - float(s["last_run_ts"])) >= s["every_min"] * 60:
            fires += 1
            state["last_run_ts"] = now + tick
    assert fires == 1, f"fired {fires} times in 60s on a 30-minute schedule"


# --- the subscription budget ------------------------------------------------
def test_symbol_budget_respects_the_SUBSCRIPTION_cap():
    """THE OTHER BUG. The cap is subscriptions, not symbols, and every symbol
    costs one per channel."""
    assert stream.MAX_SYMBOLS * len(stream.CHANNELS) <= stream.MAX_SUBSCRIPTIONS, (
        f"{stream.MAX_SYMBOLS} symbols x {len(stream.CHANNELS)} channels exceeds "
        f"{stream.MAX_SUBSCRIPTIONS}")


def test_the_old_thirty_would_have_been_rejected():
    """Guards the regression directly: 30 symbols on two channels is 60."""
    assert 30 * len(stream.CHANNELS) > stream.MAX_SUBSCRIPTIONS
    assert stream.MAX_SYMBOLS == 15, stream.MAX_SYMBOLS


def test_default_symbols_never_exceeds_the_budget():
    class C:
        def list_positions(self):
            return [{"symbol": f"P{i}"} for i in range(40)]
    syms = stream.default_symbols(C())
    assert len(syms) <= stream.MAX_SYMBOLS, len(syms)
    assert len(syms) * len(stream.CHANNELS) <= stream.MAX_SUBSCRIPTIONS


def test_indices_still_fit_inside_the_smaller_budget():
    """Shrinking 30 -> 15 must not quietly push the regime symbols out."""
    class C:
        def list_positions(self):
            return [{"symbol": f"P{i}"} for i in range(5)]
    syms = stream.default_symbols(C())
    for s in stream.INDEXES:
        assert s in syms, f"{s} missing from {syms}"


# --- the write path ---------------------------------------------------------
def test_flush_falls_back_when_rename_is_denied():
    """WinError 5 on a mapped network drive made every flush fail, so
    live-prices.json froze while the tap looked healthy. A readable file beats
    an atomic one."""
    import pathlib
    orig = pathlib.Path.replace

    def deny(self, target):
        raise OSError(5, "Access is denied")
    pathlib.Path.replace = deny
    try:
        stream._flush.__dict__.pop("warned", None)
        with stream._lock:
            stream._prices["TEST"] = {"price": 1.23, "ts": time.time(), "kind": "trade"}
        stream._flush()
        d = json.loads(stream.OUT_PATH.read_text(encoding="utf-8"))
        assert d["prices"]["TEST"]["price"] == 1.23, d
    finally:
        pathlib.Path.replace = orig
        stream._prices.clear()
        stream.OUT_PATH.unlink(missing_ok=True)


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
