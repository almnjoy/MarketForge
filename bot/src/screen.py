"""Universe screen -> candidates.json.

Pulls daily bars for each symbol, runs the pure signal engine, and writes the
'buy' setups (bounded to MAX_CANDIDATES by most recent signal) to
data/candidates.json. This is the deterministic front of the pipeline; the LLM
analysis/critique layer only runs on what this flags.

Liquidity + price floors filter junk before any LLM tokens are spent.
"""
from __future__ import annotations

import argparse
import json

import config
import signals
from alpaca_client import AlpacaClient
from sectors import sector_for


def load_universe():
    if config.UNIVERSE_PATH.exists():
        syms = [s.strip().upper() for s in config.UNIVERSE_PATH.read_text().splitlines()
                if s.strip() and not s.strip().startswith("#")]
        if syms:
            return syms
    return list(config.DEFAULT_UNIVERSE)


def screen_symbol(symbol, client, cfg=config):
    try:
        bars = client.get_daily_bars(symbol, limit=max(cfg.SMA_SLOW + 5, 260))
    except Exception as e:
        return {"symbol": symbol, "action": "error", "reason": str(e)[:120]}
    if not bars:
        return {"symbol": symbol, "action": "error", "reason": "no_bars"}

    last = bars[-1]["c"]
    if last < cfg.MIN_PRICE_CENTS:
        return {"symbol": symbol, "action": "hold", "reason": "below_price_floor"}
    if signals.avg_dollar_volume(bars) < cfg.MIN_AVG_DOLLAR_VOLUME:
        return {"symbol": symbol, "action": "hold", "reason": "below_liquidity_floor"}

    sig = signals.signal(bars, cfg)
    return {
        "symbol": symbol,
        "sector": sector_for(symbol),
        "action": sig["action"],
        "entry_cents": sig["entry_cents"],
        "stop_cents": sig["stop_cents"],
        "atr_cents": sig["atr_cents"],
        "reason": sig["reason"],
        "last_bar": bars[-1]["t"],
    }


def main():
    ap = argparse.ArgumentParser(description="Screen the universe for swing setups.")
    ap.add_argument("--symbol", help="screen one symbol")
    ap.add_argument("--all", action="store_true", help="print holds/errors too")
    args = ap.parse_args()

    client = AlpacaClient()
    universe = [args.symbol.upper()] if args.symbol else load_universe()
    print(config.env_banner())
    print(f"screening {len(universe)} symbols...")

    results = [screen_symbol(s, client) for s in universe]
    buys = [r for r in results if r.get("action") == "buy"][: config.MAX_CANDIDATES]

    config.CANDIDATES_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.CANDIDATES_PATH.write_text(json.dumps(buys, indent=2))

    for r in results:
        if args.all or r.get("action") in ("buy", "error"):
            entry = r.get("entry_cents")
            e = f"{entry/100:.2f}" if entry else "-"
            print(f"  {r['symbol']:<6} {r.get('action','?'):<6} {e:>9}  {r.get('reason','')}")
    print(f"\n{len(buys)} candidate(s) -> {config.CANDIDATES_PATH}")
    if buys:
        print("Next: run the /analyze LLM pass, then risk.py to gate + size.")


if __name__ == "__main__":
    main()
