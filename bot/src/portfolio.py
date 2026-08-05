"""Account snapshot from Alpaca. Marks positions to the live book.

Kept tiny and side-effect-free so risk.py can read the account once per cycle
and hand a consistent equity snapshot to the breakers.
"""
from __future__ import annotations

import argparse

import config
from alpaca_client import AlpacaClient


def account_snapshot(client) -> dict:
    """Returns {cash_cents, positions_value_cents, equity_cents, positions:[...]}"""
    acct = client.get_account()
    positions = client.list_positions()
    pos_value = sum(p["market_value_cents"] for p in positions)
    cash = acct["cash_cents"]
    return {
        "cash_cents": cash,
        "positions_value_cents": pos_value,
        "equity_cents": cash + pos_value,
        "account_type": acct.get("account_type"),
        "positions": positions,
    }


def main():
    ap = argparse.ArgumentParser(description="Print the live account snapshot.")
    ap.parse_args()
    client = AlpacaClient()
    snap = account_snapshot(client)
    print(config.env_banner())
    print(f"account type : {snap['account_type']}")
    print(f"cash         : ${snap['cash_cents']/100:,.2f}")
    print(f"positions    : ${snap['positions_value_cents']/100:,.2f}")
    print(f"equity       : ${snap['equity_cents']/100:,.2f}")
    for p in snap["positions"]:
        print(f"  {p['symbol']:<6} {p['qty']:>10} @ {p['avg_entry_cents']/100:>8.2f}  "
              f"mkt ${p['market_value_cents']/100:>10,.2f}  uPL ${p['unrealized_pl_cents']/100:>8,.2f}")


if __name__ == "__main__":
    main()
