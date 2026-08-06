"""Central configuration for the stock bot (Alpaca).

Sibling to the Kalshi bot: same discipline (manual .env parse, integer cents,
env-scoped SQLite ledger, the LLM never sizes money). The broker is Alpaca
instead of Kalshi, and Alpaca gives a REAL paper account (live market data,
simulated cash/fills server-side) so `paper` here talks to Alpaca's paper API,
not a locally simulated book.

All prices are handled as INTEGER CENTS end to end (e.g. $150.25 -> 15025) to
match the Kalshi bot's money discipline. Convert at the API boundary only.
"""
from __future__ import annotations

import os
from pathlib import Path

# --- Repo layout -----------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
RESEARCH_DIR = DATA_DIR / "research"
ANALYSIS_DIR = DATA_DIR / "analysis"
CANDIDATES_PATH = DATA_DIR / "candidates.json"
STAGED_ORDERS_PATH = DATA_DIR / "staged_orders.json"
LESSONS_PATH = DATA_DIR / "lessons.md"
UNIVERSE_PATH = DATA_DIR / "universe.txt"


# --- .env parsing (no python-dotenv dependency) ----------------------------
def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        # .env.template ships inline comments ("KEY=100000   # why"), and the
        # template header says to copy it to bot/.env - so a fresh install fed
        # them straight into int() and the engine died on boot. Strip a
        # whitespace-preceded # tail before anything else.
        val = val.strip()
        if " #" in val:
            val = val.split(" #", 1)[0]
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


_load_dotenv(REPO_ROOT / ".env")


def _get(name, default=None):
    return os.environ.get(name, default)


# --- Environment (paper | live) --------------------------------------------
# STAY ON PAPER until backtest + a real paper track record + the kill-switch are
# all proven. `paper` hits Alpaca's paper endpoint (real data, fake money).
# `live` hits real-money Alpaca and requires a SEPARATE key pair.
STOCK_ENV = (_get("STOCK_ENV", "paper") or "paper").lower()
if STOCK_ENV not in ("paper", "live"):
    raise SystemExit(f"STOCK_ENV must be 'paper' or 'live', got {STOCK_ENV!r}")

PAPER = STOCK_ENV == "paper"

# Per-env ledger so paper fills/equity never contaminate live breaker math.
DB_PATH = DATA_DIR / ("trades-live.db" if STOCK_ENV == "live" else "trades-paper.db")

# --- Alpaca hosts ----------------------------------------------------------
_TRADE_HOST = {
    "paper": "https://paper-api.alpaca.markets",
    "live": "https://api.alpaca.markets",
}
TRADE_BASE = _TRADE_HOST[STOCK_ENV]
# Market data is the same host for both envs (data plan, not the trading env).
DATA_BASE = _get("ALPACA_DATA_BASE", "https://data.alpaca.markets")

# Keys: paper and live are DIFFERENT credentials. Never share them.
if STOCK_ENV == "live":
    API_KEY_ID = _get("ALPACA_LIVE_KEY_ID", "") or ""
    API_SECRET = _get("ALPACA_LIVE_SECRET_KEY", "") or ""
else:
    API_KEY_ID = _get("ALPACA_KEY_ID", "") or ""
    API_SECRET = _get("ALPACA_SECRET_KEY", "") or ""

# --- Universe + screen filters ---------------------------------------------
# Default universe is loaded from data/universe.txt (one symbol per line) or this
# fallback. Keep it liquid, large-cap; the LLM edge is dubious (see IMPLEMENTATION
# -NOTES), so a clean, liquid universe matters more than breadth.
DEFAULT_UNIVERSE = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "AVGO", "JPM", "V", "COST",
    "HD", "PG", "XOM", "UNH", "LLY", "MA", "WMT", "KO", "PEP", "CAT",
]
MAX_CANDIDATES = int(_get("MAX_CANDIDATES", 8))     # keep the LLM analysis loop bounded
MIN_PRICE_CENTS = int(_get("MIN_PRICE_CENTS", 500))     # skip sub-$5 names
# Liquidity floor. NOTE: the free IEX feed sees only a slice of total volume, so
# dollar-volume reads low; this default is IEX-scaled. On the paid SIP feed
# (ALPACA_DATA_FEED=sip) raise it toward ~20M for a true consolidated floor.
MIN_AVG_DOLLAR_VOLUME = float(_get("MIN_AVG_DOLLAR_VOLUME", 2_000_000))

# --- Signal params (swing / trend framework) -------------------------------
SMA_FAST = int(_get("SMA_FAST", 50))
SMA_SLOW = int(_get("SMA_SLOW", 200))
ATR_PERIOD = int(_get("ATR_PERIOD", 14))
ATR_STOP_MULT = float(_get("ATR_STOP_MULT", 2.5))   # initial stop = entry - mult*ATR
PULLBACK_SMA = int(_get("PULLBACK_SMA", 20))        # entry trigger reference

# --- Sizing / risk gates ---------------------------------------------------
# Primary sizing is RISK-BASED (fixed-fractional off stop distance), NOT Kelly.
# Kelly is only an optional cap (edge in equities is dubious). See risk.py.
RISK_PER_TRADE_PCT = float(_get("RISK_PER_TRADE_PCT", 0.01))   # 1% of bankroll risked to the stop
MAX_POSITION_PCT = float(_get("MAX_POSITION_PCT", 0.20))       # notional cap per name
MAX_SECTOR_PCT = float(_get("MAX_SECTOR_PCT", 0.40))           # notional cap per sector
MAX_POSITIONS = int(_get("MAX_POSITIONS", 8))
KELLY_FRACTION = float(_get("KELLY_FRACTION", 0.25))           # quarter-Kelly, used only as a cap
USE_KELLY_CAP = (_get("USE_KELLY_CAP", "false") or "false").lower() == "true"

# The bot's sandbox bankroll (cents). It sizes off THIS and a hard gate stops it
# once its own deployed notional reaches it. Walls the bot off from any manual
# positions in the same Alpaca account. Default $1,000.
BOT_BANKROLL_CENTS = int(_get("BOT_BANKROLL_CENTS", 100_000))

# Circuit breakers (kill-switch). A trip HALTS the whole cycle and stages nothing.
MAX_DAILY_LOSS_PCT = float(_get("MAX_DAILY_LOSS_PCT", 0.05))
MAX_DRAWDOWN_PCT = float(_get("MAX_DRAWDOWN_PCT", 0.15))

# Wash-sale guard: block re-entry into a symbol for N days after a LOSS exit.
# An autonomous re-entry loop manufactures wash sales otherwise (see research).
WASH_SALE_COOLDOWN_DAYS = int(_get("WASH_SALE_COOLDOWN_DAYS", 31))

# Fat-finger sanity: reject orders whose limit strays too far from the reference,
# or whose notional is out of band. LLMs miscompute share counts (StockBench).
MAX_LIMIT_DEVIATION_PCT = float(_get("MAX_LIMIT_DEVIATION_PCT", 0.05))
MIN_ORDER_NOTIONAL_CENTS = int(_get("MIN_ORDER_NOTIONAL_CENTS", 2000))  # $20 floor

# Fractional shares (Alpaca supports via API). Whole shares by default for
# deterministic sizing; flip to true for tiny-account precision.
FRACTIONAL = (_get("FRACTIONAL", "false") or "false").lower() == "true"

# Unattended paper-sprint mode: when false, the pipeline trades on the deterministic
# signal ALONE (no LLM analyst/critic required) and gate G2 auto-passes. Keep TRUE
# in the real design; the paper sprint sets it false to run headless with no LLM.
REQUIRE_LLM_ANALYSIS = (_get("REQUIRE_LLM_ANALYSIS", "true") or "true").lower() == "true"

# Exit management (manage.py): a hard stop as a % below avg entry, on top of the
# trend-break exit from signals.exit_signal. Belt and suspenders on the downside.
HARD_STOP_PCT = float(_get("HARD_STOP_PCT", 0.08))

# --- AI cost knob (surfaced for the agent layer) ---------------------------
DAILY_AI_COST_LIMIT_USD = float(_get("DAILY_AI_COST_LIMIT_USD", 10))

# --- Read API (for the OpsCanvas dashboard) --------------------------------
API_PORT = int(_get("API_PORT", 8796))
# Optional bearer token; if set, /api/* requires  Authorization: Bearer <token>.
# Blank = open (fine on LAN/VPN-only).
API_TOKEN = _get("API_TOKEN", "") or ""

# --- Catalyst radar --------------------------------------------------------
# Awareness tool: flags big session movers + any fresh news catalyst, with a
# scale-out/trailing-stop reminder. It ALERTS, it does not trade.
RADAR_TOP_N = int(_get("RADAR_TOP_N", 20))
RADAR_MIN_MOVE_PCT = float(_get("RADAR_MIN_MOVE_PCT", 5.0))   # percent, e.g. 5 = 5%
# Price floor filters out the low-float penny/halted junk that dominates raw
# top-movers lists (the pump-and-dumps, not real catalysts). Default $3.
RADAR_MIN_PRICE_CENTS = int(_get("RADAR_MIN_PRICE_CENTS", 300))
RADAR_TRAIL_PCT = float(_get("RADAR_TRAIL_PCT", 0.10))        # suggested trailing stop
RADAR_DISCORD_WEBHOOK = _get("RADAR_DISCORD_WEBHOOK", "") or ""

# LLM curation of radar alerts (triage real catalyst vs noise; never predicts).
# Defaults to the homelab Ollama on llmhub (free, private). Swap RADAR_LLM_BASE_URL
# to OpenRouter/etc. to use a bigger model.
RADAR_USE_LLM = (_get("RADAR_USE_LLM", "true") or "true").lower() == "true"
RADAR_LLM_BASE_URL = _get("RADAR_LLM_BASE_URL", "http://127.0.0.1:11434/v1") or ""
RADAR_LLM_MODEL = _get("RADAR_LLM_MODEL", "qwen2.5:3b-16k") or ""

# Reddit retail-buzz layer (2026-08-05, per Dustin: "Reddit is still KING on
# finding those crazy news things / retail holds"). Subs are comma-separated.
RADAR_REDDIT_ENABLED = (_get("RADAR_REDDIT_ENABLED", "true") or "true").lower() == "true"
RADAR_REDDIT_SUBS = [x.strip() for x in (_get("RADAR_REDDIT_SUBS", "wallstreetbets,swingtrading,stocks") or "").split(",") if x.strip()]
RADAR_REDDIT_CACHE_SECS = int(_get("RADAR_REDDIT_CACHE_SECS", 600))
RADAR_LLM_API_KEY = _get("RADAR_LLM_API_KEY", "") or ""
RADAR_LLM_TIMEOUT = float(_get("RADAR_LLM_TIMEOUT", 30))
RADAR_LLM_MIN_SCORE = int(_get("RADAR_LLM_MIN_SCORE", 60))    # Discord only pushes >= this
# Catalyst auto-execute (PAPER ONLY, hard-guarded in radar.py). A high-conviction
# catalyst places one tiny market order. Awareness tool grows a trigger finger.
RADAR_AUTO_EXECUTE = (_get("RADAR_AUTO_EXECUTE", "false") or "false").lower() == "true"
RADAR_AUTO_MIN_SCORE = int(_get("RADAR_AUTO_MIN_SCORE", 80))      # only >= this LLM score
RADAR_AUTO_NOTIONAL_CENTS = int(_get("RADAR_AUTO_NOTIONAL_CENTS", 20000))  # $200/catalyst
RADAR_AUTO_MAX_POSITIONS = int(_get("RADAR_AUTO_MAX_POSITIONS", 8))

# --- LIVE training-wheels auto-trading (2026-08-05, Dustin: "minimal auto
# trades while I keep researching strategy"). live entries are BRACKET orders -
# the exit exists the moment the entry does (the paper sprint's lesson: entries
# without sell points just ride). Everything below fails closed.
LIVE_AUTO_ENABLED = (_get("LIVE_AUTO_ENABLED", "false") or "false").lower() == "true"
RADAR_AUTO_MAX_PER_DAY = int(_get("RADAR_AUTO_MAX_PER_DAY", 2))
RADAR_AUTO_MAX_EXPOSURE_CENTS = int(_get("RADAR_AUTO_MAX_EXPOSURE_CENTS", 15000))
RADAR_AUTO_TP_PCT = float(_get("RADAR_AUTO_TP_PCT", 0.12))      # take-profit +12%
RADAR_AUTO_SL_PCT = float(_get("RADAR_AUTO_SL_PCT", 0.06))      # stop-loss -6%
RADAR_AUTO_MIN_PRICE_CENTS = int(_get("RADAR_AUTO_MIN_PRICE_CENTS", 300))  # $3 floor: ZYBT-class spiker junk lives below
# Exit style: "trail" = buy, confirm fill, attach a GTC trailing stop (follows
# the peak - Dustin's "goes high then dips" ask; never round-trip a winner).
# "bracket" = fixed take-profit + stop-loss attached at entry.
RADAR_AUTO_EXIT = (_get("RADAR_AUTO_EXIT", "trail") or "trail").lower()
RADAR_AUTO_TRAIL_PCT = float(_get("RADAR_AUTO_TRAIL_PCT", 0.10))  # sell 10% off the high-water mark

# --- HTTP client -----------------------------------------------------------
HTTP_MAX_RETRIES = int(_get("HTTP_MAX_RETRIES", 5))
HTTP_TIMEOUT_SECS = float(_get("HTTP_TIMEOUT_SECS", 30))


def is_paper() -> bool:
    return STOCK_ENV == "paper"


def env_banner() -> str:
    return f"STOCK_ENV = {STOCK_ENV.upper()}  (bankroll ${BOT_BANKROLL_CENTS/100:,.0f})"
