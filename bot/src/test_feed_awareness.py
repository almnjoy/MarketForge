#!/usr/bin/env python3
"""Feed-awareness tests. Run: python bot/src/test_feed_awareness.py

THE INCIDENT THESE PREVENT (would have happened 2026-08-13, on purchase):
`bot/.env` carried `RADAR_MIN_DOLLAR_VOLUME=3000000`, hand-set against the IEX
feed. Upgrading to Algo Trader Plus flips ALPACA_DATA_FEED to sip, where the same
stock reports roughly an order of magnitude more volume. Nothing would have
errored. The scan would still run and the board would still fill - and the floor
would have silently stopped rejecting anything.

Second half of the same purchase: STREAM_MAX_SUBSCRIPTIONS defaulted to 30, which
is the BASIC plan's cap. Algo Trader Plus removes the symbol cap, so leaving it
means paying for an unlimited feed and still watching fifteen names.

Both are the session's recurring shape: a number that was true when written and
reads as current forever after.
"""
from __future__ import annotations

import importlib
import io
import os
import sys
from contextlib import redirect_stdout

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'ok  ' if cond else 'FAIL'} {name}" + (f"\n       {detail}" if detail and not cond else ""))


class Snap:
    """A SNAPSHOT of config, not a reference to it.

    importlib.reload() mutates and returns the SAME module object, so holding two
    names for two loads gives you two aliases of one thing - and every comparison
    between them silently compares the later load to itself. That is what the first
    version of this file did, and it is the same shape as the bug it was written to
    catch: two things that look independent and are not.
    """

    def __init__(self, mod):
        for k in ("DATA_FEED", "IS_SIP", "RADAR_MIN_DOLLAR_VOLUME",
                  "MIN_AVG_DOLLAR_VOLUME", "STREAM_MAX_SUBSCRIPTIONS"):
            setattr(self, k, getattr(mod, k, None))
        self.banner = mod.feed_banner()

    def feed_banner(self):
        return self.banner


def load(feed=None, **env):
    """Reimport config under a given feed + env. Returns (Snap, stdout)."""
    for k in ("ALPACA_DATA_FEED", "RADAR_MIN_DOLLAR_VOLUME",
              "MIN_AVG_DOLLAR_VOLUME", "STREAM_MAX_SUBSCRIPTIONS"):
        os.environ.pop(k, None)
    if feed is not None:
        os.environ["ALPACA_DATA_FEED"] = feed
    for k, v in env.items():
        os.environ[k] = str(v)
    buf = io.StringIO()
    with redirect_stdout(buf):
        import config
        importlib.reload(config)
    return Snap(config), buf.getvalue()


print("the feed is known, not assumed")

iex, _ = load("iex")
sip, _ = load("sip")
check("IEX is detected", iex.DATA_FEED == "iex" and not iex.IS_SIP)
check("SIP is detected", sip.DATA_FEED == "sip" and sip.IS_SIP)
check("the feed is case and whitespace tolerant", load("  SIP  ")[0].IS_SIP)
check("an unset feed defaults to the free one, not the paid one",
      load(None)[0].DATA_FEED == "iex",
      "defaulting to sip would silently request data the plan may not include")

check("two loads are two SNAPSHOTS, not two names for one module",
      load("iex")[0].DATA_FEED == "iex" and load("sip")[0].DATA_FEED == "sip"
      and load("iex")[0].DATA_FEED == "iex",
      "the guard on this test file's own bug")

print("\nfloors scale with the feed")

iex, _ = load("iex")
sip, _ = load("sip")
check("the radar floor rises on SIP",
      sip.RADAR_MIN_DOLLAR_VOLUME > iex.RADAR_MIN_DOLLAR_VOLUME * 5,
      f"iex {iex.RADAR_MIN_DOLLAR_VOLUME:,.0f} vs sip {sip.RADAR_MIN_DOLLAR_VOLUME:,.0f}")
check("the long screen's floor rises on SIP",
      sip.MIN_AVG_DOLLAR_VOLUME > iex.MIN_AVG_DOLLAR_VOLUME * 5)
check("neither floor is left as None after the two-step definition",
      isinstance(iex.MIN_AVG_DOLLAR_VOLUME, float)
      and isinstance(sip.MIN_AVG_DOLLAR_VOLUME, float),
      "MIN_AVG_DOLLAR_VOLUME is declared None then set below - if that ordering "
      "ever breaks, every comparison against it raises")

print("\nthe exact .env that was sitting on disk")

sip_stale, out = load("sip", RADAR_MIN_DOLLAR_VOLUME=3_000_000)
check("an explicit floor is still HONOURED (his number, his call)",
      sip_stale.RADAR_MIN_DOLLAR_VOLUME == 3_000_000)
check("...but it SAYS the number looks IEX-calibrated on a SIP feed",
      "looks IEX-calibrated" in out, repr(out))
check("the warning names the variable so it is findable",
      "RADAR_MIN_DOLLAR_VOLUME" in out)
check("the warning suggests a concrete replacement, not just a complaint",
      "Consider" in out)

_, quiet = load("sip", RADAR_MIN_DOLLAR_VOLUME=30_000_000)
check("a correctly-scaled explicit value is silent",
      "looks IEX-calibrated" not in quiet,
      "warning on a correct value trains you to ignore warnings")

_, backwards = load("iex", RADAR_MIN_DOLLAR_VOLUME=30_000_000)
check("the mistake in the other direction is caught too",
      "looks SIP-calibrated" in backwards,
      "a SIP floor on IEX rejects nearly everything - a board that goes empty "
      "for no visible reason")

print("\nthe websocket budget")

check("IEX keeps the Basic cap of 30 subscriptions",
      iex.STREAM_MAX_SUBSCRIPTIONS == 30)
check("SIP raises it, or the upgrade buys nothing",
      sip.STREAM_MAX_SUBSCRIPTIONS > 100,
      f"got {sip.STREAM_MAX_SUBSCRIPTIONS} - Algo Trader Plus removes the symbol cap")
check("the derived symbol count follows (2 channels per symbol)",
      sip.STREAM_MAX_SUBSCRIPTIONS // 2 >= 50 and iex.STREAM_MAX_SUBSCRIPTIONS // 2 == 15,
      "the cap is on SUBSCRIPTIONS; trades+quotes cost one each")

print("\nthe banner")

check("the banner states the feed and its consequence, both feeds",
      "15 minutes" in iex.feed_banner() and "SIP" in sip.feed_banner())
check("the IEX banner warns that floors are IEX-scaled",
      "IEX-scaled" in iex.feed_banner())
check("the SIP banner says the 15-minute blindness is gone",
      "no longer" in sip.feed_banner())

load("iex")   # leave the module in its real state
print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
