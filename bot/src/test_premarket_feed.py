#!/usr/bin/env python3
"""THE 2026-08-17 PRE-MARKET SCAN: 20 candidates in, 0 alerts out, 18 of them killed
with "verified move is only 0.0% - the screener number was stale or split-skewed".

The screener number was fine. OURS was stale. Before the bell the movers feed still
serves the LAST COMPLETED session, and on the IEX feed each candidate's "latest
trade" IS that session's closing print - HTFL's latest trade was stamped
2026-08-14T19:59:57Z against a 2026-08-14 daily close of $42.10. The radar compared
42.095 to 42.10, called it a verified 0.0% move, and reported a quiet tape.

Run: python bot/src/test_premarket_feed.py
"""
from __future__ import annotations

import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import config

config.DATA_DIR = Path(tempfile.mkdtemp(prefix="mf-premarket-"))
config.RADAR_AUTO_EXECUTE = False
config.RADAR_DISCORD_WEBHOOK = ""
config.RADAR_REDDIT_ENABLED = False
config.ANNOTATE_SUPPLY = False
config.RADAR_MIN_MOVE_PCT = 10.0
config.RADAR_MIN_PRICE_CENTS = 300
config.RADAR_MIN_DOLLAR_VOLUME = 0
config.RADAR_SKIP_LEVERAGED = False
config.RADAR_TOP_N = 20
config.RADAR_LLM_MIN_SCORE = 70
config.RADAR_TRAIL_PCT = 0.10
config.RADAR_SCAN_CLOSED_MARKET = False

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'ok  ' if cond else 'FAIL'} {name}" + (f"\n       {detail}" if detail and not cond else ""))


TODAY = datetime.now().strftime("%Y-%m-%d")
PRIOR = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d")


class Client:
    """HTFL as it actually was at 08:59 ET: screener says +28.5%, the only trade on
    the tape is the prior session's 4210c close, and there is no bar for today."""

    def __init__(self, open_market=False, last_bar=None, trade_day=None,
                 trade_cents=4210):
        self.open_market = open_market
        self.last_bar = last_bar or PRIOR
        self.trade_day = trade_day or PRIOR
        self.trade_cents = trade_cents
        self.movers_calls = 0

    def get_clock(self):
        return {"is_open": self.open_market}

    def get_movers(self, top=20):
        self.movers_calls += 1
        return {"gainers": [{"symbol": "HTFL", "percent_change": 28.5, "price": 39.85}]}

    def get_asset(self, sym):
        return {"name": f"{sym} INC"}

    def get_latest_trade(self, sym):
        return {"price_cents": self.trade_cents, "at": f"{self.trade_day}T19:59:57.833Z"}

    def get_daily_bars(self, sym, limit=5):
        days = [PRIOR, self.last_bar] if self.last_bar != PRIOR else [PRIOR, PRIOR]
        return [{"t": d, "c": 3101 if i == 0 else 4210, "h": 4210, "l": 3101,
                 "o": 3101, "v": 5_000_000} for i, d in enumerate(days)]


import scan_live  # noqa: E402

print("pre-market feed guard")

# 1. THE BUG ITSELF: a close measured against itself is not a verified 0.0%.
c = Client()
pct, price, is_ipo, verified = scan_live.verify_pct(c, "HTFL", 39.85, 28.5, config)
check("stale print does not report a verified move", verified is False,
      f"got verified={verified} pct={pct}")
check("the screener's number survives instead of being zeroed", pct == 28.5,
      f"got pct={pct}")

# 2. A REAL pre-market print (today, newer than the last close) still verifies:
#    $46.31 against the $42.10 close is a genuine +10.0% before the bell.
c2 = Client(trade_day=TODAY, trade_cents=4631)
pct2, _, _, verified2 = scan_live.verify_pct(c2, "HTFL", 39.85, 28.5, config)
check("a genuine pre-market trade still verifies against the last close",
      verified2 is True and abs(pct2 - 10.0) < 0.05, f"got verified={verified2} pct={pct2}")

# 3. Intraday (today's bar exists, market open) is untouched: prev = yesterday.
c3 = Client(open_market=True, last_bar=TODAY, trade_day=TODAY)
pct3, _, _, verified3 = scan_live.verify_pct(c3, "HTFL", 39.85, 28.5, config)
check("intraday math unchanged - compares to the PRIOR close", verified3 is True,
      f"got verified={verified3} pct={pct3}")

# 4. The scan refuses to run at all on a stale feed, and says why.
c4 = Client()
alerts = scan_live.scan(c4, object(), config)
check("stale-session scan returns no alerts", alerts == [], f"got {alerts}")
check("stale-session scan never even pulls the movers feed", c4.movers_calls == 0,
      f"movers called {c4.movers_calls}x")

log = config.DATA_DIR / "scan-live-log.json"
legacy = config.DATA_DIR / "scan-log.json"
written = log if log.exists() else legacy
check("it leaves a reason in the scan log, not a silent zero", written.exists(),
      f"looked for {log} / {legacy}")
if written.exists():
    txt = written.read_text(encoding="utf-8")
    check("the reason names the stale session", "completed session" in txt and PRIOR in txt,
          txt[:300])

# 5. After the close is NOT stale - that feed is the real session.
c5 = Client(last_bar=TODAY, trade_day=TODAY)
stale, _ = scan_live.feed_session_is_stale(c5)
check("after-hours is not treated as stale", stale is False)

# 6. Fails OPEN: a clock that throws must never silence a scan.
class Broken(Client):
    def get_clock(self): raise RuntimeError("clock down")


stale6, _ = scan_live.feed_session_is_stale(Broken())
check("a broken clock fails open", stale6 is False)

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILED: " + ", ".join(FAIL))
sys.exit(1 if FAIL else 0)
