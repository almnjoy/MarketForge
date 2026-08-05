"""SQLite persistence for the stock bot. Local disk only.

Money-affecting invariant (same as the Kalshi bot): order intent is written here
BEFORE the API call (write-ahead), then updated with the result. All money is
integer cents; all timestamps are UTC ISO strings.

Alpaca is the source of truth for live positions/cash; this ledger exists for:
  - write-ahead order intent (crash safety),
  - equity history (breaker / drawdown math),
  - realized exits (the wash-sale cooldown depends on loss-exit dates).
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_order_id TEXT UNIQUE NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,             -- buy | sell
    qty REAL NOT NULL,
    limit_price_cents INTEGER,       -- null = market order
    env TEXT NOT NULL,               -- paper | live
    status TEXT NOT NULL,            -- intent | placed | accepted | filled | partial | rejected | error | canceled
    broker_order_id TEXT,
    filled_qty REAL DEFAULT 0,
    avg_fill_price_cents REAL,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS equity_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    env TEXT NOT NULL,
    cash_cents INTEGER NOT NULL,
    positions_value_cents INTEGER NOT NULL,
    equity_cents INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS daily_equity (
    date TEXT PRIMARY KEY,          -- UTC YYYY-MM-DD
    env TEXT NOT NULL,
    open_equity_cents INTEGER NOT NULL,
    recorded_at TEXT NOT NULL
);

-- realized exits: needed for the wash-sale cooldown (block re-entry after a loss)
CREATE TABLE IF NOT EXISTS realized_exits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    exit_date TEXT NOT NULL,        -- UTC YYYY-MM-DD
    realized_pnl_cents INTEGER NOT NULL,
    env TEXT NOT NULL,
    detail TEXT
);

-- catalyst radar alerts (awareness only; not trades)
CREATE TABLE IF NOT EXISTS radar_alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    date TEXT NOT NULL,             -- UTC YYYY-MM-DD (dedupe key with symbol)
    symbol TEXT NOT NULL,
    kind TEXT NOT NULL,             -- gainer | loser | news
    pct REAL,
    price_cents INTEGER,
    headline TEXT,
    url TEXT,
    score INTEGER,
    verdict TEXT,
    catalyst_type TEXT,
    why TEXT,
    note TEXT
);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def utctoday() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def connect(path=None) -> sqlite3.Connection:
    p = Path(path) if path else config.DB_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


# --- order write-ahead -----------------------------------------------------
def record_intent(conn, *, client_order_id, symbol, side, qty,
                  limit_price_cents, env) -> None:
    now = utcnow()
    conn.execute(
        """INSERT INTO orders (client_order_id, symbol, side, qty, limit_price_cents,
              env, status, created_at, updated_at)
           VALUES (?,?,?,?,?,?, 'intent', ?, ?)""",
        (client_order_id, symbol, side, qty, limit_price_cents, env, now, now),
    )
    conn.commit()


def update_order_result(conn, *, client_order_id, status, broker_order_id=None,
                        filled_qty=0, avg_fill_price_cents=None, error=None) -> None:
    conn.execute(
        """UPDATE orders SET status=?, broker_order_id=?, filled_qty=?,
              avg_fill_price_cents=?, error=?, updated_at=?
           WHERE client_order_id=?""",
        (status, broker_order_id, filled_qty, avg_fill_price_cents, error,
         utcnow(), client_order_id),
    )
    conn.commit()


def bot_open_symbols(conn, env):
    """Symbols the bot currently holds from its OWN fills (its committed capital).
    Manual account positions are not recorded here, so this is the bot's sandbox
    exposure only."""
    return conn.execute(
        "SELECT symbol, qty, limit_price_cents FROM orders "
        "WHERE env=? AND side='buy' AND status IN ('filled','partial','accepted','placed') "
        "AND symbol NOT IN (SELECT symbol FROM realized_exits)",
        (env,),
    ).fetchall()


# --- equity / breakers -----------------------------------------------------
def record_equity(conn, *, env, cash_cents, positions_value_cents) -> int:
    equity = int(cash_cents + positions_value_cents)
    conn.execute(
        "INSERT INTO equity_snapshots (ts, env, cash_cents, positions_value_cents, equity_cents) "
        "VALUES (?,?,?,?,?)",
        (utcnow(), env, int(cash_cents), int(positions_value_cents), equity),
    )
    today = utctoday()
    exists = conn.execute("SELECT 1 FROM daily_equity WHERE date=?", (today,)).fetchone()
    if not exists:
        conn.execute(
            "INSERT INTO daily_equity (date, env, open_equity_cents, recorded_at) VALUES (?,?,?,?)",
            (today, env, equity, utcnow()),
        )
    conn.commit()
    return equity


def peak_equity(conn):
    row = conn.execute("SELECT MAX(equity_cents) m FROM equity_snapshots").fetchone()
    return row["m"] if row and row["m"] is not None else None


def open_equity_today(conn):
    row = conn.execute(
        "SELECT open_equity_cents e FROM daily_equity WHERE date=?", (utctoday(),)
    ).fetchone()
    return row["e"] if row else None


# --- realized exits / wash-sale cooldown -----------------------------------
def record_exit(conn, *, symbol, realized_pnl_cents, env, detail="") -> None:
    conn.execute(
        "INSERT INTO realized_exits (symbol, exit_date, realized_pnl_cents, env, detail) "
        "VALUES (?,?,?,?,?)",
        (symbol, utctoday(), int(realized_pnl_cents), env, detail),
    )
    conn.commit()


def symbols_in_wash_cooldown(conn, cooldown_days, today=None):
    """Set of symbols with a LOSS exit inside the cooldown window. Re-entry into
    these is blocked by gate G8 to avoid manufacturing wash sales."""
    today = today or datetime.now(timezone.utc).date()
    cutoff = (today - timedelta(days=cooldown_days)).isoformat()
    rows = conn.execute(
        "SELECT DISTINCT symbol FROM realized_exits "
        "WHERE realized_pnl_cents < 0 AND exit_date >= ?",
        (cutoff,),
    ).fetchall()
    return {r["symbol"] for r in rows}


# --- read helpers for the API ---------------------------------------------
def recent_orders(conn, env, limit=25):
    return conn.execute(
        "SELECT symbol, side, qty, limit_price_cents, status, broker_order_id, "
        "created_at, updated_at FROM orders WHERE env=? ORDER BY id DESC LIMIT ?",
        (env, limit),
    ).fetchall()


def recent_equity(conn, limit=200):
    return conn.execute(
        "SELECT ts, equity_cents, cash_cents FROM equity_snapshots "
        "ORDER BY id DESC LIMIT ?", (limit,),
    ).fetchall()


# --- radar alerts ----------------------------------------------------------
def record_alert(conn, *, symbol, kind, pct, price_cents, headline="", url="", note="",
                 score=None, verdict="", catalyst_type="", why=""):
    conn.execute(
        "INSERT INTO radar_alerts (ts, date, symbol, kind, pct, price_cents, headline, "
        "url, score, verdict, catalyst_type, why, note) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (utcnow(), utctoday(), symbol, kind, pct, price_cents, headline, url,
         score, verdict, catalyst_type, why, note),
    )
    conn.commit()


def alert_exists_today(conn, symbol, kind="gainer") -> bool:
    row = conn.execute(
        "SELECT 1 FROM radar_alerts WHERE symbol=? AND kind=? AND date=?",
        (symbol, kind, utctoday()),
    ).fetchone()
    return row is not None


def recent_alerts(conn, limit=30):
    # Only alerts from the latest scan date so a Friday mover never shows as
    # "live" on Sunday. One coherent session, refreshed on the next scan.
    return conn.execute(
        "SELECT ts, symbol, kind, pct, price_cents, headline, url, score, verdict, "
        "catalyst_type, why, note FROM radar_alerts "
        "WHERE date = (SELECT MAX(date) FROM radar_alerts) "
        "ORDER BY id DESC LIMIT ?", (limit,),
    ).fetchall()
