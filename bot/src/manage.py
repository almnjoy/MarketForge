"""Exit management: sell bot-held positions on a trend break or a hard stop.

The entry side (screen -> risk -> execute) only BUYS. Without this, an unattended
run just accumulates and never completes a trade. manage.py closes positions the
bot opened when either exit fires:
  - trend break : close < slow SMA (signals.exit_signal)
  - hard stop   : price <= avg_entry * (1 - HARD_STOP_PCT)

Exits are risk-reducing, so this places sells directly (no human gate) - you never
want a stop to wait for a confirmation. Only touches positions the bot itself
opened (db ledger); manual positions in the same account are left alone. Records a
realized exit so the wash-sale cooldown (G8) knows about any loss.
"""
from __future__ import annotations

import argparse
import uuid

import config
import db
import signals
from alpaca_client import AlpacaClient


# --- pure decision --------------------------------------------------------
def decide_exit(bars, avg_entry_cents, current_price_cents, cfg=config):
    """Returns (should_exit, reason)."""
    ex = signals.exit_signal(bars, cfg)
    if ex["exit"]:
        return True, ex["reason"]
    if avg_entry_cents and current_price_cents and \
            current_price_cents <= avg_entry_cents * (1 - cfg.HARD_STOP_PCT):
        return True, f"hard_stop_{cfg.HARD_STOP_PCT:.0%}"
    return False, "hold"


# --- I/O shell ------------------------------------------------------------
def main():
    argparse.ArgumentParser(description="Manage exits for bot-held positions.").parse_args()

    conn = db.connect()
    db.init_db(conn)
    client = AlpacaClient()
    print(config.env_banner())

    bot_syms = {r["symbol"] for r in db.bot_open_symbols(conn, config.STOCK_ENV)}
    if not bot_syms:
        print("No bot positions to manage.")
        return

    try:
        positions = {p["symbol"]: p for p in client.list_positions()}
    except Exception as e:
        print(f"WARN: could not read positions ({e}); aborting.")
        return

    for sym in sorted(bot_syms):
        p = positions.get(sym)
        if not p:
            print(f"  {sym}: no live position (closed elsewhere?) - skipping")
            continue
        try:
            bars = client.get_daily_bars(sym, limit=max(config.SMA_SLOW + 5, 260))
        except Exception as e:
            print(f"  {sym}: bars error {str(e)[:80]}")
            continue

        should, reason = decide_exit(bars, p["avg_entry_cents"], p["current_price_cents"])
        upl = p["unrealized_pl_cents"]
        if not should:
            print(f"  {sym}: hold  (uPL ${upl/100:+,.2f})")
            continue

        coid = f"stockbot-exit-{uuid.uuid4().hex[:12]}"
        db.record_intent(conn, client_order_id=coid, symbol=sym, side="sell",
                         qty=p["qty"], limit_price_cents=None, env=config.STOCK_ENV)
        try:
            resp = client.submit_order(symbol=sym, qty=p["qty"], side="sell",
                                       time_in_force="day", client_order_id=coid)
            db.update_order_result(conn, client_order_id=coid,
                                   status=resp.get("status", "accepted"),
                                   broker_order_id=resp.get("id"))
            db.record_exit(conn, symbol=sym, realized_pnl_cents=upl,
                           env=config.STOCK_ENV, detail=reason)
            print(f"  {sym}: EXIT {reason} -> sold {p['qty']} (realized ${upl/100:+,.2f})")
        except Exception as e:
            db.update_order_result(conn, client_order_id=coid, status="error",
                                   error=str(e)[:300])
            print(f"  {sym}: sell error {str(e)[:120]}")


if __name__ == "__main__":
    main()
