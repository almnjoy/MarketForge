# Prompts that work

Copy these into the COPILOT tab. They are written to trigger the right layout and
the panel kit, because "make me a page about X" tends to produce a cramped tile.

## The full-page review (the one you asked for)

```
Build me a FULL PAGE review of <SYMBOL>. One panel, size: page, nothing else on
the board - clear the board first.

Use the panel kit so it is interactive, not a screenshot:
  <link rel="stylesheet" href="/static/panel.css">
  <script src="/static/panel-kit.js"></script>
  MF.quote('#q','<SYMBOL>')   // chart + range switcher + full stat grid

Structure it like the Overview tab, top to bottom:
1. Header: symbol, company, today's move, and your one-line call.
2. MF.quote for the interactive chart (1M/3M/6M/1Y) and the stat grid.
3. Why it moved: the catalyst, with source links.
4. The bull case and the bear case, side by side (.row.two).
5. What would change your mind, as a short list.
6. Levels that matter and the exit I would arm if I took it.

Read live data, do not invent numbers. Cite what you pulled.
```

## Morning brief

```
Clear the board and build my morning brief as ONE panel, size: page. Market state,
my account, top radar signals with your read on each, reddit overlap, and the plan
for today. Use MF.chart for SPY and QQQ. End with the three things I should
actually watch.
```

## Compare a watchlist

```
Build one panel, size: page, comparing AMD, NVDA and SPY. An MF.quote block for
each in a .row, then a table of the numbers side by side, then your read on
relative strength over the last month.
```

## Replay yesterday

```
Read journal.jsonl for yesterday and build a size: page panel reconstructing the
session: what the radar fired, what I took, what I passed on, how it ended, and
one lesson about HOW I decided rather than what I made.
```

## Tuning a rule

```
Explain the <knob> on the RULES tab: what it does, what happens if I raise it, what
happens if I lower it, and what YOU would set it to given memory.md. Do not change
anything, just tell me the tradeoff and the apply path.
```

## Why prompts fail

- **You get a cramped tile** when you don't say `size: page`. Say it explicitly.
- **You get a static screenshot** when you don't name the panel kit. Name it.
- **You get five small panels** when you don't say "one panel" and "clear the board".
  The layout rule in CLAUDE.md is one THEME equals one BOARD.
- **You get invented numbers** when you don't say "read live data, do not invent".
