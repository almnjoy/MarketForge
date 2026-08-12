"""Universe screen -> short_candidates.json. Mirror of screen.py.

STAGES ONLY. Writes a JSON file. Touches no order path, no positions, no exit
logic. Per BRAIN-2-SHORT.md the short lane is PAPER ONLY until those gates close,
and this module is deliberately incapable of sending anything.

Two gates run before any per-symbol work, in this order:

  1. REGIME. Shorts are a RED and YELLOW tool. On GREEN or UNKNOWN this exits
     with zero candidates and says why. Ariel's whole framing is that market,
     group, and setup must agree; the short lane inverts the first term.

  2. BORROW. Alpaca only permits shorting easy-to-borrow names and the ETB list
     is rebuilt every morning, so a candidate that is not `shortable` today is
     not a candidate. Skipping this check produces tickets that cannot fill,
     which is worse than producing none.

Usage:
    python short_screen.py            # respects the regime gate
    python short_screen.py --force    # scan anyway, for research on a green tape
    python short_screen.py --symbol AXTI
"""
from __future__ import annotations

import argparse
import json

import config
import regime
import short_signals
import signals
from alpaca_client import AlpacaClient
from sectors import sector_for


def _shortable(client, symbol):
    """Ask Alpaca whether this name can be shorted TODAY.

    Fails CLOSED: if the assets lookup errors we return False, because the
    expensive mistake here is staging a ticket for a hard-to-borrow name, not
    missing one.
    """
    try:
        a = client._req("GET", client.trade_base, f"/v2/assets/{symbol}")
    except Exception:
        return False, "asset_lookup_failed"
    if not a:
        return False, "asset_unknown"
    if not a.get("tradable"):
        return False, "not_tradable"
    if not a.get("shortable"):
        return False, "not_shortable"
    if not a.get("easy_to_borrow"):
        return False, "hard_to_borrow"
    return True, "etb"


def screen_symbol(symbol, client, cfg=config, check_borrow=True):
    try:
        bars = client.get_daily_bars(symbol, limit=max(cfg.SMA_SLOW + 5, 260))
    except Exception as e:
        return {"symbol": symbol, "action": "error", "reason": str(e)[:120]}
    if not bars:
        return {"symbol": symbol, "action": "error", "reason": "no_bars"}

    last = bars[-1]["c"]
    floor = getattr(cfg, "SHORT_MIN_PRICE_CENTS", 500)
    if last < floor:
        # Sub-$5 shorts carry a 100% / $2.50-per-share maintenance requirement
        # and are the worst squeeze risk on the board. Never.
        return {"symbol": symbol, "action": "hold", "reason": "below_short_price_floor"}
    if signals.avg_dollar_volume(bars) < cfg.MIN_AVG_DOLLAR_VOLUME:
        return {"symbol": symbol, "action": "hold", "reason": "below_liquidity_floor"}

    sig = short_signals.short_signal(bars, cfg)
    out = {
        "symbol": symbol,
        "sector": sector_for(symbol),
        "action": sig["action"],
        "entry_cents": sig["entry_cents"],
        "stop_cents": sig["stop_cents"],
        "atr_cents": sig["atr_cents"],
        "ma_cents": sig["ma_cents"],
        "extension_atr": round(sig["extension"], 2) if sig["extension"] is not None else None,
        "reason": sig["reason"],
        "last_bar": bars[-1]["t"],
    }

    # Supply matters MORE on the short side: a squeeze is a small-supply event,
    # and shorting a microcap into a catalyst is how accounts die. Annotation
    # only - it never blocks, but "micro" on a short ticket should stop you.
    if out["action"] == "short" and getattr(cfg, "ANNOTATE_SUPPLY", True):
        try:
            import fundamentals
            out.update(fundamentals.annotate(symbol))
            if out.get("supply_class") == "micro":
                out["squeeze_warning"] = (
                    f"{out.get('shares_millions')}M shares outstanding. Small "
                    f"supply is squeeze fuel; a short here can move against you "
                    f"faster than a stop can fill.")
        except Exception as e:
            out["supply_class"] = "unknown"
            out["note"] = f"supply lookup failed: {str(e)[:80]}"

    # Only pay for the borrow lookup on names that actually triggered.
    if out["action"] == "short" and check_borrow:
        ok, why = _shortable(client, symbol)
        out["borrow"] = why
        if not ok:
            out["action"] = "hold"
            out["reason"] = f"borrow_blocked:{why}"
    return out


def main():
    ap = argparse.ArgumentParser(description="Screen the universe for short setups.")
    ap.add_argument("--symbol", help="screen one symbol")
    ap.add_argument("--all", action="store_true", help="print holds/errors too")
    ap.add_argument("--force", action="store_true",
                    help="ignore the regime gate (research only)")
    ap.add_argument("--no-borrow-check", action="store_true",
                    help="skip the ETB lookup (offline/backtest use)")
    args = ap.parse_args()

    client = AlpacaClient()
    print(config.env_banner())
    print("SHORT LANE - stages candidates only, sends nothing. See BRAIN-2-SHORT.md")

    r = regime.read(client)
    posture = r.get("short_posture")
    print(f"regime: {r['label']} ({r.get('pct')})  short posture: {posture}")
    print(f"  {r.get('short_note')}")
    if r.get("max_distribution_days") is not None:
        print(f"  distribution days (worst index): {r['max_distribution_days']}")
    if r.get("follow_through_broken"):
        print(f"  FAILED follow-through: {r['follow_through_broken']} "
              f"- a confirmed rally that lost its low")

    if posture == "stand_down" and not args.force:
        out_path = config.DATA_DIR / "short_candidates.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps([], indent=2))
        print("\nSTAND DOWN. No short scan on this tape. Use --force to override.")
        return

    from screen import load_universe
    universe = [args.symbol.upper()] if args.symbol else load_universe()
    print(f"\nscreening {len(universe)} symbols for shorts...")

    results = [screen_symbol(s, client, check_borrow=not args.no_borrow_check)
               for s in universe]
    shorts = [x for x in results if x.get("action") == "short"][: config.MAX_CANDIDATES]

    out_path = config.DATA_DIR / "short_candidates.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(shorts, indent=2))

    for x in results:
        if args.all or x.get("action") in ("short", "error"):
            e = f"{x['entry_cents']/100:.2f}" if x.get("entry_cents") else "-"
            s = f"{x['stop_cents']/100:.2f}" if x.get("stop_cents") else "-"
            print(f"  {x['symbol']:<6} {x.get('action','?'):<6} entry {e:>8} "
                  f"stop {s:>8}  ext {str(x.get('extension_atr')):>6}  {x.get('reason','')}")

    print(f"\n{len(shorts)} short candidate(s) -> {out_path}")
    if shorts:
        print("These are PAPER candidates. Nothing is staged to a live account.")


if __name__ == "__main__":
    main()
