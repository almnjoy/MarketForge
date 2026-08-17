"""Thin Alpaca REST client. Only `requests` as a runtime dep (mirrors the Kalshi
bot's minimalism; no alpaca-py needed for this surface).

Auth is header-based key/secret (APCA-API-KEY-ID / APCA-API-SECRET-KEY), so there
is no gateway session to babysit (the reason we picked Alpaca over IBKR for an
always-on autonomous bot). The paper vs live host is chosen in config from
STOCK_ENV; the key pair MUST match the env.

Convention: every PRICE this client returns is INTEGER CENTS. Dollars only ever
exist at the JSON boundary, converted here.
"""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timedelta, timezone

import requests

import config


def dollars_to_cents(x) -> int:
    return int(round(float(x) * 100))


def cents_to_dollars(c) -> float:
    return round(int(c) / 100.0, 2)


class AlpacaError(RuntimeError):
    pass


class AlpacaClient:
    def __init__(self, key_id=None, secret=None, trade_base=None, data_base=None):
        self.key_id = key_id or config.API_KEY_ID
        self.secret = secret or config.API_SECRET
        self.trade_base = trade_base or config.TRADE_BASE
        self.data_base = data_base or config.DATA_BASE
        self._s = requests.Session()
        self._s.headers.update({
            "APCA-API-KEY-ID": self.key_id,
            "APCA-API-SECRET-KEY": self.secret,
            "Accept": "application/json",
        })

    # --- low-level ---------------------------------------------------------
    def _req(self, method, base, path, *, params=None, json=None):
        url = f"{base}{path}"
        last = None
        for attempt in range(config.HTTP_MAX_RETRIES):
            try:
                r = self._s.request(method, url, params=params, json=json,
                                    timeout=config.HTTP_TIMEOUT_SECS)
            except requests.RequestException as e:
                last = e
                time.sleep(min(2 ** attempt, 8))
                continue
            if r.status_code == 429 or r.status_code >= 500:
                # Alpaca rate limit is 200 req/min; back off and retry.
                last = AlpacaError(f"{r.status_code} {r.text[:200]}")
                time.sleep(min(2 ** attempt, 8))
                continue
            if r.status_code >= 400:
                raise AlpacaError(f"{method} {path} -> {r.status_code} {r.text[:300]}")
            return r.json() if r.text else {}
        raise AlpacaError(f"{method} {path} failed after retries: {last}")

    # --- account / positions ----------------------------------------------
    def get_account(self) -> dict:
        a = self._req("GET", self.trade_base, "/v2/account")
        return {
            "cash_cents": dollars_to_cents(a.get("cash", 0)),
            "equity_cents": dollars_to_cents(a.get("equity", 0)),
            "buying_power_cents": dollars_to_cents(a.get("buying_power", 0)),
            "account_type": "cash" if not a.get("shorting_enabled") and float(a.get("multiplier", 1)) == 1 else "margin",
            "status": a.get("status"),
            "raw": a,
        }

    def get_asset(self, symbol) -> dict:
        """Asset metadata. The `name` is the tell for a derivative wrapper:
        "GraniteShares 2x Long CRWV Daily ETF" says everything the symbol does
        not. Returns {} on any failure so callers never have to guard it."""
        try:
            return self._req("GET", self.trade_base, f"/v2/assets/{symbol}") or {}
        except Exception:
            return {}

    def list_positions(self) -> list:
        rows = self._req("GET", self.trade_base, "/v2/positions") or []
        out = []
        for p in rows:
            out.append({
                "symbol": p["symbol"],
                "qty": float(p["qty"]),
                "avg_entry_cents": dollars_to_cents(p["avg_entry_price"]),
                "market_value_cents": dollars_to_cents(p.get("market_value", 0)),
                "unrealized_pl_cents": dollars_to_cents(p.get("unrealized_pl", 0)),
                "current_price_cents": dollars_to_cents(p.get("current_price", 0)),
            })
        return out

    # --- market data -------------------------------------------------------
    def get_daily_bars(self, symbol, limit=250) -> list:
        """Most-recent `limit` daily bars, ascending by time, prices in cents.

        Uses an explicit `start` window + pagination instead of `limit` alone.
        The bars endpoint windows by DATE, so passing only `limit` returns far
        fewer bars than requested (the "insufficient_history" trap). We reach
        ~1.7 calendar days back per requested trading day, follow next_page_token,
        then keep the last `limit`. IEX feed by default (free); set
        ALPACA_DATA_FEED=sip for full-market history."""
        feed = config._get("ALPACA_DATA_FEED", "iex")
        start = (datetime.now(timezone.utc)
                 - timedelta(days=int(limit * 1.7) + 45)).strftime("%Y-%m-%d")
        raw, page_token = [], None
        while True:
            params = {"timeframe": "1Day", "limit": 10000, "feed": feed,
                      "adjustment": "split", "start": start, "sort": "asc"}
            if page_token:
                params["page_token"] = page_token
            data = self._req("GET", self.data_base, f"/v2/stocks/{symbol}/bars",
                            params=params)
            raw.extend(data.get("bars") or [])
            page_token = data.get("next_page_token")
            if not page_token:
                break
        bars = [{
            "t": b["t"],
            "o": dollars_to_cents(b["o"]),
            "h": dollars_to_cents(b["h"]),
            "l": dollars_to_cents(b["l"]),
            "c": dollars_to_cents(b["c"]),
            "v": int(b["v"]),
        } for b in raw]
        return bars[-limit:]

    def get_intraday_bars(self, symbol, timeframe="1Min", start=None, end=None,
                          limit=10000, session_date=None) -> list:
        """Minute bars, ascending, prices in CENTS. This is Brain 3's whole input.

        THE FREE-PLAN RULE THAT BITES: Basic cannot query the last 15 minutes. Ask
        for it and Alpaca returns `subscription does not permit querying recent
        SIP data` - a 403, not an empty list, so a naive caller reads it as "the
        API is broken" rather than "you asked for something your plan excludes."

        So on a non-SIP feed `end` is clamped to 16 minutes ago automatically.
        HISTORY IS NOT RESTRICTED - yesterday, last week, last quarter all come
        back complete and free. That is what makes the day-lane pattern testable
        before paying for a live feed: build against the past, then decide.

        session_date="YYYY-MM-DD" is the convenience form - one full RTH session,
        09:30-16:00 ET, which is the unit a day-trading backtest actually wants.
        """
        feed = config._get("ALPACA_DATA_FEED", "iex")
        if session_date:
            # ET offset is -04:00 in DST, -05:00 otherwise. Asking for the whole
            # calendar day in UTC and letting the API bound it is simpler and does
            # not silently drop the last half hour when the offset guess is wrong.
            start = f"{session_date}T00:00:00Z"
            end = f"{session_date}T23:59:59Z"
        if start is None:
            start = (datetime.now(timezone.utc) - timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
        if str(feed).lower() != "sip":
            cutoff = datetime.now(timezone.utc) - timedelta(minutes=16)
            cutoff_s = cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")
            if end is None or end > cutoff_s:
                end = cutoff_s
        raw, page_token = [], None
        while True:
            params = {"timeframe": timeframe, "limit": 10000, "feed": feed,
                      "adjustment": "split", "start": start, "end": end, "sort": "asc"}
            if page_token:
                params["page_token"] = page_token
            data = self._req("GET", self.data_base, f"/v2/stocks/{symbol}/bars",
                             params=params)
            raw.extend(data.get("bars") or [])
            page_token = data.get("next_page_token")
            if not page_token or len(raw) >= limit * 4:
                break
        bars = [{
            "t": b["t"],
            "o": dollars_to_cents(b["o"]),
            "h": dollars_to_cents(b["h"]),
            "l": dollars_to_cents(b["l"]),
            "c": dollars_to_cents(b["c"]),
            "v": int(b["v"]),
        } for b in raw]
        return bars[-limit:]

    def get_latest_trade(self, symbol) -> dict:
        """Latest trade WITH its timestamp: {"price_cents": int, "at": RFC-3339 str}.

        The timestamp is the point. Outside the session the "latest" trade is the
        LAST SESSION'S CLOSING PRINT, so anything that compares it to that session's
        close is comparing a number to itself and getting 0.0% - which the radar then
        reported as a verified move (2026-08-17: 18 of 20 pre-market candidates
        killed that way). Callers that care about staleness read `at`.
        """
        feed = config._get("ALPACA_DATA_FEED", "iex")
        data = self._req("GET", self.data_base, f"/v2/stocks/{symbol}/trades/latest",
                        params={"feed": feed})
        trade = data.get("trade") or {}
        return {"price_cents": dollars_to_cents(trade.get("p", 0)),
                "at": str(trade.get("t", "") or "")}

    def get_latest_price(self, symbol) -> int:
        return self.get_latest_trade(symbol)["price_cents"]

    # --- news + movers (for the catalyst radar) ----------------------------
    def get_news(self, symbols=None, limit=10) -> list:
        """Recent market news (Benzinga via Alpaca). Each item: headline, summary,
        url, source, created_at, symbols. Available on the free tier."""
        params = {"limit": limit, "sort": "desc"}
        if symbols:
            params["symbols"] = ",".join(symbols)
        data = self._req("GET", self.data_base, "/v1beta1/news", params=params)
        return data.get("news") or []

    def get_movers(self, top=10) -> dict:
        """Top % gainers/losers of the session. Each: symbol, percent_change,
        change, price (dollars). Returns {gainers:[...], losers:[...]}."""
        data = self._req("GET", self.data_base, "/v1beta1/screener/stocks/movers",
                        params={"top": top})
        return {"gainers": data.get("gainers") or [], "losers": data.get("losers") or []}

    # --- orders ------------------------------------------------------------
    def submit_order(self, *, symbol, qty, side, limit_price_cents=None,
                     time_in_force="day", client_order_id=None) -> dict:
        body = {
            "symbol": symbol,
            "qty": str(qty),
            "side": side,
            "time_in_force": time_in_force,
            "client_order_id": client_order_id or f"stockbot-{uuid.uuid4().hex[:16]}",
        }
        if limit_price_cents is not None:
            body["type"] = "limit"
            body["limit_price"] = str(cents_to_dollars(limit_price_cents))
        else:
            body["type"] = "market"
        return self._req("POST", self.trade_base, "/v2/orders", json=body)

    def submit_market_order(self, *, symbol, side="buy", notional=None, qty=None,
                            client_order_id=None) -> dict:
        """Manual market order: size by notional (dollars) OR qty (shares). Alpaca
        requires time_in_force=day for notional/fractional orders."""
        body = {"symbol": symbol, "side": side, "type": "market", "time_in_force": "day",
                "client_order_id": client_order_id or f"manual-{uuid.uuid4().hex[:12]}"}
        if notional is not None:
            body["notional"] = str(round(float(notional), 2))
        elif qty is not None:
            body["qty"] = str(qty)
        return self._req("POST", self.trade_base, "/v2/orders", json=body)

    def get_order(self, broker_order_id) -> dict:
        return self._req("GET", self.trade_base, f"/v2/orders/{broker_order_id}")

    def submit_trailing_stop_sell(self, *, symbol, qty, trail_percent,
                                  client_order_id=None) -> dict:
        """GTC trailing stop SELL: the stop follows the position's high-water
        mark and fires trail_percent below it (triggers a market sell). Whole
        shares only. Alpaca can't attach this at entry (bracket legs are fixed
        prices only), so the auto-trader buys, confirms the fill, THEN arms this."""
        body = {"symbol": symbol, "side": "sell", "type": "trailing_stop",
                "time_in_force": "gtc", "qty": str(int(qty)),
                "trail_percent": f"{float(trail_percent):.2f}",
                "client_order_id": client_order_id or f"radar-trail-{uuid.uuid4().hex[:10]}"}
        return self._req("POST", self.trade_base, "/v2/orders", json=body)

    def submit_trailing_stop_buy(self, *, symbol, qty, trail_percent,
                                 client_order_id=None) -> dict:
        """GTC trailing stop BUY: the exit for a SHORT position.

        Mirror of submit_trailing_stop_sell. The stop follows the position's
        LOW-water mark and fires trail_percent ABOVE it, triggering a market buy
        to cover. Whole shares only.

        This exists because arm_trail() used to send a SELL for every position.
        On a short that does not close anything - it doubles the short. Requires
        a margin account with >= $2,000 equity; on a cash account the broker
        rejects the underlying short before this is ever reached.
        """
        body = {"symbol": symbol, "side": "buy", "type": "trailing_stop",
                "time_in_force": "gtc", "qty": str(int(qty)),
                "trail_percent": f"{float(trail_percent):.2f}",
                "client_order_id": client_order_id or f"cover-trail-{uuid.uuid4().hex[:10]}"}
        return self._req("POST", self.trade_base, "/v2/orders", json=body)

    def submit_bracket_order(self, *, symbol, qty, take_profit_cents,
                             stop_loss_cents, client_order_id=None) -> dict:
        """Whole-share market BUY with its exits attached (OCO legs): take-profit
        limit + stop-loss, all GTC so nothing expires unmanaged overnight.
        Alpaca requires WHOLE shares for bracket orders - no notional/fractional
        (that's why the live auto path floors qty instead of sending dollars)."""
        body = {
            "symbol": symbol, "side": "buy", "type": "market",
            "time_in_force": "gtc", "qty": str(int(qty)), "order_class": "bracket",
            "take_profit": {"limit_price": f"{take_profit_cents / 100:.2f}"},
            "stop_loss": {"stop_price": f"{stop_loss_cents / 100:.2f}"},
            "client_order_id": client_order_id or f"radar-{uuid.uuid4().hex[:12]}",
        }
        return self._req("POST", self.trade_base, "/v2/orders", json=body)

    def cancel_order(self, broker_order_id) -> None:
        self._req("DELETE", self.trade_base, f"/v2/orders/{broker_order_id}")

    def get_clock(self) -> dict:
        return self._req("GET", self.trade_base, "/v2/clock")
