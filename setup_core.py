"""Market Forge - setup logic, shared by BOTH wizards.

The terminal wizard (setup.py) and the web wizard (/api/setup/* in app.py) call
the exact same functions here. Two copies of key validation and .env writing
would drift, and the drift would be silent and about money - so there is one.

Everything is stdlib. No Flask, no requests.
"""
from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path


def _root() -> Path:
    # Frozen (PyInstaller): the app folder is the one holding the exe - bot/,
    # static/ and friends ship as plain sibling files, not bundled data.
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


ROOT = _root()
ENV = ROOT / "bot" / ".env"
TEMPLATE = ROOT / "bot" / ".env.template"

TRADE = {"paper": "https://paper-api.alpaca.markets", "live": "https://api.alpaca.markets"}
DATA = "https://data.alpaca.markets"


def read_env() -> dict:
    """Current settings: bot/.env if present, else the template.

    Inline comments are stripped from VALUES ("KEY=100000  # why" -> "100000"):
    the template uses them, and carrying them into a written .env fed comment
    text into int() inside the engine (found by the first frozen-build test)."""
    cur = {}
    src = ENV if ENV.exists() else TEMPLATE
    if src.exists():
        for line in src.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^([A-Z_]+)=(.*)$", line.strip())
            if m:
                val = m.group(2)
                if " #" in val:
                    val = val.split(" #", 1)[0]
                # quote handling mirrors the engine's _load_dotenv exactly, or
                # a hand-quoted STOCK_ENV="live" reads differently here vs there
                cur[m.group(1)] = val.strip().strip('"').strip("'")
    return cur


def _api(base, path, key, sec, timeout=15):
    req = urllib.request.Request(f"{base}{path}", headers={
        "APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": sec, "accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def check_keys(env_name: str, key: str, sec: str):
    """Validate a key pair against /v2/account.

    Returns (account_dict, None) on success, (None, human_error) on failure.
    The error strings are shown to the user verbatim - keep them plain.
    """
    if env_name not in TRADE:
        return None, "env must be 'paper' or 'live'"
    if not key or not sec:
        return None, "both fields are required"
    if key.startswith("YOUR_") or sec.startswith("YOUR_"):
        return None, "those are still the placeholder values"
    try:
        acct = _api(TRADE[env_name], "/v2/account", key, sec)
        return acct, None
    except urllib.error.HTTPError as e:
        return None, (f"Alpaca rejected these keys ({e.code}). Paper keys only work in "
                      f"paper mode and live keys only in live mode - make sure you "
                      f"copied the right pair.")
    except Exception as e:
        return None, f"could not reach Alpaca: {str(e)[:90]}"


def detect_feed(key: str, sec: str):
    """Is this account entitled to REAL-TIME consolidated (SIP) data?

    Probe the LATEST TRADE endpoint, not historical bars: free plans can read
    older SIP bars, so a bars probe answers 200 and lies. Recent SIP data
    returns 403 without the real-time add-on. Returns "sip", "iex", or None
    (could not check).
    """
    try:
        _api(DATA, "/v2/stocks/AAPL/trades/latest?feed=sip", key, sec, timeout=12)
        return "sip"
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return "iex"   # the entitlement answer: no real-time add-on
        return None        # 429/5xx: we could not CHECK - say so, don't guess
    except Exception:
        return None


def write_env(cur: dict) -> Path:
    """Write bot/.env, preserving the template's order and comments."""
    base = TEMPLATE.read_text(encoding="utf-8") if TEMPLATE.exists() else ""
    out, seen = [], set()
    for line in base.splitlines():
        m = re.match(r"^([A-Z_]+)=", line)
        if m and m.group(1) in cur:
            out.append(f"{m.group(1)}={cur[m.group(1)]}")
            seen.add(m.group(1))
        else:
            out.append(line)
    for k, v in cur.items():
        if k not in seen:
            out.append(f"{k}={v}")
    ENV.parent.mkdir(exist_ok=True)
    ENV.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")
    return ENV


def _placeholder(v: str) -> bool:
    return (not v) or v.startswith("YOUR_")


def first_run_state() -> dict:
    """Server-side first-run truth, so the frontend never guesses.

    First run = no usable key pair for the configured STOCK_ENV. bot/.env
    missing, still holding template placeholders, or empty keys all count.
    The .env content IS the state - there is deliberately no second flag to
    fall out of sync with it.
    """
    env_exists = ENV.exists()
    cur = read_env() if env_exists else {}
    stock_env = (cur.get("STOCK_ENV") or "paper").lower()
    has_paper = env_exists and not (_placeholder(cur.get("ALPACA_KEY_ID", ""))
                                    or _placeholder(cur.get("ALPACA_SECRET_KEY", "")))
    has_live = env_exists and not (_placeholder(cur.get("ALPACA_LIVE_KEY_ID", ""))
                                   or _placeholder(cur.get("ALPACA_LIVE_SECRET_KEY", "")))
    has_keys = has_live if stock_env == "live" else has_paper
    return {
        "first_run": not (env_exists and has_keys),
        "has_paper_keys": has_paper,
        "has_live_keys": has_live,
        "env_exists": env_exists,
        "stock_env": stock_env,
        "has_keys": env_exists and has_keys,
        "mode": cur.get("MF_MODE") or ("research" if not has_keys else "manual"),
        "feed": cur.get("ALPACA_DATA_FEED") or "iex",
        "has_webhook": bool(cur.get("RADAR_DISCORD_WEBHOOK")),
    }


def apply_answers(cur: dict, *, env_name: str, key: str, sec: str, feed,
                  mode: str, webhook="") -> dict:
    """Fold wizard answers into an env dict, mirroring the terminal wizard's
    rules exactly: which key fields, auto flags fail closed, feed fallback."""
    kid_field = "ALPACA_KEY_ID" if env_name == "paper" else "ALPACA_LIVE_KEY_ID"
    sec_field = "ALPACA_SECRET_KEY" if env_name == "paper" else "ALPACA_LIVE_SECRET_KEY"
    cur[kid_field], cur[sec_field] = key, sec
    cur["STOCK_ENV"] = env_name
    if feed == "sip":
        cur["ALPACA_DATA_FEED"] = "sip"
        # the engine's knob is MIN_AVG_DOLLAR_VOLUME (config.py); both wizards
        # used to write MIN_DOLLAR_VOLUME, a key nothing reads
        cur.pop("MIN_DOLLAR_VOLUME", None)
        cur.setdefault("MIN_AVG_DOLLAR_VOLUME", "20000000")
    elif feed == "iex":
        cur["ALPACA_DATA_FEED"] = "iex"
    else:
        cur["ALPACA_DATA_FEED"] = cur.get("ALPACA_DATA_FEED", "iex")
    if mode == "auto":
        cur["RADAR_AUTO_EXECUTE"] = "true"
        cur["LIVE_AUTO_ENABLED"] = "true" if env_name == "live" else "false"
    else:
        cur["RADAR_AUTO_EXECUTE"] = "false"
        cur["LIVE_AUTO_ENABLED"] = "false"
    cur["MF_MODE"] = mode
    # Scoring is no longer a question: it rides the coding-agent CLI when one
    # exists and degrades to alert-only when none does (2026-08-06, Dustin:
    # no Ollama dependency). An OpenAI endpoint stays available as an env-only
    # override (RADAR_LLM_PROVIDER=openai in bot/.env) and is left untouched.
    cur["RADAR_USE_LLM"] = "true"
    cur.setdefault("RADAR_LLM_PROVIDER", "auto")
    if webhook:
        cur["RADAR_DISCORD_WEBHOOK"] = webhook
    return cur
