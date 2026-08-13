#!/usr/bin/env python3
"""RETIRED 2026-08-13. The live lane now lives in `scan_live.py`.

This file was 531 lines and it was doing three jobs at once: the catalyst gates,
the auto-execute path, and a pile of scanner mechanics that had nothing to do with
catalysts. Splitting the desk into three lanes meant those mechanics would have
been copied three times and then fixed in only one of them, so they moved to
`scanner_core.py` instead:

    the cross-process scan lock      CWVX scored twice, 75 and 72
    claim-before-expensive-work      the LLM call sat between check and write
    candidate dedupe                 OFAL and SMCL, one second apart, in ONE scan
    the scan log                     rejections used to `continue` silently
    the leveraged-wrapper test       the board was two ideas counted seven times
    filer-first ranking              ...which is what put them there

Where everything went:

    scanner_core.py   lane-agnostic mechanics, one copy, shared by all three lanes
    scan_live.py      the long-lane gates - this file's `scan()`
    autotrade.py      the auto-execute path, lifted verbatim
    daily_play.py     Brain 3, the day lane (new, unrelated to this file)

Behaviour is unchanged and `test_scan_live_parity.py` proves it: both scanners are
driven with the same fake client and the alert lists are compared field by field,
including the duplicate feed, the wrapper, the collapsed-move case and the
liquidity floor. 531 lines were not deleted on the strength of looking right.

THIS SHIM EXISTS ONLY BECAUSE THINGS STILL CALL IT BY NAME - `api.py` shells out to
`radar.py` and imports it. Delete it once those point at `scan_live` directly.
"""
from __future__ import annotations

import sys

import config
import scan_live
from scanner_core import ScanBusy, ScanLock          # noqa: F401  (api.py imports these)

LANE = scan_live.LANE

# The old module-level names, kept pointing at their new homes so an existing
# `import radar; radar.scan(...)` behaves exactly as it did.
discipline_note = scan_live.discipline_note
_verify_pct = scan_live.verify_pct


def scan(client, conn, cfg=config):
    """The live lane. Signature preserved for callers that predate the split."""
    autotrade = None
    if getattr(cfg, "RADAR_AUTO_EXECUTE", False):
        try:
            import autotrade as _at
            autotrade = _at
        except Exception as e:
            print(f"[radar] auto-execute requested but unavailable ({str(e)[:80]}); "
                  f"scanning read-only")
    return scan_live.scan(client, conn, cfg, autotrade=autotrade)


def main():
    return scan_live.main()


if __name__ == "__main__":
    print("[radar] retired - delegating to scan_live.py", file=sys.stderr)
    main()
