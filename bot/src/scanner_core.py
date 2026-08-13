#!/usr/bin/env python3
"""Shared scanner mechanics. ONE copy, used by all three lanes.

WHY THIS FILE EXISTS
--------------------
`radar.py` was 531 lines, and most of the hard-won ones were not about catalysts at
all - they were about not scoring the same symbol twice, not letting rejections
disappear, and not ranking a 2x ETP above the company whose news it was tracking.

Splitting into three lane scanners by copying radar.py three times would have made
three copies of those fixes, and they would not have been fixed in lockstep. Every
one of them below was a real incident:

  ScanLock       CWVX scored twice in one board (75 and 72) - two overlapping scans
  claim_alert    the other half of that: the LLM call sat between check and write
  dedupe         OFAL and SMCL scored twice one second apart INSIDE a single scan,
                 because the movers feed handed the loop the same symbol twice
  ScanLog        rejections used to `continue` silently, so a floor could not be
                 tuned because nobody could see what it rejected
  rank_          the entire top board was leveraged wrappers of two companies
  is_leveraged_  ...which is what put them there

THE THING A NAIVE EXTRACTION GETS WRONG: the lock and the log were module-level
paths. Three scanners sharing one lock file would BLOCK EACH OTHER, which is the
exact opposite of the separation the restructure is for. Every path here is
per-lane, and there is a test that two lanes do not collide.

Lane-specific gates do NOT belong here. This file knows nothing about catalysts,
pullbacks, or declining moving averages.
"""
from __future__ import annotations

import json
import os
import time
import urllib.request

import config


class ScanBusy(RuntimeError):
    """Raised when a scan of the SAME lane is already running."""


class Lane:
    """Identity + file locations for one scanner. Everything is namespaced.

    `legacy_scanlog` exists only so the live lane can keep writing the filename the
    UI already reads while the restructure is in flight. New lanes must not use it.
    """

    def __init__(self, name, legacy_scanlog=None):
        self.name = str(name).strip().lower()
        if not self.name:
            raise ValueError("a lane needs a name; the paths are derived from it")
        self._legacy_scanlog = legacy_scanlog

    @property
    def lock_path(self):
        return config.DATA_DIR / f"{self.name}-scan.lock"

    @property
    def scanlog_path(self):
        if self._legacy_scanlog:
            return config.DATA_DIR / self._legacy_scanlog
        return config.DATA_DIR / f"{self.name}-scan-log.json"

    def __repr__(self):
        return f"Lane({self.name!r})"


LOCK_STALE_S = 300          # a scan that has run 5 minutes is dead, not busy


class ScanLock:
    """One scan at a time PER LANE, across processes.

    /api/run/<lane> shells out to a fresh python process, so a threading.Lock
    cannot see it. The lock has to be on disk.

    It REFUSES rather than waits. A second scan arriving while one is running is a
    duplicate, not a queue - waiting for it would just run it late with staler data.
    """

    def __init__(self, lane, stale_s=LOCK_STALE_S):
        self.lane = lane if isinstance(lane, Lane) else Lane(lane)
        self.stale_s = stale_s

    def __enter__(self):
        p = self.lane.lock_path
        try:
            if p.exists():
                age = time.time() - p.stat().st_mtime
                if age < self.stale_s:
                    raise ScanBusy(f"another {self.lane.name} scan started "
                                   f"{age:.0f}s ago ({p.name}); refusing to run a second")
                print(f"[{self.lane.name}] clearing a stale lock ({age:.0f}s old)")
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(str(os.getpid()), encoding="utf-8")
        except ScanBusy:
            raise
        except Exception as e:
            # Fail OPEN on a broken lock: a scanner that cannot write a lock file
            # should still scan. Says so out loud rather than pretending it locked.
            print(f"[{self.lane.name}] lock unavailable ({e}); continuing unlocked")
        return self

    def __exit__(self, *a):
        try:
            self.lane.lock_path.unlink(missing_ok=True)
        except Exception:
            pass
        return False


class ScanLog:
    """Every DECISION, not just the ones that passed.

    Rejections used to `continue` silently. You cannot tune a floor you cannot see
    rejecting things, and the $3M-on-a-20-day-average finding was invisible until
    this existed. Renders as the lane's Scoring tab.
    """

    def __init__(self, lane):
        self.lane = lane if isinstance(lane, Lane) else Lane(lane)
        self.rows = []
        self.started = time.strftime("%Y-%m-%dT%H:%M:%S")

    def row(self, symbol, decision, reason, **extra):
        self.rows.append({"symbol": symbol, "decision": decision, "reason": reason,
                          "at": time.strftime("%H:%M:%S"), **extra})

    def skip(self, symbol, reason, **extra):
        self.row(symbol, "skipped", reason, **extra)

    def alert(self, symbol, reason, **extra):
        self.row(symbol, "alerted", reason, **extra)

    @property
    def counts(self):
        a = sum(1 for r in self.rows if r["decision"] == "alerted")
        return {"alerted": a, "skipped": len(self.rows) - a}

    def write(self):
        try:
            p = self.lane.scanlog_path
            p.parent.mkdir(parents=True, exist_ok=True)
            payload = {"lane": self.lane.name, "started": self.started,
                       "finished": time.strftime("%Y-%m-%dT%H:%M:%S"),
                       **self.counts, "rows": self.rows}
            p.write_text(json.dumps(payload, indent=1), encoding="utf-8")
            return True
        except Exception as e:
            print(f"[{self.lane.name}] scan log write failed: {e}")
            return False


def dedupe_candidates(rows, key="symbol"):
    """Keep the FIRST occurrence of each symbol. Returns (kept, dropped_count).

    OFAL and SMCL were each scored twice at 14:00:15 and 14:00:16 - one second
    apart, inside a SINGLE scan, so neither the lock nor the claim could help:
    both guards live inside the loop, and the loop was handed the same symbol
    twice. First occurrence wins because these feeds arrive sorted by rank.
    """
    seen, kept = set(), []
    for r in rows or []:
        s = str((r or {}).get(key) or "").upper().strip()
        if not s or s in seen:
            continue
        seen.add(s)
        kept.append(r)
    return kept, len(rows or []) - len(kept)


LEVERAGE_WORDS = ("2X", "3X", "-1X", "LEVERAGED", "INVERSE", "BULL ", "BEAR ", " ETN")
FUND_WORDS = ("ETF", "ETN", "DAILY")


def is_leveraged_wrapper(name):
    """True for a leveraged/inverse product tracking someone else's news.

    Requires BOTH a leverage word AND a fund word, so a real company with '2X' in
    its name does not vanish. CWVX/CRWG/CRWU wrap CRWV; NBIL/NBIG/NBEX wrap NBIS -
    the radar was not finding seven ideas, it was finding two and counting them
    seven times, scoring the wrapper HIGHER because a 2x product moves twice as far
    on the same news.
    """
    nm = str(name or "").upper()
    if not nm:
        return False
    return any(w in nm for w in LEVERAGE_WORDS) and any(w in nm for w in FUND_WORDS)


def rank_companies_first(alerts, score_key="score", supply_key="supply_class"):
    """Sort by score, but a real filer always outranks a non-filer at any score.

    Sorting on score alone puts 2x single-stock ETPs on top by construction: they
    move twice as far on the same news. They still appear - a filer just wins.
    Absence from the SEC ticker map ('not_a_filer') is the signal, and it is a
    FINDING rather than missing data.
    """
    def key(a):
        is_company = (a or {}).get(supply_key) not in (None, "not_a_filer")
        sc = (a or {}).get(score_key)
        return (1 if is_company else 0, sc if sc is not None else -1)
    return sorted(alerts or [], key=key, reverse=True)


def claim_then_score(claim, expensive, finalize, on_claim_fail=None):
    """Claim the symbol BEFORE the slow part, then fill the claimed row in.

    The shape of nearly every duplicate this desk has produced is check-then-act
    with expensive work in the gap: `alert_exists_today()` at the top of the loop,
    `record_alert()` at the bottom, an LLM call taking seconds in between, and two
    overlapping scans both passing the check and both writing.

    The lock makes overlap impossible. This makes it HARMLESS if it happens anyway
    (a cron and a manual run racing on the same second), which matters because the
    lock fails open.

    Returns finalize(result), or None if the claim failed.
    """
    try:
        claim()
    except Exception as e:
        if on_claim_fail:
            on_claim_fail(e)
        return None
    return finalize(expensive())


def post_discord(webhook, content):
    """Best effort, never raises, never blocks a scan on a chat outage."""
    if not webhook:
        return False
    try:
        req = urllib.request.Request(
            webhook, data=json.dumps({"content": str(content)[:1900]}).encode(),
            headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10)
        return True
    except Exception:
        return False
