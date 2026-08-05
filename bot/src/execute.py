"""Order execution. Human-gated by default; autonomous only behind an explicit
env flag. Reads data/staged_orders.json (produced by risk.py) and places each
order on Alpaca.

Safety model (mirrors the Kalshi bot):
  - Write-ahead: intent is recorded in SQLite BEFORE the API call, then updated
    with the broker result. A crash mid-flight leaves an 'intent' row to reconcile.
  - Human gate: interactive runs require typing the env name to confirm.
  - Autonomous: only when AUTO_EXECUTE=true AND --auto is passed. Keep this OFF
    until backtest + a paper track record + the kill-switch are all proven. Even
    then, gate the flip in config, not in code you have to edit.
"""
from __future__ import annotations

import argparse
import json
import uuid

import config
import db
from alpaca_client import AlpacaClient

AUTO_EXECUTE = (config._get("AUTO_EXECUTE", "false") or "false").lower() == "true"


def _load_staged():
    try:
        return json.loads(config.STAGED_ORDERS_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _confirm(n, env):
    print(f"\nAbout to place {n} order(s) in {env.upper()}.")
    if env == "live":
        print("!! LIVE = REAL MONEY. This is your bot's walled-off bankroll only, but it is real.")
    ans = input(f"Type '{env}' to confirm, anything else to abort: ").strip().lower()
    return ans == env


def place_one(client, conn, order):
    coid = f"stockbot-{uuid.uuid4().hex[:16]}"
    db.record_intent(conn, client_order_id=coid, symbol=order["symbol"], side="buy",
                     qty=order["qty"], limit_price_cents=order["limit_price_cents"],
                     env=config.STOCK_ENV)
    try:
        resp = client.submit_order(
            symbol=order["symbol"], qty=order["qty"], side="buy",
            limit_price_cents=order["limit_price_cents"], time_in_force="day",
            client_order_id=coid,
        )
        db.update_order_result(conn, client_order_id=coid, status=resp.get("status", "accepted"),
                               broker_order_id=resp.get("id"))
        print(f"  placed {order['symbol']} qty {order['qty']} @ "
              f"{order['limit_price_cents']/100:.2f} -> {resp.get('status')} ({resp.get('id')})")
        return True
    except Exception as e:
        db.update_order_result(conn, client_order_id=coid, status="error", error=str(e)[:300])
        print(f"  ERROR {order['symbol']}: {str(e)[:200]}")
        return False


def main():
    ap = argparse.ArgumentParser(description="Place staged orders (human-gated).")
    ap.add_argument("--auto", action="store_true",
                    help="skip the prompt (requires AUTO_EXECUTE=true in .env)")
    ap.add_argument("--yes", action="store_true", help="skip prompt for THIS run (interactive override)")
    args = ap.parse_args()

    staged = _load_staged()
    print(config.env_banner())
    if not staged:
        print("Nothing staged. Run risk.py first.")
        return

    for o in staged:
        print(f"  {o['symbol']:<6} qty {str(o['qty']):>8} @ {o['limit_price_cents']/100:>8.2f}  "
              f"notional ${o['notional_cents']/100:>8,.2f}  ({o.get('confidence','?')})")

    autonomous = args.auto and AUTO_EXECUTE
    if args.auto and not AUTO_EXECUTE:
        print("\n--auto ignored: AUTO_EXECUTE is not true in .env. Falling back to the prompt.")

    if not autonomous and not args.yes:
        if not _confirm(len(staged), config.STOCK_ENV):
            print("Aborted. Nothing placed.")
            return

    conn = db.connect()
    db.init_db(conn)
    client = AlpacaClient()

    placed = sum(place_one(client, conn, o) for o in staged)
    print(f"\nPlaced {placed}/{len(staged)}. Clearing the staged file.")
    config.STAGED_ORDERS_PATH.write_text("[]")


if __name__ == "__main__":
    main()
