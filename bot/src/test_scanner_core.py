#!/usr/bin/env python3
"""scanner_core tests. Run: python bot/src/test_scanner_core.py

Every case below is a real incident from the radar's history. The point of the
extraction is that these fixes now exist ONCE, so this file is the thing that
proves a lane scanner cannot quietly lose one.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

import config

# Point DATA_DIR at a temp dir BEFORE importing scanner_core - its paths derive
# from it, and a test that writes to the real data dir is a test that seeded 60
# fake alerts into the production radar store and left them there. Once was enough.
_TMP = tempfile.mkdtemp(prefix="mf-scancore-")
config.DATA_DIR = Path(_TMP)

import scanner_core as sc   # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'ok  ' if cond else 'FAIL'} {name}" + (f"\n       {detail}" if detail and not cond else ""))


print("lane isolation - the thing a naive extraction breaks")

daily, live, paper = sc.Lane("daily"), sc.Lane("live"), sc.Lane("paper")

check("three lanes get three lock files",
      len({daily.lock_path, live.lock_path, paper.lock_path}) == 3,
      f"{daily.lock_path} / {live.lock_path} / {paper.lock_path}")

check("three lanes get three scan logs",
      len({daily.scanlog_path, live.scanlog_path, paper.scanlog_path}) == 3)

with sc.ScanLock(daily):
    busy = False
    try:
        with sc.ScanLock(daily):
            pass
    except sc.ScanBusy:
        busy = True
    check("a second scan of the SAME lane is refused", busy)

    other_ok = True
    try:
        with sc.ScanLock(live):
            pass
    except sc.ScanBusy:
        other_ok = False
    check("a DIFFERENT lane is not blocked by it", other_ok,
          "sharing one lock file would make the lanes depend on each other - "
          "the exact thing the restructure exists to prevent")

check("the lock is released on exit", not daily.lock_path.exists())

# Failure inside the block must still release, or one crash wedges the lane for
# five minutes and every later scan reports 'busy' about a process that is gone.
try:
    with sc.ScanLock(daily):
        raise RuntimeError("boom")
except RuntimeError:
    pass
check("an exception inside the block still releases the lock",
      not daily.lock_path.exists())

# Stale takeover: a killed process leaves the file behind forever.
daily.lock_path.write_text("99999")
old = time.time() - (sc.LOCK_STALE_S + 10)
os.utime(daily.lock_path, (old, old))
took = False
with sc.ScanLock(daily):
    took = True
check("a stale lock is taken over, not obeyed forever", took)

check("it REFUSES rather than waits",
      sc.ScanBusy.__mro__[1] is RuntimeError,
      "waiting would run the duplicate late with staler data")

print("\ndedupe - OFAL/SMCL scored twice one second apart inside ONE scan")

kept, dropped = sc.dedupe_candidates(
    [{"symbol": "OFAL"}, {"symbol": "SMCL"}, {"symbol": "OFAL"}, {"symbol": "NBIS"}])
check("duplicates are dropped", len(kept) == 3 and dropped == 1)
check("the FIRST occurrence survives (feeds arrive rank-sorted)",
      kept[0]["symbol"] == "OFAL" and [k["symbol"] for k in kept] == ["OFAL", "SMCL", "NBIS"])
check("case and whitespace do not smuggle a dupe through",
      sc.dedupe_candidates([{"symbol": "ofal"}, {"symbol": " OFAL "}])[1] == 1)
check("blank symbols are dropped, not kept as a group",
      sc.dedupe_candidates([{"symbol": ""}, {"symbol": None}, {"symbol": "X"}])[0]
      == [{"symbol": "X"}])
check("empty input does not explode", sc.dedupe_candidates([]) == ([], 0))

print("\nleveraged wrappers - the board was two ideas counted seven times")

check("CoreWeave 2x ETF is a wrapper",
      sc.is_leveraged_wrapper("GRANITESHARES 2X LONG CRWV DAILY ETF"))
check("an inverse ETN is a wrapper",
      sc.is_leveraged_wrapper("T-REX -1X INVERSE NBIS DAILY ETF"))
check("a real company with 2X in the name is NOT a wrapper",
      not sc.is_leveraged_wrapper("2X GENOMICS INC"),
      "requires BOTH a leverage word and a fund word, precisely so this survives")
check("a plain ETF is not a leveraged wrapper",
      not sc.is_leveraged_wrapper("SPDR S&P 500 ETF TRUST"))
check("an ordinary company is not a wrapper",
      not sc.is_leveraged_wrapper("SUPER MICRO COMPUTER INC"))
check("a missing name is not a wrapper", not sc.is_leveraged_wrapper(None))

print("\nranking - a filer outranks a non-filer at any score")

ranked = sc.rank_companies_first([
    {"symbol": "CWVX", "score": 95, "supply_class": "not_a_filer"},
    {"symbol": "CRWV", "score": 60, "supply_class": "low_supply"},
    {"symbol": "NBIL", "score": 88, "supply_class": "not_a_filer"},
])
check("the 60-score company beats the 95-score ETP",
      ranked[0]["symbol"] == "CRWV", [r["symbol"] for r in ranked])
check("ETPs still appear, just below", len(ranked) == 3)
check("among ETPs, score still orders them",
      [r["symbol"] for r in ranked[1:]] == ["CWVX", "NBIL"])
check("an unscored company still outranks a scored ETP",
      sc.rank_companies_first([
          {"symbol": "ETP", "score": 99, "supply_class": "not_a_filer"},
          {"symbol": "CO", "score": None, "supply_class": "micro_supply"},
      ])[0]["symbol"] == "CO",
      "'not scored' must not be read as 'scored zero'")

print("\nclaim-before-expensive-work")

order = []
out = sc.claim_then_score(
    claim=lambda: order.append("claim"),
    expensive=lambda: order.append("llm") or "verdict",
    finalize=lambda v: (order.append("finalize"), v)[1])
check("the claim happens BEFORE the slow call", order == ["claim", "llm", "finalize"], str(order))
check("finalize receives the expensive result", out == "verdict")

failed = []
out2 = sc.claim_then_score(
    claim=lambda: (_ for _ in ()).throw(RuntimeError("db locked")),
    expensive=lambda: failed.append("ran anyway") or "x",
    finalize=lambda v: v,
    on_claim_fail=lambda e: failed.append(f"reported: {e}"))
check("a failed claim skips the expensive call entirely",
      out2 is None and not any(f == "ran anyway" for f in failed), str(failed))
check("a failed claim is reported, not swallowed",
      any(str(f).startswith("reported:") for f in failed))

print("\nscan log - rejections are the product")

log = sc.ScanLog(daily)
log.skip("AAPL", "under the price floor", price=1.20)
log.skip("ZZZZ", "leveraged wrapper")
log.alert("HQI", "real catalyst", score=82)
check("counts split alerted from skipped", log.counts == {"alerted": 1, "skipped": 2}, str(log.counts))
check("a rejection keeps its reason AND its numbers",
      log.rows[0]["reason"] == "under the price floor" and log.rows[0]["price"] == 1.20)
check("the log writes", log.write() and daily.scanlog_path.exists())

import json  # noqa: E402
written = json.loads(daily.scanlog_path.read_text())
check("the file names its lane, so two logs cannot be confused",
      written["lane"] == "daily")
check("every row carries a decision", all(r.get("decision") for r in written["rows"]))

print("\nplumbing")

check("discord with no webhook is a no-op, not a crash", sc.post_discord("", "hi") is False)


def _rejects(name):
    try:
        sc.Lane(name)
        return False
    except ValueError:
        return True


check("an unnamed lane is rejected outright", _rejects("") and _rejects("   "),
      "deriving paths from an empty name silently gives two lanes the same files")

check("lane names normalise, so 'Daily' and 'daily' are one lane not two",
      sc.Lane("Daily").lock_path == sc.Lane("daily").lock_path)

check("the live lane can keep the filename the UI already reads",
      sc.Lane("live", legacy_scanlog="scan-log.json").scanlog_path.name == "scan-log.json",
      "migration hatch: the Scoring tab must not go blank mid-restructure")

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
