# Live-narrated demo run

One take. OBS rolls, the page speaks each line through Voicebox (Dustin's Kokoro
profile) while the browser is driven to match, so narration is baked into the
recording and there is nothing to edit afterward.

Measured 2026-08-05: a 7.7s line renders in 564ms, so the next beat is always
ready before the current one finishes.

Raw capture lands in `demo/raw/`. Crop the browser chrome and taskbar off with:

```
ffmpeg -i demo/raw/<file>.mkv -vf "crop=2560:1228:0:170,scale=1920:-2" \
       -c:v libx264 -crf 20 -preset medium -c:a aac -b:a 160k demo/market-forge-demo.mp4
```

## The beats

| # | Screen | Line |
|---|---|---|
| 1 | Overview | This is Market Forge. It's a trading desk that runs on my own machine, with my own broker keys, and an AI copilot sitting in the seat next to me. |
| 2 | Overview, stat row | That's a real brokerage account. Real equity, real positions, real profit and loss. And the badge up top says PAPER, because that's how this ships. Nothing trades itself until you deliberately turn it on. |
| 3 | Catalyst radar | Four times a day it pulls the biggest movers in the market and re-checks every single percentage against real price bars and the live tape. |
| 4 | Catalyst radar, a dropped row | That verification is the whole point. A screener told me one of these was up four hundred percent. The actual tape said it was down eight. That one gets thrown out before I ever see it. |
| 5 | Copilot, prompt sent | Everything else gets read, scored, and explained. And when I want a board, I just ask for one. |
| 6 | Workbench, panels appear | It's building that live, right now, in plain files on my disk. Any coding agent can drive this thing. There's no plugin and no API key. |
| 7 | Rules | These are the gates. Score floor, price floor, size per trade, entries per day, total exposure, and a kill switch. They fail closed, and every entry gets an exit armed the moment it fills. |
| 8 | Journal | And all of it gets written down. Not just what I made or lost, but what I decided and why, so I can replay the day with the copilot and actually learn something. |
| 9 | Overview | Market Forge. Open source, runs on your machine, your keys never leave it. Link's in the description. |
