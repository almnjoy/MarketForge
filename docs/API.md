# Market Forge API

Everything the desk exposes, on `http://localhost:8410`. All local, no auth, no
internet round trip. This is the contract the copilot uses, and it is the same
contract your own scripts or another agent can use.

Two layers:

- **`/api/...`** the dashboard's own endpoints (files, chat, panels, TradingView)
- **`/api/bot/...`** a proxy through to the trading engine. GETs are allowlisted
  in `BOT_GET`; writes have explicit handlers

---

## Account and market data

| | |
|---|---|
| `GET /api/bot/status` | account: env, equity, cash, day P/L, drawdown, kill switch |
| `GET /api/bot/positions` | open positions with entry, price, market value, unrealized P/L |
| `GET /api/bot/orders` | **the LOCAL sqlite ledger.** Does NOT know about broker-side stops |
| `GET /api/bot/broker/orders` | **live from the broker.** `?status=open\|closed\|all`. Use this to answer "is there a stop on it" |
| `GET /api/bot/equity` | equity curve points |
| `GET /api/bot/bars?symbol=&limit=` | daily OHLCV. **Returns DOLLARS** |
| `GET /api/bot/news?symbol=&limit=` | headlines |
| `GET /api/bot/spark?symbols=A,B` | tiny series for the ticker tape |
| `GET /api/bot/config` | live risk gates. **Dollars under short keys**: `notional`, `max_exposure`, `min_price`, `trail_pct` |

> **Units trap:** `/api/bot/bars` is in **dollars**; radar rows are in **cents**.
> This has already caused a 100x display bug. Check before you divide.

## Signals

| | |
|---|---|
| `GET /api/bot/radar` | catalyst rows: symbol, verified %, score, catalyst, headline |
| `GET /api/bot/reddit` | retail buzz, round-robin across the configured subs |
| `POST /api/bot/run/radar` | force a scan. Takes 15-40s. Returns `{ok, stdout}` |
| `GET /api/bot/log` | recent engine log lines |

## Trading

| | |
|---|---|
| `POST /api/bot/order` | place an order. `{symbol, side, notional\|qty, exit_trail_pct}` |
| `POST /api/bot/protect` | **arm a trailing stop on an EXISTING position.** `{symbol, trail_pct, qty?}` |
| `GET /api/bot/unprotected` | positions with no working exit. Drives the red banner |

**The exit guarantee.** `exit_trail_pct` on a buy arms a GTC trailing stop at fill.
If the fill is slow, the order goes on a disk-backed queue and a watcher keeps
trying for 6 hours; a sweep every 30s flags anything still naked. `/api/protect`
exists because there was previously **no way** to attach an exit to a position that
already existed. Read `CLAUDE.md` before touching any of this.

## TradingView

Requires `run-tradingview.bat` (Chrome with the DevTools protocol on :9222).

| | |
|---|---|
| `GET /api/tv/status` | is the debug browser up, is TradingView open |
| `POST /api/tv/open` | `{symbol, interval}`. Intervals: `1m 5m 15m 30m 1h 4h 1d 1w` or raw TV codes |
| `POST /api/tv/shot` | capture the chart to `tv-shots/`. Returns `web_path` |
| `GET /api/shot?name=` | serve a capture back (embed it in a panel) |

The screenshot is the point: it turns "what does this chart look like" into a file
an image-capable agent can read.

## Copilot and panels

| | |
|---|---|
| `GET /api/chat` | conversation. `?day=YYYY-MM-DD` for one day; also returns the `days` index |
| `POST /api/chat/send` | `{text}`. Triggers a bridge turn |
| `GET /api/bridge` · `POST /api/bridge/stop` | bridge state / kill the in-flight turn |
| `GET /api/panels` · `GET /api/panel?name=` | Workbench contents |
| `POST /api/panels/delete` | remove a panel |
| `POST /api/workbench/save\|load\|clear` · `GET /api/workbench/saved` | boards |
| `GET /api/memory` · `GET /api/rules` | standing orders, trading plan |
| `GET /api/journal` · `POST /api/journal/add` | the decision log |
| `GET /api/watch` | one cheap poll target: has anything changed |

## Voice

| | |
|---|---|
| `POST /api/tts` | `{text, profile_id?}` returns WAV |
| `GET /api/tts/health` · `GET /api/tts/profiles` | which voice, and all of them |
| `POST /api/stt` | raw audio body (the frontend sends PCM WAV) -> `{ok, text, duration}` via Voicebox `/transcribe`. `?lang=en` |
| `GET /api/stt/health` | is voice INPUT available (Voicebox reachable) |

**Presets require an `engine` field; cloned voices reject one.** The relay
negotiates per profile and caches which shape worked. Set the default with
`voicebox_profile` in `config.json` - unconfigured, the picker prefers a fast
kokoro preset (a clone's engine can grind the GPU for minutes, which is how
the desk once went silently mute).

**Send WAV to `/api/stt`, not webm.** Voicebox 500s on containers it cannot
decode; the frontend taps raw PCM and encodes WAV in the page for exactly
this reason.

## Setup and shell (added with the desktop build)

| | |
|---|---|
| `GET /api/setup/state` | first-run truth, server-side: has keys, env, mode, per-env stored-pair flags |
| `POST /api/setup/validate-keys` | `{env, key_id, secret}` -> live Alpaca check + feed detection in one round trip. Empty pair = validate the STORED pair |
| `GET /api/setup/probe-extras` | green/grey dots: voicebox, claude on PATH |
| `POST /api/setup/save` | write `bot/.env` + restart the engine. REFUSES (409) while a buy is working at the broker - see the exit guarantee |
| `GET /api/shell` | `{shell: "browser"\|"pywebview", can_focus}` - feature-detect, never shell-detect |
| `POST /api/shell/open` | `{url}` opens in the SYSTEM browser (no-op-ish in a plain browser; the page uses window.open there) |
| `POST /api/shell/focus` · `POST /api/shell/quit` | single-instance handoff / the tray-less Quit. Both no-op without a shell |

`GET /` serves the setup wizard instead of the desk while `bot/.env` holds no
usable keys; `/setup` re-runs it any time, prefilled, keys keepable.

## Meta

| | |
|---|---|
| `GET /api/meta` | port, root, user, configured theme |
| `GET /api/admin` | read-only inventory: lanes, models, files, trading state, measured copilot cost |

---

## `state.json` - the file-based alternative

The desk writes a full snapshot to `state.json` every 20 seconds: `status`,
`positions`, `orders`, `unprotected`, `broker_orders`, `radar`, `config`, plus `ts`
and `stale_after_s`.

**Why it exists:** not every agent can reach `localhost`. Cowork's shell is a
sandboxed VM with the folder mounted but no route to the host network, so `curl`
fails there while file reads work fine. Anything that can read a file can read live
state.

Check `ts` before trusting it. Older than `stale_after_s` means the desk is stopped,
not that the account is flat. It is read-only: acting still goes through the API.
