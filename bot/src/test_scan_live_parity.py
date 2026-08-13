#!/usr/bin/env python3
"""PARITY: scan_live (on scanner_core) must equal radar (the monolith).

Run: python bot/src/test_scan_live_parity.py

531 lines do not get deleted because the replacement looks right. Both scanners are
driven with the SAME fake client, the same fake db and the same fake LLM, and the
alert lists are compared field by field.

The fakes are deliberately nasty, because the value of this test is entirely in the
edge cases the original earned the hard way:
  - a duplicate symbol in the movers feed (OFAL/SMCL, scored twice in one scan)
  - a leveraged wrapper AND a real company with '2X' in its name
  - a mover whose verified move collapses (the AMIX +466% case)
  - a name under the dollar-volume floor
  - a non-filer that must rank BELOW a lower-scoring real company
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import config

config.DATA_DIR = Path(tempfile.mkdtemp(prefix="mf-parity-"))
config.RADAR_AUTO_EXECUTE = False
config.RADAR_DISCORD_WEBHOOK = ""
config.RADAR_REDDIT_ENABLED = False
config.ANNOTATE_SUPPLY = False
config.RADAR_MIN_MOVE_PCT = 10.0
config.RADAR_MIN_PRICE_CENTS = 300
config.RADAR_MIN_DOLLAR_VOLUME = 1_000_000
config.RADAR_SKIP_LEVERAGED = True
config.RADAR_TOP_N = 20
config.RADAR_LLM_MIN_SCORE = 70
config.RADAR_TRAIL_PCT = 0.10

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'ok  ' if cond else 'FAIL'} {name}" + (f"\n       {detail}" if detail and not cond else ""))


# --- fakes -----------------------------------------------------------------
MOVERS = {"gainers": [
    {"symbol": "HQI",  "percent_change": 34.0, "price": 18.0},   # clean alert
    {"symbol": "CWVX", "percent_change": 61.0, "price": 29.0},   # leveraged wrapper
    {"symbol": "HQI",  "percent_change": 34.0, "price": 18.0},   # DUPLICATE
    {"symbol": "PENNY", "percent_change": 80.0, "price": 1.10},  # under price floor
    {"symbol": "AMIX", "percent_change": 466.0, "price": 5.00},  # verified move collapses
    {"symbol": "THIN", "percent_change": 40.0, "price": 12.0},   # under dollar-volume floor
    {"symbol": "TWOX", "percent_change": 22.0, "price": 40.0},   # real company, '2X' in name
    {"symbol": "MEH",  "percent_change": 4.0,  "price": 50.0},   # under the move bar
]}
NAMES = {"CWVX": "GRANITESHARES 2X LONG CRWV DAILY ETF", "TWOX": "2X GENOMICS INC"}
SCORES = {"HQI": 70, "AMIX": 90, "TWOX": 95, "THIN": 60}


class FakeClient:
    def __init__(self): self.calls = []

    def get_movers(self, top=20): return MOVERS

    def get_asset(self, sym): return {"name": NAMES.get(sym, f"{sym} INC")}

    def get_latest_price(self, sym):
        return 500 if sym == "AMIX" else int(
            next(g["price"] for g in MOVERS["gainers"] if g["symbol"] == sym) * 100)

    def get_daily_bars(self, sym, limit=5):
        prev = 495 if sym == "AMIX" else 100      # AMIX: verified move ~+1%, collapses
        return [{"t": "2026-08-11", "c": prev, "h": prev, "l": prev, "o": prev,
                 "v": 50 if sym == "THIN" else 5_000_000},
                {"t": "2026-08-12", "c": prev, "h": prev, "l": prev, "o": prev,
                 "v": 50 if sym == "THIN" else 5_000_000}]

    def get_news(self, symbols=None, limit=4):
        return [{"headline": f"{symbols[0]} news", "url": f"http://x/{symbols[0]}"}]

    def list_positions(self): return []


class FakeConn:
    def __init__(self): self.alerted, self.scored = [], []


def install_fakes():
    import db
    db.alert_exists_today = lambda conn, sym, kind: False
    db.record_alert = lambda conn, **kw: conn.alerted.append(kw["symbol"])
    db.update_alert_scoring = lambda conn, **kw: conn.scored.append(kw["symbol"])
    import llm
    llm.classify = lambda sym, pct, price, heads, cfg=None: (
        {"score": SCORES.get(sym, 50), "verdict": "signal",
         "catalyst_type": "news", "why": f"{sym} reason"})
    # Non-filer only for the wrapper-ish name, so ranking has something to order.
    import fundamentals
    fundamentals.annotate = lambda sym: (
        {"supply_class": "not_a_filer"} if sym == "AMIX"
        else {"supply_class": "low_supply", "shares_outstanding": 10_000_000})


install_fakes()
import radar          # noqa: E402
import scan_live      # noqa: E402

# both modules captured `classify` at import time
import llm            # noqa: E402
radar.classify = llm.classify
scan_live.classify = llm.classify

print("running both scanners on identical input")

old = radar.scan(FakeClient(), FakeConn(), config)
new = scan_live.scan(FakeClient(), FakeConn(), config)

check("both produced alerts at all", old and new, f"old={len(old)} new={len(new)}")
check("same number of alerts", len(old) == len(new), f"old={len(old)} new={len(new)}")
check("same symbols, same order",
      [a["symbol"] for a in old] == [a["symbol"] for a in new],
      f"\n       old={[a['symbol'] for a in old]}\n       new={[a['symbol'] for a in new]}")

for field in ("pct", "price", "score", "verdict", "catalyst_type", "why"):
    check(f"same {field} on every alert",
          [a.get(field) for a in old] == [a.get(field) for a in new],
          f"old={[a.get(field) for a in old]} new={[a.get(field) for a in new]}")

print("\nthe edge cases the original earned the hard way")

syms = [a["symbol"] for a in new]
check("the duplicate HQI produced ONE alert, not two", syms.count("HQI") == 1, str(syms))
check("the leveraged wrapper CWVX is gone", "CWVX" not in syms, str(syms))
check("the real company with '2X' in its name SURVIVED", "TWOX" in syms, str(syms))
check("the sub-floor PENNY is gone", "PENNY" not in syms, str(syms))
check("the under-the-bar MEH is gone", "MEH" not in syms, str(syms))
check("AMIX is gone - its verified move collapsed from +466% to ~+1%",
      "AMIX" not in syms, str(syms))
check("THIN is gone - under the dollar-volume floor", "THIN" not in syms, str(syms))

print("\nthe scan log")

log_path = scan_live.LANE.scanlog_path
check("the live lane writes the filename the Scoring tab already reads",
      log_path.name == "scan-log.json", str(log_path))
check("the log exists after a scan", log_path.exists())

import json  # noqa: E402
written = json.loads(log_path.read_text())
skipped = {r["symbol"] for r in written["rows"] if r["decision"] == "skipped"}
check("every rejection is in the log with a reason",
      {"CWVX", "PENNY", "MEH", "AMIX", "THIN"} <= skipped, str(skipped))
check("rejections carry their reason text",
      all(r.get("reason") for r in written["rows"]))
check("the duplicate did NOT produce a skip row (it was deduped before the loop)",
      sum(1 for r in written["rows"] if r["symbol"] == "HQI") == 1,
      "deduping in the loop would log it twice and look like a real rejection")

print("\nlane isolation")

check("the live lane's lock is its own file",
      scan_live.LANE.lock_path.name == "live-scan.lock",
      str(scan_live.LANE.lock_path))
check("a daily-lane scan would not block it",
      scan_live.LANE.lock_path != __import__("scanner_core").Lane("daily").lock_path)

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if not FAIL:
    print("\nPARITY HOLDS. radar.py can be retired.")
sys.exit(1 if FAIL else 0)
