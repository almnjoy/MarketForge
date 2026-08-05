"""Status CLI: account, open positions marked to market, breaker state, and the
bot's bankroll utilisation. Read-only; safe to run any time.
"""
from __future__ import annotations

import argparse

import config
import db
import portfolio
import risk
from alpaca_client import AlpacaClient


def main():
    ap = argparse.ArgumentParser(description="Show bot + account status.")
    ap.parse_args()

    conn = db.connect()
    db.init_db(conn)
    client = AlpacaClient()

    print(config.env_banner())
    try:
        snap = portfolio.account_snapshot(client)
    except Exception as e:
        print(f"WARN: could not read account: {e}")
        return

    current = snap["equity_cents"]
    db.record_equity(conn, env=config.STOCK_ENV, cash_cents=snap["cash_cents"],
                     positions_value_cents=snap["positions_value_cents"])
    open_eq = db.open_equity_today(conn) or current
    peak = db.peak_equity(conn) or current
    bs = risk.breaker_state(open_eq, current, peak)

    print(f"account type : {snap['account_type']}")
    print(f"equity       : ${current/100:,.2f}   (cash ${snap['cash_cents']/100:,.2f})")
    print(f"day P/L      : {(-bs['daily_loss_pct']):+.2%}   drawdown {bs['drawdown_pct']:.2%}")
    print(f"kill-switch  : {'TRIPPED' if bs['tripped'] else 'armed'}  "
          f"(limits: daily {config.MAX_DAILY_LOSS_PCT:.0%}, dd {config.MAX_DRAWDOWN_PCT:.0%})")

    bot = risk.bot_deployed(conn)
    cooldown = db.symbols_in_wash_cooldown(conn, config.WASH_SALE_COOLDOWN_DAYS)
    print(f"bankroll     : ${config.BOT_BANKROLL_CENTS/100:,.0f}  "
          f"committed ${bot['committed']/100:,.2f}  open {bot['open']}/{config.MAX_POSITIONS}")
    if cooldown:
        print(f"wash cooldown: {sorted(cooldown)}")

    if snap["positions"]:
        print("\npositions:")
        for p in snap["positions"]:
            print(f"  {p['symbol']:<6} {p['qty']:>10} @ {p['avg_entry_cents']/100:>8.2f}  "
                  f"now {p['current_price_cents']/100:>8.2f}  uPL ${p['unrealized_pl_cents']/100:>8,.2f}")


if __name__ == "__main__":
    main()
