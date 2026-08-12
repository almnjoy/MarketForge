"""Real-time price tap. Closes the 15-minute REST blackout, for free.

The problem
-----------
On Alpaca's free Basic plan the historical/REST API **cannot return the latest
15 minutes**. That is not latency, it is the plan. Everything the desk does goes
through REST, so every radar price, staged-ticket reference and sizing figure is
built on data that is at best a quarter hour old. FF alerted at $6.86 and was
$6.29 by the time anyone looked.

The same free plan DOES include a real-time websocket, capped at 30 symbols, on
the IEX feed.

So this subscribes to the 30 symbols that matter and writes what it hears to
`data/live-prices.json`. Any lane that can read a file gets live prices - the
same file-bus contract as state.json and panels/.

WHAT IEX IS AND IS NOT
----------------------
IEX is roughly **2% of consolidated volume**. It is genuinely real-time, it is
just thin. For a liquid name the IEX print tracks the tape closely. For a thin
small cap there may be no IEX print for minutes at a time.

**A stale IEX price means "no data", never "the price has not moved."** Every
record carries `age_s` so a reader can tell the difference, and `fresh` is False
past STREAM_STALE_S. Treating an old print as a current price is the one way this
module can hurt you.

Optional dependency: `websocket-client`. Without it this module does nothing and
says so; the desk works exactly as before.

Run:
    python bot/src/stream.py                 # positions + radar + indices
    python bot/src/stream.py --symbols AAPL,NVDA
"""
from __future__ import annotations

import argparse
import json
import threading
import time

import config

OUT_PATH = config.DATA_DIR / "live-prices.json"
MAX_SYMBOLS = 30                  # free-plan websocket cap
STALE_S = float(getattr(config, "STREAM_STALE_S", 90))

_prices = {}
_lock = threading.Lock()
_state = {"connected": False, "feed": None, "error": None, "since": None,
          "messages": 0}


def _flush():
    """Write the snapshot. Called on a timer, not per message - a busy tape
    would otherwise rewrite this file hundreds of times a second."""
    now = time.time()
    with _lock:
        rows = {
            s: {**v,
                # epoch_ts is the load-bearing field: age_s is only true at the
                # instant of the flush, and a reader opening this file 10 minutes
                # later needs to compute age against ITS own clock.
                "epoch_ts": round(v["ts"], 2),
                "age_s": round(now - v["ts"], 1),
                "fresh": (now - v["ts"]) <= STALE_S}
            for s, v in _prices.items()
        }
    payload = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "epoch": round(now, 1),
        "feed": _state.get("feed"),
        "connected": _state.get("connected"),
        "error": _state.get("error"),
        "messages": _state.get("messages"),
        "stale_after_s": STALE_S,
        # Said out loud in the file itself, because whoever reads this next is
        # not going to read the module docstring first.
        "caveat": ("IEX feed is ~2% of consolidated volume. A stale price means "
                   "NO DATA, not an unchanged price. Check `fresh`."),
        "prices": rows,
    }
    try:
        OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = OUT_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=1), encoding="utf-8")
        tmp.replace(OUT_PATH)      # atomic: a reader never sees a half file
    except Exception as e:
        print(f"[stream] write failed: {e}")


def read_live(symbol=None, max_age_s=None):
    """What another lane calls. Returns None rather than a stale price.

    This is the safe accessor: it refuses to hand back a price that is older
    than the staleness window, so a caller cannot accidentally size a trade
    against a print from twenty minutes ago.
    """
    try:
        d = json.loads(OUT_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None
    limit = max_age_s if max_age_s is not None else d.get("stale_after_s", STALE_S)
    now = time.time()
    rows = {}
    for s, v in (d.get("prices") or {}).items():
        epoch = v.get("epoch_ts")
        if not epoch:
            continue                       # no timestamp = cannot vouch for it
        age = now - float(epoch)
        if age <= limit:
            rows[s] = {**v, "age_s": round(age, 1), "fresh": True}
    if symbol:
        return rows.get(symbol.upper())
    return rows


INDEXES = ("SPY", "QQQ", "IWM")


def default_symbols(client=None):
    """The 30 that matter, in priority order.

    RESERVED, in this order, because these can never be allowed to lose their
    slot to a noisy day:
      1. open positions - the highest-value subscription you will ever have
      2. SPY/QQQ/IWM   - what the regime gate reads

    Then the day's radar fills whatever is left.

    Indices used to be APPENDED LAST, which meant a 60-alert radar day silently
    evicted them and the regime read went stale without saying so. Found by a
    test running against a database that still had 60 leftover rows in it - the
    accident reproduced the exact condition the bug needed.
    """
    syms, seen = [], set()

    def add(s):
        s = str(s or "").upper().strip()
        if s and s not in seen and len(syms) < MAX_SYMBOLS:
            seen.add(s)
            syms.append(s)

    if client is not None:
        try:
            for p in (client.list_positions() or []):
                add(p.get("symbol"))
        except Exception:
            pass
    for s in INDEXES:
        add(s)
    # THE RADAR LIVES IN SQLITE, NOT A JSON FILE.
    # This originally read data/radar.json, which does not exist and never did -
    # radar.py calls db.record_alert(). The read failed, the except swallowed it,
    # and the tap silently subscribed to 5 symbols instead of 30 while looking
    # like it worked. Guessing at a filename instead of checking is what caused
    # it; the empty `except` is what hid it.
    try:
        import db
        conn = db.connect()
        db.init_db(conn)
        rows = db.recent_alerts(conn, limit=60) or []
        for r in sorted(rows, key=lambda x: -((x["score"] if "score" in x.keys()
                                               else 0) or 0)):
            add(r["symbol"])
    except Exception as e:
        # Say it. A silent fallback to 5 symbols is the bug this comment is about.
        print(f"[stream] could not read recent alerts ({str(e)[:90]}); "
              f"subscribing to positions and indices only")
    return syms


def run(symbols, on_update=None):
    """Connect and stream until interrupted. Blocking."""
    try:
        import websocket           # websocket-client, optional dependency
    except ImportError:
        print("[stream] websocket-client is not installed. This feature is "
              "optional and the desk works without it.\n"
              "         pip install websocket-client")
        return 1

    feed = (getattr(config, "ALPACA_DATA_FEED", None) or "iex").lower()
    _state["feed"] = feed
    url = f"wss://stream.data.alpaca.markets/v2/{feed}"
    key, secret = config.API_KEY_ID, config.API_SECRET
    if not key or not secret:
        print("[stream] no Alpaca keys in bot/.env")
        return 1

    symbols = symbols[:MAX_SYMBOLS]
    print(f"[stream] {feed} feed, {len(symbols)} symbols -> {OUT_PATH}")
    print(f"[stream] {', '.join(symbols)}")
    if feed == "iex":
        print("[stream] NOTE: IEX is ~2% of volume. Thin names may print rarely; "
              "a stale price is NO DATA, not a flat price.")

    def on_open(ws):
        ws.send(json.dumps({"action": "auth", "key": key, "secret": secret}))

    def on_message(ws, raw):
        try:
            msgs = json.loads(raw)
        except Exception:
            return
        for m in msgs if isinstance(msgs, list) else [msgs]:
            t = m.get("T")
            if t == "success" and m.get("msg") == "authenticated":
                _state.update(connected=True, since=time.strftime("%H:%M:%S"))
                ws.send(json.dumps({"action": "subscribe",
                                    "trades": symbols, "quotes": symbols}))
                print("[stream] authenticated, subscribed")
            elif t == "error":
                _state["error"] = m.get("msg")
                print(f"[stream] ERROR {m.get('code')}: {m.get('msg')}")
            elif t == "t":                      # trade
                with _lock:
                    _prices[m["S"]] = {"price": m["p"], "size": m.get("s"),
                                       "ts": time.time(), "kind": "trade"}
                _state["messages"] += 1
                if on_update:
                    on_update(m["S"], m["p"])
            elif t == "q":                      # quote: mid, when no trades print
                bid, ask = m.get("bp") or 0, m.get("ap") or 0
                if bid and ask:
                    with _lock:
                        cur = _prices.get(m["S"])
                        # never let a quote overwrite a fresher trade
                        if not cur or cur["kind"] != "trade" or \
                           time.time() - cur["ts"] > 5:
                            _prices[m["S"]] = {"price": round((bid + ask) / 2, 4),
                                               "bid": bid, "ask": ask,
                                               "ts": time.time(), "kind": "quote"}
                    _state["messages"] += 1

    def on_error(ws, err):
        _state.update(connected=False, error=str(err)[:200])
        print(f"[stream] {err}")

    def on_close(ws, code, msg):
        _state["connected"] = False
        print(f"[stream] closed {code} {msg}")

    def flusher():
        while True:
            time.sleep(2)
            _flush()

    threading.Thread(target=flusher, daemon=True).start()

    ws = websocket.WebSocketApp(url, on_open=on_open, on_message=on_message,
                                on_error=on_error, on_close=on_close)
    while True:
        ws.run_forever(ping_interval=20, ping_timeout=10)
        _flush()
        # Alpaca allows ONE websocket connection per key. If the desk or another
        # process grabbed it, reconnecting in a tight loop just fights over it.
        print("[stream] disconnected, retrying in 15s (Ctrl-C to stop)")
        time.sleep(15)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", help="comma list; default = positions + radar + indices")
    ap.add_argument("--print-only", action="store_true",
                    help="show what would be subscribed, then exit")
    args = ap.parse_args()

    if args.symbols:
        syms = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    else:
        client = None
        try:
            from alpaca_client import AlpacaClient
            client = AlpacaClient()
        except Exception:
            pass
        syms = default_symbols(client)

    if args.print_only:
        print(f"{len(syms)} symbols: {', '.join(syms)}")
        return 0
    if not syms:
        print("[stream] nothing to subscribe to")
        return 1
    try:
        return run(syms)
    except KeyboardInterrupt:
        _flush()
        print("\n[stream] stopped")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
