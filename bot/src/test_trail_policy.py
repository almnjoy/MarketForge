#!/usr/bin/env python3
"""THE 2026-08-17 BOARD: two rows, both junk, both of them past every gate.

    SIC    +244%, $14.49, score 85, verdict SIGNAL
    AESPW  +40.8%, $0.06, score 15, verdict noise

SIC's catalyst was Sun Capital buying Select Interior Concepts at $14.50 A SHARE -
in OCTOBER 2021. The deal closed, the ticker died, and the $14.49 print is the deal
price, not a move. The scorer was asked "does this news explain this move" and
answered yes, correctly, about a different decade.

AESPW is a $0.06 warrant. It was supposed to die on the dollar-volume floor, the
filter that replaced the price floor. It did not, because NEITHER name has a single
daily bar - and with no bars there is no dollar volume, so `adv` came back None and
the floor's `if adv is not None` let both walk. The junk filter was not running on
the junkiest names on the board.

Also covers the per-venue trail policy set the same day: paper keeps one blanket
failsafe, live is per symbol, and a live name with no read yet is still armed rather
than left naked.

Run: python bot/src/test_trail_policy.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import config

config.DATA_DIR = Path(tempfile.mkdtemp(prefix="mf-trail-"))
config.TRAIL_PLAN_PATH = config.DATA_DIR / "trail-plan.json"
config.RADAR_AUTO_EXECUTE = False
config.RADAR_DISCORD_WEBHOOK = ""
config.RADAR_REDDIT_ENABLED = False
config.ANNOTATE_SUPPLY = False
config.RADAR_MIN_MOVE_PCT = 10.0
config.RADAR_MIN_PRICE_CENTS = 0
config.RADAR_MIN_DOLLAR_VOLUME = 1_000_000
config.RADAR_SKIP_LEVERAGED = False
config.RADAR_REQUIRE_BARS = True
config.RADAR_MAX_HEADLINE_AGE_DAYS = 14
config.RADAR_TOP_N = 20
config.RADAR_TRAIL_PCT = 0.10
config.RADAR_SCAN_CLOSED_MARKET = True     # tested elsewhere; not the subject here

from testkit import stub_flask_if_missing   # noqa: E402

stub_flask_if_missing()

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'ok  ' if cond else 'FAIL'} {name}"
          + (f"\n       {detail}" if detail and not cond else ""))


import scan_live   # noqa: E402

TODAY = datetime.now().strftime("%Y-%m-%d")
NOW = datetime.now(timezone.utc)

# --- 1. the stale headline -------------------------------------------------
print("\nheadline age: a 2021 article is not today's catalyst")

NEWS = [
    {"headline": "Select Interior Concepts Acquired By Affiliate Of Sun Capital "
                 "Partners For $14.50/Share",
     "created_at": "2021-10-15T13:30:00Z", "url": "http://x/sic"},
    {"headline": "fresh one", "created_at": NOW.isoformat(), "url": "http://x/new"},
    {"headline": "undated one", "url": "http://x/undated"},
]

kept, dropped = scan_live.fresh_news(NEWS, config)
heads = [n["headline"] for n in kept]
check("the 2021 buyout headline is withheld from the scorer", dropped == 1,
      f"dropped={dropped}")
check("today's headline survives", "fresh one" in heads, heads)
check("an UNDATED item is kept - unknown age is not proof of staleness",
      "undated one" in heads, heads)

edge = [{"headline": "just inside", "created_at": (NOW - timedelta(days=13)).isoformat()},
        {"headline": "just outside", "created_at": (NOW - timedelta(days=15)).isoformat()}]
kept2, dropped2 = scan_live.fresh_news(edge, config)
check("the cutoff is the configured window, not a guess",
      dropped2 == 1 and kept2[0]["headline"] == "just inside", f"{kept2} {dropped2}")

class _Off:
    RADAR_MAX_HEADLINE_AGE_DAYS = 0
check("0 disables the check entirely", scan_live.fresh_news(NEWS, _Off())[1] == 0)
check("an empty feed is not an error", scan_live.fresh_news([], config) == ([], 0))


# --- 2. the no-bars mover -------------------------------------------------
print("\nno bar history: the hole the dollar-volume floor could not cover")

MOVERS = {"gainers": [
    {"symbol": "SIC",   "percent_change": 244.37, "price": 14.49},   # dead ticker
    {"symbol": "AESPW", "percent_change": 40.85,  "price": 0.06},    # warrant
    {"symbol": "REAL",  "percent_change": 22.0,   "price": 30.0},    # has history
]}
NO_BARS = {"SIC", "AESPW"}


class Client:
    def get_movers(self, top=20):
        return MOVERS

    def get_asset(self, sym):
        return {"name": f"{sym} INC"}

    def get_clock(self):
        return {"is_open": True}

    def get_latest_trade(self, sym):
        px = next(g["price"] for g in MOVERS["gainers"] if g["symbol"] == sym)
        return {"price_cents": int(px * 100), "at": f"{TODAY}T14:30:00Z"}

    def get_daily_bars(self, sym, limit=5):
        if sym in NO_BARS:
            return []                      # exactly what the live feed returns
        prev = 2400
        return [{"t": f"{TODAY}", "c": 3000, "h": 3000, "l": 3000, "o": 3000,
                 "v": 2_000_000},
                {"t": "2026-08-14", "c": prev, "h": prev, "l": prev, "o": prev,
                 "v": 2_000_000}]

    def get_news(self, symbols=None, limit=4):
        return [{"headline": f"{symbols[0]} beat and raised",
                 "created_at": NOW.isoformat(), "url": f"http://x/{symbols[0]}"}]

    def list_positions(self):
        return []


class Conn:
    def __init__(self):
        self.alerted, self.scored = [], []


import db     # noqa: E402
db.alert_exists_today = lambda conn, sym, kind: False
db.record_alert = lambda conn, **kw: conn.alerted.append(kw["symbol"])
db.update_alert_scoring = lambda conn, **kw: conn.scored.append(kw["symbol"])
import llm    # noqa: E402
llm.classify = lambda sym, pct, price, heads, cfg=None: {
    "score": 85, "verdict": "signal", "catalyst_type": "M&A", "why": f"{sym} reason"}
scan_live.classify = llm.classify

alerts = scan_live.scan(Client(), Conn(), config)
syms = [a["symbol"] for a in alerts]
check("SIC is rejected - no bars means nothing here can be evaluated",
      "SIC" not in syms, syms)
check("AESPW is rejected the same way, not on price", "AESPW" not in syms, syms)
check("a name WITH history still alerts", "REAL" in syms, syms)

log = json.loads((config.DATA_DIR / "scan-log.json").read_text())
rows = {r["symbol"]: r for r in log.get("rows", log if isinstance(log, list) else [])}
sic = rows.get("SIC", {})
check("the rejection says WHY in the scan log, not silently",
      "no daily bar history" in str(sic.get("reason", "")) or
      "no daily bar history" in str(sic), str(sic)[:200])

config.RADAR_REQUIRE_BARS = False
back = [a["symbol"] for a in scan_live.scan(Client(), Conn(), config)]
check("the gate is a knob - false lets them back on the board", "SIC" in back, back)
config.RADAR_REQUIRE_BARS = True


# --- 3. the per-venue trail policy ---------------------------------------
print("\ntrail policy: paper is blanket, live is per name, nothing is ever naked")

config.SWEEP_TRAIL_PCT_PAPER = 10.0
config.SWEEP_TRAIL_PCT_LIVE = 10.0
import api    # noqa: E402

api.config = config

pct, src = api.sweep_trail_for("paper", "ANY")
check("paper gets the blanket failsafe regardless of symbol", pct == 10.0, f"{pct} {src}")
check("...and says it is the blanket", "blanket" in src, src)

pct, src = api.sweep_trail_for("live", "CSCO")
check("a live name with NO read is still armed - naked is never the answer",
      pct == 10.0, f"{pct} {src}")
check("...but it is labelled a PLACEHOLDER so it gets a real width later",
      "PLACEHOLDER" in src, src)

config.TRAIL_PLAN_PATH.write_text(json.dumps(
    {"CSCO": {"trail_pct": 5.0, "why": "only runs 3-4%, 10 hands it all back"}}))
pct, src = api.sweep_trail_for("live", "CSCO")
check("with a plan entry, live uses the EVALUATED width", pct == 5.0, f"{pct} {src}")
check("...and carries the reason into the log line", "runs 3-4%" in src, src)
check("lowercase symbols resolve to the same plan entry",
      api.sweep_trail_for("live", "csco")[0] == 5.0)
check("an unplanned name is unaffected by another symbol's plan",
      api.sweep_trail_for("live", "SMCI")[0] == 10.0)

config.TRAIL_PLAN_PATH.write_text('{"CSCO": {"trail_pct": "junk"}}')
check("a corrupt width falls back to the placeholder instead of raising",
      api.sweep_trail_for("live", "CSCO")[0] == 10.0)
config.TRAIL_PLAN_PATH.write_text("not json at all")
check("an unreadable plan file does not break the sweep",
      api.sweep_trail_for("live", "CSCO")[0] == 10.0)
config.TRAIL_PLAN_PATH.unlink()
check("a MISSING plan file is the normal starting state, not an error",
      api.sweep_trail_for("live", "CSCO")[0] == 10.0)
check("paper never consults the live plan at all",
      api.sweep_trail_for("paper", "CSCO")[0] == 10.0)

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILED: " + ", ".join(FAIL))
sys.exit(1 if FAIL else 0)
