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
#
# 2026-08-11: all three layers were hardwired to ONE venue. Every protection
# path built a bare AlpacaClient(), which reads config.API_KEY_ID/TRADE_BASE and
# therefore *is* the live account whenever STOCK_ENV=live. The paper lane
# inherited none of it: no watcher, no sweep, no /protect. Paper entries armed a
# trail inline or not at all, and "not at all" is what happened, because the
# protective order was submitted while the entry was still working.
#
# The guarantee is now per-VENUE. Everything below takes a venue string, and the
# worker iterates every venue that is configured.
# ---------------------------------------------------------------------------
PROTECT_FILE = config.DATA_DIR / "pending-protect.json"
_protect_lock = threading.Lock()

LIVE_VENUE = config.STOCK_ENV        # whatever this process was started as


def _venue_client(venue):
    """An AlpacaClient for the named venue.

    The PROCESS venue always uses the plain client, exactly as before. Only the
    *extra* paper venue goes through paper.client().

    That asymmetry is deliberate. paper.client() enforces a PK key prefix, and
    routing the process's own venue through it would make a previously working
    STOCK_ENV=paper desk fail its sweep the moment a key did not match that
    shape. A hardening check on a new code path must not become a new failure
    mode on the old one.
    """
    if venue == LIVE_VENUE:
        return AlpacaClient()
    if venue == "paper":
        import paper
        return paper.client()
    raise RuntimeError(f"unknown venue {venue!r}")


def _venues():
    """Venues this process can protect, best-effort. The process venue always
    counts; paper is added when its keys check out (and is not duplicated when
    the process itself is paper)."""
    out = [LIVE_VENUE]
    if LIVE_VENUE != "paper":
        try:
            import paper
            if paper.configured()[0]:
                out.append("paper")
        except Exception:
            pass
    return out


def _protect_load():
    try:
        rows = json.loads(PROTECT_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []
    # Rows written before venues existed have no venue key. They were, by
    # definition, this process's venue.
    for r in rows:
        r.setdefault("venue", LIVE_VENUE)
    return rows


def _protect_save(rows):
    try:
        PROTECT_FILE.parent.mkdir(parents=True, exist_ok=True)
        PROTECT_FILE.write_text(json.dumps(rows, indent=1), encoding="utf-8")
    except Exception as e:
        print(f"[protect] could not persist queue: {e}")


def _protect_queue_add(order_id, symbol, trail_pct, venue=None):
    if not order_id:
        return
    venue = venue or LIVE_VENUE
    with _protect_lock:
        rows = [r for r in _protect_load() if r.get("order_id") != order_id]
        rows.append({"order_id": order_id, "symbol": symbol, "venue": venue,
                     "trail_pct": float(trail_pct), "added": time.time()})
        _protect_save(rows)
    print(f"[protect/{venue}] queued {symbol} order {order_id} for a {trail_pct}% trail")


def arm_after_fill(client, symbol, trail_pct, order_id, venue, poll=6, gap=3):
    """Wait for the entry to fill, THEN arm the exit. Hand off on timeout.

    THE BUG THIS EXISTS TO KILL (found on paper 2026-08-11): submitting the
    protective order immediately after the entry gets rejected by the broker,
    because the entry is still working. On a short entry Alpaca returns

        403 - cannot open a long buy while a short sell order is open

    since a buy while a sell is working reads as opening the opposite side. The
    entry then sits filled and naked. Same race that left VRM naked on live.

    So: poll for the fill first, and if it has not landed by the time we give
    up, queue it for the durable watcher instead of returning "arm it yourself".
    Never returns without the position being either armed or queued.
    """
    filled = 0
    for _ in range(poll):
        time.sleep(gap)
        try:
            o = client.get_order(order_id)
        except Exception:
            continue
        st = str(o.get("status"))
        if st == "filled":
            filled = int(float(o.get("filled_qty") or 0))
            break
        if st in ("canceled", "expired", "rejected", "suspended"):
            return {"armed": False, "error": f"entry order {st}; nothing to protect"}

    if filled >= 1:
        res = arm_trail(client, symbol, trail_pct, filled)
        if res.get("ok"):
            return {"armed": True, "qty": filled, "trail_percent": float(trail_pct),
                    "side": res.get("side"), "id": res.get("id")}
        # "already has a working <side> order" is arm_trail REFUSING TO DOUBLE UP,
        # which means the position is already guarded. Reporting that as a failure
        # and queueing it made the watcher retry a no-op every 30s forever and put
        # a scary "trail did not arm" on a position that was fine. Protected is
        # protected, whoever placed the order.
        if "already has a working" in str(res.get("error", "")):
            return {"armed": True, "qty": filled, "pre_existing": True,
                    "note": "a working exit order was already on this position"}
        # Anything else may be transient. Do NOT drop it.
        _protect_queue_add(order_id, symbol, trail_pct, venue)
        return {"armed": False, "pending": True, "error": res.get("error"),
                "note": "handed to the watcher"}

    _protect_queue_add(order_id, symbol, trail_pct, venue)
    return {"armed": False, "pending": True,
            "error": f"fill not confirmed in {poll * gap}s - the watcher will arm "
                     f"the trail as soon as it fills"}


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


def _scoring_state():
    """What is ACTUALLY doing catalyst triage right now.

    `auto` resolves at call time (agent CLI if one is on PATH, else the HTTP
    endpoint), so this has to ask the same question llm.classify() asks rather
    than report the configured string and hope.
    """
    try:
        import llm
        agent_bin, agent_kind = llm._agent_bin()
    except Exception:
        agent_bin, agent_kind = None, None
    provider = getattr(config, "RADAR_LLM_PROVIDER", "auto")
    if not config.RADAR_USE_LLM:
        effective, runtime, model, where = "off", "scoring disabled", "rules-only (no LLM)", ""
    elif provider in ("auto", "agent") and agent_bin:
        effective = "agent"
        runtime = f"{agent_kind} CLI (local)" if agent_kind else "coding-agent CLI (local)"
        model, where = config.RADAR_AGENT_MODEL, agent_bin
    elif provider == "agent":
        effective, runtime = "unavailable", "coding-agent CLI (NOT FOUND on PATH)"
        model, where = config.RADAR_AGENT_MODEL, ""
    elif config.RADAR_LLM_BASE_URL:
        effective, runtime = "endpoint", "OpenAI-compatible endpoint"
        model, where = config.RADAR_LLM_MODEL, config.RADAR_LLM_BASE_URL
    else:
        effective, runtime = "unavailable", "no scorer configured"
        model, where = "rules-only (no LLM)", ""
    return {"enabled": bool(config.RADAR_USE_LLM), "provider": provider,
            "effective": effective, "runtime": runtime, "model": model, "where": where,
            "agent_kind": agent_kind, "min_score": config.RADAR_LLM_MIN_SCORE}


_sweep_attempts: dict = {}   # (venue, symbol) -> failed arm attempts
_sweep_seen: set = set()     # (venue, symbol) already announced; cleared when it clears


def _protect_worker():
    """Arms pending exits late, then sweeps for anything naked. EVERY venue."""
    while True:
        for venue in _venues():
            try:
                _protect_pass(venue)
            except Exception as e:
                print(f"[protect/{venue}] worker error: {e}")
        time.sleep(30)


def _protect_pass(venue):
    """One watcher + sweep pass for a single venue."""
    client = _venue_client(venue)
    if True:
        try:
            with _protect_lock:
                rows = _protect_load()
            keep = []
            for r in rows:
                if r.get("venue", LIVE_VENUE) != venue:
                    keep.append(r)          # another venue's row, leave it alone
                    continue
                age = time.time() - float(r.get("added", 0))
                try:
                    o = client.get_order(r["order_id"])
                    st = str(o.get("status"))
                    if st == "filled":
                        q = int(float(o.get("filled_qty") or 0))
                        if q >= 1:
                            res = arm_trail(client, r["symbol"], r["trail_pct"], q)
                            print(f"[protect/{venue}] late-armed {r['symbol']}: {res}")
                        continue                      # done either way
                    if st in ("canceled", "expired", "rejected", "suspended"):
                        print(f"[protect/{venue}] {r['symbol']} order {st}; dropping")
                        continue
                except Exception as e:
                    print(f"[protect/{venue}] check failed for {r.get('symbol')}: {e}")
                if age < 6 * 3600:                    # give up after 6h, not 18s
                    keep.append(r)
                else:
                    print(f"[protect/{venue}] giving up on {r.get('symbol')} after 6h")
            with _protect_lock:
                if keep != rows:
                    _protect_save(keep)

            # The sweep. This used to only print, which found the problem and then
            # did nothing about it. Now it arms, because the one thing this app
            # promises is that a position is never left without an exit.
            naked = unprotected_positions(client)
            open_now = {u["symbol"] for u in naked}
            # Counters are keyed by (venue, symbol). Keying by symbol alone let a
            # live position and a paper position in the same ticker share one
            # attempt budget and one "already announced" flag.
            for gone in [k for k in list(_sweep_seen)
                         if k[0] == venue and k[1] not in open_now]:
                _sweep_seen.discard(gone)        # protected or closed; report it again if it returns
                _sweep_attempts.pop(gone, None)
            for u in naked:
                sym = u["symbol"]
                key = (venue, sym)
                qty = abs(float(u.get("qty") or 0))
                if key not in _sweep_seen:
                    # Say it ONCE per episode. Reprinting five symbols every 30s
                    # buries the one line that matters under its own noise.
                    _sweep_seen.add(key)
                    print(f"[protect/{venue}] !! UNPROTECTED POSITION: {sym} qty {u['qty']} "
                          f"@ {u['avg_entry']} - no working exit order")
                if not config.SWEEP_AUTO_ARM:
                    continue
                if _sweep_attempts.get(key, 0) >= config.SWEEP_MAX_ATTEMPTS:
                    continue                      # already tried enough, stay quiet
                if qty < 1:
                    # Alpaca cannot attach a trailing stop to a fractional
                    # position. That is a permanent property of the position, not
                    # a transient failure, so retrying it forever is pointless.
                    _sweep_attempts[key] = config.SWEEP_MAX_ATTEMPTS
                    print(f"[protect/{venue}] {sym} is FRACTIONAL ({qty:g} shares) - a trailing "
                          f"stop needs at least 1 whole share, so this position cannot "
                          f"be auto-protected. Close it by hand, or size in whole shares.")
                    continue
                try:
                    res = arm_trail(client, sym, config.SWEEP_TRAIL_PCT)
                except Exception as e:
                    res = {"ok": False, "error": f"{e.__class__.__name__}: {e}"}
                # arm_trail RETURNS a status dict, it does not raise. Treating a
                # missing exception as success cleared the attempt counter every
                # pass, so the cap never engaged and the sweep retried forever.
                if res.get("ok"):
                    _sweep_attempts.pop(key, None)
                    print(f"[protect/{venue}] sweep-armed {sym} at {config.SWEEP_TRAIL_PCT}%")
                else:
                    n = _sweep_attempts.get(key, 0) + 1
                    _sweep_attempts[key] = n
                    print(f"[protect/{venue}] sweep could NOT arm {sym} "
                          f"(attempt {n}/{config.SWEEP_MAX_ATTEMPTS}): {res.get('error')}")
        except Exception as e:
            print(f"[protect/{venue}] pass error: {e}")


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
        # Everything the Scan settings form reads back. Without these the form
        # renders defaults instead of what the engine is actually running.
        "radar_scan_hours": sorted(config.RADAR_SCAN_HOURS),
        "radar_top_n": config.RADAR_TOP_N,
        "radar_min_price_cents": config.RADAR_MIN_PRICE_CENTS,
        "reddit_enabled": config.RADAR_REDDIT_ENABLED,
        "reddit_cache_secs": config.RADAR_REDDIT_CACHE_SECS,
        "radar_llm_min_score": config.RADAR_LLM_MIN_SCORE,
        # How movers actually get scored. This block did not exist, so the
        # dashboard's Admin lane had no keys to read and rendered "rules-only
        # (no LLM) / off" no matter what the engine was really doing.
        "radar_scoring": _scoring_state(),
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
        if exit_trail is not None:
            # Was inlined here and duplicated nowhere, which is how the paper
            # lane ended up shipping without it. One helper, both venues, and
            # it now covers side='sell' too (a short entry needs a buy to cover;
            # the old `side == "buy"` guard silently skipped protecting shorts).
            trail = arm_after_fill(client, symbol, exit_trail, resp.get("id"),
                                   venue=LIVE_VENUE)
        return jsonify({"ok": True, "symbol": symbol, "side": side, "notional": notional,
                        "qty": qty, "status": resp.get("status"), "id": resp.get("id"),
                        "trail": trail})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:300]}), 502


# --- The paper lane -------------------------------------------------------
# Always paper, regardless of STOCK_ENV. See paper.py for why this exists: the
# live account cannot short (under the $2,000 Reg T minimum), so without a paper
# destination every short setup the engine finds produces zero data.

@app.get("/api/paper/status")
def api_paper_status():
    """Is the paper account still linked? Proves it with a real account fetch."""
    import paper
    return jsonify(paper.check())


@app.get("/api/paper/overview")
def api_paper_overview():
    """Everything the PAPER page renders. READ-ONLY."""
    import paper
    r = paper.overview()
    return jsonify(r), (200 if r.get("ok") else 502)


@app.get("/api/paper/unprotected")
def api_paper_unprotected():
    """Paper positions with no working exit. Same check the live desk runs."""
    import paper
    try:
        return jsonify({"positions": unprotected_positions(paper.client())})
    except Exception as e:
        return jsonify({"positions": [], "error": str(e)[:200]}), 502


@app.post("/api/paper/protect")
def api_paper_protect():
    """Arm a trailing stop on an EXISTING paper position.

    The paper lane had no equivalent of /api/protect, so once an entry filled
    naked there was no way to fix it from anywhere - not the UI, not the copilot,
    not the sweep. This is the manual layer; the worker is the automatic one.
    """
    import paper
    body = request.get_json(silent=True) or {}
    symbol = (body.get("symbol") or "").upper().strip()
    if not symbol:
        return jsonify({"ok": False, "error": "symbol required"}), 400
    try:
        trail = float(body.get("trail_pct") or config.SWEEP_TRAIL_PCT)
    except Exception:
        return jsonify({"ok": False, "error": "trail_pct must be a number"}), 400
    try:
        res = arm_trail(paper.client(), symbol, trail, body.get("qty"))
        return jsonify(res), (200 if res.get("ok") else 400)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:300]}), 502


@app.post("/api/paper/order")
def api_paper_order():
    """Place a trade on PAPER, and guarantee its exit the same way live does.

    No confirm gate: the live endpoint demands confirm == STOCK_ENV because it
    spends real money. This one cannot - paper.py is hard-wired to the paper host
    and refuses a key that does not start with PK. Making the human confirm a
    fake-money order would train exactly the reflex we do not want on live.

    What it DOES share with live is the exit guarantee: entry first, poll for the
    fill, then arm. Never both in one breath.
    """
    import paper
    body = request.get_json(silent=True) or {}
    symbol = (body.get("symbol") or "").upper().strip()
    if not symbol:
        return jsonify({"ok": False, "error": "symbol required"}), 400

    notional = body.get("notional")
    cap = config.PAPER_MAX_NOTIONAL
    capped = None
    if cap and notional is not None and float(notional) > cap:
        # Opt-in only, and LOUD. Silently shrinking a paper order corrupts the
        # exact record the shadow book exists to build, and the operator finds
        # out weeks later when the P/L does not reconcile.
        capped = {"requested": float(notional), "capped_to": cap,
                  "why": "PAPER_MAX_NOTIONAL is set in bot/.env; set it to 0 to size freely"}
        print(f"[paper] !! SIZE CAPPED {symbol}: ${float(notional):.2f} requested, "
              f"${cap:.2f} placed (PAPER_MAX_NOTIONAL={cap})")
        notional = cap

    try:
        res = paper.place(symbol, body.get("side") or "buy",
                          notional=notional, qty=body.get("qty"),
                          trail_pct=body.get("exit_trail_pct"),
                          note=body.get("note") or "",
                          allow_repeat=bool(body.get("allow_repeat")))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:300]}), 502

    # A duplicate is a 409, not a 200 and not a 500. The caller retried; tell it
    # the truth without placing anything and without looking like a failure.
    if res.get("duplicate"):
        print(f"[paper] duplicate refused: {res.get('error')}")
        return jsonify(res), 409

    if capped:
        res["size_capped"] = capped

    trail_pct = body.get("exit_trail_pct")
    if trail_pct is not None and res.get("order_id"):
        res["trail"] = arm_after_fill(paper.client(), symbol, float(trail_pct),
                                      res["order_id"], venue="paper")
    elif trail_pct is None:
        # An entry with no requested exit is still the sweep's problem, and the
        # sweep now covers paper. Say so rather than implying it is protected.
        res["trail"] = {"armed": False,
                        "note": "no exit requested; the 30s sweep will flag it"}
    return jsonify(res)


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


@app.route("/api/shutdown-check", methods=["GET"])
def api_shutdown_check():
    """Is it safe to stop the desk right now?

    The exit guarantee is only guaranteed while this process is ALIVE. The queue
    survives a restart and re-arms on boot, but between the fill and the next
    boot the position sits at the broker with no stop on it. So closing the app
    while an entry is working is the one action that can still reproduce the
    original bug, and it deserves to be refused rather than warned about.
    """
    reasons = []
    try:
        with _protect_lock:
            pending = _protect_load()
    except Exception:
        pending = []
    for r in pending:
        reasons.append(f"{r.get('symbol')} is queued for a {r.get('trail_pct')}% trail "
                       f"and has not filled yet")
    working = []
    try:
        client = AlpacaClient()
        raw = client._req("GET", client.trade_base, "/v2/orders",
                          params={"status": "open", "limit": 100}) or []
        for o in raw:
            if str(o.get("side")) == "buy" and str(o.get("status")) in (
                    "new", "accepted", "partially_filled", "pending_new"):
                working.append({"symbol": o.get("symbol"), "status": o.get("status"),
                                "qty": o.get("qty"), "filled_qty": o.get("filled_qty")})
                reasons.append(f"{o.get('symbol')} has a working BUY ({o.get('status')}) "
                               f"that would fill with no stop behind it")
        for u in unprotected_positions(client):
            reasons.append(f"{u['symbol']} is open with NO working exit right now")
    except Exception as e:
        # Fail CLOSED. If we cannot see the broker we cannot promise it is safe.
        reasons.append(f"could not reach the broker to check ({str(e)[:80]})")
    return jsonify({"safe": not reasons, "reasons": reasons,
                    "pending": pending, "working_buys": working})


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
    """On-demand market brief. Runs `claude -p` ON THIS MACHINE using the operator's
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
