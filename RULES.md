# Training-Wheels Rules (live money, minimal auto)

The bot on rocker auto-trades REAL money inside these caps while Dustin researches
strategy. This doc is the discussion copy - the live values render on the left from
the bot itself. To change: edit `/opt/docker/stock-bot/.env` on rocker + RECREATE the
container (env bakes at creation; a restart is not enough).

## Auto-entry gates (all must pass, fail-closed)
| Gate | Setting | Why |
|---|---|---|
| Master switch | LIVE_AUTO_ENABLED=true | one flag kills all live autos |
| Quality | LLM score >= 85 AND verdict=signal | only high-conviction catalysts |
| Data trust | % verified + not IPO-flagged | screener lies (stale prints, splits) |
| Price floor | >= $3.00 | ZYBT-class spiker/halt junk lives below |
| Size | $50/trade, whole shares | tiny while learning |
| Frequency | max 2 entries/day | no machine-gunning |
| Total exposure | $150 auto capital max | 15% of the account |
| Kill switch | G5 breaker armed | drawdown/daily-loss halt |
| Dedup | never adds to a held symbol | no averaging by accident |

## Exits (the part that makes it survivable)
- Default: GTC **trailing stop 10%** armed right after the fill (two-step: buy ->
  confirm fill -> trail). Rides winners, cuts faders - never round-trip a winner.
- Alt mode: RADAR_AUTO_EXIT=bracket (fixed +12% take-profit / -6% stop).
- Trail caveats: too tight = shaken out by noise on volatile names; trigger fires a
  MARKET sell so thin names can fill below the stop; gaps gap.

## Worst realistic day
2 entries x $50, both stop out ~-6-10% = about **-$10** (plus gap risk).

## Manual lane (Dustin + CC copilot)
- Trade ticket defaults trail 10%. Type "live" to arm. No naked entries.
- The paper account still exists (trades-paper.db) for testing ideas risk-free.

## Tuning backlog (discuss with CC, then apply on rocker)
- Trail width per volatility class (10% is one-size; ATR-based would be better)
- Time-of-day filter (skip the 4pm scan's late entries?)
- Reddit-buzz as a score booster vs its own lane
- Scale-out: sell half at +15%, trail the rest (needs OCO leg management)
- Postmortem loop: settled trades -> lessons.md -> feed back to the LLM screener
