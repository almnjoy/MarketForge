# Plan: native shell, splash, and first-run setup

Goal: your friend downloads a folder, double-clicks one thing, sees the Market Forge
logo, gets walked through his API keys, and lands on the desk. No terminal, no browser
tab, no README.

Status: planned 2026-08-05. Nothing here is built yet.

## Decisions locked

**Shell: pywebview first, Tauri kept possible.** pywebview is ~15 lines, stays 100%
Python, and uses WebView2 which ships on every Win10/11 box. Tauri is what Voicebox
uses and gives a real MSI plus an auto-updater, but our backend is Python, so Tauri
would have to bundle it as a sidecar binary, and that bundling is the actual work. We
get 90% of the feel for 10% of the effort, and we keep the door open by making the
frontend shell-agnostic (below).

**Splash: every launch, ~2.5s, any click or keypress skips.** Configurable via
`config.json` `splash_ms` (0 disables). A 5s unskippable intro is a tax you pay on
every restart, and restarts happen a lot while testing.

**Setup wizard: a web page, not native.** Same HTML/JS as the rest of the desk. This
is the single most important call in this doc, because it means splash and first-run
work in a plain browser TODAY and the shell becomes an independent, low-risk step.

## The shell-agnostic rule

The frontend must never know whether it is in Chrome, pywebview, or Tauri. Practical
consequences:

- No pywebview JS API calls from page code. Anything the shell must do (close, minimize,
  open an external link, pick a folder) goes through a **`/api/shell/*`** endpoint that
  is a no-op when running in a browser.
- Feature-detect, never shell-detect. `GET /api/shell` returns
  `{shell: "browser"|"pywebview"|"tauri", can_minimize, can_tray, ...}` and the UI
  hides what is unavailable.
- External links (Alpaca signup, docs) must open in the SYSTEM browser, not inside the
  app window. In a webview an unhandled `target=_blank` either does nothing or traps the
  user in a chromeless window with no back button. Route every outbound link through
  `POST /api/shell/open` with a browser fallback of `window.open`.

## Phase 0 - splash + first-run, browser only (no native code)

Ship this first. It is fully useful on its own and de-risks everything after it.

### 0a. Splash

- `static/splash.html` fragment injected as a full-viewport overlay by `app.js` on load,
  removed on timeout / click / keypress / `Escape`.
- Art: reuse the existing mark. `static/logo.svg` is his original; `demo/logo-on-dark.png`
  is the same mark already keyed to solid white + orange for dark backgrounds, which is
  what the splash wants.
- Animation, in CSS only, no library: mark fades up and scales ~1.04, the orange forge
  glow pulses out from behind it (reuse the `drop-shadow(0 0 40px rgba(255,106,0,.45))`
  already on the top-bar logo), wordmark fades in, whole thing cross-fades to the desk.
- Respect `prefers-reduced-motion` by cutting straight to a static frame.
- **Gotcha:** do not gate the app's data loading behind the splash. Fetch Overview data
  underneath it so the desk is already populated when the splash lifts, otherwise you
  traded a 2.5s logo for a 2.5s logo plus a loading spinner.

### 0b. First-run detection

First run = `bot/.env` missing, OR present but still holding template placeholders, OR
`config.json` lacks `setup_done: true`. Checked server-side and exposed at
`GET /api/setup/state` so the frontend never guesses.

### 0c. The wizard

New tab/route, shown instead of the desk when first-run is true. Steps:

1. **Welcome.** What this is, one paragraph. States plainly: paper mode, nothing trades
   itself, keys stay on this machine.
2. **Alpaca keys.** Two fields. Beside them, a "Where do I get these?" panel with the
   literal click path (alpaca.markets, sign up free, Paper dashboard, API keys, the
   secret shows once) and a button that opens it in the system browser.
   **Validate live** against `/v2/account` before letting him continue, with the real
   error surfaced. Wrong-keys-typed-in is the single most likely failure and it must not
   surface 20 minutes later as an empty dashboard.
3. **Feed detect.** Automatic, no question asked. Probe
   `/v2/stocks/AAPL/trades/latest?feed=sip` (403 = free IEX). Show the result as a fact.
   **Do not probe the bars endpoint: it returns 200 on free accounts and lies.**
4. **Mode.** Research only / manual trading / automatic. Research is preselected.
   Automatic requires a second explicit confirm.
5. **Optional extras**, each with a live green/grey status dot, each skippable:
   Ollama (scoring), Voicebox (natural voice), Discord webhook (phone alerts),
   AI copilot (is `claude` on PATH?).
6. **Connect your copilot.** Shows the absolute folder path with a copy button, plus the
   one-liner, so he can point Claude Code or Cursor at it. This is the step that answers
   "where do I put the folder so Claude can pull it".
7. **Done.** Writes `bot/.env`, sets `setup_done`, drops him on the desk.

Re-runnable later from the RULES tab, prefilled with current values.

### 0d. Do not fork the setup logic

`setup.py` already validates keys, detects the feed, and writes `bot/.env` preserving
template order and comments. **Extract that into `setup_core.py`** and have BOTH the
terminal wizard and the new `/api/setup/*` endpoints call it. Two copies of this logic
will drift, and the drift will be silent and about money.

New endpoints: `GET /api/setup/state`, `POST /api/setup/validate-keys`,
`GET /api/setup/detect-feed`, `GET /api/setup/probe-extras`, `POST /api/setup/save`.

## Phase 1 - pywebview shell

`shell.py` at the repo root. Starts the existing server on a thread, opens one window at
`http://127.0.0.1:<port>`, exits cleanly on window close (must also kill the embedded bot
engine, which today is handled by the `atexit` hook in `app.py`).

- Window: 1600x1000, min 1100x700, dark background colour set so there is no white flash
  before first paint.
- `/api/shell` starts reporting `pywebview`.
- Single-instance guard: if :8410 is already bound, focus the existing window instead of
  starting a second server. Trivially hit by double-clicking the icon twice.
- System tray: show/hide, Re-scan, Quit.

**Known risk:** WebView2 does NOT support the Web Speech API, so the hot mic dies the
moment we leave Chrome. See Phase 1b. Everything else, including Voicebox voice output,
is unaffected because that already goes through our own `/api/tts`.

### Phase 1b - move voice input off Chrome (unblocks the whole wrapper)

Voicebox exposes `POST /transcribe` (multipart: `file`, optional `language`, `model`) and
his install already has `whisper-base` and `whisper-turbo` downloaded. So:

- Record mic audio with `MediaRecorder` (works everywhere, unlike `SpeechRecognition`).
- POST the blob to a new `/api/stt` that relays to Voicebox `/transcribe`.
- Fall back to the existing browser `SpeechRecognition` when Voicebox is absent AND we
  are running in Chrome.

Better than what we have today even in the browser: fully offline, no Google dependency,
better accuracy, and it works identically in every shell. Do this BEFORE Phase 1 lands
so the wrapper never ships a regression.

## Phase 2 - packaging

- PyInstaller one-folder (not one-file: one-file unpacks to temp on every launch and
  makes a stdlib server feel slow).
- Ship `static/`, `panels/00-welcome.html`, `bot/`, `RULES.md`, `CLAUDE.md`,
  `bot/.env.template`. Never ship `bot/.env`.
- Icon from the existing mark.
- Output `MarketForge.exe` plus a zip. Optional NSIS installer later.
- **Gotcha:** PyInstaller and a stdlib `ThreadingHTTPServer` are fine together, but
  anything resolved relative to `__file__` breaks under a frozen build. Audit every path
  in `app.py` and switch to a `resource_path()` helper that honours `sys._MEIPASS`.
- Keep `run-portable.bat` working unchanged. The exe is an addition, not a replacement.

## Phase 3 - Tauri, only if wanted

Justified only by a real MSI installer, auto-update, and code signing. Work is bundling
Python as a Tauri sidecar. If Phase 0 and 1 respected the shell-agnostic rule, this
touches the launcher and nothing else.

## Build order

1. `setup_core.py` extraction + `/api/setup/*`
2. Wizard UI + first-run gate
3. Splash
4. `/api/stt` via Voicebox `/transcribe`, hot mic switched over
5. `shell.py` + `/api/shell` + tray + single-instance
6. PyInstaller spec, icon, zip
7. Test the whole thing on a machine that has never run it

Step 7 is the real test and the only one that matters: a box with no Python, no keys, no
repo. Everything before it is a guess about what that box does.
