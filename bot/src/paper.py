"""The paper lane. A second broker connection that is ALWAYS paper.

Why this module exists
----------------------
`STOCK_ENV` picks one venue for the entire process. With `STOCK_ENV=live` the
desk physically could not reach the paper account, so there was nowhere to
practise a strategy the live account cannot execute.

That is now the binding constraint. The short lane needs $2,000 of equity to
short at all and the live account is $1,000, so **every short setup the engine
finds is unexecutable live and always will be until it is funded**. Without a
paper lane those setups produce no data at all - they scroll past and are gone.

So: paper is not a testing mode here, it is the **record**. Every plan executes
on paper immediately. The live ticket is staged and waits for a human. One
strategy, two destinations, and the destination that always fires is the one
that cannot lose money.

Safety properties
-----------------
- This module can ONLY talk to `paper-api.alpaca.markets`. The base URL is a
  constant, not a parameter. There is no argument you can pass to make it hit
  live, which is the point: a bug here must not be able to reach real money.
- It refuses to run if the paper key looks like a live key (`AK*`). Alpaca paper
  keys start with `PK`. Pasting the live pair into the paper slots would
  otherwise silently trade real money from a module named "paper".
- Fills are recorded to `trades-paper.db`, never the live ledger.
"""
from __future__ import annotations

import uuid

import config
from alpaca_client import AlpacaClient

# Hard-coded. Not read from config, not overridable by an argument.
_PAPER_BASE = "https://paper-api.alpaca.markets"


class PaperUnavailable(RuntimeError):
    pass


def configured():
    """(ok, reason). Cheap, no network - safe to call on every page load."""
    if not config.PAPER_KEY_ID or not config.PAPER_SECRET:
        return False, "ALPACA_KEY_ID / ALPACA_SECRET_KEY not set in bot/.env"
    if config.PAPER_KEY_ID.upper().startswith("AK"):
        # AK is the live prefix. This would be a live key in the paper slot.
        return False, ("ALPACA_KEY_ID looks like a LIVE key (starts with AK). "
                       "Paper keys start with PK. Refusing to use it.")
    if not config.PAPER_KEY_ID.upper().startswith("PK"):
        return False, (f"ALPACA_KEY_ID does not look like a paper key "
                       f"(expected PK..., got {config.PAPER_KEY_ID[:2]}...)")
    return True, "ok"


def client():
    """An AlpacaClient pinned to paper. Raises if the keys are wrong."""
    ok, why = configured()
    if not ok:
        raise PaperUnavailable(why)
    return AlpacaClient(key_id=config.PAPER_KEY_ID,
                        secret=config.PAPER_SECRET,
                        trade_base=_PAPER_BASE)


def check():
    """Live round-trip against the paper account. This is the 'is it still
    linked?' answer, and it proves the link by fetching the account rather than
    by checking that a string is non-empty.

    Never raises. Returns a dict the UI and the CLI both render.
    """
    ok, why = configured()
    out = {"configured": ok, "reason": why, "base": _PAPER_BASE,
           "key_prefix": (config.PAPER_KEY_ID[:4] + "...") if config.PAPER_KEY_ID else None,
           "process_env": config.STOCK_ENV, "linked": False}
    if not ok:
        return out
    try:
        c = client()
        a = c.get_account()
        raw = a.get("raw") or {}
        equity = a["equity_cents"] / 100.0
        out.update({
            "linked": True,
            "status": a.get("status"),
            "equity": equity,
            "cash": a["cash_cents"] / 100.0,
            "buying_power": a["buying_power_cents"] / 100.0,
            "account_type": a.get("account_type"),
            "shorting_enabled": bool(raw.get("shorting_enabled")),
            "multiplier": raw.get("multiplier"),
            # The whole reason the short lane is paper-only. Reported from the
            # paper account's own numbers, not assumed.
            "can_short": bool(raw.get("shorting_enabled")) and equity >= 2000,
            "short_gate": ("ok" if equity >= 2000 else
                           f"equity ${equity:,.2f} is under the $2,000 Reg T minimum"),
            "positions": len(c.list_positions() or []),
        })
    except Exception as e:
        out["error"] = str(e)[:300]
    return out


def _record(client_order_id, symbol, side, qty, resp):
    """Write the shadow fill to trades-paper.db, never the live ledger.

    db.connect() honours config.DB_PATH, which points at the LIVE ledger when
    STOCK_ENV=live. Passing the path explicitly is what keeps paper out of it.
    """
    try:
        import db
        conn = db.connect(config.PAPER_DB_PATH)
        db.init_db(conn)
        db.record_intent(conn, client_order_id=client_order_id, symbol=symbol,
                         side=side, qty=int(qty or 0), limit_price_cents=None,
                         env="paper")
        db.update_order_result(conn, client_order_id=client_order_id,
                               status=resp.get("status", "accepted"),
                               broker_order_id=resp.get("id"))
    except Exception as e:
        # A ledger write failing must not lose the fill that already happened.
        return str(e)[:200]
    return None


def place(symbol, side, *, notional=None, qty=None, trail_pct=None, note=""):
    """Submit the ENTRY on paper. Does NOT arm the exit - see below.

    THE DEFECT THIS FUNCTION USED TO HAVE (2026-08-11): it submitted the
    protective order immediately after the entry, in the same call. The broker
    rejects that, because the entry is still working:

        403 - cannot open a long buy while a short sell order is open

    A trailing-stop BUY placed while the entry SELL is live reads as opening a
    long, so it bounces, and the short then fills with no exit on it. That is
    the identical race that left VRM naked on the live side, reintroduced on
    paper because this function did not reuse the machinery that fixed it.

    Arming is therefore the CALLER's job, via api.arm_after_fill(), which polls
    for the fill and hands off to the durable watcher on timeout. This function
    returns `order_id` and `trail_pct` so the caller can do that.

    Whole shares: a trailing stop cannot attach to a fractional position, so any
    order that wants an exit is converted to whole shares here. If it cannot buy
    at least one share, it does not place a position it could never protect.
    """
    c = client()
    symbol = symbol.upper().strip()
    side = (side or "buy").lower()
    want_trail = trail_pct is not None

    # Force whole shares whenever an exit is wanted, and by default always, so
    # the paper book stops accumulating fractional positions that are
    # permanently unprotectable (BEX, CWVX, IESC, IREG, MANH, TCX got in that
    # way before this guard existed).
    if (want_trail or config.PAPER_WHOLE_SHARES_ONLY) and qty is None:
        if notional is None:
            raise PaperUnavailable("provide notional or qty")
        px = c.get_latest_price(symbol)
        if not px:
            raise PaperUnavailable("no price available to size whole shares")
        qty = int(float(notional) * 100 // px)
        notional = None
        if qty < 1:
            raise PaperUnavailable(
                f"${float(notional or 0):.2f} is under one share of {symbol} "
                f"(${px/100:.2f}). A fractional position cannot carry a trailing "
                f"stop, so this order was not placed.")

    coid = f"paper-{uuid.uuid4().hex[:12]}"
    resp = c.submit_market_order(symbol=symbol, side=side, notional=notional,
                                 qty=qty, client_order_id=coid)
    ledger_error = _record(coid, symbol, side, qty, resp)

    out = {"ok": True, "venue": "paper", "symbol": symbol, "side": side,
           "notional": notional, "qty": qty, "status": resp.get("status"),
           "id": resp.get("id"), "order_id": resp.get("id"),
           "client_order_id": coid, "trail_pct": trail_pct, "note": note}
    if ledger_error:
        out["ledger_error"] = ledger_error
    return out


def overview():
    """Everything the paper page renders, in one call. READ-ONLY."""
    ok, why = configured()
    if not ok:
        return {"ok": False, "error": why}
    try:
        c = client()
        acct = c.get_account()
        positions = c.list_positions() or []
        try:
            orders = c._req("GET", _PAPER_BASE, "/v2/orders",
                            params={"status": "open", "limit": 100, "nested": "true"}) or []
        except Exception:
            orders = []

        # Same unprotected check the live desk runs, pointed at paper. Shorts
        # count as guarded by a working BUY, longs by a working SELL.
        guarded = {}
        for o in orders:
            if o.get("symbol"):
                guarded.setdefault(o["symbol"], set()).add(str(o.get("side")))
        naked = []
        for p in positions:
            q = float(p.get("qty") or 0)
            if q == 0:
                continue
            need = "sell" if q > 0 else "buy"
            if need not in guarded.get(p["symbol"], ()):
                naked.append({"symbol": p["symbol"], "qty": q,
                              "side": "long" if q > 0 else "short", "needs": need})

        raw = acct.get("raw") or {}
        equity = acct["equity_cents"] / 100.0
        return {
            "ok": True,
            "venue": "paper",
            "process_env": config.STOCK_ENV,
            "equity": equity,
            "cash": acct["cash_cents"] / 100.0,
            "buying_power": acct["buying_power_cents"] / 100.0,
            "status": acct.get("status"),
            "account_type": acct.get("account_type"),
            "shorting_enabled": bool(raw.get("shorting_enabled")),
            "can_short": bool(raw.get("shorting_enabled")) and equity >= 2000,
            "long_market_value": float(raw.get("long_market_value") or 0),
            "short_market_value": float(raw.get("short_market_value") or 0),
            "positions": [{
                "symbol": p["symbol"],
                "qty": p["qty"],
                "side": "long" if p["qty"] > 0 else "short",
                "entry": p["avg_entry_cents"] / 100.0,
                "price": p["current_price_cents"] / 100.0,
                "value": p["market_value_cents"] / 100.0,
                "pl": p["unrealized_pl_cents"] / 100.0,
            } for p in positions],
            "orders": [{
                "symbol": o.get("symbol"), "side": o.get("side"),
                "type": o.get("order_type"), "qty": o.get("qty"),
                "trail_percent": o.get("trail_percent"), "status": o.get("status"),
            } for o in orders],
            "unprotected": naked,
        }
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}


if __name__ == "__main__":
    import json
    print(json.dumps(check(), indent=2))
