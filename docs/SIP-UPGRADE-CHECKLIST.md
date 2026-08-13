# Switching to Algo Trader Plus (SIP) - what to change and what breaks

Alpaca Algo Trader Plus, $99/mo: full consolidated real-time tape, 10,000 rpm, and
**no websocket symbol cap** (still one concurrent connection).

## The whole change

```ini
# bot/.env
ALPACA_DATA_FEED=sip
```

Seven modules read that variable, so the desk switches over on restart. **Do not
hand-edit the floors** - they now scale themselves, and the two below are the ones
that would have silently broken.

## The two things that would have gone wrong quietly

**1. Your radar floor stops filtering.** `bot/.env` line 29 carries
`RADAR_MIN_DOLLAR_VOLUME=3000000`, set by hand against IEX. IEX is a few percent of
the consolidated tape, so the same stock on the same day reports roughly an order of
magnitude more volume on SIP. Nothing errors. The scan runs, the board fills, and a
$3M floor now passes names doing a few hundred thousand dollars of real volume.

Config now warns at startup:

```
[config] RADAR_MIN_DOLLAR_VOLUME=3,000,000 looks IEX-calibrated but the feed is
SIP. SIP reports far more volume for the same stock, so this floor will pass
almost everything. Consider ~30,000,000.
```

**Either delete the line from `.env`** (the SIP default of $30M applies), or set it
to a SIP-scaled number you chose. The warning fires until one of those happens.

**2. The tap keeps watching 15 symbols.** `STREAM_MAX_SUBSCRIPTIONS` defaulted to
30, which is the *Basic* plan's cap - and it is a cap on SUBSCRIPTIONS, not symbols,
with trades+quotes costing one each. Algo Trader Plus removes it. That default is
now feed-derived: 30 on IEX, 400 on SIP, so the tap goes from 15 symbols to 200
without you touching anything.

If it had stayed at 30 you would have paid $99/mo for an unlimited feed and kept
watching fifteen names.

## What gets better immediately

- **REST stops being blind to the last 15 minutes.** Every price on every screen
  becomes current, not just the handful in the tap.
- **The tap stops being a 2%-of-volume sample.** A quiet symbol now means quiet,
  not "IEX did not see it" - the `read_live()` staleness refusal becomes a real
  signal instead of mostly noise.
- **Brain 3 becomes possible at all.** Market-wide snapshots to find today's gapper
  plus real-time minute bars on the winner is exactly what the day lane needs and
  exactly what the free plan cannot do.
- The `[feed]` banner prints which feed is live at startup, so it is never assumed.

## Verify it took

```powershell
python -c "import sys; sys.path.insert(0,'bot/src'); import config; print(config.feed_banner()); print('radar floor', f'{config.RADAR_MIN_DOLLAR_VOLUME:,.0f}'); print('subs', config.STREAM_MAX_SUBSCRIPTIONS)"
```

Expect the SIP banner, a floor around 30,000,000, and 400 subscriptions. Then start
the tap and expect `200 symbols x 2 channels = 400/400` instead of 15 x 2.

## Still true after upgrading

- **One websocket connection.** Three lanes still cannot have three taps; the
  priority order in the restructure plan still applies, it just has far more room.
- **Live is a cash account.** T+1 settlement still caps the day lane at roughly one
  round trip per day on settled cash. Data was never that constraint.
- **EDGAR still gives shares outstanding, not float.** No data plan fixes that.
