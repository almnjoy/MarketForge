"""Market Forge - guided setup.

Walks you through the whole thing: checks your Python, takes your Alpaca keys,
VALIDATES them against the API, auto-detects whether your account gets real-time
(SIP) data or the free delayed feed, picks a mode, and writes bot/.env for you.

Safe to re-run - it shows your current settings and lets you keep them.

    python setup.py
"""
from __future__ import annotations

import sys

# One shared implementation for validation, feed detection and .env writing.
# The web wizard (/api/setup/* in app.py) calls the same functions - keep ALL
# setup logic in setup_core so the two wizards cannot drift.
import setup_core
from setup_core import ENV

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


def check_keys(env_name, key, sec):
    """Validate against the account endpoint. Returns the account dict or None."""
    acct, err = setup_core.check_keys(env_name, key, sec)
    if acct:
        ok(f"keys work - account {acct.get('status', '?').lower()}, "
           f"equity ${float(acct.get('equity', 0)):,.2f}")
        return acct
    bad(err)
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

    cur = setup_core.read_env()

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

    head("3. Market data")
    dim("Checking what your account is entitled to...")
    feed = setup_core.detect_feed(key, sec)
    if feed == "sip":
        ok("REAL-TIME (SIP) data available on this account - using it")
        dim("full consolidated tape, all exchanges")
    elif feed == "iex":
        ok("free IEX feed (no real-time subscription found)")
        dim("IEX only, a slice of total volume. Alpaca sells real-time SIP as an add-on;")
        dim("if you subscribe later, rerun this setup and it will switch automatically.")
    else:
        warn("could not check the data feed - leaving it on the free IEX feed")

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

    head("5. Catalyst scoring (optional)")
    dim("An LLM labels each mover signal or noise with a 0-100 score and a reason.")
    dim("Easiest free option: install Ollama, then `ollama pull qwen2.5:3b`.")
    use_llm = input("  enable scoring? [y/N]: ").strip().lower() == "y"
    llm_base = llm_model = ""
    if use_llm:
        llm_base = ask("LLM base url", cur.get("RADAR_LLM_BASE_URL") or "http://127.0.0.1:11434/v1")
        llm_model = ask("model", cur.get("RADAR_LLM_MODEL") or "qwen2.5:3b")
    else:
        dim("rules-only: you still get alerts, just no scores (and no auto entries)")

    head("6. Alerts (optional)")
    dim("A Discord webhook pushes scored catalysts to your phone. Enter to skip.")
    hook = input("  webhook url: ").strip()

    setup_core.apply_answers(cur, env_name=mode_env, key=key, sec=sec, feed=feed,
                             mode=use, use_llm=use_llm, llm_base=llm_base,
                             llm_model=llm_model, webhook=hook)
    setup_core.write_env(cur)

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
