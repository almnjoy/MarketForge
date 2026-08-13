#!/usr/bin/env python3
"""Auto-execute for the live lane. Lifted verbatim out of radar.py, 2026-08-13.

OFF BY DEFAULT (RADAR_AUTO_EXECUTE) and it stays that way until the exit guarantee
has been proven on a real slow fill. It is separated from the scanner because
scanning is awareness and placing an order is not, and the one file that can spend
money should be the one file you can read in a sitting.

Every guard here is load-bearing and was written after something went wrong:
  - the killswitch read FAILS CLOSED - no read means no auto-trade
  - the trail is armed from the FILLED quantity, never the requested one
  - if the fill cannot be confirmed in 18s it says so LOUDLY and arms nothing,
    because a filled position with no stop is the worst state this desk can reach
"""
from __future__ import annotations

import time
import uuid

import config
import db
import portfolio
import risk
import scanner_core as sc


def killswitch_ok(client, conn):
    return _killswitch_ok(client, conn)


def maybe(client, conn, sym, price, score, vlabel, held, cfg=config,
          verified=True, is_ipo=False):
    return maybe_autotrade(client, conn, sym, price, score, vlabel, held, cfg,
                           verified=verified, is_ipo=is_ipo)


def _open_symbols(client):
    try:
        return {p.get("symbol") for p in client.list_positions()}
    except Exception:
        return set()


def _killswitch_ok(client, conn):
    """True only if the same breaker the dashboard shows is armed (not tripped).
    Fails closed: no read = no auto-trade."""
    try:
        snap = portfolio.account_snapshot(client)
        current = snap["equity_cents"]
        open_eq = db.open_equity_today(conn) or current
        peak = db.peak_equity(conn) or current
        return not risk.breaker_state(open_eq, current, peak)["tripped"]
    except Exception:
        return False


def _auto_entries_today(conn, cfg=config):
    """Auto entries already made today (radar- client_order_ids in orders)."""
    row = conn.execute(
        "SELECT COUNT(*) FROM orders WHERE client_order_id LIKE 'radar-%' "
        "AND env=? AND date(created_at) = date('now')", (cfg.STOCK_ENV,)).fetchone()
    return int(row[0] if row else 0)


def _auto_exposure_cents(conn, held, cfg=config):
    """Approximate live auto exposure: currently-held symbols that were entered
    by radar- orders, costed at the standard auto notional."""
    rows = conn.execute(
        "SELECT DISTINCT symbol FROM orders WHERE client_order_id LIKE 'radar-%' AND env=?",
        (cfg.STOCK_ENV,)).fetchall()
    return len({r[0] for r in rows} & set(held)) * cfg.RADAR_AUTO_NOTIONAL_CENTS


def maybe_autotrade(client, conn, sym, price, score, vlabel, held, cfg=config,
                    verified=True, is_ipo=False):
    """Training-wheels auto-entry (2026-08-05). paper: tiny notional market buy
    (original behaviour). live: requires LIVE_AUTO_ENABLED and enters as a
    whole-share BRACKET - take-profit +RADAR_AUTO_TP_PCT and stop-loss
    -RADAR_AUTO_SL_PCT attached at entry, GTC. Hard rules, all fail-closed:
      quality: verdict=signal + score >= RADAR_AUTO_MIN_SCORE + % VERIFIED + not IPO
      price:   >= RADAR_AUTO_MIN_PRICE_CENTS (spiker/halt junk lives below)
      dedup:   never add to a held symbol
      caps:    RADAR_AUTO_MAX_PER_DAY entries/day, RADAR_AUTO_MAX_EXPOSURE_CENTS
               total auto cost (paper additionally: RADAR_AUTO_MAX_POSITIONS)
      breaker: caller only invokes when the G5 kill-switch is armed (ks_ok)."""
    if not cfg.RADAR_AUTO_EXECUTE:
        return None
    live = cfg.STOCK_ENV == "live"
    if live and not cfg.LIVE_AUTO_ENABLED:
        return None
    if vlabel != "signal" or score is None or score < cfg.RADAR_AUTO_MIN_SCORE:
        return None
    if sym in held:
        return None
    notional = cfg.RADAR_AUTO_NOTIONAL_CENTS / 100.0
    if not live:
        if len(held) >= cfg.RADAR_AUTO_MAX_POSITIONS:
            return None
        try:
            client.submit_market_order(symbol=sym, side="buy", notional=notional)
            held.add(sym)
            print(f"[RADAR-AUTO] BUY {sym} ~${notional:,.0f} PAPER (score {score})")
            return True
        except Exception as e:
            print(f"[RADAR-AUTO] {sym} order failed: {str(e)[:120]}")
            return None
    # ---- LIVE path ----
    if not verified or is_ipo:
        print(f"[RADAR-AUTO] {sym} skipped: move math {'IPO-flagged' if is_ipo else 'unverified'}")
        return None
    if price * 100 < cfg.RADAR_AUTO_MIN_PRICE_CENTS:
        print(f"[RADAR-AUTO] {sym} skipped: ${price:,.2f} under the ${cfg.RADAR_AUTO_MIN_PRICE_CENTS/100:.0f} floor")
        return None
    if _auto_entries_today(conn, cfg) >= cfg.RADAR_AUTO_MAX_PER_DAY:
        print(f"[RADAR-AUTO] {sym} skipped: daily entry cap ({cfg.RADAR_AUTO_MAX_PER_DAY}) reached")
        return None
    if _auto_exposure_cents(conn, held, cfg) + cfg.RADAR_AUTO_NOTIONAL_CENTS > cfg.RADAR_AUTO_MAX_EXPOSURE_CENTS:
        print(f"[RADAR-AUTO] {sym} skipped: auto exposure cap (${cfg.RADAR_AUTO_MAX_EXPOSURE_CENTS/100:.0f}) reached")
        return None
    qty = int(notional // price)
    if qty < 1:
        print(f"[RADAR-AUTO] {sym} skipped: ${notional:.0f} buys < 1 whole share @ ${price:,.2f}")
        return None
    coid = f"radar-{uuid.uuid4().hex[:12]}"
    if cfg.RADAR_AUTO_EXIT == "trail":
        # Two-step: market buy -> confirm fill -> arm a GTC trailing stop that
        # follows the high-water mark (sell trail% below the peak; a runner gets
        # kept, a fader gets cut). Alpaca can't attach trailing at entry.
        try:
            db.record_intent(conn, client_order_id=coid, symbol=sym, side="buy",
                             qty=qty, limit_price_cents=None, env=cfg.STOCK_ENV)
            resp = client.submit_market_order(symbol=sym, side="buy", qty=qty,
                                              client_order_id=coid)
            oid = resp.get("id")
            db.update_order_result(conn, client_order_id=coid,
                                   status=resp.get("status", "accepted"), broker_order_id=oid)
            filled_qty = 0
            for _ in range(6):  # market orders in RTH fill in seconds
                time.sleep(3)
                o = client.get_order(oid)
                if o.get("status") == "filled":
                    filled_qty = int(float(o.get("filled_qty") or 0))
                    break
            if filled_qty < 1:
                msg = (f"[RADAR-AUTO] {sym} BUY placed but fill unconfirmed after 18s - "
                       f"NO trailing stop armed yet. Check the position.")
                print(msg)
                if cfg.RADAR_DISCORD_WEBHOOK:
                    sc.post_discord(cfg.RADAR_DISCORD_WEBHOOK, msg)
                held.add(sym)
                return True
            trail = client.submit_trailing_stop_sell(
                symbol=sym, qty=filled_qty, trail_percent=cfg.RADAR_AUTO_TRAIL_PCT * 100)
            held.add(sym)
            print(f"[RADAR-AUTO] LIVE BUY {sym} x{filled_qty} @ ~${price:,.2f} (score {score}) "
                  f"+ trailing stop {cfg.RADAR_AUTO_TRAIL_PCT:.0%} armed (GTC, id {trail.get('id')})")
            return True
        except Exception as e:
            msg = f"[RADAR-AUTO] {sym} trail-entry failed: {str(e)[:140]}"
            print(msg)
            if cfg.RADAR_DISCORD_WEBHOOK:
                sc.post_discord(cfg.RADAR_DISCORD_WEBHOOK, msg + "\nIf the BUY filled, set a stop manually.")
            return None
    tp = int(round(price * 100 * (1 + cfg.RADAR_AUTO_TP_PCT)))
    sl = int(round(price * 100 * (1 - cfg.RADAR_AUTO_SL_PCT)))
    try:
        db.record_intent(conn, client_order_id=coid, symbol=sym, side="buy",
                         qty=qty, limit_price_cents=None, env=cfg.STOCK_ENV)
        resp = client.submit_bracket_order(symbol=sym, qty=qty, take_profit_cents=tp,
                                           stop_loss_cents=sl, client_order_id=coid)
        db.update_order_result(conn, client_order_id=coid,
                               status=resp.get("status", "accepted"),
                               broker_order_id=resp.get("id"))
        held.add(sym)
        print(f"[RADAR-AUTO] LIVE BRACKET BUY {sym} x{qty} @ ~${price:,.2f} (score {score}, "
              f"tp ${tp/100:,.2f} +{cfg.RADAR_AUTO_TP_PCT:.0%}, sl ${sl/100:,.2f} -{cfg.RADAR_AUTO_SL_PCT:.0%})")
        return True
    except Exception as e:
        print(f"[RADAR-AUTO] {sym} order failed: {str(e)[:120]}")
        return None
