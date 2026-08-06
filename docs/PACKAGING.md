# Packaging Market Forge as a Windows desktop app

Built 2026-08-06 on the `packaging` branch. `run-portable.bat` is unchanged;
`MarketForge.exe` is an addition, not a replacement.

## Build it

```bash
pip install pyinstaller pillow pywebview
python build.py
```

Output: `dist/MarketForge/` (double-clickable folder) and
`dist/MarketForge-win64.zip` (~21 MB). `build.py` refuses to ship if
`bot/.env` or any personal state file lands in the dist.

## The shape of the build (and why)

**One-folder, resources as sibling files.** PyInstaller one-file unpacks to
temp on every launch and makes a stdlib server feel slow. More importantly:
this product's whole thesis is that `panels/`, `chat-*.jsonl`, `memory.md` and
`bot/` are plain files any agent can read and write. So the exe carries only
the Python runtime (+ Flask, requests, pywebview) in `_internal/`, and
everything else ships as ordinary files next to it. `ROOT` is the exe's folder
when frozen (`_app_root()` in app.py); `resource_path()` honors `sys._MEIPASS`
for anything ever moved into the bundle proper, which today is nothing.

**The engine stays a subprocess; frozen spawns go through a dispatch shim.**
`app.py` spawns the engine as `[sys.executable, bot/run_bot.py]`, and the
engine spawns `radar.py` the same way (twice: scheduler + re-scan endpoint).
Under PyInstaller, `sys.executable` is MarketForge.exe - without intervention
every spawn relaunches the whole app forever. Two candidate fixes:

1. run the engine in-process - rejected: the supervisor's crash-restart, the
   orphan pid-kill on startup, and the timeout-killable radar scan are
   process-level safety properties that would silently vanish;
2. **an argv dispatch shim at the top of shell.py** - chosen: if the exe is
   launched with a path to `run_bot.py` or `radar.py` as argv[1], it emulates
   `python <script>` (runpy from the sibling files on disk) and exits. This
   fixes all three spawn sites with ZERO changes to `bot/src/api.py` (whose
   order path is untouchable), keeps one source of truth for engine code (the
   visible files, not a bundled copy), and the worst case - an unknown argv -
   exits with an error instead of looping, because a relaunch hits the
   busy-port guard anyway.

**Windowed-build plumbing.** A `--noconsole` exe has no std handles, so:
`shell.py` redirects the main process's stdout/stderr to `logs/marketforge.log`;
the supervisor gives the engine child `logs/engine.log` and a devnull stdin
(unset stdin surfaces as WinError 6 inside the child's own subprocess calls);
every console helper (`tasklist`, `taskkill`, the bridge's `claude` run) gets
`CREATE_NO_WINDOW` + explicit stdin, or each call flashes a console window.

**Shutdown is explicit, not atexit.** The dev test proved the supervisor's
atexit hook does NOT reliably run when the shell exits: the window closed clean
and left a live engine. `stop_engine()` in app.py now kills the engine
synchronously on window close (with `ENGINE_STOP` so the supervisor cannot
respawn mid-teardown); atexit and the startup stale-pid sweep remain as
backstops only.

## What the clean-folder test verified (frozen exe, no repo, no keys)

Copied `dist/MarketForge/` to a short temp path and ran `MarketForge.exe`:

- boots windowed, logs to `logs/marketforge.log`, `ROOT` = the exe folder
- **first run serves the setup wizard at `/`** (no `bot/.env` shipped)
- `/api/setup/validate-keys` validated the paper pair against Alpaca
  (ACTIVE account + feed detected) and `/api/setup/save` wrote `bot/.env`
- the parked engine started within seconds of the save; the child process
  image is **MarketForge.exe, zero python processes involved** (dispatch shim
  proven)
- `POST /api/bot/run/radar` ran a real frozen radar scan (6 live alerts) -
  that's the engine spawning a SECOND frozen child through `bot/src/api.py`'s
  unmodified `sys.executable` call
- `/api/stt` transcribed a WAV through Voicebox (the TTS->STT roundtrip
  returns the spoken sentence verbatim)
- second exe launch exits in ~1s after asking the first to focus
- `/api/shell/quit` (same path as clicking X): server down, engine dead,
  **zero MarketForge processes left**
- WebView2 initialized its profile (`webview-data/EBWebView`), i.e. the window
  really loaded the app

## Assumed / needs a human at the machine

- **The window looks right** (paint, sizing, dark background, no white flash).
  All programmatic evidence says it rendered; nobody has eyeballed it.
- **Mic permission inside WebView2.** MediaRecorder + `/api/stt` is the new
  voice path and the server side is proven, but the getUserMedia permission
  prompt behavior inside pywebview/WebView2 needs a real click and a real mic.
  Fallback if it misbehaves: voice input stays a Chrome feature (the desk in a
  browser is unaffected).
- **A machine with no Python installed.** This box has Python; the test proves
  no python process is ever spawned and no repo files are read outside the
  dist folder, but it is not literally a bare machine.
- **A machine with no WebView2 runtime** (pre-2021 Win10). pywebview would fail
  to open the window; the server would still run. Not handled - acceptable for
  the friend edition.

## Bugs this session found that were NOT packaging bugs

1. `requirements.txt` never listed `requests`, which the engine imports - a
   fresh `pip install -r requirements.txt` install was already broken.
2. **Windows has no timezone database**, so without the `tzdata` package
   `ZoneInfo("America/New_York")` throws and the scan scheduler silently runs
   on LOCAL time - scans fire an hour off for anyone outside Eastern. Both are
   in requirements.txt now and bundled in the exe.
3. **`.env.template` inline comments crashed the engine** on any fresh
   copy-the-template install (`int("100000   # ...")`). Both parsers now strip
   them.
4. `panels/00-welcome.html` was whitelisted in `.gitignore` but never
   committed - the public repo always shipped an empty first board. Authored
   and committed.

## Adversarial review (34-agent pass over the full diff)

Five review lenses, every finding independently verified against the code;
16 confirmed, 13 refuted. All 16 are fixed, the notable ones:

- **`/api/setup/save` could kill the engine inside the order path's fill-poll
  window** (up to ~18s after a BUY submits, before the disk-backed protect row
  exists) - the one window where an exit intent lives only in engine memory.
  The save now checks the broker for working BUY orders first and refuses with
  a retry message instead of killing at an arbitrary instant. The refusal path
  is code-reviewed, not executed (exercising it would mean placing an order,
  which this tree never does). Residual, accepted: a kill can still land mid
  `pending-protect.json` write inside the engine - that write is in the
  untouchable api.py; the quiesce guard shrinks the window to near-zero.
- `hookLinks` same-origin test was a string prefix - beaten by userinfo URLs
  (`http://127.0.0.1:8410@evil.com`) and port-extension. Now a real
  `new URL().origin` comparison.
- The voice engines leaked state: PTT never released the mic, hot-mic toggle
  raced its own awaits, a Voicebox outage latched `sttOk` and silently killed
  the SR fallback. All refcounted/guarded now.
- Both wizards (terminal one included, since 775d3a7) wrote a dead config key
  on the SIP path (`MIN_DOLLAR_VOLUME`; the engine reads
  `MIN_AVG_DOLLAR_VOLUME`) - SIP users kept the IEX liquidity floor silently.
- Re-running `/setup` demanded the Alpaca secret again (shown once, ever).
  Empty key fields now mean "keep the stored pair", validated server-side,
  mirroring the terminal wizard's defaults.
- The live-account labels understated things: live+auto now says REAL money in
  the confirm, and the Live radio no longer claims auto stays off
  unconditionally.

## Known trims for later (not blockers)

- `_internal/` carries cryptography/setuptools (~pulled via pythonnet's
  dependency graph); excludes could shave a few MB.
- Deep unzip paths can hit Windows MAX_PATH because of `_internal`'s long
  names (the build itself hit this in a 200-char temp dir). A README line
  telling users to unzip somewhere short, or an NSIS installer, fixes it.
- No code signing: SmartScreen will warn on first run. Tauri/MSI is the
  documented phase 3 if this ever needs to feel commercial.
