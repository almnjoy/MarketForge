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

# --- Layout: CODE vs the user's DATA ---------------------------------------
# REPO_ROOT is where the engine's code lives. BOT_HOME is where the user's keys
# and ledger live. In a source checkout they are the same folder and nothing
# changes. In a packaged build app.py sets MF_BOT_HOME to a directory OUTSIDE
# the program folder, so updating the app is "delete it, unzip the new one" and
# the trade ledger, the .env and the pending-exit queue are never in the blast
# radius of an upgrade.
REPO_ROOT = Path(__file__).resolve().parent.parent
_bot_home = os.environ.get("MF_BOT_HOME")
BOT_HOME = Path(_bot_home).expanduser().resolve() if _bot_home else REPO_ROOT
DATA_DIR = BOT_HOME / "data"
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
        if val.startswith("#"):
            # "KEY=      # why it is blank" - the whole value is a comment, so
            # the value is empty. Without this the comment text itself became
            # the value and the next float()/int() killed the engine on boot.
            val = ""
        elif " #" in val:
            val = val.split(" #", 1)[0]
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


_load_dotenv(BOT_HOME / ".env")


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

# --- The paper lane, ALWAYS available regardless of STOCK_ENV --------------
# STOCK_ENV picks ONE venue for the whole process, so with STOCK_ENV=live the
# desk had no way to touch paper at all. The shadow lane needs both at once:
# every plan executes on paper for the data, and only the live ticket is staged
# for a human. These constants are therefore independent of STOCK_ENV and always
# point at the paper endpoint with the paper key pair.
PAPER_TRADE_BASE = _TRADE_HOST["paper"]
PAPER_KEY_ID = _get("ALPACA_KEY_ID", "") or ""
PAPER_SECRET = _get("ALPACA_SECRET_KEY", "") or ""
# Shadow fills land here even when the process is running live, so paper never
# contaminates the live breaker math (same reason DB_PATH splits).
PAPER_DB_PATH = DATA_DIR / "trades-paper.db"
# Alpaca cannot attach a trailing stop to a FRACTIONAL position, so a fractional
# entry is a position that can never be protected. Default on: convert notional
# to whole shares, and refuse the order if it cannot afford one share.
PAPER_WHOLE_SHARES_ONLY = (_get("PAPER_WHOLE_SHARES_ONLY", "true") or "true").lower() == "true"
# Optional ceiling on paper order size. DEFAULT OFF (0).
#
# This shipped 2026-08-10 defaulting to 50 and it SILENTLY halved orders: a $100
# paper order was recorded as $50 with no notice, which corrupts the P/L record
# the whole shadow book exists to produce. Two things were wrong with that. The
# $50 training-wheels cap had already been retired, and a silent clamp is the
# hard-block behavior the risk layer just moved away from.
# Left in as an explicit opt-in. When set it is announced, never silent.
PAPER_MAX_NOTIONAL = float(_get("PAPER_MAX_NOTIONAL", 0))
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

# --- Short lane (see short_signals.py + BRAIN-2-SHORT.md) ------------------
# Derived from Ariel Hernandez's stated rules, not invented. PAPER ONLY until
# the BRAIN-2 gates are closed.
SHORT_TREND_SMA = int(_get("SHORT_TREND_SMA", 50))       # the "is it broken" line
SHORT_ENTRY_SMA = int(_get("SHORT_ENTRY_SMA", 20))       # the average it fails at
SHORT_SLOPE_LOOKBACK = int(_get("SHORT_SLOPE_LOOKBACK", 5))   # bars used to call an MA "declining"
# THE rule: reject anything more than this many ATRs BELOW the trend SMA.
# "Do not short in the hole unless you absolutely hate your money."
SHORT_MAX_ATR_BELOW = float(_get("SHORT_MAX_ATR_BELOW", 4.0))
SHORT_MA_TAG_TOLERANCE = float(_get("SHORT_MA_TAG_TOLERANCE", 0.01))  # how close the high must get
SHORT_STOP_ATR_BUFFER = float(_get("SHORT_STOP_ATR_BUFFER", 0.25))    # stop = max(high, MA) + this*ATR
SHORT_MIN_PRICE_CENTS = int(_get("SHORT_MIN_PRICE_CENTS", 500))       # never short sub-$5

# --- Supply annotation (SEC EDGAR, see fundamentals.py) --------------------
# A catalyst is DEMAND; the share count is SUPPLY. The same headline on a 14M
# share company and a 24,000M share company are not the same event.
# NOTE: SEC gives shares OUTSTANDING, not free float. Annotation only, never a
# gate, and it degrades to "unknown" if EDGAR is unreachable.
ANNOTATE_SUPPLY = (_get("ANNOTATE_SUPPLY", "true") or "true").lower() == "true"
SEC_USER_AGENT = _get("SEC_USER_AGENT", "")   # SEC asks for a real contact string

# --- Sizing / risk gates ---------------------------------------------------
# Primary sizing is RISK-BASED (fixed-fractional off stop distance), NOT Kelly.
# Kelly is only an optional cap (edge in equities is dubious). See risk.py.
# advisory (default) = caps become plain-language NOTICES and the ticket still
#   stages; only G1 signal / G5 breakers / G9 fat-finger actually block.
# strict = the original behavior, all nine gates must pass.
RISK_MODE = (_get("RISK_MODE", "advisory") or "advisory").lower()
RISK_PER_TRADE_PCT = float(_get("RISK_PER_TRADE_PCT", 0.01))   # 1% of bankroll risked to the stop

# The position cap answers a DIFFERENT question than RISK_PER_TRADE_PCT:
#   RISK_PER_TRADE_PCT -> what you lose if the stop WORKS
#   MAX_POSITION_PCT   -> what you lose if it does NOT (gap, halt, reopen)
# So the cap is derived from a gap tolerance rather than hand-picked:
#   MAX_POSITION_PCT = MAX_GAP_LOSS_PCT / ASSUMED_GAP_PCT
# "I will not lose more than 3% to a 30% overnight gap" -> a 10% position cap.
# Set either to 0 to fall back to the literal MAX_POSITION_PCT below.
MAX_GAP_LOSS_PCT = float(_get("MAX_GAP_LOSS_PCT", 0.03))
ASSUMED_GAP_PCT = float(_get("ASSUMED_GAP_PCT", 0.30))
MAX_POSITION_PCT = float(_get("MAX_POSITION_PCT", 0.20))       # fallback / literal cap
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
def _hours(raw, default):
    """Parse "10,12,14,16" into a set of valid hours, ignoring junk.

    Fails to the DEFAULT rather than to an empty set: an empty set means the
    scheduler silently never runs, and a scanner that quietly stops scanning is
    worse than one on the wrong schedule.
    """
    out = set()
    for part in str(raw or "").split(","):
        part = part.strip()
        if part.isdigit() and 0 <= int(part) <= 23:
            out.add(int(part))
    return out or set(default)


# When the radar scans, ET, weekdays. Was hardcoded in run_bot.py, so "how often
# does this look at the market" was the one scanner setting nobody could change.
RADAR_SCAN_HOURS = _hours(_get("RADAR_SCAN_HOURS", ""), {10, 12, 14, 16})
RADAR_TOP_N = int(_get("RADAR_TOP_N", 20))
RADAR_MIN_MOVE_PCT = float(_get("RADAR_MIN_MOVE_PCT", 5.0))   # percent, e.g. 5 = 5%
# Price floor filters out the low-float penny/halted junk that dominates raw
# top-movers lists (the pump-and-dumps, not real catalysts). Default $3.
RADAR_MIN_PRICE_CENTS = int(_get("RADAR_MIN_PRICE_CENTS", 300))
# Liquidity floor on the RADAR. The long screen has had one forever
# (MIN_AVG_DOLLAR_VOLUME); the radar had none, so it surfaced names nobody could
# trade at size. $1M to start - deliberately permissive, tighten once the scan
# log shows what it is actually rejecting. 0 disables.
RADAR_MIN_DOLLAR_VOLUME = float(_get("RADAR_MIN_DOLLAR_VOLUME", 1_000_000))
# Drop leveraged/inverse wrappers from the gainer universe. CWVX/CRWG/CRWU all
# wrap CRWV and NBIL/NBIG/NBEX all wrap NBIS, so the board showed two ideas
# seven times and ranked the wrapper above the company. Set false to see them.
RADAR_SKIP_LEVERAGED = (_get("RADAR_SKIP_LEVERAGED", "true") or "true").lower() == "true"
RADAR_TRAIL_PCT = float(_get("RADAR_TRAIL_PCT", 0.10))        # suggested trailing stop
RADAR_DISCORD_WEBHOOK = _get("RADAR_DISCORD_WEBHOOK", "") or ""

# LLM curation of radar alerts (triage real catalyst vs noise; never predicts).
RADAR_USE_LLM = (_get("RADAR_USE_LLM", "true") or "true").lower() == "true"
# Provider: "auto" (default) scores through the LOCAL CODING-AGENT CLI when one
# is on PATH - the same optional dependency that powers the copilot seat, so
# scoring costs no extra install (no Ollama) - and falls back to the
# OpenAI-compatible endpoint below when no agent exists. "agent" / "openai"
# pin one explicitly.
RADAR_LLM_PROVIDER = (_get("RADAR_LLM_PROVIDER", "auto") or "auto").lower()
RADAR_AGENT_MODEL = _get("RADAR_AGENT_MODEL", "haiku") or "haiku"
RADAR_AGENT_TIMEOUT = float(_get("RADAR_AGENT_TIMEOUT", 90))
RADAR_LLM_BASE_URL = _get("RADAR_LLM_BASE_URL", "http://127.0.0.1:11434/v1") or ""
RADAR_LLM_MODEL = _get("RADAR_LLM_MODEL", "qwen2.5:3b-16k") or ""

# Reddit retail-buzz layer. The rationale: reddit is still where a lot of
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

# --- LIVE auto-trading, deliberately minimal ("a little automation
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
# the peak - for names that run then fade; never round-trip a winner).
# "bracket" = fixed take-profit + stop-loss attached at entry.
RADAR_AUTO_EXIT = (_get("RADAR_AUTO_EXIT", "trail") or "trail").lower()
RADAR_AUTO_TRAIL_PCT = float(_get("RADAR_AUTO_TRAIL_PCT", 0.10))  # sell 10% off the high-water mark

# --- the naked-position sweep ----------------------------------------------
# The 30s sweep used to only PRINT that a position had no working exit. It found
# the problem and then did nothing about it, which is the same outcome as not
# looking. With this on it also ARMS a trailing stop on anything it finds naked.
#
# UNITS TRAP: the queue and arm_trail() take a PERCENT (10.0). RADAR_AUTO_TRAIL_PCT
# is a FRACTION (0.10). Multiply, or you will arm a 0.1% trail and get stopped out
# by the spread on the next tick.
SWEEP_AUTO_ARM = (_get("SWEEP_AUTO_ARM", "true") or "true").lower() == "true"
# `or` the default in, because a key PRESENT BUT BLANK ("SWEEP_TRAIL_PCT=") makes
# _get return "" and float("") raises - which would stop the engine booting over
# an empty line in a config file. Blank means "use the default", not "crash".
SWEEP_TRAIL_PCT = float(_get("SWEEP_TRAIL_PCT", "") or RADAR_AUTO_TRAIL_PCT * 100)
# Give up on a symbol after this many failed arm attempts so one un-armable
# position (odd asset class, halted, fractional qty) cannot spam the log forever.
SWEEP_MAX_ATTEMPTS = int(_get("SWEEP_MAX_ATTEMPTS", 3))

# --- HTTP client -----------------------------------------------------------
HTTP_MAX_RETRIES = int(_get("HTTP_MAX_RETRIES", 5))
HTTP_TIMEOUT_SECS = float(_get("HTTP_TIMEOUT_SECS", 30))


def is_paper() -> bool:
    return STOCK_ENV == "paper"


def env_banner() -> str:
    return f"STOCK_ENV = {STOCK_ENV.upper()}  (bankroll ${BOT_BANKROLL_CENTS/100:,.0f})"
