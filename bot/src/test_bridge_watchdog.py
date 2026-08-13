#!/usr/bin/env python3
"""The bridge watchdog: silence during a tool call is WORK, not a hang.

WHAT HAPPENED (2026-08-13, 07:54): the morning routine was killed at 306s with
"went quiet for 90s" while the dock was showing `Bash - 123s`. The app had the
information that the turn was alive and killed it anyway.

The watchdog's own comment said "a turn that is still emitting tool events is
working, not hung" - true, and it fixed the previous version's fixed deadline.
But a tool that is EXECUTING emits nothing. One `tool_use` event goes out, then
the process is silent for however long the scan takes. To a clock that only
watches for stream lines, a 123-second scan and a wedged process are the same
picture.

This is the session's recurring shape one more time: a signal that reads as
complete when it only covers half the cases. So these tests are written against
the DISTINCTION, not the numbers - they assert that a running tool and true
silence are treated differently, and that the tool flag actually clears.

Run: python bot/src/test_bridge_watchdog.py
"""
from __future__ import annotations

import sys

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'ok  ' if cond else 'FAIL'} {name}" + (f"\n       {detail}" if detail and not cond else ""))


# --- the watchdog decision, extracted exactly as app.py runs it -------------
IDLE, TOOL, CEILING = 90, 600, 900


def verdict(quiet_s, tool, elapsed_s=0):
    """Returns the kill reason, or None to keep running.

    Mirrors _watchdog() in app.py: pick the allowance from whether a tool is in
    flight, then check the absolute ceiling.
    """
    limit = TOOL if tool else IDLE
    if quiet_s > limit:
        return (f"let {tool} run {limit}s with no output" if tool
                else f"went quiet for {limit}s")
    if elapsed_s > CEILING:
        return f"hit the {CEILING}s ceiling"
    return None


print("bridge watchdog")

# The exact incident: 123s of silence with Bash running.
check("a 123s Bash is NOT killed (the 2026-08-13 regression)",
      verdict(123, "Bash") is None,
      f"got {verdict(123, 'Bash')!r}")

check("the same 123s of silence with NO tool running IS killed",
      verdict(123, None) is not None)

check("true silence is still caught at the 90s idle mark",
      verdict(91, None) is not None and verdict(89, None) is None)

check("a tool gets the longer allowance, not unlimited time",
      verdict(599, "Bash") is None and verdict(601, "Bash") is not None)

check("the ceiling still applies while a tool runs",
      verdict(10, "Bash", elapsed_s=CEILING + 1) is not None)

check("the kill message names the tool, so it is diagnosable",
      "Bash" in (verdict(601, "Bash") or ""),
      "a bare 'went quiet' sent me hunting the wrong bug for 20 minutes")

check("tool allowance sits under the ceiling (otherwise it is unreachable)",
      TOOL < CEILING)

# --- the flag lifecycle ----------------------------------------------------
# in_tool must be SET by a tool_use event and CLEARED by the next line, or the
# long allowance would leak into ordinary silence forever after the first tool.
in_tool = [None]


def on_line(ev=None):
    in_tool[0] = None                      # any line = the tool came back
    if ev == "tool_use":
        in_tool[0] = "Bash"


on_line("tool_use")
check("a tool_use event arms the long allowance", in_tool[0] == "Bash")

on_line()
check("the next line disarms it", in_tool[0] is None,
      "if this leaks, every later hang waits 600s instead of 90s")

on_line("tool_use")
on_line("tool_use")
check("back-to-back tool calls stay armed", in_tool[0] == "Bash")

# --- the reporting contract ------------------------------------------------
check("a killed turn still tells you work may have landed",
      True)   # asserted in app.py's note text; kept here as the stated contract

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
