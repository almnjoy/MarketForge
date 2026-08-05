"""Market Forge - guided setup.

Walks you through the whole thing: checks your Python, takes your Alpaca keys,
VALIDATES them against the API, auto-detects whether your account gets real-time
(SIP) data or the free delayed feed, picks a mode, and writes bot/.env for you.

Safe to re-run - it shows your current settings and lets you keep them.

    python setup.py
"""
from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENV = ROOT / "bot" / ".env"
TEMPLATE = ROOT / "bot" / ".env.template"

TRADE = {"paper": "https://paper-api.alpaca.markets", "live": "https://api.alpaca.markets"}
DATA = "https://data.alpaca.markets"

C = {"b": "\033[1m", "d": "\033[2m", "g": "\033[32m", "y": "\033[33m",
     "r": "\033[31m", "c": "\033[36m", "x": "\033[0m"}
if sys.platform == "win32":
    try:                       # enable ANSI on older Windows terminals
        import ctypes
        ctypes.windll.kernel32.SetConsoleMode(ctypes.windll.kernel32.GetStdHandle(-11), 7)
    except Exception:
        C = {k: "" for k in C}


def say(msg=""): print(msg)
def head(msg): say(f"\n{C['b']}{C['c']}{msg}{C['x']}")
def ok(msg): say(f"  {C['g']}OK{C['x']}  {msg}")
def warn(msg): say(f"  {C['y']}!{C['x']}   {msg}")
def bad(msg): say(f"  {C['r']}X{C['x']}   {msg}")
def dim(msg): say(f"  {C['d']}{msg}{C['x']}")


def ask(prompt, default=None, secret=False):
    d = f" [{C['d']}{'*' * 6 if secret and default else (default or '')}{C['x']}]" if default else ""
    while True:
        v = input(f"  {prompt}{d}: ").strip()
        if v:
            return v
        if default is not None:
            return default


def choose(prompt, options):
    say(f"\n  {C['b']}{prompt}{C['x']}")
    for i, (_, label, note) in enumerate(options, 1):
        say(f"    {C['c']}{i}{C['x']}) {label}")
        if note:
            dim(f"       {note}")
    while True:
        v = input(f"  choose 1-{len(options)} [1]: ").strip() or "1"
        if v.isdigit() and 1 <= int(v) <= len(options):
            return options[int(v) - 1][0]


def read_env():
    cur = {}
    src = ENV if ENV.exists() else TEMPLATE
    if src.exists():
        for line in src.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^([A-Z_]+)=(.*)$", line.strip())
            if m:
                cur[m.group(1)] = m.group(2)
    return cur


def api(base, path, key, sec, timeout=15):
    req = urllib.request.Request(f"{base}{path}", headers={
        "APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": sec, "accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def check_keys(env_name, key, sec):
    """Validate against the account endpoint. Returns the account dict or None."""
    try:
        acct = api(TRADE[env_name], "/v2/account", key, sec)
        ok(f"keys work - account {acct.get('status', '?').lower()}, "
           f"equity ${float(acct.get('equity', 0)):,.2f}")
        return acct
    except urllib.error.HTTPError as e:
        bad(f"Alpaca rejected these keys ({e.code}). Paper keys only work in paper mode "
            f"and live keys only in live mode - make sure you copied the right pair.")
    except Exception as e:
        bad(f"could not reach Alpaca: {str(e)[:90]}")
    return None


def detect_feed(key, sec):
    """Is this account entitled to REAL-TIME consolidated (SIP) data?

    Probe the LATEST TRADE endpoint, not historical bars: free plans can read
    older SIP bars, so a bars probe answers 200 and lies. Recent SIP data returns
    403 "subscription does not permit querying recent SIP data" without the
    real-time add-on.
    """
    try:
        api(DATA, "/v2/stocks/AAPL/trades/latest?feed=sip", key, sec, timeout=12)
        return "sip"
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return "iex"
        return "iex"
    except Exception:
        return None


def main():
    say(f"\n{C['b']}  MARKET FORGE - setup{C['x']}")
    dim("  open source - runs on your machine - your keys stay in bot/.env")

    head("1. Python")
    v = sys.version_info
    (ok if v >= (3, 10) else bad)(f"Python {v.major}.{v.minor}.{v.micro}")
    if v < (3, 10):
        say("\n  Python 3.10 or newer is required. Install it, then rerun.")
        return 1
    try:
        import flask  # noqa: F401
        ok("Flask installed")
    except ImportError:
        warn("Flask missing - run:  python -m pip install -r requirements.txt")

    cur = read_env()

    head("2. Alpaca keys")
    dim("Free account at alpaca.markets. Use the PAPER dashboard's API keys to start -")
    dim("real market data, fake money. The secret is shown once, so copy it now.")
    mode_env = choose("Which account are you setting up?", [
        ("paper", "Paper (recommended)", "fake money, real data - start here"),
        ("live",  "Live", "real money; auto-trading still stays off unless you turn it on"),
    ])
    kid_field = "ALPACA_KEY_ID" if mode_env == "paper" else "ALPACA_LIVE_KEY_ID"
    sec_field = "ALPACA_SECRET_KEY" if mode_env == "paper" else "ALPACA_LIVE_SECRET_KEY"

    acct = None
    while acct is None:
        key = ask("key id", cur.get(kid_field) or None)
        sec = ask("secret key", cur.get(sec_field) or None, secret=True)
        if key.startswith("YOUR_") or sec.startswith("YOUR_"):
            bad("those are still the placeholder values")
            continue
        acct = check_keys(mode_env, key, sec)
        if acct is None and input("  try again? [Y/n]: ").strip().lower() == "n":
            return 1
    cur[kid_field], cur[sec_field] = key, sec
    cur["STOCK_ENV"] = mode_env

    head("3. Market data")
    dim("Checking what your account is entitled to...")
    feed = detect_feed(key, sec)
    if feed == "sip":
        ok("REAL-TIME (SIP) data available on this account - using it")
        dim("full consolidated tape, all exchanges")
        cur["ALPACA_DATA_FEED"] = "sip"
        cur.setdefault("MIN_DOLLAR_VOLUME", "20000000")
    elif feed == "iex":
        ok("free IEX feed (no real-time subscription found)")
        dim("IEX only, a slice of total volume. Alpaca sells real-time SIP as an add-on;")
        dim("if you subscribe later, rerun this setup and it will switch automatically.")
        cur["ALPACA_DATA_FEED"] = "iex"
    else:
        warn("could not check the data feed - leaving it on the free IEX feed")
        cur["ALPACA_DATA_FEED"] = cur.get("ALPACA_DATA_FEED", "iex")

    head("4. How will you use it?")
    use = choose("Pick a mode (you can change it any time)", [
        ("research", "Research only", "radar, charts, chatter, copilot. No trading at all."),
        ("manual",   "Research + manual trades", "you place trades from the ticket; nothing automatic"),
        ("auto",     "Let it take small automatic entries", "hard-capped, trailing stop on every entry"),
    ])
    if use == "auto":
        warn("auto entries: $50 a trade, 2 a day, $150 total, trailing stop always armed")
        if mode_env == "live":
            warn("this is REAL money. Both switches must be on, and they are - by your choice.")
        cur["RADAR_AUTO_EXECUTE"] = "true"
        cur["LIVE_AUTO_ENABLED"] = "true" if mode_env == "live" else "false"
    else:
        cur["RADAR_AUTO_EXECUTE"] = "false"
        cur["LIVE_AUTO_ENABLED"] = "false"
    cur["MF_MODE"] = use

    head("5. Catalyst scoring (optional)")
    dim("An LLM labels each mover signal or noise with a 0-100 score and a reason.")
    dim("Easiest free option: install Ollama, then `ollama pull qwen2.5:3b`.")
    if input("  enable scoring? [y/N]: ").strip().lower() == "y":
        cur["RADAR_USE_LLM"] = "true"
        cur["RADAR_LLM_BASE_URL"] = ask("LLM base url", cur.get("RADAR_LLM_BASE_URL") or "http://127.0.0.1:11434/v1")
        cur["RADAR_LLM_MODEL"] = ask("model", cur.get("RADAR_LLM_MODEL") or "qwen2.5:3b")
    else:
        cur["RADAR_USE_LLM"] = "false"
        dim("rules-only: you still get alerts, just no scores (and no auto entries)")

    head("6. Alerts (optional)")
    dim("A Discord webhook pushes scored catalysts to your phone. Enter to skip.")
    hook = input("  webhook url: ").strip()
    if hook:
        cur["RADAR_DISCORD_WEBHOOK"] = hook

    # write bot/.env, preserving template order and comments
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

    head("Done")
    ok(f"wrote {ENV}")
    say(f"""
  {C['b']}Mode:{C['x']} {use}   {C['b']}Account:{C['x']} {mode_env}   {C['b']}Data:{C['x']} {cur['ALPACA_DATA_FEED'].upper()}\
{'  (real-time)' if cur['ALPACA_DATA_FEED'] == 'sip' else '  (free, delayed slice)'}

  Start the desk:
    {C['c']}run-portable.bat{C['x']}          (Windows)
    {C['c']}MF_EMBEDDED=1 python app.py{C['x']}   (macOS / Linux)
  Then open {C['c']}http://localhost:8410{C['x']}

  Want the copilot? Open a terminal here and start your coding agent
  (for Claude Code, just run `claude`). It reads CLAUDE.md and takes the seat.

  Docs: https://docs.madeformeai.com/marketforge/index
""")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        say("\n  cancelled - nothing was written")
        sys.exit(130)
