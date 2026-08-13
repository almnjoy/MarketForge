"""Scan lock, scan log and brief. No network, no keys, no model.

Regression target (2026-08-12): CWVX appeared twice on one board with scores 75
and 72. Not brain 1 vs brain 2 - the short lane never writes to radar_alerts. It
was ONE scanner running twice, because `alert_exists_today()` is checked at the
top of the loop and `record_alert()` happens at the bottom with the LLM call in
between. Two overlapping runs both passed the check, both scored, both wrote.

Same shape as the duplicate paper fires: check-then-act with expensive work in
the gap.

Run: python bot/src/test_scan_lock.py
"""
from __future__ import annotations

import time
import types

import db
import radar          # the shim; kept to prove the old entry point still works


# ---------------------------------------------------------------------------
# THE LOCK AND SCAN-LOG TESTS THAT USED TO LIVE HERE MOVED.
# Their subject moved to scanner_core.py in the 2026-08-13 lane split, and the
# per-lane versions in test_scanner_core.py are strictly better: they also assert
# that one lane's lock does not block another, which is the property the split
# exists to create and which could not be expressed while the paths were globals.
# Duplicating them here would mean two copies drifting - the exact thing the
# extraction was done to prevent.
# ---------------------------------------------------------------------------

def test_claim_then_update_leaves_one_row_not_two():
    """The radar claims a symbol BEFORE scoring and fills the row in after. If
    the second write inserted instead of updating, every alert would double."""
    conn = db.connect()
    db.init_db(conn)
    conn.execute("DELETE FROM radar_alerts WHERE symbol='ZZTEST'")
    conn.commit()

    db.record_alert(conn, symbol="ZZTEST", kind="gainer", pct=40.0,
                    price_cents=1000, note="scoring...", verdict="pending")
    db.update_alert_scoring(conn, symbol="ZZTEST", kind="gainer", score=77,
                            verdict="signal", why="because", headline="h",
                            pct=41.0, price_cents=1100)

    rows = conn.execute(
        "SELECT score, verdict, why, pct FROM radar_alerts WHERE symbol='ZZTEST'"
    ).fetchall()
    assert len(rows) == 1, f"{len(rows)} rows, expected 1"
    assert rows[0]["score"] == 77 and rows[0]["verdict"] == "signal"
    assert rows[0]["why"] == "because" and abs(rows[0]["pct"] - 41.0) < 1e-6
    conn.execute("DELETE FROM radar_alerts WHERE symbol='ZZTEST'")
    conn.commit()


def test_alert_exists_today_sees_the_claim():
    """The claim is what makes a concurrent scan skip the symbol. If it did not
    register immediately, the lock would be the only protection."""
    conn = db.connect()
    db.init_db(conn)
    conn.execute("DELETE FROM radar_alerts WHERE symbol='ZZCLAIM'")
    conn.commit()
    assert not db.alert_exists_today(conn, "ZZCLAIM")
    db.record_alert(conn, symbol="ZZCLAIM", kind="gainer", pct=1.0,
                    price_cents=100, verdict="pending")
    assert db.alert_exists_today(conn, "ZZCLAIM"), "claim not visible to the dedupe"
    conn.execute("DELETE FROM radar_alerts WHERE symbol='ZZCLAIM'")
    conn.commit()


def test_companies_outrank_leveraged_products():
    """A real filer wins the tie against an ETP. On 2026-08-12 the whole top
    board was 2x single-stock ETFs, because sorting on move alone favours them."""
    alerts = [
        {"symbol": "CWVX", "score": 75, "supply_class": "not_a_filer"},
        {"symbol": "CRWV", "score": 70, "supply_class": "large"},
        {"symbol": "HQI", "score": 68, "supply_class": "micro"},
        {"symbol": "NBIL", "score": 90, "supply_class": "not_a_filer"},
    ]

    def rank(a):
        is_company = a.get("supply_class") not in (None, "not_a_filer")
        return (1 if is_company else 0, a["score"] if a["score"] is not None else -1)

    alerts.sort(key=rank, reverse=True)
    assert [a["symbol"] for a in alerts] == ["CRWV", "HQI", "NBIL", "CWVX"], alerts
    # a 90-score ETP still ranks below a 68-score company, and still APPEARS
    assert "NBIL" in [a["symbol"] for a in alerts]


def test_brief_is_quiet_when_nothing_changed():
    """A brief that fires every 5 minutes saying 'no change' trains you to ignore
    it, and then it is useless on the day something does change."""
    import brief
    diff = {"quiet": True, "changes": []}
    assert brief.phrase(diff) is None


def test_brief_falls_back_to_machine_voice_without_a_model():
    """No model configured must still produce the facts, never an empty brief."""
    import brief
    import llm
    orig = llm.complete
    llm.complete = lambda *a, **k: None
    try:
        diff = {"quiet": False, "changes": [
            {"kind": "risk", "severity": "high", "text": "UNPROTECTED: META"}]}
        out = brief.phrase(diff)
        assert out and "UNPROTECTED: META" in out, out
    finally:
        llm.complete = orig


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
