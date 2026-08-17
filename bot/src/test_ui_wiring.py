#!/usr/bin/env python3
"""Structural checks on the UI wiring. Run: python bot/src/test_ui_wiring.py

THE BUG THIS EXISTS FOR (2026-08-14): the lane sub-nav was given
class="subtabs" so it would inherit the pill styling. A separate loop binds every
`nav.subtabs` button with `b.onclick = ...` - ASSIGNMENT, not addEventListener -
and it runs later in the file, so it silently replaced the lane handler with one
that highlights the button and toggles a subview id that does not exist.

The tab lit up. Nothing moved. No console error, because nothing threw.

That is not catchable by reading either handler on its own; it only exists in the
relationship between them. So these tests read the actual files and assert the
relationships. They are cheap and they run without a browser.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
JS = (ROOT / "static" / "app.js").read_text(encoding="utf-8", errors="replace")
HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8", errors="replace")
CSS = (ROOT / "static" / "style.css").read_text(encoding="utf-8", errors="replace")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'ok  ' if cond else 'FAIL'} {name}" + (f"\n       {detail}" if detail and not cond else ""))


print("the handler clobber that made Radar and Settings dead")

check("the generic subtabs handler EXCLUDES the lane bar",
      "nav.subtabs:not(#lanetabs)" in JS,
      "without :not(#lanetabs) the later `b.onclick =` assignment replaces "
      "showLane and the lane tabs highlight without switching anything")

check("the lane bar still gets its own handler",
      re.search(r"#lanetabs button.*?onclick.*?showLane", JS, re.S) is not None)

check("lane buttons in the main nav are bound to showLane, not the tab handler",
      "#tabs button[data-lane]" in JS and "#tabs button[data-tab]" in JS,
      "one selector for both would make every lane click also run the tab handler")

print("\nevery lane route resolves to a section that exists")

view_ids = set(re.findall(r'id="(view-[\w-]+)"', HTML))
lane_block = re.search(r"const LANES = \{(.+?)\n\};", JS, re.S)
check("the LANES table is present", lane_block is not None)
routed = set(re.findall(r"'(view-[\w-]+)'", lane_block.group(1) if lane_block else ""))
check("every routed view id exists in the html",
      routed <= view_ids, f"missing: {sorted(routed - view_ids)}")
check("all three lanes are routed", len(re.findall(r"^\s{2}\w+: \{", lane_block.group(1),
                                                   re.M)) == 3 if lane_block else False)

print("\nevery sub-tab button has somewhere to go")

subs = set(re.findall(r'data-lanesub="(\w+)"', HTML))
# Sub-tabs split into two kinds since 2026-08-16:
#   universal - every lane must route it, or the tab highlights and shows the
#               wrong pane
#   lane-only - declared once, routed by SOME lanes. Consensus is Live-only
#               because the Investment Council workspace is a swing/portfolio
#               tool and means nothing on an intraday lane.
# The original concern is unchanged and still enforced below: a button that a
# lane cannot route must never be CLICKABLE on that lane.
UNIVERSAL = {"overview", "radar", "settings"}
LANE_ONLY = {"consensus"}
check("the declared sub-tabs are the expected set",
      subs == UNIVERSAL | LANE_ONLY, str(subs))
for s in sorted(subs & UNIVERSAL):
    check(f"'{s}' is routed for every lane",
          lane_block and lane_block.group(1).count(f"{s}:") >= 3,
          "a lane missing a route silently falls back to overview, so the tab "
          "highlights and shows the wrong pane")
for s in sorted(subs & LANE_ONLY):
    check(f"'{s}' is routed by at least one lane",
          lane_block and lane_block.group(1).count(f"{s}:") >= 1,
          "declared in the nav but no lane can show it")

# The safety net that makes lane-only tabs legal at all.
check("showLane hides sub-tabs the active lane cannot route",
      re.search(r"cfg\.views\[x\.dataset\.lanesub\]", JS) is not None
      and re.search(r"x\.style\.display\s*=", JS) is not None,
      "without this a lane-only tab is clickable everywhere and falls back to "
      "overview while staying highlighted - the exact bug the universal rule "
      "was written to prevent")
check("a hidden sub-tab cannot also be marked selected",
      re.search(r"x\.classList\.toggle\('on',\s*has\s*&&", JS) is not None,
      "hiding the button but leaving .on set means the highlight survives on a "
      "lane that cannot render the pane")

print("\nlane buttons exist and exactly one starts on")

lanes_html = re.findall(r'data-lane="(\w+)"', HTML)
check("three lane buttons in the nav", len(lanes_html) == 3, str(lanes_html))
check("exactly one lane starts selected",
      len(re.findall(r'data-lane="\w+" class="on"', HTML)) == 1)
check("exactly one sub-tab starts selected",
      len(re.findall(r'data-lanesub="\w+" class="on"', HTML)) == 1)

print("\nthe sub-nav hides itself over non-lane tabs")

check("switching to a plain tab hides the lane bar",
      re.search(r"data-tab.*?lanetabs.*?display\s*=\s*'none'", JS, re.S) is not None,
      "leaving it visible over ADMIN implies ADMIN has Overview/Radar/Settings")

print("\nthe command palette cannot point at a lane that is not a tab")

check("tabTo routes lane names through showLane",
      "LANE_ROUTES" in JS and "showLane(r[0], r[1])" in JS,
      "querySelector('[data-tab=overview]') returns null now, and the ?. swallows "
      "it - a palette entry that looks live and does nothing")

print("\nstyling")

check("the lane bar is aligned with the content, not the window edge",
      "#lanetabs {" in CSS and "max-width" in CSS.split("#lanetabs {")[1][:200],
      "it lives outside <main>, so it needs main's padding or it hugs the edge")

print("\nendpoint discovery, so the copilot stops guessing paths")

check("/api/endpoints exists",
      '"/api/endpoints"' in (ROOT / "app.py").read_text(encoding="utf-8", errors="replace"))
check("it lists the paper endpoints the copilot claimed did not exist",
      all(x in (ROOT / "app.py").read_text(encoding="utf-8", errors="replace")
          for x in ("paper/overview", "paper/status", "paper/unprotected")))

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
