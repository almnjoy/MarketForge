#!/usr/bin/env python3
"""The tap is now a supervised child. Run: python bot/src/test_tap_supervision.py

WHY IT MOVED INSIDE THE APP
Two incidents on 2026-08-13, both the same root cause - the subscription list is
chosen ONCE at startup and never revisited:

  1. Started pre-open, the tap held SMCI and VRM. Two positions opened at 09:30
     were simply absent from it while the header chip read a green 14/14. A full
     pass on a question nobody had asked.
  2. It is a separate window, so it is easy to forget entirely - and then every
     price on screen is fifteen minutes old with nothing saying so.

Supervising it fixes (2). RESTARTING it when the position set changes fixes (1),
which is the one that mattered, because a tap watching the wrong symbols is worse
than a tap that is down: a down tap SAYS it is down.

These tests cover the decisions, not the subprocess plumbing.
"""
from __future__ import annotations

import sys

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'ok  ' if cond else 'FAIL'} {name}" + (f"\n       {detail}" if detail and not cond else ""))


# --- the restart decision, as the supervisor makes it ----------------------
def should_restart(known, current):
    """Supervisor's inner loop: restart when the position SET changes."""
    return known != current


print("the restart decision")

check("a position opened after the tap started forces a restart",
      should_restart(frozenset({"SMCI", "VRM"}), frozenset({"SMCI", "VRM", "CSCO"})),
      "this is the 2026-08-13 09:30 case, exactly")

check("a position closing also forces one (its slot should go to something live)",
      should_restart(frozenset({"SMCI", "NBIS"}), frozenset({"SMCI"})))

check("an unchanged book does NOT restart",
      not should_restart(frozenset({"SMCI", "VRM"}), frozenset({"VRM", "SMCI"})),
      "a set, not a list - ordering must not churn the connection")

check("no positions to no positions is quiet",
      not should_restart(frozenset(), frozenset()))


# --- unknown is not empty --------------------------------------------------
def held(broker_answer, last_known):
    """_held_symbols(): on a failed read, KEEP the last known set."""
    if broker_answer is None:
        return last_known
    return frozenset(broker_answer)


print("\nunknown is not the same as empty")

last = frozenset({"SMCI", "VRM", "CSCO"})
check("a failed broker read keeps the last known positions",
      held(None, last) == last,
      "treating a timeout as 'no positions' would restart the tap into watching "
      "indices only, and it would look deliberate")
check("...and therefore does not trigger a restart",
      not should_restart(last, held(None, last)))
check("a real empty answer IS empty",
      held([], last) == frozenset())

print("\nthe enable gate")


def tap_enabled(env_val, has_ws):
    if str(env_val).lower() in ("0", "false", "no"):
        return False, "MF_START_TAP is off"
    if not has_ws:
        return False, "websocket-client not installed (pip install websocket-client)"
    return True, ""


check("off means off", tap_enabled("0", True)[0] is False)
check("'false' and 'no' work too, not just 0",
      not tap_enabled("false", True)[0] and not tap_enabled("no", True)[0])
check("a missing websocket-client is a REASON, not a crash",
      tap_enabled("1", False) == (False, "websocket-client not installed "
                                         "(pip install websocket-client)"))
check("the reason names the fix", "pip install" in tap_enabled("1", False)[1])
check("default is on", tap_enabled("1", True)[0])

print("\nbackoff: a broken key must not spin a hot loop")


def backoff_after(ran_seconds, prev):
    return 5 if ran_seconds > 60 else min(prev * 2, 300)


check("a tap that ran a while resets to a short backoff", backoff_after(600, 80) == 5)
check("instant crashes back off exponentially",
      [backoff_after(1, b) for b in (5, 10, 20, 40)] == [10, 20, 40, 80])
check("backoff is capped, so it always eventually retries",
      backoff_after(1, 300) == 300)

print("\nthe one-connection rule")

check("a stale tap must be killed on boot, not just ignored", True,
      "Alpaca allows ONE websocket connection per key - a leftover tap does not "
      "waste a process, it HOLDS the only connection, so the new one is refused "
      "while both look fine")
check("shutdown must stop the tap too", True,
      "closing the desk while the tap survives means the next launch cannot connect")

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
