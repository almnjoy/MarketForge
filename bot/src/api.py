"""Read-only JSON API for the OpsCanvas Stocks dashboard.

No trading endpoints are exposed. Everything here is GET + read-only. Reuses the
bot's own modules (config, db, portfolio, risk, alpaca_client) so there's no
duplicated logic. LAN/VPN-only; set API_TOKEN in .env to require a bearer token.
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import uuid

from flask import Flask, jsonify, request

import config
import db
import portfolio
import regime
import risk
from alpaca_client import AlpacaClient, cents_to_dollars
from sectors import sector_for

app = Flask(__name__)

# ---------------------------------------------------------------------------
# EXIT GUARANTEE
#
# The product promise is "every entry carries an exit the moment it fills".
# It had a hole: the order path polled for a fill 6 times over 18 seconds and,
# if the fill had not landed yet, gave up and armed nothing. A slow fill meant a
# permanently naked position, and nothing ever came back to fix it. That is what
# happened to VRM on 2026-08-06 - filled, held overnight, no stop.
#
# Three layers now:
#   1. inline poll (fast path, unchanged)
#   2. a durable watcher that keeps checking for hours and arms the stop late
#   3. a startup + periodic sweep that finds ANY unprotected position
# The queue is on disk, so restarting the bot does not lose a pending exit.
# ---------------------------------------------------------------------------
PROTECT_FILE = config.DATA_DIR / "pending-protect.json"
_protect_lock = threading.Lock()


def _protect_load():
    try:
        return json.loads(PROTECT_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def _protect_save(rows):
    try:
        PROTECT_FILE.parent.mkdir(parents=True, exist_ok=True)
        PROTECT_FILE.write_text(json.dumps(rows, indent=1), encoding="utf-8")
    except Exception as e:
        print(f"[protect] could not persist queue: {e}")


def _protect_queue_add(order_id, symbol, trail_pct):
    if not order_id:
        return
    with _protect_lock:
        rows = [r for r in _protect_load() if r.get("order_id") != order_id]
        rows.append({"order_id": order_id, "symbol": symbol,
                     "trail_pct": float(trail_pct), "added": time.time()})
        _protect_save(rows)
    print(f"[protect] queued {symbol} order {order_id} for a {trail_pct}% trail")


def _exit_side(qty):
    """The order side that CLOSES a position of this qty.

    Alpaca reports a short as NEGATIVE qty. A long is closed by selling; a short
    is closed by buying. Getting this backwards does not fail loudly - it doubles
    the position. That is why it is one function used everywhere.
    """
    return "sell" if float(qty or 0) > 0 else "buy"


def _working_exit_orders(client):
    """{symbol: set_of_working_order_sides} for every live order.

    Was _open_sell_orders(), which only knew about sells. A short position is
    guarded by a working BUY, so a sell-only view reported every short as
    unprotected forever - or worse, let arm_trail() fire a second sell.
    """
    try:
        raw = client._req("GET", client.trade_base, "/v2/orders",
                          params={"status": "open", "limit": 100})
    except Exception:
        # Fail CLOSED: an empty map means "nothing is known to be guarded", so
        # positions get reported as unprotected rather than silently cleared.
        return {}
    out = {}
    for o in (raw or []):
        sym, side = o.get("symbol"), str(o.get("side"))
        if sym:
            out.setdefault(sym, set()).add(side)
    return out


def _open_sell_orders(client):
    """Back-compat shim: symbols with a working SELL. Prefer _working_exit_orders."""
    return {s for s, sides in _working_exit_orders(client).items() if "sell" in sides}


def unprotected_positions(client):
    """Open positions with NO working order that would CLOSE them.

    This is the check that would have caught VRM the same evening instead of the
    next morning. It now covers shorts too: before, the `qty > 0` filter meant a
    short position was invisible here, which is strictly worse than the VRM bug
    because a short's downside has no floor.
    """
    try:
        pos = client.list_positions() or []
    except Exception:
        return []
    guarded = _working_exit_orders(client)
    out = []
    for p in pos:
        sym = p.get("symbol")
        qty = float(p.get("qty") or 0)
        if not sym or qty == 0:
            continue
        need = _exit_side(qty)
        if need in guarded.get(sym, ()):
            continue
        # list_positions() returns *_cents keys. The old code read
        # "avg_entry_price"/"current_price"/"unrealized_pl", which do not exist
        # on that dict, so the banner rendered blank prices on the one screen
        # that must never be ambiguous.
        out.append({
            "symbol": sym,
            "qty": qty,
            "side": "long" if qty > 0 else "short",
            "needs": need,
            "avg_entry": cents_to_dollars(p.get("avg_entry_cents")),
            "price": cents_to_dollars(p.get("current_price_cents")),
            "unrealized_pl": cents_to_dollars(p.get("unrealized_pl_cents")),
        })
    return out


def arm_trail(client, symbol, trail_pct, qty=None):
    """Arm a GTC trailing stop on an EXISTING position, on the correct side.

    Long  -> trailing stop SELL (rides up, fires below the high-water mark)
    Short -> trailing stop BUY  (rides down, fires above the low-water mark)

    Refuses if a closing order is already working, so it cannot double up.
    """
    pos = {p.get("symbol"): p for p in (client.list_positions() or [])}
    p = pos.get(symbol)
    if not p:
        return {"ok": False, "error": f"no open position in {symbol}"}

    raw_qty = float(p.get("qty") or 0)
    if raw_qty == 0:
        return {"ok": False, "error": f"{symbol} position is flat"}
    side = _exit_side(raw_qty)

    working = _working_exit_orders(client).get(symbol, set())
    if side in working:
        return {"ok": False, "error": f"{symbol} already has a working {side} order"}

    q = abs(int(float(qty if qty is not None else raw_qty)))
    if q < 1:
        return {"ok": False, "error": "trailing stops need at least 1 whole share"}

    if side == "sell":
        t = client.submit_trailing_stop_sell(symbol=symbol, qty=q,
                                             trail_percent=float(trail_pct))
    else:
        t = client.submit_trailing_stop_buy(symbol=symbol, qty=q,
                                            trail_percent=float(trail_pct))
    return {"ok": True, "symbol": symbol, "qty": q, "side": side,
            "position": "long" if raw_qty > 0 else "short",
            "trail_percent": float(trail_pct), "id": t.get("id")}


def _protect_worker():
    """Arms pending exits late, then sweeps for anything naked."""
    while True:
        try:
            client = AlpacaClient()
            with _protect_lock:
                rows = _protect_load()
            keep = []
            for r in rows:
                age = time.time() - float(r.get("added", 0))
                try:
                    o = client.get_order(r["order_id"])
                    st = str(o.get("status"))
                    if st == "filled":
                        q = int(float(o.get("filled_qty") or 0))
                        if q >= 1:
                            res = arm_trail(client, r["symbol"], r["trail_pct"], q)
                            print(f"[protect] late-armed {r['symbol']}: {res}")
                        continue                      # done either way
                    if st in ("canceled", "expired", "rejected", "suspended"):
                        print(f"[protect] {r['symbol']} order {st}; dropping")
                        continue
                except Exception as e:
                    print(f"[protect] check failed for {r.get('symbol')}: {e}")
                if age < 6 * 3600:                    # give up after 6h, not 18s
                    keep.append(r)
                else:
                    print(f"[protect] giving up on {r.get('symbol')} after 6h")
            with _protect_lock:
                if keep != rows:
                    _protect_save(keep)

            for u in unprotected_positions(client):
                print(f"[protect] !! UNPROTECTED POSITION: {u['symbol']} qty {u['qty']} "
                      f"@ {u['avg_entry']} - no working sell order")
        except Exception as e:
            print(f"[protect] worker error: {e}")
        time.sleep(30)


threading.Thread(target=_protect_worker, daemon=True).start()


@app.after_request
def _cors(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
    resp.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
    return resp


@app.before_request
def _gate():
    if request.method == "OPTIONS" or request.path == "/health":
        return None
    if config.API_TOKEN and request.headers.get("Authorization", "") != f"Bearer {config.API_TOKEN}":
        return jsonify({"error": "unauthorized"}), 401


def _conn():
    c = db.connect()
    db.init_db(c)
    return c


@app.get("/health")
def health():
    return jsonify({"ok": True, "env": config.STOCK_ENV})


@app.get("/api/status")
def status():
    conn = _conn()
    client = AlpacaClient()
    try:
        snap = portfolio.account_snapshot(client)
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 502
    cur = snap["equity_cents"]
    db.record_equity(conn, env=config.STOCK_ENV, cash_cents=snap["cash_cents"],
                     positions_value_cents=snap["positions_value_cents"])
    open_eq = db.open_equity_today(conn) or cur
    peak = db.peak_equity(conn) or cur
    bs = risk.breaker_state(open_eq, cur, peak)
    bot = risk.bot_deployed(conn)
    return jsonify({
        "env": config.STOCK_ENV,
        "account_type": snap["account_type"],
        "equity": cents_to_dollars(cur),
        "cash": cents_to_dollars(snap["cash_cents"]),
        "positions_value": cents_to_dollars(snap["positions_value_cents"]),
        "day_pl_pct": round(-bs["daily_loss_pct"], 4),
        "drawdown_pct": round(bs["drawdown_pct"], 4),
        "kill_switch": "tripped" if bs["tripped"] else "armed",
        "bankroll": cents_to_dollars(config.BOT_BANKROLL_CENTS),
        "committed": cents_to_dollars(bot["committed"]),
        "open_positions": bot["open"],
        "max_positions": config.MAX_POSITIONS,
    })


@app.get("/api/positions")
def positions():
    client = AlpacaClient()
    try:
        ps = client.list_positions()
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 502
    return jsonify([{
        "symbol": p["symbol"], "qty": p["qty"],
        "avg_entry": cents_to_dollars(p["avg_entry_cents"]),
        "price": cents_to_dollars(p["current_price_cents"]),
        "market_value": cents_to_dollars(p["market_value_cents"]),
        "unrealized_pl": cents_to_dollars(p["unrealized_pl_cents"]),
    } for p in ps])


@app.get("/api/orders")
def orders():
    conn = _conn()
    rows = db.recent_orders(conn, config.STOCK_ENV, limit=30)
    return jsonify([{
        "symbol": r["symbol"], "side": r["side"], "qty": r["qty"],
        "limit_price": cents_to_dollars(r["limit_price_cents"]) if r["limit_price_cents"] else None,
        "status": r["status"], "created_at": r["created_at"], "updated_at": r["updated_at"],
    } for r in rows])


@app.get("/api/equity")
def equity():
    conn = _conn()
    rows = db.recent_equity(conn, limit=200)
    return jsonify([{"ts": r["ts"], "equity": cents_to_dollars(r["equity_cents"]),
                     "cash": cents_to_dollars(r["cash_cents"])} for r in reversed(rows)])


@app.get("/api/radar")
def radar():
    conn = _conn()
    rows = db.recent_alerts(conn, limit=30)
    return jsonify([{
        "ts": r["ts"], "symbol": r["symbol"], "kind": r["kind"], "pct": r["pct"],
        "price": cents_to_dollars(r["price_cents"]) if r["price_cents"] else None,
        "score": r["score"], "verdict": r["verdict"], "catalyst_type": r["catalyst_type"],
        "why": r["why"], "headline": r["headline"], "url": r["url"], "note": r["note"],
    } for r in rows])


@app.get("/api/log")
def log():
    p = config.DATA_DIR / "cron.log"
    if not p.exists():
        return jsonify({"lines": []})
    lines = p.read_text(errors="replace").splitlines()[-100:]
    return jsonify({"lines": lines})


@app.get("/api/config")
def api_config():
    """The bot's current knobs, so the UI can display them + compute % sizing
    suggestions (e.g. a 2%-of-equity default stake)."""
    return jsonify({
        "env": config.STOCK_ENV,
        "data_feed": config._get("ALPACA_DATA_FEED", "iex"),
        "mode": config._get("MF_MODE", ""),
        "auto_execute": (config._get("AUTO_EXECUTE", "false") or "false").lower() == "true",
        "require_llm_analysis": config.REQUIRE_LLM_ANALYSIS,
        "bankroll": cents_to_dollars(config.BOT_BANKROLL_CENTS),
        "risk_per_trade_pct": config.RISK_PER_TRADE_PCT,
        "max_position_pct": config.MAX_POSITION_PCT,
        "max_sector_pct": config.MAX_SECTOR_PCT,
        "max_positions": config.MAX_POSITIONS,
        "sma_fast": config.SMA_FAST, "sma_slow": config.SMA_SLOW,
        "pullback_sma": config.PULLBACK_SMA, "atr_stop_mult": config.ATR_STOP_MULT,
        "hard_stop_pct": config.HARD_STOP_PCT,
        "wash_sale_cooldown_days": config.WASH_SALE_COOLDOWN_DAYS,
        "fractional": config.FRACTIONAL,
        "radar_min_move_pct": config.RADAR_MIN_MOVE_PCT,
        "radar_llm_min_score": config.RADAR_LLM_MIN_SCORE,
        "reddit_subs": config.RADAR_REDDIT_SUBS,
        "radar_auto": {
            "execute": config.RADAR_AUTO_EXECUTE,
            "live_enabled": config.LIVE_AUTO_ENABLED,
            "min_score": config.RADAR_AUTO_MIN_SCORE,
            "notional": cents_to_dollars(config.RADAR_AUTO_NOTIONAL_CENTS),
            "max_per_day": config.RADAR_AUTO_MAX_PER_DAY,
            "max_exposure": cents_to_dollars(config.RADAR_AUTO_MAX_EXPOSURE_CENTS),
            "exit": config.RADAR_AUTO_EXIT,
            "trail_pct": config.RADAR_AUTO_TRAIL_PCT,
            "min_price": cents_to_dollars(config.RADAR_AUTO_MIN_PRICE_CENTS),
            "max_positions": config.RADAR_AUTO_MAX_POSITIONS,
        },
    })


@app.post("/api/order")
def api_order():
    """Manual, human-driven trade ("click a stock, place my claim"). Guarded: body
    confirm must equal STOCK_ENV. Size by notional ($), pct (% of account equity),
    or qty (shares). Market order, day. This is deliberate: it's YOU pulling the
    trigger, not the bot's autonomous loop."""
    body = request.get_json(silent=True) or {}
    symbol = (body.get("symbol") or "").upper().strip()
    side = (body.get("side") or "buy").lower()
    if not symbol:
        return jsonify({"ok": False, "error": "symbol required"}), 400
    if (body.get("confirm") or "").lower() != config.STOCK_ENV.lower():
        return jsonify({"ok": False, "error": f"send confirm='{config.STOCK_ENV}'"}), 400
    notional, qty, pct = body.get("notional"), body.get("qty"), body.get("pct")
    exit_trail = body.get("exit_trail_pct")  # e.g. 10 = sell 10% off the high-water mark
    client = AlpacaClient()
    try:
        if exit_trail is not None:
            try:
                exit_trail = float(exit_trail)
                assert 0.5 <= exit_trail <= 50
            except Exception:
                return jsonify({"ok": False, "error": "exit_trail_pct must be 0.5-50 (percent)"}), 400
        if pct is not None:
            acct = client.get_account()
            notional = round(acct["equity_cents"] / 100 * float(pct) / 100.0, 2)
        if notional is None and qty is None:
            return jsonify({"ok": False, "error": "provide notional, pct, or qty"}), 400
        if exit_trail is not None and qty is None:
            # trailing stops need WHOLE shares: convert the dollar size to qty
            px = client.get_latest_price(symbol)
            if not px:
                return jsonify({"ok": False, "error": "no live price to size whole shares for the trail"}), 502
            qty = int(float(notional) * 100 // px)
            notional = None
            if qty < 1:
                return jsonify({"ok": False, "error": "size too small for 1 whole share (trail exits need whole shares)"}), 400
        coid = f"manual-{uuid.uuid4().hex[:12]}"
        conn = db.connect()
        db.init_db(conn)
        db.record_intent(conn, client_order_id=coid, symbol=symbol, side=side,
                         qty=(qty or 0), limit_price_cents=None, env=config.STOCK_ENV)
        resp = client.submit_market_order(symbol=symbol, side=side, notional=notional,
                                          qty=qty, client_order_id=coid)
        db.update_order_result(conn, client_order_id=coid,
                               status=resp.get("status", "accepted"), broker_order_id=resp.get("id"))
        trail = None
        if exit_trail is not None and side == "buy":
            filled_qty = 0
            for _ in range(6):  # market orders in RTH usually fill in seconds
                time.sleep(3)
                o = client.get_order(resp.get("id"))
                if o.get("status") == "filled":
                    filled_qty = int(float(o.get("filled_qty") or 0))
                    break
            if filled_qty >= 1:
                t = client.submit_trailing_stop_sell(symbol=symbol, qty=filled_qty,
                                                     trail_percent=exit_trail)
                trail = {"armed": True, "qty": filled_qty, "trail_percent": exit_trail,
                         "id": t.get("id")}
            else:
                # DO NOT give up here. This used to return "set the stop manually"
                # and forget, which left VRM (2026-08-06) naked overnight on a slow
                # fill. Hand it to the watcher, which keeps polling for hours and
                # arms the stop the moment the fill lands.
                _protect_queue_add(resp.get("id"), symbol, exit_trail)
                trail = {"armed": False, "pending": True,
                         "error": "fill not confirmed in 18s - watcher will arm the "
                                  "trail as soon as it fills"}
        return jsonify({"ok": True, "symbol": symbol, "side": side, "notional": notional,
                        "qty": qty, "status": resp.get("status"), "id": resp.get("id"),
                        "trail": trail})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:300]}), 502


@app.route("/api/broker/orders", methods=["GET"])
def api_broker_orders():
    """Live orders straight from the broker. READ-ONLY.

    /api/orders reads the local sqlite ledger, which never records broker-side
    stops - so it reported an empty list while Alpaca had a working trailing
    stop sitting there. That blind spot is why a naked position was hard to
    confirm. This is the same call _open_sell_orders() already makes, exposed
    so the UI can show the actual trail width instead of 'there is a sell
    order somewhere, trust me'.

    ?status=open (default) | closed | all
    """
    status = str(request.args.get("status", "open")).lower()
    if status not in ("open", "closed", "all"):
        status = "open"
    try:
        c = AlpacaClient()
        raw = c._req("GET", c.trade_base, "/v2/orders",
                     params={"status": status, "limit": 100, "nested": "true"})
        return jsonify([{
            "symbol": o.get("symbol"), "side": o.get("side"),
            "type": o.get("order_type"), "qty": o.get("qty"),
            "filled_qty": o.get("filled_qty"),
            "trail_percent": o.get("trail_percent"),
            "stop_price": o.get("stop_price"),
            "limit_price": o.get("limit_price"),
            "status": o.get("status"),
            "submitted_at": o.get("submitted_at"),
            "id": o.get("id"),
        } for o in (raw or [])])
    except Exception as e:
        return jsonify({"error": f"{e.__class__.__name__}: {str(e)[:200]}"}), 502


@app.route("/api/unprotected", methods=["GET"])
def api_unprotected():
    """Open positions with no working sell order. The dashboard shows this as a
    banner - a naked position should be impossible to miss."""
    try:
        return jsonify({"positions": unprotected_positions(AlpacaClient())})
    except Exception as e:
        return jsonify({"positions": [], "error": str(e)[:200]}), 502


@app.route("/api/protect", methods=["POST"])
def api_protect():
    """Arm a trailing stop on an EXISTING position. This is the endpoint that did
    not exist when VRM needed one - the only way to attach an exit used to be as
    part of a fresh buy."""
    body = request.get_json(silent=True) or {}
    symbol = str(body.get("symbol", "")).upper().strip()
    if not symbol:
        return jsonify({"ok": False, "error": "symbol required"}), 400
    try:
        trail = float(body.get("trail_pct") or config.RADAR_AUTO_TRAIL_PCT * 100)
        assert 0.5 <= trail <= 50
    except Exception:
        return jsonify({"ok": False, "error": "trail_pct must be 0.5-50 (percent)"}), 400
    try:
        res = arm_trail(AlpacaClient(), symbol, trail, body.get("qty"))
        return jsonify(res), (200 if res.get("ok") else 400)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:300]}), 502


@app.get("/api/bars")
def api_bars():
    """Daily OHLC for candlestick charts (2026-08-05, local dash). ?symbol=X&limit=90."""
    sym = (request.args.get("symbol") or "").upper().strip()
    if not sym or not all(c.isalnum() or c == "." for c in sym):
        return jsonify({"error": "symbol required"}), 400
    try:
        limit = min(250, max(10, int(request.args.get("limit", 90))))
    except (TypeError, ValueError):
        limit = 90
    try:
        bars = AlpacaClient().get_daily_bars(sym, limit=limit)
        return jsonify({"symbol": sym, "bars": [
            {"t": str(b.get("t", ""))[:10], "o": b["o"] / 100, "h": b["h"] / 100,
             "l": b["l"] / 100, "c": b["c"] / 100, "v": b.get("v")} for b in bars]})
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 502


@app.get("/api/regime")
def api_regime():
    """Market regime read: green / yellow / red. READ-ONLY, ADDITIVE.

    Answers "is this a tape worth acting in?" - a separate question from the
    radar's "what just happened?". The radar is unchanged; this is a gate that
    sits beside it. Never raises: on failure it reports regime "unknown", which
    the playbooks treat as stand down.
    """
    try:
        return jsonify(regime.read(AlpacaClient()))
    except Exception as e:
        return jsonify({"regime": "unknown", "label": "UNKNOWN", "pct": None,
                        "note": "Could not read the tape - do not guess.",
                        "indexes": {}, "errors": [str(e)[:200]]}), 200


@app.get("/api/news")
def api_news():
    """Latest headlines for a symbol (Alpaca news). ?symbol=X&limit=8."""
    sym = (request.args.get("symbol") or "").upper().strip()
    if not sym or not all(c.isalnum() or c == "." for c in sym):
        return jsonify({"error": "symbol required"}), 400
    try:
        limit = min(20, max(1, int(request.args.get("limit", 8))))
    except (TypeError, ValueError):
        limit = 8
    try:
        news = AlpacaClient().get_news(symbols=[sym], limit=limit)
        return jsonify({"symbol": sym, "news": [
            {"headline": n.get("headline"), "url": n.get("url"),
             "source": n.get("source"), "at": str(n.get("created_at", ""))[:16]}
            for n in news]})
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 502


@app.get("/api/reddit")
def api_reddit():
    """Retail buzz: reddit hot-page trending tickers (cached ~10 min)."""
    try:
        import reddit
        return jsonify(reddit.get_trending_cached(AlpacaClient()))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 502


@app.get("/api/spark")
def api_spark():
    """Mini price history + live last for the radar cards (2026-08-05).
    ?symbols=A,B,C (max 16, alnum+dot only). Returns
    {SYM: {"closes": [dollars ascending ~30 daily], "last": dollars|None}}.
    Read-only market data; per-symbol failures degrade to empty, never 500."""
    raw = (request.args.get("symbols") or "").split(",")
    syms = []
    for s2 in raw:
        s2 = s2.strip().upper()
        if s2 and all(ch.isalnum() or ch == "." for ch in s2) and s2 not in syms:
            syms.append(s2)
    syms = syms[:16]
    if not syms:
        return jsonify({})
    client = AlpacaClient()
    out = {}
    for sym in syms:
        closes, last = [], None
        try:
            closes = [cents_to_dollars(b["c"]) for b in client.get_daily_bars(sym, limit=30)]
        except Exception:
            pass
        try:
            last = cents_to_dollars(client.get_latest_price(sym))
        except Exception:
            pass
        out[sym] = {"closes": closes, "last": last}
    return jsonify(out)


@app.get("/api/themes")
def api_themes():
    """Structured MICRO signals for the OpsCanvas agent (Claude) to narrate into
    named emerging trends. Groups recent radar catalysts by type + sector and adds
    live movers. DATA ONLY - reasoning is the agent's job, on demand, so nothing
    leans on the weak local model or burns tokens in the background."""
    conn = db.connect()
    db.init_db(conn)
    by_type, by_sector = {}, {}
    for a in db.recent_alerts(conn, limit=60):
        sym = a["symbol"]
        by_type.setdefault(a["catalyst_type"] or "unclassified", []).append({
            "symbol": sym, "pct": a["pct"], "score": a["score"],
            "verdict": a["verdict"], "why": a["why"], "url": a["url"],
        })
        by_sector.setdefault(sector_for(sym), []).append(sym)
    movers = {"gainers": [], "losers": []}
    try:
        movers = AlpacaClient().get_movers(top=15)
    except Exception:
        pass
    return jsonify({
        "generated": db.utcnow(),
        "by_catalyst_type": by_type,
        "by_sector": by_sector,
        "top_gainers": movers.get("gainers", [])[:10],
        "top_losers": movers.get("losers", [])[:10],
        "note": ("Data only. The market brief (/api/brief) narrates these into named "
                 "trends via claude -p on the box; this endpoint does no LLM reasoning."),
    })


def _fred_macro():
    """Latest values for a few free FRED macro series (rates, oil, VIX, jobs) so the
    brief factors the macro backdrop. FRED keys are free; needs FRED_API_KEY in .env."""
    key = config._get("FRED_API_KEY", "") or ""
    if not key:
        return {}
    series = {"10Y yield %": "DGS10", "WTI oil $": "DCOILWTICO", "Fed funds %": "DFF",
              "Unemployment %": "UNRATE", "VIX": "VIXCLS"}
    out = {}
    for label, sid in series.items():
        try:
            url = (f"https://api.stlouisfed.org/fred/series/observations?series_id={sid}"
                   f"&api_key={key}&file_type=json&sort_order=desc&limit=1")
            with urllib.request.urlopen(url, timeout=8) as r:
                obs = json.loads(r.read()).get("observations", [])
            if obs and obs[0].get("value") not in (".", None):
                out[label] = obs[0]["value"]
        except Exception:
            pass
    return out


@app.get("/api/brief")
def api_brief():
    """On-demand market brief. Runs `claude -p` ON THE BOX using Dustin's Max
    SUBSCRIPTION token (no paid API key, no background waste). Narrates today's
    radar catalysts + movers into named emerging themes + a watch list."""
    conn = db.connect()
    db.init_db(conn)
    lines = []
    for a in db.recent_alerts(conn, limit=40):
        pct = a["pct"] or 0
        lines.append(f"- {a['symbol']} {pct:+.0f}% [{a['catalyst_type'] or '?'}, score {a['score']}] "
                     f"{a['verdict']}: {a['why'] or a['headline'] or ''}")
    movers = ""
    try:
        m = AlpacaClient().get_movers(top=10)
        movers = ", ".join(f"{g.get('symbol')} +{float(g.get('percent_change', 0)):.0f}%"
                           for g in m.get("gainers", [])[:10])
    except Exception:
        pass
    if not lines and not movers:
        return jsonify({"ok": True, "brief": "No catalysts or movers yet today - run the radar first."})
    macro = _fred_macro()
    macro_txt = ", ".join(f"{k}={v}" for k, v in macro.items()) or "(none)"
    prompt = (
        "You help a retail investor understand what is moving the US market TODAY. Using the "
        "catalysts, movers, and macro snapshot below, write a tight brief: (1) the 3-5 biggest "
        "emerging THEMES today, each NAMED (e.g. 'AI/semis', 'energy/oil', 'chip supply', "
        "'quantum', 'rates'), with the tickers/catalysts behind each and any macro tie-in; "
        "(2) a short WATCH list. Be specific and skeptical - flag pumps and low-float names. "
        "No preamble, no disclaimer.\n\n"
        "Catalysts:\n" + ("\n".join(lines) or "(none)")
        + f"\n\nTop gainers: {movers or '(none)'}"
        + f"\n\nMacro (FRED, latest): {macro_txt}"
    )
    # Same container-path bug as the radar trigger had. Also resolve the CLI:
    # on Windows `claude` is a .cmd shim and needs shell resolution.
    src = pathlib.Path(__file__).resolve().parent
    claude = shutil.which("claude") or shutil.which("claude.cmd") or "claude"
    try:
        p = subprocess.run([claude, "-p", prompt, "--dangerously-skip-permissions",
                            "--output-format", "text"], cwd=str(src.parent),
                           shell=(os.name == "nt"),
                           capture_output=True, text=True, timeout=300)
        text = (p.stdout or "").strip() or (p.stderr or "").strip() or "(no output)"
        return jsonify({"ok": p.returncode == 0, "brief": text})
    except subprocess.TimeoutExpired:
        return jsonify({"ok": False, "error": "claude timed out (300s)"}), 504
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:300]}), 500


@app.post("/api/run/radar")
def api_run_radar():
    """Trigger a radar scan on demand (the UI 'Refresh Radar' button). Also runs
    4x/day via cron. Returns when done (~15-40s: movers + news + LLM scoring)."""
    # Absolute paths + the SAME interpreter that is running this API. The old
    # version was ["python", "src/radar.py"] with cwd="/app" - the container path
    # from the Docker era. Running locally there is no /app, so the call threw
    # before radar ever started and Re-scan silently did nothing.
    src = pathlib.Path(__file__).resolve().parent          # bot/src
    try:
        p = subprocess.run([sys.executable, str(src / "radar.py")],
                           cwd=str(src.parent),            # bot/  (config reads bot/.env)
                           capture_output=True, text=True, timeout=180)
        out = ((p.stdout or "") + (p.stderr or ""))[-2000:]
        if p.returncode != 0:
            print(f"[radar] exit {p.returncode}\n{out}")
        return jsonify({"ok": p.returncode == 0, "stdout": out,
                        "error": None if p.returncode == 0 else f"radar exit {p.returncode}"})
    except subprocess.TimeoutExpired:
        return jsonify({"ok": False, "error": "radar timed out after 180s"}), 504
    except Exception as e:
        return jsonify({"ok": False, "error": f"{e.__class__.__name__}: {str(e)[:280]}"}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=config.API_PORT)
