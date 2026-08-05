"""Catalyst radar: awareness, not trades.

Flags big session movers that have a fresh news catalyst, with a discipline
reminder (scale out, trail a stop, don't round-trip). Stores alerts for the API /
dashboard and optionally pings Discord. It NEVER places an order. Being early is
an awareness problem the radar helps with; the exit discipline is yours.

This is the "SpaceX moment" tool: know when something's moving. It does not tell
you the top - it reminds you to have an exit plan before you touch it.
"""
from __future__ import annotations

import argparse
import json
import urllib.request
import time
import uuid

import config
import db
import portfolio
import risk
from datetime import datetime

from alpaca_client import AlpacaClient
from llm import classify


def discipline_note(price, cfg=config):
    trail = price * (1 - cfg.RADAR_TRAIL_PCT)
    return (f"If you play it: size tiny, scale some out into strength, trail a stop "
            f"~{cfg.RADAR_TRAIL_PCT:.0%} (~${trail:,.2f}). Don't round-trip a winner. "
            f"IPOs / low-float names are the highest-risk version.")


def _post_discord(webhook, content):
    try:
        req = urllib.request.Request(
            webhook, data=json.dumps({"content": content[:1900]}).encode(),
            headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10)
    except Exception:
        pass


def _verify_pct(client, sym, price, screener_pct, cfg=config):
    """Authoritative move math (2026-08-05). The screener's percent_change AND
    price are UNTRUSTED: par-priced SPAC/warrant math, unadjusted reverse
    splits, and stale after-hours prints have all produced fake triple-digit
    movers (AMIX read +466% while the live quote was down on the day).
    Recompute BOTH sides:
      current = live latest trade (fallback: the screener print)
      prev    = close of the last completed session BEFORE the session the
                current price belongs to (newest split-adjusted daily bar dated
                today -> bars[-2]; otherwise bars[-1])
    Returns (pct, price, is_ipo, verified). On any data gap the screener numbers
    pass through with verified=False so the alert can say so; < 2 real daily
    bars = genuine IPO/first sessions, flagged not corrected."""
    cur = price
    try:
        lp = client.get_latest_price(sym)
        if lp:
            cur = lp / 100.0
    except Exception:
        pass
    try:
        bars = client.get_daily_bars(sym, limit=5)
        rows = [(str(b.get("t", ""))[:10], b["c"]) for b in bars if b.get("c")]
        if len(rows) < 2:
            return screener_pct, cur, True, False  # genuine IPO / first sessions
        today = datetime.now().strftime("%Y-%m-%d")  # container TZ = America/Detroit = ET dates
        prev_cents = rows[-2][1] if rows[-1][0] == today else rows[-1][1]
        prev = prev_cents / 100.0
        if prev > 0 and cur > 0:
            return round((cur - prev) / prev * 100.0, 2), cur, False, True
        return screener_pct, cur, False, False
    except Exception:
        return screener_pct, cur, False, False


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
                    _post_discord(cfg.RADAR_DISCORD_WEBHOOK, msg)
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
                _post_discord(cfg.RADAR_DISCORD_WEBHOOK, msg + "\nIf the BUY filled, set a stop manually.")
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


def scan(client, conn, cfg=config):
    try:
        movers = client.get_movers(top=cfg.RADAR_TOP_N)
    except Exception as e:
        print(f"radar: movers unavailable ({str(e)[:120]})")
        return []
    held = _open_symbols(client) if cfg.RADAR_AUTO_EXECUTE else set()
    ks_ok = _killswitch_ok(client, conn) if cfg.RADAR_AUTO_EXECUTE else False
    # Retail buzz (2026-08-05): reddit hot-page mentions, folded into the LLM's
    # context + the note. Cache-backed; {} on any failure, never blocks the scan.
    buzz = {}
    if cfg.RADAR_REDDIT_ENABLED:
        try:
            import reddit
            buzz = reddit.mention_map(client, cfg)
        except Exception as e:
            print(f"radar: reddit buzz unavailable ({str(e)[:80]})")
    alerts = []
    for g in movers.get("gainers", []):
        sym = g.get("symbol")
        pct = float(g.get("percent_change", 0) or 0)
        price = float(g.get("price", 0) or 0)
        if not sym or pct < cfg.RADAR_MIN_MOVE_PCT:
            continue
        if price * 100 < cfg.RADAR_MIN_PRICE_CENTS:
            continue  # skip low-float penny/halted junk
        if db.alert_exists_today(conn, sym, "gainer"):
            continue

        pct, price, is_ipo, verified = _verify_pct(client, sym, price, pct, cfg)
        if verified and pct < cfg.RADAR_MIN_MOVE_PCT:
            continue  # screener move was fake (split/stale print); the REAL move is under the bar

        headlines, url = [], ""
        try:
            news = client.get_news(symbols=[sym], limit=4)
            headlines = [n.get("headline", "") for n in news if n.get("headline")]
            if news:
                url = news[0].get("url", "") or ""
        except Exception:
            pass
        headline = headlines[0] if headlines else ""

        rb = buzz.get(sym)
        if rb:
            for bp in rb.get("posts", [])[:2]:
                headlines.append(f"[Reddit r/{bp['sub']} {bp['score']}pts] {bp['title']}")

        # LLM curation: real catalyst vs noise (None if the model is off/unreachable)
        verdict = classify(sym, pct, price, headlines, cfg)
        score = verdict["score"] if verdict else None
        vlabel = verdict["verdict"] if verdict else ""
        ctype = verdict["catalyst_type"] if verdict else ""
        why = verdict["why"] if verdict else ""

        note = discipline_note(price, cfg)
        if is_ipo:
            note = ("IPO / first sessions: the % is vs the first available close, "
                    "not a normal prior day. Treat the number as unreliable. ") + note
        elif not verified:
            note = ("Move math UNVERIFIED (bars/latest unavailable): % and price are "
                    "screener-reported and may be stale or split-skewed. ") + note
        if rb:
            note = f"Reddit buzz: {rb['mentions']} mention(s) in hot posts right now. " + note
        if ks_ok:
            maybe_autotrade(client, conn, sym, price, score, vlabel, held, cfg,
                            verified=verified, is_ipo=is_ipo)
        db.record_alert(conn, symbol=sym, kind="gainer", pct=pct, price_cents=int(price * 100),
                        headline=headline, url=url, note=note, score=score,
                        verdict=vlabel, catalyst_type=ctype, why=why)
        alerts.append({"symbol": sym, "pct": pct, "price": price, "score": score,
                       "verdict": vlabel, "catalyst_type": ctype, "why": why})

        tag = f" [{score} {vlabel}]" if score is not None else ""
        print(f"[RADAR] {sym} +{pct:.1f}% @ ${price:,.2f}{tag}"
              + (f"  {why or headline}" if (why or headline) else ""))

        # Discord: with the LLM on, only push curated (high-score) catalysts, not
        # every mover. With the LLM off, push all (rules-only behaviour).
        push = (score is None) or (vlabel == "signal" and score >= cfg.RADAR_LLM_MIN_SCORE)
        if cfg.RADAR_DISCORD_WEBHOOK and push:
            msg = f"**{sym}** +{pct:.1f}% @ ${price:,.2f}"
            if score is not None:
                msg += f"  (score {score} - {ctype or vlabel})"
            if why:
                msg += f"\n{why}"
            msg += f"\n{note}"
            if url:
                msg += f"\n{url}"
            _post_discord(cfg.RADAR_DISCORD_WEBHOOK, msg)

    alerts.sort(key=lambda a: (a["score"] if a["score"] is not None else -1), reverse=True)
    return alerts


def main():
    argparse.ArgumentParser(description="Catalyst radar (awareness only).").parse_args()
    conn = db.connect()
    db.init_db(conn)
    client = AlpacaClient()
    print(config.env_banner())
    alerts = scan(client, conn)
    print(f"radar: {len(alerts)} new alert(s) (min move {config.RADAR_MIN_MOVE_PCT:.0f}%).")


if __name__ == "__main__":
    main()
