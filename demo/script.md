# Market Forge - demo script

~3 minutes, 9 scenes. Each scene has a **narration** line (`>`, fed to Voicebox by
`make-narration.py`) and an **on screen** list (what to do while it plays).

Record with OBS: Display or Window capture, 1920x1080, 30fps. Put the generated WAVs
on an audio track and perform the screen actions against them. If a scene runs short,
hold still - dead air on a clean screen edits fine.

**Before you roll:** browser at 100% zoom, F11 fullscreen (no tabs or bookmarks in
frame), Market Forge already open, Alpaca in a second window if you want the reveal,
notifications off.

---

## 1. Cold open

> This is Market Forge. An open source trading desk that runs on my own machine, with
> an A.I. copilot sitting in the seat next to me. Let me show you what a morning looks
> like.

On screen:
- Overview tab, full screen, ticker tape moving
- Slow beat on the logo, then let the stats breathe

## 2. The account

> Everything starts here. My account, my positions, my day. This is a real broker
> account through Alpaca, running in paper mode, which means real market data and fake
> money while I learn.

On screen:
- Point at equity / cash / day P/L with the cursor
- Hover a position row

## 3. The radar

> This is the catalyst radar. Every one of these is a stock that moved hard today, and
> the number you see is not the number the screener gave me. Market Forge recomputes
> every move against real price bars and the live tape, so stale prints and fake
> triple digit spikes get thrown out instead of shown.

On screen:
- Click CATALYST_RADAR
- Scroll slowly through the cards
- Pause on a high-score signal card

## 4. Signal versus noise

> Each card gets scored by a language model. Signal or noise, zero to one hundred, with
> the reason in one line. It reads the headlines, and it reads what Reddit is saying,
> and then it tells me which of these is a real catalyst and which is just a crowd.

On screen:
- Hover the score badge on an 85
- Click a headline link (opens the source) - come back
- Click RETAIL_RADAR, show the Reddit cards

## 5. The rules

> Here is the part I care about most. My discipline is in code, not in willpower. Fifty
> dollars a trade. Two entries a day. A hard exposure ceiling. A price floor so it never
> touches a penny spiker. And every single entry gets a trailing stop armed the moment
> it fills, so a winner can run and a fader gets cut.

On screen:
- Click RULES
- Scroll the pipeline paragraph, then the knob grid
- Click one knob to show it drafting a question to the copilot (do not send)

## 6. The copilot

> And this is the copilot. It is a coding agent that reads and writes plain files in
> the project folder, so it can actually do things instead of just talking. Watch what
> happens when I ask it for something.

On screen:
- Click COPILOT
- Type: `build me a board on today's top catalyst with a chart, the news links, and your read`
- Send. Let the working indicator and tool steps show

## 7. It builds while you talk

> That is it working. Those are its real steps, writing a panel file to disk. And on
> the workbench, the board just appears.

On screen:
- Wait for the reply, let the voice play a moment
- Click WORKBENCH and let the new panel render in frame
- Scroll the panel

## 8. The journal

> Everything that happens gets logged. Every scan, every board, every order. So
> tomorrow morning I can ask it to replay yesterday, and it reconstructs what fired,
> what I took, what I skipped, and one lesson about how I decided. That is worth more
> than the profit and loss number.

On screen:
- Click JOURNAL
- Scroll the timeline
- Hover the Replay button (do not click, or pre-record a replay for a cutaway)

## 9. Close

> It is open source, it is M.I.T. licensed, and it runs on your machine with your keys.
> One line to install. Market forge dot, sorry - made for me A.I. dot com, slash market
> forge. Links below.

On screen:
- Back to OVERVIEW, or the site's install block
- Optionally show the one-liner being pasted

---

## After recording

1. `python demo/make-narration.py` (Voicebox running) → `demo/audio/*.wav`
2. Drop the WAVs on an audio track in your editor, screen capture on video
3. Trim the gaps, add the logo as a lower-third or title card if you want
4. Export 1080p, and keep a vertical 9:16 cut for socials if you plan to post it
