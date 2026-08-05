# Demo kit

Record a product demo without recording your voice.

## The lazy path

1. **Generate the narration in your own voice** ([Voicebox](https://voicebox.sh/) running with a Kokoro
   profile):
   ```
   python demo/make-narration.py
   ```
   You get `demo/audio/01-cold-open.wav`, `02-the-account.wav`, and so on - one file
   per scene from `script.md`.

2. **Record the screen** with OBS (Display capture, 1920x1080, 30fps) while you click
   through the scenes in `script.md`.

3. **Line them up** in your editor: audio track = the WAVs, video track = the capture.
   Trim the gaps. Done.

## Options

```
python demo/make-narration.py --list        # see the scenes and word counts
python demo/make-narration.py --scene 3     # redo just scene 3 after a script edit
```

Edit `script.md` and rerun - the narration lines are the `>` quotes under each
`## N. Title` heading.

## Tips that make it look expensive

- **F11 fullscreen** the browser first. Tabs and bookmarks make it look like a
  screenshot, not a product.
- **Move the mouse slowly** and pause on what you are talking about. Fast cursors read
  as nervous.
- **Let the copilot actually work on camera.** The pause while it builds a panel is
  the most convincing moment in the whole video - do not cut it out.
- **Do the takes out of order.** Scene 7 needs a real panel build; do it once, well.
- **Numbers on screen should be paper numbers.** Check the header says PAPER before you
  hit record if you would rather not show a live balance.

## If you would rather have it driven for you

Ask your copilot (or Cowork) to drive the browser through the scene list while you
record - it can navigate tabs, type into the chat, and pace itself while OBS runs.
Screen capture still has to happen on your machine; nothing here can record your
display for you.
