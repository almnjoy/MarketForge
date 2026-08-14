"""Agentic Stock Bot - LOCAL dashboard server.

Pure Python stdlib (no pip installs, runs on any Python 3.9+). Serves the
dashboard UI, runs the bot engine in-process, and exposes the
two file buses Claude Code drives:

  panels/*.html      -> every file renders as a live card on the WORKBENCH tab
                        (sandboxed iframe; edit/add/delete = the page transforms)
  chat-inbox.jsonl   -> what the operator says in the COPILOT tab (voice or typed)
  chat-outbox.jsonl  -> what Claude Code answers; the tab renders + speaks it

Run:  run-portable.bat  ->  http://localhost:8410  (dashboard + engine, one window)
      stop.bat           ->  full stop + verify nothing is left listening
      MF_PORT=8412 python app.py  ->  a second instance on another port
Bot base URL + port live in config.json.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import setup_core   # shared setup logic - the /api/setup/* endpoints and the
                    # terminal wizard (setup.py) call the SAME functions

FROZEN = bool(getattr(sys, "frozen", False))


def _app_root() -> Path:
    # Frozen (PyInstaller one-folder): the app folder is the one holding the
    # exe. Everything lives there as plain files - panels/, chat-*.jsonl,
    # memory.md, bot/ - because the folder IS the product surface agents read
    # and write. __file__ would point into the bundle and break all of it.
    if FROZEN:
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


ROOT = _app_root()

# Market Forge version. Single source of truth: the release workflow and the
# update check both read this, and Admin shows it.
VERSION = "0.1.0"


def _workspace() -> Path:
    """Where the USER's things live: keys, trading plan, journal, panels, boards.

    Source checkout -> the repo folder, exactly as before. Nothing moves.
    Packaged build  -> ~/MarketForge, OUTSIDE the program folder.

    The reason is updates. The program folder has to be disposable ("delete it,
    unzip the new one") and everything personal has to survive that untouched.
    It also keeps the file-bus honest: there is still one real, visible folder
    you point a coding agent at, and now it is a friendly path instead of a
    directory full of DLLs.

    MF_WORKSPACE overrides both, which is how you test the packaged layout from
    a source run.
    """
    env = os.environ.get("MF_WORKSPACE")
    if env:
        return Path(env).expanduser().resolve()
    if FROZEN:
        return Path.home() / "MarketForge"
    return ROOT


WORK = _workspace()
BOT_HOME = WORK / "bot"          # the user's .env + data/, NOT the engine's code

# setup_core is imported ABOVE this line, so it already resolved its own paths
# from an MF_BOT_HOME that only ever gets set on the ENGINE subprocess. Left
# alone, the wizard writes keys to <program>/bot/.env while the engine reads
# <workspace>/bot/.env: setup reports success, and the engine stays parked
# forever with "bot/.env missing". Point them at the SAME file, and export the
# variable so this process and every child agree.
os.environ["MF_BOT_HOME"] = str(BOT_HOME)
setup_core.BOT_HOME = BOT_HOME
setup_core.ENV = BOT_HOME / ".env"


def resource_path(rel: str) -> Path:
    """Resolve a shipped read-only resource (static/, bundled docs).

    This build ships resources as SIBLING FILES of the exe, not as data inside
    the bundle, so the normal answer is ROOT/rel in both dev and frozen runs.
    sys._MEIPASS is still honored first in case a resource is ever moved into
    the bundle proper."""
    mei = getattr(sys, "_MEIPASS", None)
    if mei and (Path(mei) / rel).exists():
        return Path(mei) / rel
    return ROOT / rel


STATIC = resource_path("static")
PANELS = WORK / "panels"
INBOX = WORK / "chat-inbox.jsonl"
OUTBOX = WORK / "chat-outbox.jsonl"
RULES = WORK / "RULES.md"
SAVED = WORK / "saved-workbenches"
MEMORY = WORK / "memory.md"        # standing preferences the copilot honors
JOURNAL = WORK / "journal.jsonl"   # the decision log Replay reconstructs from
SHOTS = WORK / "tv-shots"          # TradingView captures - agents READ these

DEFAULT_MEMORY = """# Trading Memory
The copilot honors these on every turn. Edit here or tell it "remember ...".

- Max $50 per trade while learning
- Every entry gets an exit: trailing stop, 10% default
- Don't chase IPOs or sub-$3 low-float spikers (ZYBT class)
- Prefer earnings-driven catalysts over vague momentum
- One idea at a time; no revenge trades
"""


def _journal_log(kind, text):
    """Append one line to the decision log. Never throws."""
    try:
        with _chat_lock:
            with JOURNAL.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                    "kind": kind, "text": str(text)[:400]}) + "\n")
    except Exception:
        pass

_cfg = {"bot_base": "http://127.0.0.1:8796", "port": 8410,
        "voicebox_url": "http://127.0.0.1:17493"}
try:
    _cfgp = WORK / "config.json"
    if not _cfgp.exists():
        _cfgp = ROOT / "config.json"      # shipped default on a fresh install
    _cfg.update(json.loads(_cfgp.read_text(encoding="utf-8")))
except Exception:
    pass
# EMBEDDED mode (the "friend edition"): MF_EMBEDDED=1 (or config "embedded": true)
# makes this server spawn the bot engine from bot/run_bot.py and point itself at
# it - one folder, one command, no server, no Docker.
EMBEDDED = os.environ.get("MF_EMBEDDED", "").strip() == "1" or bool(_cfg.get("embedded"))
# Engine port: MF_BOT_PORT wins, same reasoning as MF_PORT below. Two desks side
# by side need DISTINCT engine ports too - both on 8796 means the second desk
# silently proxies the first desk's account. The supervisor passes this to the
# engine child as API_PORT (a real env var beats bot/.env in config.py), so one
# knob moves both sides of the proxy together.
BOT_PORT = int(os.environ.get("MF_BOT_PORT") or _cfg.get("bot_port", 8796))
if EMBEDDED:
    _cfg["bot_base"] = f"http://127.0.0.1:{BOT_PORT}"
BOT = str(_cfg["bot_base"]).rstrip("/")
# Port: MF_PORT wins over config.json so the LIVE and PAPER desks can run side by
# side on different ports. They used to share 8410, which meant whichever started
# first owned the URL and the second silently served the wrong account.
PORT = int(os.environ.get("MF_PORT") or _cfg["port"])

# Windowed builds (pyinstaller --noconsole) have no console to hand to helper
# processes: without these flags every tasklist/taskkill/claude call flashes a
# console window, and an unset stdin can surface as WinError 6 because there is
# no valid handle to inherit.
NOWIN = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}

# Shell integration. app.py NEVER imports pywebview - staying stdlib-only is a
# hard rule. shell.py registers itself here at boot; in a plain browser these
# defaults stand and /api/shell/* answers honestly that there is nothing to do.
SHELL = {"shell": "browser", "can_focus": False}
SHELL_HOOKS = {}   # shell.py may register: focus() -> bring the window forward


BOT_PID = BOT_HOME / "data" / "engine.pid"
TAP_PID_PATH = BOT_HOME / "data" / "tap.pid"


def _pid_alive(pid):
    try:
        if os.name == "nt":
            out = subprocess.run(["tasklist", "/FI", f"PID eq {int(pid)}"],
                                 capture_output=True, text=True, timeout=8,
                                 stdin=subprocess.DEVNULL, **NOWIN).stdout
            return str(int(pid)) in out
        os.kill(int(pid), 0)
        return True
    except Exception:
        return False


def _kill_engine_now() -> bool:
    """Kill the running engine so the supervisor respawns it with a fresh .env.

    This IS the apply mechanism for any bot/.env change: config.py reads the file
    once at import, so nothing short of a new process picks up an edit. Callers
    must check _shutdown_safety() first - a kill inside the order path's fill
    window is the VRM failure class.
    """
    ENGINE_KICK.set()
    try:
        pid = int(BOT_PID.read_text().strip() or 0) if BOT_PID.exists() else 0
    except Exception:
        pid = 0
    if not (pid and _pid_alive(pid)):
        return False
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)],
                       capture_output=True, timeout=10,
                       stdin=subprocess.DEVNULL, **NOWIN)
    else:
        os.kill(pid, 9)
    return True


def _kill_stale_engine():
    """Kill an engine left over from a previous run.

    Closing the console window sends CTRL_CLOSE_EVENT and Python gets ~5s, so
    atexit does NOT reliably fire on Windows - the engine can survive with no
    window attached. Two of those running at once means two radar schedulers on
    one brokerage account, which is exactly the double-entry problem we retired
    a second engine to avoid. So: record the pid, and clean it up on the way IN as well
    as on the way out.
    """
    try:
        if not BOT_PID.exists():
            return
        pid = int(BOT_PID.read_text().strip() or 0)
        if pid and pid != os.getpid() and _pid_alive(pid):
            print(f"[embedded bot] killing orphaned engine pid {pid} from a previous run")
            if os.name == "nt":
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)],
                               capture_output=True, timeout=10,
                               stdin=subprocess.DEVNULL, **NOWIN)
            else:
                os.kill(pid, 9)
            time.sleep(1)
        BOT_PID.unlink(missing_ok=True)
    except Exception as e:
        print(f"[embedded bot] stale-pid check failed: {e}")


# --------------------------------------------------------------- THE TAP
# The live price tap used to be a window you opened by hand, and that cost real
# money-adjacent confusion twice in one day:
#   1. Started pre-open, it held SMCI and VRM. Two positions opened at 09:30 were
#      simply absent from it, while the header chip read a green 14/14 - a full
#      pass on a question nobody had asked.
#   2. It is easy to forget entirely, and then every price on screen is quietly
#      fifteen minutes old with nothing saying so.
#
# BOTH ARE THE SAME BUG: the subscription list is chosen ONCE at startup and never
# revisited. Supervising it here fixes the second problem; RESTARTING it when the
# position set changes fixes the first, which is the one that mattered.
TAP_PID = TAP_PID_PATH
TAP_STOP = threading.Event()
TAP_KICK = threading.Event()
_tap = {"proc": None, "symbols": [], "positions": frozenset(), "restarts": 0,
        "started": 0, "last_error": ""}


def _tap_enabled():
    """Off means off: an explicit 0/false, or websocket-client not installed."""
    if str(os.environ.get("MF_START_TAP", "1")).lower() in ("0", "false", "no"):
        return False, "MF_START_TAP is off"
    try:
        import websocket  # noqa: F401
    except Exception:
        return False, "websocket-client not installed (pip install websocket-client)"
    return True, ""


def _held_symbols():
    """What the BROKER says we hold. The tap's reserved slots depend on this."""
    try:
        raw, _ = _bot_get("positions", timeout=10)
        return frozenset(p.get("symbol") for p in (json.loads(raw) or []) if p.get("symbol"))
    except Exception:
        return _tap["positions"]      # unknown != empty; keep the last known set


def _tap_supervisor():
    """Run the tap, restart it on crash, and restart it when positions change.

    The position check is the whole point. A tap that is healthy and watching the
    wrong symbols is worse than one that is down, because a down tap SAYS it is
    down and a stale one shows you a green chip.
    """
    _kill_stale_tap()
    ok, why = _tap_enabled()
    if not ok:
        _tap["last_error"] = why
        print(f"[tap] not starting: {why}")
        return
    backoff = 5
    while not TAP_STOP.is_set():
        held = _held_symbols()
        _tap["positions"] = held
        try:
            logs = WORK / "logs"
            logs.mkdir(exist_ok=True)
            lp = logs / "tap.log"
            mode = "w" if (lp.exists() and lp.stat().st_size > 5_000_000) else "a"
            tap_log = open(lp, mode, buffering=1, encoding="utf-8", errors="replace")
            proc = subprocess.Popen(
                [sys.executable, str(ROOT / "bot" / "src" / "stream.py")],
                cwd=str(ROOT / "bot" / "src"),
                env={**os.environ, "MF_BOT_HOME": str(BOT_HOME)},
                stdin=subprocess.DEVNULL, stdout=tap_log, stderr=subprocess.STDOUT,
                **NOWIN)
        except Exception as e:
            _tap["last_error"] = str(e)[:200]
            print(f"[tap] could not start: {e}")
            return
        _tap["proc"], _tap["started"] = proc, time.time()
        try:
            if TAP_PID:
                TAP_PID.parent.mkdir(parents=True, exist_ok=True)
                TAP_PID.write_text(str(proc.pid), encoding="utf-8")
        except Exception:
            pass
        atexit.register(lambda p=proc: p.poll() is None and _kill_proc(p))
        print(f"[tap] started pid {proc.pid} watching {len(held)} position(s) + indices")

        t0 = time.time()
        while proc.poll() is None and not TAP_STOP.is_set():
            time.sleep(10)
            now = _held_symbols()
            if now != _tap["positions"]:
                # THE FIX. A position opened after the tap started would otherwise
                # never be watched, and nothing on screen would say so.
                added = sorted(now - _tap["positions"])
                gone = sorted(_tap["positions"] - now)
                print(f"[tap] positions changed"
                      + (f" (+{','.join(added)})" if added else "")
                      + (f" (-{','.join(gone)})" if gone else "")
                      + " - restarting so the new set is actually subscribed")
                _tap["positions"] = now
                TAP_KICK.set()
                break
            if TAP_KICK.is_set():
                break
        if proc.poll() is None:
            _kill_proc(proc)
        if TAP_STOP.is_set():
            return
        if TAP_KICK.is_set():
            TAP_KICK.clear()
            _tap["restarts"] += 1
            backoff = 5
            continue
        # Crashed on its own. Back off so a broken key does not spin a hot loop.
        _tap["restarts"] += 1
        ran = time.time() - t0
        backoff = 5 if ran > 60 else min(backoff * 2, 300)
        print(f"[tap] exited after {ran:.0f}s; restarting in {backoff}s")
        for _ in range(backoff):
            if TAP_STOP.is_set() or TAP_KICK.is_set():
                break
            time.sleep(1)


# Set by /api/setup/save after it rewrites bot/.env and kills the engine:
# tells the supervisor to respawn immediately instead of riding out backoff.
ENGINE_KICK = threading.Event()
# Set at shutdown: the supervisor must stop respawning and stay down.
ENGINE_STOP = threading.Event()


def _setup_key_pair(envn: str, body: dict):
    """The submitted key pair, or - when BOTH fields arrive empty - the pair
    already stored for that env. Mirrors the terminal wizard's keep-current
    defaults so re-running /setup does not force retyping a secret Alpaca only
    ever showed once. One empty field is a typo, not intent: no substitution."""
    key = str(body.get("key_id") or "").strip()
    sec = str(body.get("secret") or "").strip()
    if key or sec:
        return key, sec
    cur = setup_core.read_env()
    if envn == "live":
        k, s = cur.get("ALPACA_LIVE_KEY_ID", ""), cur.get("ALPACA_LIVE_SECRET_KEY", "")
    else:
        k, s = cur.get("ALPACA_KEY_ID", ""), cur.get("ALPACA_SECRET_KEY", "")
    if k and s and not (k.startswith("YOUR_") or s.startswith("YOUR_")):
        return k, s
    return key, sec


def stop_engine():
    """Deterministic engine kill for shutdown paths.

    PROVEN NECESSARY: relying on the supervisor's atexit hook alone left a live
    engine behind when the pywebview shell exited (dev test 2026-08-06) - and
    Windows console-close never ran atexit either, which is why the stale-pid
    kill on startup exists. Shutdown paths call THIS synchronously; atexit and
    the startup sweep stay as backstops."""
    ENGINE_STOP.set()
    _kill_stale_engine()   # kills the pid on file, removes the file
    _stop_tap()            # ...and the tap, which holds the ONLY allowed socket


def _kill_stale_tap():
    """Same reasoning as _kill_stale_engine, plus one that is specific to the tap:
    Alpaca allows ONE websocket connection per key. A leftover tap does not merely
    waste a process, it holds the only connection - so the new one is refused and
    the desk runs blind while both look fine."""
    try:
        if not TAP_PID.exists():
            return
        pid = int(TAP_PID.read_text().strip() or 0)
        if pid and pid != os.getpid() and _pid_alive(pid):
            print(f"[tap] killing orphaned tap pid {pid} from a previous run")
            if os.name == "nt":
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)],
                               capture_output=True, timeout=10,
                               stdin=subprocess.DEVNULL, **NOWIN)
            else:
                os.kill(pid, 9)
            time.sleep(1)
        TAP_PID.unlink(missing_ok=True)
    except Exception as e:
        print(f"[tap] stale-pid check failed: {e}")


def _stop_tap():
    TAP_STOP.set()
    p = _tap.get("proc")
    if p is not None and p.poll() is None:
        _kill_proc(p)
    _kill_stale_tap()


def _bot_supervisor():
    """Keep the embedded bot engine alive: spawn, watch, restart with backoff.

    The child argv is [sys.executable, bot/run_bot.py] in BOTH dev and frozen
    runs. Under PyInstaller sys.executable is MarketForge.exe - the dispatch
    shim at the top of shell.py recognizes the script path and emulates
    `python run_bot.py` instead of relaunching the app (which would loop).
    """
    import atexit
    _kill_stale_engine()
    env_file = BOT_HOME / ".env"
    eng_log = None
    backoff = 3
    while not ENGINE_STOP.is_set():
        if not env_file.exists():
            # First run: setup has not written keys yet. Park instead of
            # crash-looping run_bot.py's missing-env exit every few seconds.
            print("[embedded bot] bot/.env missing - engine parked until setup writes it")
            while not env_file.exists():
                if ENGINE_STOP.is_set():
                    return
                time.sleep(2)
            print("[embedded bot] bot/.env appeared - starting the engine")
        if FROZEN:
            # No console in a windowed build: give the engine a real stdout
            # (its prints + Flask's per-request log land here) and a real
            # stdin, or subprocess handle inheritance inside it gets WinError
            # 6. (Re)opened PER SPAWN so the >5MB rotation actually happens on
            # a desk left running for days, not only at launch.
            try:
                eng_log and eng_log.close()
            except Exception:
                pass
            logs = WORK / "logs"
            logs.mkdir(exist_ok=True)
            lp = logs / "engine.log"
            mode = "w" if (lp.exists() and lp.stat().st_size > 5_000_000) else "a"
            eng_log = open(lp, mode, buffering=1, encoding="utf-8", errors="replace")
        kw = dict(stdin=subprocess.DEVNULL, stdout=eng_log,
                  stderr=subprocess.STDOUT) if eng_log else {}
        proc = subprocess.Popen([sys.executable, str(ROOT / "bot" / "run_bot.py")],
                                cwd=str(ROOT / "bot"),
                                # one knob moves engine + proxy together; a real
                                # env var beats bot/.env inside config.py.
                                # MF_BOT_HOME points the engine at the USER's
                                # .env and ledger, which in a packaged build
                                # live outside the disposable program folder.
                                env={**os.environ, "API_PORT": str(BOT_PORT),
                                     "MF_BOT_HOME": str(BOT_HOME)},
                                **kw)
        try:
            BOT_PID.parent.mkdir(parents=True, exist_ok=True)
            BOT_PID.write_text(str(proc.pid), encoding="utf-8")
        except Exception:
            pass
        atexit.register(lambda p=proc: p.poll() is None and _kill_proc(p))
        t0 = time.time()
        rc = proc.wait()
        if ENGINE_STOP.is_set():
            return               # shutdown killed it on purpose - stay down
        if ENGINE_KICK.is_set() or time.time() - t0 > 30:
            backoff = 3          # deliberate restart or a long healthy run
            ENGINE_KICK.clear()
        print(f"[embedded bot] exited rc={rc}; restarting in {backoff}s "
              f"(check bot/.env if this loops)")
        time.sleep(backoff)
        backoff = min(60, backoff * 2)
VOICEBOX = str(_cfg["voicebox_url"]).rstrip("/")
_vb_profile = {"id": None}   # the app's default voice, resolved once from config
_VB_ENGINE_OK = {}           # profile_id -> the engine field shape Voicebox accepted
_STARTED_AT = time.time()    # process start, for the Admin tab's uptime
# When a BROWSER last hit us. The page polls constantly (chat 2.5s, overview
# 15s), so a long silence means every tab is gone. The engine never calls in
# here - app.py proxies OUT to it - so this really is a client signal.
LAST_CLIENT = [time.time()]
USAGE = WORK / "usage.jsonl"  # measured copilot spend, one line per bridge turn
STATE = WORK / "state.json"   # live snapshot ON DISK - see _state_writer()
STAGED = WORK / "staged-trade.json"   # a trade the copilot proposes; you confirm


def _state_writer():
    """Write a live snapshot of the desk to state.json every 20s.

    WHY THIS EXISTS: not every agent lane can reach http://localhost. Cowork's
    shell is a sandboxed VM with this folder MOUNTED but no route to the host
    network, so `curl localhost:8410` fails there even though its file tools are
    reading the real disk. Rather than make those lanes drive a browser to see
    whether a position is naked, the desk publishes its own state as a file that
    anything with read access can consume. Files are the lowest common
    denominator every lane shares - same idea as the panels and chat buses.

    Read-only by definition: acting on it still goes through the API.
    """
    while True:
        snap = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "port": PORT,
                "embedded": EMBEDDED, "stale_after_s": 60}
        for key, ep in (("status", "status"), ("positions", "positions"),
                        ("orders", "orders"), ("unprotected", "unprotected"),
                        ("broker_orders", "broker/orders"),
                        ("radar", "radar"), ("config", "config")):
            try:
                raw, code = _bot_get(ep, timeout=12)
                snap[key] = json.loads(raw) if code == 200 else {"error": f"HTTP {code}"}
            except Exception as e:
                snap[key] = {"error": f"{e.__class__.__name__}: {str(e)[:120]}"}
        try:
            STATE.write_text(json.dumps(snap, indent=1), encoding="utf-8")
        except Exception:
            pass
        time.sleep(20)


def _usage_log(ev: dict, model: str):
    """Append one bridge turn's real usage. `ev` is a stream-json result event."""
    u = ev.get("usage") or {}
    row = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "lane": "bridge", "model": ev.get("model") or model,
        "cost_usd": ev.get("total_cost_usd"),
        "ms": ev.get("duration_ms"),
        "in": u.get("input_tokens", 0), "out": u.get("output_tokens", 0),
        "cache_w": u.get("cache_creation_input_tokens", 0),
        "cache_r": u.get("cache_read_input_tokens", 0),
    }
    with _chat_lock:
        with USAGE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")

# ---- live copilot bridge -------------------------------------------------
# New chat-inbox lines automatically get a headless `claude -p` turn (no more
# telling CC "check chat"). Windows spawn wisdom cribbed from the Interview
# Bot: .cmd shims must go through cmd.exe, the prompt travels over stdin, and
# the fast flags skip the MCP/plugin boot that eats most of the latency.
BRIDGE = bool(_cfg.get("bridge", True))
BRIDGE_MODEL = str(_cfg.get("bridge_model", "sonnet"))
BRIDGE_TIMEOUT = int(_cfg.get("bridge_timeout", 150))   # legacy key, no longer enforced
# Kill on SILENCE, not on elapsed time. A turn that is still emitting tool events
# is working, not hung, and board-building legitimately runs for minutes. The old
# fixed deadline killed healthy turns whose panels had already landed on disk,
# which read as "it failed" and got the same work asked for twice.
BRIDGE_IDLE_TIMEOUT = int(_cfg.get("bridge_idle_timeout", 90))   # quiet seconds = hung
# ...but a tool that is RUNNING emits nothing either, and that is the hole the
# idle timeout left. A scan that takes 123s produces one tool_use event and then
# silence until it returns, which is byte-for-byte what a hang looks like. The
# 2026-08-13 morning routine died at 306s this way, mid-Bash, with the dock
# showing "Bash - 123s" - the UI knew the turn was alive while the watchdog
# killed it for being quiet. Silence WITH a tool in flight gets its own, longer
# allowance; silence with nothing running is still 90s.
BRIDGE_TOOL_TIMEOUT = int(_cfg.get("bridge_tool_timeout", 600))  # one tool call
BRIDGE_MAX_S = int(_cfg.get("bridge_max_s", 900))                # absolute ceiling
_bridge = {"status": "off", "turns": 0, "last_ms": 0, "error": ""}
_bridge_current = {"proc": None}   # the in-flight claude process, so Stop can kill it


def _kill_proc(p):
    """Kill the WHOLE process tree. Killing only the parent leaves children
    holding the stdout pipe (reader blocks forever), and closing the pipe from
    another thread deadlocks against the blocked reader - so: tree kill, then
    the pipe EOFs naturally. Windows: taskkill /T; POSIX: killpg."""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)],
                           capture_output=True, timeout=10,
                           stdin=subprocess.DEVNULL, **NOWIN)
        else:
            import signal
            os.killpg(os.getpgid(p.pid), signal.SIGKILL)
    except Exception:
        try:
            p.kill()
        except Exception:
            pass

BRIDGE_PROMPT = """You are the LIVE bridge turn for the MARKET FORGE trading dashboard. CLAUDE.md in
this folder is your full brief; its trading rules are HARD rules for you too.

The user's standing MEMORY (memory.md - HONOR these, they override defaults; if they say
"remember ..." APPEND it to memory.md as a "- " bullet and confirm):
{memory}
- Reply CONVERSATIONALLY in 1-3 short sentences. Your stdout IS the reply: it renders in
  the COPILOT tab and is SPOKEN ALOUD via Voicebox. Plain text only - no markdown, no lists.
- You MAY use tools: build/edit panels/*.html (the Workbench renders live, ~2.5s) and read
  the bot over http://{bot}/api/* (status, radar, reddit, bars, news, spark, unprotected,
  broker/orders). When an answer deserves a panel, build it and say so in a sentence.
- TRADINGVIEW is open in a browser you can drive, on THIS dashboard's own API
  (http://127.0.0.1:{port}), not the bot's:
    POST /api/tv/open  {{"symbol":"AMWL","interval":"1d"}}   move his chart
    POST /api/tv/shot  {{}}                                  capture it to tv-shots/*.png
  **"pull up X", "show me X", "chart X", "put X on the daily" = MOVE HIS TRADINGVIEW
  CHART with /api/tv/open.** Do not answer that by building a panel - he is looking at
  TradingView, and a panel is not what he asked for. Build a panel when he asks for a
  board, a brief, a review, a comparison, or research he wants to keep.
  Both is fine when it helps: move the chart, then build the notes beside it.
- NEVER place a trade from this lane. The trade ticket and the interactive CC window are
  The operator's execution lanes, not yours.

Recent conversation:
{history}

Newest message(s) from the operator:
{new}"""


# Which coding agents this desk can drive. The dialects differ in ways that are
# not cosmetic: how the prompt is delivered, which flags exist, and what the
# output stream looks like.
#
#   claude - prompt on stdin, stream-json events, so the UI shows REAL tool steps
#   codex  - prompt as an argument (`codex exec` hangs on an open stdin), plain
#            text out, so the turn works but without live step reporting
#
# AGENT_BIN pins the binary, AGENT_KIND pins the dialect. CLAUDE_BIN still works.
AGENT_KINDS = {
    "claude": {
        "names": ("claude", "claude.cmd", "claude.exe"),
        "probe": (lambda: [Path(os.environ.get("APPDATA", "x")) / "npm" / "claude.cmd",
                           Path.home() / ".local" / "bin" / "claude.exe",
                           Path.home() / ".local" / "bin" / "claude"]),
        "stream": True,
    },
    "codex": {
        "names": ("codex", "codex.cmd", "codex.exe"),
        "probe": (lambda: [Path.home() / ".local" / "bin" / "codex.exe",
                           Path.home() / ".local" / "bin" / "codex"]),
        "stream": False,
    },
}


def _resolve_agent():
    """(binary, kind) for the copilot's CLI, or (None, None)."""
    want = (os.environ.get("AGENT_KIND", "auto") or "auto").lower()
    override = os.environ.get("AGENT_BIN") or os.environ.get("CLAUDE_BIN")
    if override:
        if want in AGENT_KINDS:
            return override, want
        low = str(override).lower()
        return override, next((k for k in AGENT_KINDS if k in low), "claude")
    order = [want] if want in AGENT_KINDS else list(AGENT_KINDS)
    for k in order:
        for name in AGENT_KINDS[k]["names"]:
            f = shutil.which(name)
            if f:
                return f, k
        for cand in AGENT_KINDS[k]["probe"]():
            if cand.exists():
                return str(cand), k
    return None, None


def _resolve_claude():
    """Back-compat: the binary only. Prefer _resolve_agent()."""
    return _resolve_agent()[0]


def _iter_lines(proc):
    """Yield stdout lines; a closed-from-our-side pipe (Stop) ends cleanly."""
    try:
        yield from proc.stdout
    except (ValueError, OSError):
        return


def _bridge_turn(new_texts):
    """One bridge turn over stream-json so the UI can show REAL steps (the
    actual tool calls: Read radar, Write panels/x.html) instead of a fake
    progress bar. Fallback to plain text if the stream never yields a result."""
    bin_, kind = _resolve_agent()
    if not bin_:
        return ("Bridge error: no coding-agent CLI found. Install Claude Code or "
                "Codex and put it on PATH, or set AGENT_BIN to the full path, then "
                "restart the desk.")
    hist = [*_read_jsonl(INBOX, 20), *_read_jsonl(OUTBOX, 20)]
    hist.sort(key=lambda m: str(m.get("ts", "")))
    hist_txt = "\n".join(f"{m.get('role')}: {str(m.get('text', ''))[:300]}" for m in hist[-14:])
    mem = ""
    try:
        mem = MEMORY.read_text(encoding="utf-8", errors="replace")[:1400]
    except Exception:
        pass
    prompt = BRIDGE_PROMPT.format(bot=BOT.split("//", 1)[-1], port=PORT,
                                  memory=mem or "(empty)",
                                  history=hist_txt or "(none)", new="\n".join(new_texts))
    streaming = AGENT_KINDS.get(kind, {}).get("stream", False)
    if kind == "codex":
        # `codex exec` takes the prompt as an ARGUMENT and hangs forever on an
        # open stdin, so the prompt cannot go the way claude's does. It also has
        # no stream-json equivalent we parse, so this turn runs "quiet": you get
        # the answer, just no live tool steps in the activity dock.
        argv = [bin_, "exec", "--model", BRIDGE_MODEL,
                "--skip-git-repo-check", prompt]
        stdin_mode = subprocess.DEVNULL
    else:
        argv = [bin_, "-p", "--model", BRIDGE_MODEL, "--dangerously-skip-permissions",
                "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
                "--no-session-persistence",
                "--output-format", "stream-json", "--verbose"]  # verbose is REQUIRED for stream-json in -p mode
        stdin_mode = subprocess.PIPE
    if os.environ.get("AGENT_ARGS"):
        # Escape hatch: a CLI's flags are not ours to guarantee, and being able
        # to fix an argv without editing code is worth more than being clever.
        argv = [bin_] + [a.replace("{model}", BRIDGE_MODEL).replace("{prompt}", prompt)
                         for a in os.environ["AGENT_ARGS"].split()]
    if bin_.lower().endswith((".cmd", ".bat")):
        argv = ["cmd", "/c"] + argv          # .cmd shims cannot be exec'd directly
    proc = subprocess.Popen(argv, stdin=stdin_mode, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, text=True, cwd=str(WORK),
                            encoding="utf-8", errors="replace",
                            start_new_session=(os.name != "nt"),  # own group -> killpg gets the tree
                            **NOWIN)
    _bridge_current["proc"] = proc

    # Watchdog: kill only after BRIDGE_IDLE_TIMEOUT seconds of TOTAL SILENCE, or
    # at the absolute BRIDGE_MAX_S ceiling. Every stream event resets the clock,
    # so a turn that is genuinely working is never killed for taking a while.
    started = time.time()
    last_event = [started]
    # [name] of the tool currently executing, or [None]. Set when a tool_use
    # event arrives, cleared by the NEXT line off the stream (which is the tool
    # returning). While this is set, silence is expected work, not a hang.
    in_tool = [None]
    killed = {"why": None}

    def _watchdog():
        while proc.poll() is None:
            now = time.time()
            tool = in_tool[0]
            limit = BRIDGE_TOOL_TIMEOUT if tool else BRIDGE_IDLE_TIMEOUT
            if now - last_event[0] > limit:
                killed["why"] = (f"let {tool} run {limit}s with no output"
                                 if tool else f"went quiet for {limit}s")
                _kill_proc(proc)
                return
            if now - started > BRIDGE_MAX_S:
                killed["why"] = f"hit the {BRIDGE_MAX_S}s ceiling"
                _kill_proc(proc)
                return
            time.sleep(2)

    threading.Thread(target=_watchdog, daemon=True).start()
    try:
        if proc.stdin is not None:
            proc.stdin.write(prompt)
            proc.stdin.close()
        text_parts, result_text = [], None
        for line in _iter_lines(proc):
            last_event[0] = time.time()      # proof of life; resets the watchdog
            in_tool[0] = None                # a line means the tool came back
            line = line.strip()
            if not line:
                continue
            if not streaming:
                # Plain-text agent: every line IS the answer. No tool events to
                # report, so the dock stays quiet rather than showing fake steps.
                text_parts.append(line)
                continue
            try:
                ev = json.loads(line)
            except Exception:
                continue
            et = ev.get("type")
            if et == "assistant":
                for blk in (ev.get("message", {}).get("content") or []):
                    if blk.get("type") == "tool_use":
                        inp = blk.get("input") or {}
                        hint = str(inp.get("file_path") or inp.get("path")
                                   or inp.get("command") or inp.get("url") or "")
                        hint = hint.replace(str(ROOT), "").strip("\\/")[:56]
                        _bridge["steps"] = _bridge.get("steps", 0) + 1
                        _bridge["step"] = f"{blk.get('name', 'tool')}{' · ' + hint if hint else ''}"
                        # About to go silent for as long as this tool takes.
                        in_tool[0] = blk.get("name", "a tool")
                    elif blk.get("type") == "text" and blk.get("text"):
                        text_parts.append(blk["text"])
            elif et == "result":
                result_text = ev.get("result")
                # The result event carries REAL usage and cost for the turn, so the
                # Admin tab reports measured spend rather than a token estimate.
                # (The bridge runs --no-session-persistence, so these turns never
                # appear in ~/.claude/projects - this file is the only record.)
                try:
                    _usage_log(ev, BRIDGE_MODEL)
                except Exception:
                    pass
        proc.wait(timeout=15)
    finally:
        _bridge_current["proc"] = None
    if _bridge.pop("stopping", None):
        return "(stopped by you - say the word when you want me back on it)"
    sep = " " if streaming else "\n"
    partial = (result_text or sep.join(text_parts) or "").strip()
    if killed["why"]:
        # Say what landed. A killed turn has usually already written its panels
        # and files, so reporting a bare error invites you to ask for the same
        # work twice - which is exactly what used to happen.
        note = (f"(I {killed['why']} and was stopped after "
                f"{int(time.time() - started)}s. Anything I had already written to "
                f"disk is saved - check the Workbench before asking again.)")
        return f"{partial}\n\n{note}".strip() if partial else note
    if proc.returncode and proc.returncode != 0 and not partial:
        return f"Bridge error (exit {proc.returncode})."
    return partial or "(no reply)"


def _bridge_loop():
    _bridge["status"] = "idle"
    seen = len(_read_jsonl(INBOX, 100000))   # only answer messages sent from now on
    while True:
        time.sleep(1.5)
        try:
            msgs = _read_jsonl(INBOX, 100000)
            if len(msgs) <= seen:
                continue
            new = [str(m.get("text", "")) for m in msgs[seen:]]
            seen = len(msgs)
            _bridge["status"] = "thinking"
            _bridge["since"] = round(time.time(), 1)
            _bridge["steps"] = 0
            _bridge["step"] = ""
            t0 = time.time()
            try:
                reply = _bridge_turn(new)
            except subprocess.TimeoutExpired:
                reply = (f"Bridge error: the agent went quiet for "
                         f"{BRIDGE_IDLE_TIMEOUT}s and was stopped.")
            except Exception as e:
                reply = f"Bridge error: {str(e)[:150]}"
            _bridge["last_ms"] = int((time.time() - t0) * 1000)
            _bridge["turns"] += 1
            entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "role": "assistant",
                     "text": reply.strip()[:1500]}
            with _chat_lock:
                with OUTBOX.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(entry) + "\n")
            _journal_log("bridge", f"({_bridge['steps']} steps, {_bridge['last_ms'] // 1000}s) {reply[:160]}")
            _bridge["status"] = "idle"
        except Exception as e:
            _bridge["error"] = str(e)[:140]
            _bridge["status"] = "idle"

_chat_lock = threading.Lock()

# Only these bot GET paths are reachable through the proxy (mirror of what the
# UI needs; keeps anything else on the bot unreachable from the browser).
BOT_GET = {"status", "positions", "orders", "equity", "radar", "reddit",
           "config", "log", "spark", "bars", "news", "unprotected",
           "broker/orders",   # live broker orders; /orders is the local ledger
           "regime",          # market regime gate (read-only, additive)
           "paper/status",    # is the paper account linked (proves it by fetching)
           "paper/overview",  # the PAPER tab: shadow book, read-only
           "paper/unprotected",  # paper positions with no working exit
           "scanlog",         # every decision from the last scan, incl. rejects
           "changed",         # the latest "what changed" diff (see also /api/brief)
           "shutdown-check"}  # is anything working that must not be abandoned


_update_cache = {"ts": 0.0, "latest": None, "url": None}


def _newer(a: str, b: str) -> bool:
    """Is version a newer than b? Tolerates junk by comparing what parses."""
    def parts(v):
        out = []
        for chunk in str(v or "").strip().lstrip("vV").split("."):
            digits = "".join(c for c in chunk if c.isdigit())
            out.append(int(digits) if digits else 0)
        return out
    pa, pb = parts(a), parts(b)
    pa += [0] * (len(pb) - len(pa))
    pb += [0] * (len(pa) - len(pb))
    return pa > pb


def _check_update():
    """Ask GitHub once a day whether a newer release exists. Notify only.

    Deliberately NOT an auto-updater. Windows cannot overwrite a running exe, so
    self-update needs a helper process and a restart dance, and getting that
    wrong on an app that holds broker keys is a bad trade. Set
    "update_check": false in config.json to disable the call entirely.
    """
    if not _cfg.get("update_check", True):
        return None
    if time.time() - _update_cache["ts"] < 86400:
        return _update_cache
    _update_cache["ts"] = time.time()
    try:
        req = urllib.request.Request(
            "https://api.github.com/repos/almnjoy/MarketForge/releases/latest",
            headers={"Accept": "application/vnd.github+json",
                     "User-Agent": f"MarketForge/{VERSION}"})
        with urllib.request.urlopen(req, timeout=6) as r:
            d = json.loads(r.read())
        _update_cache["latest"] = str(d.get("tag_name") or "").lstrip("vV")
        _update_cache["url"] = d.get("html_url")
    except Exception:
        pass                      # offline, rate-limited, or no release yet
    return _update_cache


# Files the user may edit from inside the app. An ALLOWLIST, not a filter: a
# path-traversal check on a free-form name is a bug waiting to happen, and this
# server has broker keys in the same process. bot/.env is deliberately absent -
# keys go through the setup wizard, which validates them against Alpaca first.
EDITABLE = {
    "RULES.md":    ("Your trading plan. The copilot treats it as binding.", "md"),
    "memory.md":   ("Standing orders injected into every copilot turn.", "md"),
    "AGENTS.md":   ("The copilot's brief and hard rules. CANONICAL - edit this one.", "md"),
    "CLAUDE.md":   ("Generated copy of AGENTS.md, for CLIs that look for this name.", "md"),
    "PROMPTS.md":  ("Prompts that reliably work. Notes to yourself.", "md"),
    "config.json": ("Ports, model, theme, voice. Must stay valid JSON.", "json"),
}


def _editable_path(name: str):
    if name not in EDITABLE:
        return None
    return WORK / name


def _write_atomic(path: Path, text: str):
    """Write via a temp file + replace, keeping one .bak.

    A half-written RULES.md is a copilot reading a truncated trading plan, and a
    half-written config.json will not boot. Neither is worth saving two lines.
    """
    if path.exists():
        try:
            path.with_suffix(path.suffix + ".bak").write_bytes(path.read_bytes())
        except Exception:
            pass
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    tmp.replace(path)


# Scanner settings the UI may write into bot/.env. Allowlisted and typed, so a
# form post can never inject an arbitrary env var into the engine's process.
#   name: (kind, min, max)   kind: int | float | csv_hours | csv_words | bool
SCAN_KEYS = {
    "RADAR_SCAN_HOURS":        ("csv_hours", 0, 23),
    "RADAR_MIN_MOVE_PCT":      ("float", 0.5, 100),
    "RADAR_TOP_N":             ("int", 1, 200),
    "RADAR_MIN_PRICE_CENTS":   ("int", 0, 1_000_000),
    "RADAR_LLM_MIN_SCORE":     ("int", 0, 100),
    "RADAR_REDDIT_ENABLED":    ("bool", 0, 1),
    "RADAR_REDDIT_SUBS":       ("csv_words", 0, 0),
    "RADAR_REDDIT_CACHE_SECS": ("int", 60, 86_400),
}


def _coerce_scan(key, val):
    """Validate one setting. Returns (ok, cleaned_string_or_error)."""
    kind, lo, hi = SCAN_KEYS[key]
    s = str(val).strip()
    try:
        if kind == "int":
            n = int(float(s))
            if not (lo <= n <= hi):
                return False, f"{key} must be between {lo} and {hi}"
            return True, str(n)
        if kind == "float":
            f = float(s)
            if not (lo <= f <= hi):
                return False, f"{key} must be between {lo} and {hi}"
            return True, str(f)
        if kind == "bool":
            return True, "true" if s.lower() in ("1", "true", "yes", "on") else "false"
        if kind == "csv_hours":
            hrs = sorted({int(x) for x in s.replace(" ", "").split(",") if x.isdigit()})
            if not hrs:
                return False, "pick at least one scan hour, or the radar never runs"
            if any(h < lo or h > hi for h in hrs):
                return False, "scan hours must be 0-23"
            return True, ",".join(str(h) for h in hrs)
        if kind == "csv_words":
            words = [w.strip().lstrip("r/") for w in s.split(",")]
            words = [w for w in words if w and w.replace("_", "").isalnum()]
            if not words:
                return False, "give at least one subreddit"
            return True, ",".join(words)
    except (TypeError, ValueError):
        return False, f"{key}: '{s}' is not a valid value"
    return False, f"{key}: unsupported"


def _env_update(updates: dict):
    """Rewrite matching KEY= lines in bot/.env, appending any that are missing.

    Line-oriented on purpose: the file carries the template's comments and
    ordering, and those comments are the only documentation most people will
    read. Atomic, with a .bak, because a truncated .env is an engine that will
    not boot.
    """
    path = BOT_HOME / ".env"
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    remaining = dict(updates)
    out = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            k = stripped.split("=", 1)[0].strip()
            if k in remaining:
                out.append(f"{k}={remaining.pop(k)}")
                continue
        out.append(line)
    for k, v in remaining.items():
        out.append(f"{k}={v}")
    _write_atomic(path, "\n".join(out) + "\n")


def _qs_truthy(qs: str, key: str) -> bool:
    vals = urllib.parse.parse_qs(qs or "").get(key) or []
    return bool(vals) and str(vals[0]).lower() not in ("0", "false", "no", "")


def _shutdown_safety(timeout=8):
    """(safe, reasons) - is anything working that must not be abandoned?

    Fails OPEN when the ENGINE is unreachable: if the engine is already down it
    is not protecting anything, so blocking the quit is friction with no safety
    benefit. The engine's own handler fails CLOSED when the BROKER is unreachable,
    which is the case that actually matters.
    """
    try:
        raw, _code = _bot_get("shutdown-check", timeout=timeout)
        data = json.loads(raw)
        return bool(data.get("safe")), list(data.get("reasons") or [])
    except Exception:
        return True, []


def _bot_get(path_qs: str, timeout=25):
    req = urllib.request.Request(f"{BOT}/api/{path_qs}", headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(), r.status


def _bot_post(path: str, body: dict, timeout=45):
    data = json.dumps(body).encode()
    req = urllib.request.Request(f"{BOT}/api/{path}", data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(), r.status


# ---------------------------------------------------------------------------
# SCHEDULED SCANS ("cron")
#
# Asked for 2026-08-11: "when I ask for 30min scans, I can see where it's
# logged". The scans were happening with no visible record, so there was no way
# to tell a scan that found nothing from a scheduler that had quietly died.
#
# Deliberately an in-process timer thread, NOT Windows Task Scheduler. A Windows
# task would fire whether or not the desk is up, and a scan with no desk to write
# to is a scan that goes nowhere. This lives and dies with the app, which is the
# honest lifetime, and every run appends to journal.jsonl where the JOURNAL tab
# already renders it.
# ---------------------------------------------------------------------------
SCHEDULE_FILE = WORK / "schedule.json"
_sched_lock = threading.Lock()
_sched_state = {"runs": []}          # in-memory ring of recent runs


def _sched_load():
    try:
        d = json.loads(SCHEDULE_FILE.read_text(encoding="utf-8"))
    except Exception:
        d = {}
    return {"enabled": bool(d.get("enabled", False)),
            "every_min": int(d.get("every_min", 30) or 30),
            "job": str(d.get("job", "radar")),
            "market_hours_only": bool(d.get("market_hours_only", True)),
            "last_run": d.get("last_run"),
            # THE FIELD THE DUE-CHECK ACTUALLY READS. It was not in this dict,
            # so every reload lost it: `float(None or 0)` is 0, `time.time() - 0`
            # is enormous, and therefore ALWAYS overdue. The worker ticks every
            # 20s, so enabling a 30-minute scan fired it three times in 43
            # seconds. An allowlisting loader that forgets one key silently
            # resets the state machine it is loading.
            "last_run_ts": float(d.get("last_run_ts") or 0),
            "last_result": d.get("last_result"),
            "next_run": d.get("next_run")}


def _sched_save(d):
    try:
        SCHEDULE_FILE.write_text(json.dumps(d, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"[schedule] could not persist: {e}")


def _market_hours_now():
    """Rough RTH check in ET, weekdays 09:30-16:00. Good enough to avoid
    scanning at 3am; not a holiday calendar and does not pretend to be."""
    try:
        from datetime import datetime, timedelta, timezone
        et = datetime.now(timezone.utc) - timedelta(hours=(4 if 3 <= datetime.now(timezone.utc).month <= 10 else 5))
        if et.weekday() >= 5:
            return False
        mins = et.hour * 60 + et.minute
        return 570 <= mins <= 960
    except Exception:
        return True


def _sched_worker():
    while True:
        try:
            s = _sched_load()
            if s["enabled"]:
                # First enable runs once immediately (last_run_ts == 0), then the
                # interval governs. Guarded by last_run_ts surviving the reload.
                due = (time.time() - float(s.get("last_run_ts") or 0)
                       >= s["every_min"] * 60)
                if due:
                    skip = s["market_hours_only"] and not _market_hours_now()
                    if skip:
                        # Say it, once per cycle. A silent skip is why "is the
                        # scheduler alive?" was unanswerable.
                        s["last_result"] = "skipped (outside market hours)"
                    else:
                        try:
                            raw, code = _bot_post(f"run/{s['job']}", {}, timeout=120)
                            # Jobs return different shapes: the radar returns a
                            # LIST of alerts, the change-brief returns a DICT.
                            # len() on the dict would have reported "5 result(s)"
                            # meaning five keys, which is a number that looks
                            # like information and is not.
                            out = json.loads(raw) if raw else None
                            if isinstance(out, list):
                                s["last_result"] = f"ok, {len(out)} alert(s)"
                            elif isinstance(out, dict) and "quiet" in out:
                                s["last_result"] = ("ok, nothing changed" if out.get("quiet")
                                                    else f"ok, {len(out.get('changes') or [])} change(s)")
                            else:
                                s["last_result"] = "ok"
                            _journal_log("scan", f"scheduled {s['job']} run "
                                                 f"(every {s['every_min']}m): {s['last_result']}")
                        except Exception as e:
                            s["last_result"] = f"FAILED: {str(e)[:140]}"
                            _journal_log("scan", f"scheduled {s['job']} scan FAILED: {str(e)[:140]}")
                    s["last_run"] = time.strftime("%Y-%m-%dT%H:%M:%S")
                    s["last_run_ts"] = time.time()
                    s["next_run"] = time.strftime("%Y-%m-%dT%H:%M:%S",
                                                  time.localtime(time.time() + s["every_min"] * 60))
                    with _sched_lock:
                        _sched_save(s)
                    _sched_state["runs"].insert(0, {"ts": s["last_run"], "job": s["job"],
                                                    "result": s["last_result"]})
                    del _sched_state["runs"][20:]
        except Exception as e:
            print(f"[schedule] worker error: {e}")
        time.sleep(20)


threading.Thread(target=_sched_worker, daemon=True).start()


def _size_advisories(symbol, side, body):
    """Plain-language size notices for a ticket about to be staged.

    Advisory ONLY. This never returns a veto and nothing downstream treats it as
    one. It exists so the operator sees the number before he clicks, in the
    sentence he asked for:

        "$5,000 account. 10 shares at $500 = $5,000, 25% of the account."

    Best-effort by design: if the engine is unreachable or a price is missing it
    returns fewer notices rather than blocking a trade over its own plumbing.
    """
    try:
        raw, _ = _bot_get("status", timeout=6)
        st = json.loads(raw)
    except Exception:
        return []
    equity = float(st.get("equity") or 0)
    if equity <= 0:
        return []

    notional = body.get("notional")
    qty = body.get("qty")
    px = None
    try:
        raw, _ = _bot_get(f"bars?symbol={urllib.parse.quote(symbol)}&limit=1", timeout=8)
        bars = json.loads(raw)
        rows = bars.get("bars") if isinstance(bars, dict) else bars
        if rows:
            px = float(rows[-1].get("c") or 0)     # /api/bot/bars returns DOLLARS
    except Exception:
        px = None

    if notional is None and qty and px:
        notional = float(qty) * px
    if notional is None:
        return []
    notional = float(notional)
    if qty is None and px:
        qty = int(notional // px) or None

    pct = notional / equity
    out = []
    per_share = f"each share is about ${px:,.2f}, " if px else ""
    shares = f"{qty} share{'s' if qty != 1 else ''} " if qty else ""
    out.append({
        "code": "sizing",
        "severity": "caution" if pct > 0.10 else "info",
        "message": (f"${equity:,.2f} account. {shares}{per_share}"
                    f"${notional:,.2f} committed puts you at {pct:.0%} of the "
                    f"overall account.").replace("  ", " "),
    })
    if side == "sell":
        out.append({
            "code": "short_venue",
            "severity": "info",
            "message": "Short ticket. The live Alpaca account is under the "
                       "$2,000 Reg T minimum, so this can only fill on paper or "
                       "in E*TRADE by hand.",
        })
    return out


def _read_jsonl(path: Path, limit=200):
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]:
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def _vb_profiles():
    """Every Voicebox profile as [{id, name, engine, is_clone}].

    Voicebox reports the engine as `preset_engine` for its built-in voices and
    `default_engine` for voices you cloned yourself - which is how we tell the
    two apart."""
    req = urllib.request.Request(f"{VOICEBOX}/profiles", headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=8) as r:
        data = json.loads(r.read())
    items = data if isinstance(data, list) else data.get("profiles", [])
    out = []
    for p in items:
        pid = p.get("id") or p.get("profile_id")
        if not pid:
            continue
        preset = (p.get("preset_engine") or "").lower()
        out.append({"id": pid,
                    "name": str(p.get("name") or p.get("title") or ""),
                    "engine": preset or (p.get("default_engine") or "").lower(),
                    "is_clone": not preset})
    return out


def _vb_pick_profile(want=None):
    """Which voice actually speaks. Priority:

      1. an explicit profile id/name passed per request
      2. config.json `voicebox_profile` (id, or a case-insensitive name match)
      3. a KOKORO preset, then any preset, then a clone, then anything

    UNCONFIGURED defaults prefer presets now (flipped 2026-08-06): the first
    exe run auto-picked a cloned voice, whose cloning engine ground the GPU for
    minutes and wedged Voicebox's whole HTTP loop - the desk went silent with
    no error. Presets (kokoro) answer in a couple of seconds, every install
    that has Voicebox has them, and anyone who wants their clone says so with
    one config.json line - sounding like *you* is still the point, it just
    has to be a choice, not a trap."""
    want = str(want or _cfg.get("voicebox_profile") or "").strip()
    if _vb_profile["id"] and not want:
        return _vb_profile["id"]
    profs = _vb_profiles()
    if not profs:
        return None
    explicit = bool(want) and want != str(_cfg.get("voicebox_profile") or "").strip()
    pick = None
    if want:
        pick = next((p for p in profs if p["id"] == want), None) \
            or next((p for p in profs if want.lower() in p["name"].lower()), None)
    if not pick:
        pick = next((p for p in profs if p["engine"] == "kokoro"), None) \
            or next((p for p in profs if not p["is_clone"]), None) \
            or profs[0]
    # A one-off voice (say, a narrator) must not become the app's default, or the
    # dashboard quietly adopts it for every reply after the first.
    if not explicit:
        _vb_profile.update({"id": pick["id"], "name": pick["name"],
                            "engine": pick["engine"] or "", "is_clone": pick["is_clone"]})
    else:
        _vb_profile["engine"] = pick["engine"] or ""
        _vb_profile["is_clone"] = pick["is_clone"]
    return pick["id"]


def _usage_totals():
    """Measured copilot spend from usage.jsonl, split by model and by day.

    Cost is what the CLI itself reported for the turn. On a Max subscription
    that number is the API-equivalent value of work you already paid a flat fee
    for, not an incremental charge - which is exactly the "saved this month"
    framing the OpsCanvas AI Usage page uses."""
    rows = _read_jsonl(USAGE, 20000)
    tot = {"turns": 0, "cost_usd": 0.0, "tokens_total": 0, "cached": 0,
           "by_model": {}, "by_day": {}}
    for r in rows:
        t = (r.get("in", 0) or 0) + (r.get("out", 0) or 0) \
            + (r.get("cache_w", 0) or 0) + (r.get("cache_r", 0) or 0)
        c = float(r.get("cost_usd") or 0)
        m, day = r.get("model", "?"), str(r.get("ts", ""))[:10]
        tot["turns"] += 1
        tot["cost_usd"] += c
        tot["tokens_total"] += t
        tot["cached"] += (r.get("cache_r", 0) or 0)
        bm = tot["by_model"].setdefault(m, {"turns": 0, "cost_usd": 0.0, "tokens": 0})
        bm["turns"] += 1; bm["cost_usd"] += c; bm["tokens"] += t
        bd = tot["by_day"].setdefault(day, {"turns": 0, "cost_usd": 0.0, "tokens": 0})
        bd["turns"] += 1; bd["cost_usd"] += c; bd["tokens"] += t
    tot["cost_usd"] = round(tot["cost_usd"], 4)
    for d in list(tot["by_model"].values()) + list(tot["by_day"].values()):
        d["cost_usd"] = round(d["cost_usd"], 4)
    return tot


def _panels_state(root=None, order="new"):
    """[{name, title, size, mtime}]. Title from a leading <!-- title: X -->
    comment; size from <!-- size: page|full|wide|tall --> (layout hint: page = a
    whole document surface for deep dives, full = whole row, wide = double
    width, tall = extra height).

    order="new" (default): NEWEST FIRST by mtime, so a panel that was just
    written or edited lands at the top of the workbench instead of hiding behind
    whatever filename prefix sorts lower. Filename prefixes still order ties.
    order="name": the old filename sort, kept for saved-board rendering where
    the author's intended sequence matters more than recency.
    """
    root = root or PANELS
    items = []
    if root.exists():
        for f in sorted(root.glob("*.html")):
            title, size = f.stem, "normal"
            try:
                head = f.read_text(encoding="utf-8", errors="replace")[:400]
                if "title:" in head:
                    title = head.split("title:", 1)[1].split("-->", 1)[0].strip() or title
                if "size:" in head:
                    cand = head.split("size:", 1)[1].split("-->", 1)[0].strip().lower()
                    if cand in ("page", "full", "wide", "tall"):
                        size = cand
            except Exception:
                pass
            items.append({"name": f.name, "title": title, "size": size,
                          "mtime": round(f.stat().st_mtime, 2)})
    if order == "new":
        items.sort(key=lambda i: i["mtime"], reverse=True)
    return items


def _safe_board_name(name):
    keep = "".join(c if (c.isalnum() or c in "-_ ") else "-" for c in str(name)).strip()
    return keep.replace(" ", "-")[:60] or None


# Browsers abort in-flight polls constantly (refresh, tab close, superseded
# fetch). On Windows that surfaces as WinError 10053/10054 mid-write; it is
# routine, not a failure - swallow it instead of stack-tracing the console.
_CLIENT_GONE = (ConnectionAbortedError, ConnectionResetError, BrokenPipeError, TimeoutError)


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        import sys
        exc = sys.exc_info()[1]
        if isinstance(exc, _CLIENT_GONE):
            return
        super().handle_error(request, client_address)


class Handler(BaseHTTPRequestHandler):
    server_version = "AgenticStockLocal/1.0"

    # ---- helpers ----
    def _json(self, obj, code=200):
        raw = json.dumps(obj).encode()
        try:
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        except _CLIENT_GONE:
            pass  # client already gone; nothing to tell it

    def _raw(self, raw: bytes, ctype: str, code=200):
        try:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        except _CLIENT_GONE:
            pass

    def _file(self, path: Path, ctype: str):
        try:
            self._raw(path.read_bytes(), ctype)
        except FileNotFoundError:
            self._json({"error": "not found"}, 404)

    def log_message(self, fmt, *args):  # quiet console; errors still surface
        pass

    # ---- GET ----
    # One endpoint raising must not take down the request thread. Before this,
    # a ValueError in the Admin file list left the tab silently EMPTY with the
    # traceback only in a log file nobody opens. A 500 with the reason in it is
    # something you can actually see and report.
    def do_GET(self):
        LAST_CLIENT[0] = time.time()
        try:
            return self._get()
        except Exception as e:
            traceback.print_exc()
            return self._json({"error": f"{e.__class__.__name__}: {str(e)[:300]}",
                               "where": self.path}, 500)

    def do_POST(self):
        LAST_CLIENT[0] = time.time()
        try:
            return self._post()
        except Exception as e:
            traceback.print_exc()
            return self._json({"error": f"{e.__class__.__name__}: {str(e)[:300]}",
                               "where": self.path}, 500)

    def _get(self):
        parsed = urllib.parse.urlparse(self.path)
        path, qs = parsed.path, parsed.query

        if path in ("/", "/index.html"):
            # First run (no usable keys in bot/.env): the desk would just be
            # empty panels and proxy errors, so serve the setup wizard instead.
            # Checked server-side on every load - the frontend never guesses.
            try:
                if setup_core.first_run_state()["first_run"]:
                    return self._file(STATIC / "setup.html", "text/html; charset=utf-8")
            except Exception as e:
                # Do NOT swallow this silently. If the first-run check breaks,
                # the app serves the desk to someone with no keys, which looks
                # like "the engine is down" rather than "you have not set up".
                print(f"[setup] first-run check failed ({e.__class__.__name__}: "
                      f"{str(e)[:150]}) - serving the desk")
            return self._file(STATIC / "index.html", "text/html; charset=utf-8")
        if path == "/setup":
            # re-runnable any time, prefilled with current values
            return self._file(STATIC / "setup.html", "text/html; charset=utf-8")
        if path.startswith("/static/"):
            f = (STATIC / path[len("/static/"):]).resolve()
            if not str(f).startswith(str(STATIC.resolve())):
                return self._json({"error": "bad path"}, 400)
            ctype = ("text/css" if f.suffix == ".css" else
                     "application/javascript" if f.suffix == ".js" else
                     "image/svg+xml" if f.suffix == ".svg" else
                     "image/png" if f.suffix == ".png" else
                     "image/jpeg" if f.suffix in (".jpg", ".jpeg") else
                     "text/html" if f.suffix == ".html" else "application/octet-stream")
            return self._file(f, f"{ctype}; charset=utf-8")

        # bot proxy: /api/bot/<endpoint>[?qs]
        if path.startswith("/api/bot/"):
            ep = path[len("/api/bot/"):]
            if ep.split("?")[0] not in BOT_GET:
                return self._json({"error": f"endpoint '{ep}' not allowed"}, 404)
            try:
                raw, code = _bot_get(ep + (f"?{qs}" if qs else ""))
                return self._raw(raw, "application/json", code)
            except urllib.error.HTTPError as e:
                return self._raw(e.read(), "application/json", e.code)
            except Exception as e:
                return self._json({"error": f"bot unreachable: {str(e)[:120]}"}, 502)

        if path == "/api/panels":
            return self._json({"panels": _panels_state()})
        if path == "/api/panel":
            name = urllib.parse.parse_qs(qs).get("name", [""])[0]
            f = (PANELS / name).resolve()
            if not name.endswith(".html") or not str(f).startswith(str(PANELS.resolve())):
                return self._json({"error": "bad panel name"}, 400)
            return self._file(f, "text/html; charset=utf-8")

        if path == "/api/chat":
            # Conversations are grouped BY DAY. Nothing is ever deleted or
            # archived: a "new chat" is simply a new day with no messages yet,
            # and the sidebar is the list of days that do have messages.
            q = urllib.parse.parse_qs(qs)
            day = (q.get("day", [""])[0] or "")[:10]
            inbox, outbox = _read_jsonl(INBOX, 4000), _read_jsonl(OUTBOX, 4000)
            allm = inbox + outbox
            sess = {}
            for m in allm:
                d = str(m.get("ts", ""))[:10]
                if not d:
                    continue
                s = sess.setdefault(d, {"day": d, "n": 0, "last": "", "preview": ""})
                s["n"] += 1
                if str(m.get("ts", "")) >= s["last"]:
                    s["last"] = str(m.get("ts", ""))
                if m.get("role") == "user" and not s["preview"]:
                    s["preview"] = str(m.get("text", ""))[:60]
            for s in sess.values():          # fall back to any text for the label
                if not s["preview"]:
                    first = next((m for m in allm if str(m.get("ts", ""))[:10] == s["day"]), None)
                    s["preview"] = str((first or {}).get("text", ""))[:60]
            today = time.strftime("%Y-%m-%d")
            # Always list TODAY, even before it has any messages. Otherwise the
            # sidebar has no Today button on a fresh day and there is no way to
            # get back to the live conversation except reloading.
            if today not in sess:
                sess[today] = {"day": today, "n": 0, "last": "", "preview": "new day"}
            days = sorted(sess.values(), key=lambda s: s["day"], reverse=True)

            # THE BUG (fixed 2026-08-11): an absent `day` meant "today" to the
            # frontend and "everything, unfiltered" to this handler, so on a new
            # day the copilot tab opened showing the whole back history. The
            # default is now today; ask for `?day=all` to get the firehose.
            want = day or today
            if want != "all":
                inbox = [m for m in inbox if str(m.get("ts", "")).startswith(want)]
                outbox = [m for m in outbox if str(m.get("ts", "")).startswith(want)]
            return self._json({"inbox": inbox, "outbox": outbox, "days": days,
                               "day": day, "resolved_day": want, "today": today})

        if path == "/api/live":
            # Is the real-time tap running, and how much of it is actually fresh?
            # Freshness is computed HERE against the current clock, not read from
            # the file's own age_s, which was only true at flush time.
            p = BOT_HOME / "data" / "live-prices.json"
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                return self._json({"running": False, "connected": False,
                                   "fresh_count": 0, "prices": {},
                                   "hint": "python bot/src/stream.py "
                                           "(needs: pip install websocket-client)"})
            now = time.time()
            limit = float(d.get("stale_after_s") or 90)
            rows, fresh = {}, 0
            for sym, v in (d.get("prices") or {}).items():
                epoch = v.get("epoch_ts")
                age = (now - float(epoch)) if epoch else None
                ok = age is not None and age <= limit
                fresh += 1 if ok else 0
                rows[sym] = {**v, "age_s": round(age, 1) if age is not None else None,
                             "fresh": ok}
            return self._json({
                "running": (now - float(d.get("epoch") or 0)) < 30,
                "connected": bool(d.get("connected")),
                "feed": d.get("feed"),
                "error": d.get("error"),
                "stale_after_s": limit,
                "fresh_count": fresh,
                "total": len(rows),
                "caveat": d.get("caveat"),
                "prices": rows,
            })

        if path == "/api/brain":
            # A lane's brain doc, for its Settings tab. READ ONLY, and it refuses
            # to leave the brains directory: `lane` arrives from a query string,
            # so "../../bot/.env" is a thing someone could type. Resolve, then
            # confirm the resolved path is still inside the folder.
            lane = urllib.parse.parse_qs(qs).get("lane", [""])[0]
            root = (WORK / "TheTradingBrains").resolve()
            try:
                target = (root / lane / "00-BRAIN.md").resolve()
                if not str(target).startswith(str(root)):
                    return self._json({"ok": False, "error": "outside the brains folder"}, 400)
                if not target.exists():
                    tmpl = root / "_templates" / lane / "00-BRAIN.md"
                    return self._json({"ok": False, "error": "no brain written yet",
                                       "template": str(tmpl) if tmpl.exists() else None})
                return self._json({"ok": True, "lane": lane,
                                   "path": str(target),
                                   "text": target.read_text(encoding="utf-8")[:20000]})
            except Exception as e:
                return self._json({"ok": False, "error": str(e)[:200]}, 500)

        if path == "/api/schedule":
            s = _sched_load()
            s["runs"] = _sched_state["runs"][:10]
            s["market_open_now"] = _market_hours_now()
            return self._json(s)

        if path == "/api/watch":
            # one cheap poll target: anything changed?
            try:
                out_m = round(OUTBOX.stat().st_mtime, 2) if OUTBOX.exists() else 0
            except OSError:
                out_m = 0
            return self._json({"panels": _panels_state(), "outbox_mtime": out_m,
                               "ts": round(time.time(), 1)})

        if path == "/api/rules":
            txt = RULES.read_text(encoding="utf-8", errors="replace") if RULES.exists() else "# RULES.md missing"
            return self._raw(txt.encode(), "text/markdown; charset=utf-8")

        if path == "/api/tv/status":
            try:
                import tv
                return self._json(tv.status())
            except Exception as e:
                return self._json({"ok": False, "error": str(e)[:160]}, 501)

        if path == "/api/shot":
            # serve a TradingView capture back to the browser / a panel
            q = urllib.parse.parse_qs(qs)
            name = os.path.basename((q.get("name", [""])[0] or ""))
            f = (SHOTS / name).resolve()
            if not name.endswith(".png") or not str(f).startswith(str(SHOTS.resolve())):
                return self._json({"error": "bad name"}, 400)
            return self._file(f, "image/png")

        if path == "/api/staged":
            # A trade the copilot has PROPOSED. It writes the file; this only
            # reads it. Same file-bus contract as panels/ - the agent's output
            # is a file on disk, not a privileged API call, which is exactly
            # why it can never place an order by writing one.
            try:
                t = json.loads(STAGED.read_text(encoding="utf-8")) if STAGED.exists() else None
            except Exception:
                t = None
            if not isinstance(t, dict) or not t.get("symbol"):
                return self._json({"staged": None})
            ttl = int(t.get("ttl_s") or 1800)
            age = time.time() - float(t.get("ts") or 0)
            # Expire rather than hide: a ticket reasoned about at 09:40 is not a
            # one-click buy at 15:30 against a different price.
            t["age_s"] = int(age)
            t["expired"] = age > ttl
            return self._json({"staged": t})

        if path == "/api/file":
            name = urllib.parse.parse_qs(qs).get("name", [""])[0]
            p = _editable_path(name)
            if not p:
                return self._json({"error": "not an editable file"}, 404)
            try:
                text = p.read_text(encoding="utf-8") if p.exists() else ""
            except Exception as e:
                return self._json({"error": str(e)[:200]}, 500)
            what, kind = EDITABLE[name]
            return self._json({"name": name, "text": text, "what": what, "kind": kind,
                               "exists": p.exists(), "path": str(p)})

        if path == "/api/meta":
            upd = _check_update() or {}
            newer = bool(upd.get("latest") and _newer(upd["latest"], VERSION))
            return self._json({"bot_base": BOT, "port": PORT, "root": str(ROOT),
                               "workspace": str(WORK), "version": VERSION,
                               "update": {"available": newer,
                                          "latest": upd.get("latest"),
                                          "url": upd.get("url")},
                               "voicebox": VOICEBOX, "user": str(_cfg.get("user", "")),
                               "theme": str(_cfg.get("theme", "forge")),
                               "app": "marketforge",   # single-instance probe checks this
                               "shell": SHELL["shell"],
                               "splash_ms": int(_cfg.get("splash_ms", 2500)),
                               # how long you must stop talking before hot mic
                               # decides the utterance is over
                               "voice_endpoint_ms": int(_cfg.get("voice_endpoint_ms", 1800))})

        if path == "/api/shell":
            # Feature-detect, never shell-detect: the UI hides what is missing.
            return self._json(SHELL)

        if path == "/api/setup/state":
            try:
                st = setup_core.first_run_state()
                st["root"] = str(ROOT)
                return self._json(st)
            except Exception as e:
                return self._json({"error": str(e)[:160]}, 500)

        if path == "/api/setup/probe-extras":
            # Live green/grey dots for the wizard's optional-extras step.
            # Short timeouts: a wizard step must not hang on a dead socket.
            def _up(url, t=2.0):
                try:
                    with urllib.request.urlopen(url, timeout=t) as r:
                        return r.status == 200
                except Exception:
                    return False
            return self._json({
                "voicebox": _up(f"{VOICEBOX}/health"),
                "agent": bool(_resolve_agent()[0]),
                "agent_kind": _resolve_agent()[1],
                "claude": bool(_resolve_agent()[0]),   # legacy key, same answer
            })

        if path == "/api/stt/health":
            # STT rides Voicebox /transcribe; reachable Voicebox = STT on.
            try:
                with urllib.request.urlopen(f"{VOICEBOX}/health", timeout=2.5) as r:
                    return self._json({"ok": r.status == 200})
            except Exception as e:
                return self._json({"ok": False, "error": str(e)[:100]})

        if path == "/api/admin":
            # READ-ONLY inventory: what is running, on which model, from which files.
            # Deliberately cannot create or edit anything - this is a "show me what
            # is in place" screen, not a control panel. Secrets are NEVER returned,
            # only whether a file exists and how big it is.
            def finfo(p, label, what):
                # Show the path relative to whichever root it actually lives
                # under. Since the workspace split, user files are under WORK and
                # shipped files under ROOT, and WORK is NOT necessarily inside
                # ROOT - unzip the app into ~/MarketForge and WORK is its PARENT.
                # relative_to() then raises ValueError, which is not an OSError,
                # so it escaped this handler and 500'd the whole Admin tab.
                rel = p.name
                for base in (WORK, ROOT):
                    try:
                        rel = str(p.relative_to(base))
                        break
                    except ValueError:
                        continue
                try:
                    st = p.stat()
                    return {"label": label, "what": what, "path": rel,
                            "exists": True, "bytes": st.st_size,
                            "mtime": time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime))}
                except OSError:
                    return {"label": label, "what": what, "path": rel,
                            "exists": False, "bytes": 0, "mtime": ""}

            bot_cfg = {}
            try:
                raw, code = _bot_get("config", timeout=6)
                if code == 200:
                    bot_cfg = json.loads(raw)
            except Exception:
                pass
            ra = (bot_cfg.get("radar_auto") or {}) if isinstance(bot_cfg, dict) else {}
            _sc = (bot_cfg.get("radar_scoring") or {}) if isinstance(bot_cfg, dict) else {}

            jrn = _read_jsonl(JOURNAL, 4000)
            kinds = {}
            for e in jrn:
                kinds[e.get("kind", "?")] = kinds.get(e.get("kind", "?"), 0) + 1

            lanes = [{
                "name": "Bridge (in-app copilot)",
                "runtime": (lambda k: f"{k}, headless" if k else "no agent CLI found"
                            )(_resolve_agent()[1]),
                "model": BRIDGE_MODEL, "enabled": BRIDGE,
                "status": _bridge.get("status"), "turns": _bridge.get("turns", 0),
                "last_ms": _bridge.get("last_ms", 0), "timeout_s": BRIDGE_IDLE_TIMEOUT,
                "idle_timeout_s": BRIDGE_IDLE_TIMEOUT, "max_s": BRIDGE_MAX_S,
                "binary": _resolve_agent()[0] or "not found on PATH",
                "note": "One short turn per chat message. Builds panels, answers, speaks.",
            }, {
                "name": "Your terminal session", "runtime": "claude (interactive)",
                "model": "whatever /model says in that window", "enabled": None,
                "status": "external", "turns": None, "last_ms": None, "timeout_s": None,
                "binary": "", "note": "The deep-work lane. Market Forge cannot see or set its model.",
            }, {
                # Read the engine's resolved answer, do not re-derive it here.
                # The old code guessed from keys the engine never sent, so this
                # lane reported "rules-only / off" regardless of the truth.
                "name": "Catalyst scoring",
                "runtime": str(_sc.get("runtime") or "unknown"),
                "model": str(_sc.get("model") or "rules-only (no LLM)"),
                "enabled": bool(_sc.get("enabled")),
                "status": {"agent": "ready", "endpoint": "ready", "off": "off"}.get(
                    str(_sc.get("effective")), "unavailable"),
                "turns": None, "last_ms": None, "timeout_s": None,
                "binary": str(_sc.get("where") or ""),
                "note": "Scores each mover 0-100 signal-vs-noise. Auto-entries need a score.",
            }, {
                "name": "Voice", "runtime": "Voicebox (local)",
                "model": f'{_vb_profile.get("name") or _cfg.get("voicebox_profile") or "auto"}'
                         f'{" / " + _vb_profile["engine"] if _vb_profile.get("engine") else ""}',
                "enabled": True, "status": "ready" if _vb_profile.get("id") else "not probed",
                "turns": None, "last_ms": None, "timeout_s": None, "binary": VOICEBOX,
                "note": "Speech out. Falls back to the browser voice if Voicebox is down.",
            }]

            files = [
                finfo(WORK / "CLAUDE.md", "CLAUDE.md", "The copilot's brief and hard rules"),
                finfo(RULES, "RULES.md", "Your written trading plan"),
                finfo(MEMORY, "memory.md", "Standing orders injected into every turn"),
                finfo(WORK / "PROMPTS.md", "PROMPTS.md", "Prompts that reliably work"),
                finfo(WORK / "config.json", "config.json", "Ports, model, theme, Voicebox"),
                finfo(BOT_HOME / ".env", "bot/.env", "Your keys. Never displayed, never committed."),
                finfo(JOURNAL, "journal.jsonl", "Every scan, chat, order and board"),
                finfo(INBOX, "chat-inbox.jsonl", "What you said"),
                finfo(OUTBOX, "chat-outbox.jsonl", "What the copilot said"),
            ]

            return self._json({
                "runtime": {
                    "version": VERSION,
                    "update": (lambda u: {"available": bool(u.get("latest") and
                                                            _newer(u["latest"], VERSION)),
                                          "latest": u.get("latest"), "url": u.get("url")}
                               )(_check_update() or {}),
                    "python": sys.version.split()[0], "platform": sys.platform,
                    "embedded": EMBEDDED, "port": PORT, "root": str(ROOT),
                    "workspace": str(WORK), "bot_base": BOT,
                    "started": time.strftime("%Y-%m-%d %H:%M", time.localtime(_STARTED_AT)),
                    "uptime_s": int(time.time() - _STARTED_AT),
                },
                "lanes": lanes,
                "files": files,
                "counts": {
                    "panels": len(_panels_state()),
                    "boards": len([d for d in SAVED.glob("*") if d.is_dir()])
                              if SAVED.exists() else 0,
                    "journal": len(jrn), "journal_kinds": kinds,
                    "chat": len(_read_jsonl(INBOX, 4000)) + len(_read_jsonl(OUTBOX, 4000)),
                },
                # NOTE: /api/config exposes DOLLARS under short keys (notional,
                # max_exposure, min_price) - not the *_cents names from bot/.env.
                # Reading the .env names here silently rendered "--" in Admin.
                "trading": {
                    "env": bot_cfg.get("env"), "auto": bool(ra.get("execute")),
                    "live_auto": bool(ra.get("live_enabled")),
                    "per_trade": ra.get("notional"),
                    "max_exposure": ra.get("max_exposure"),
                    "min_price": ra.get("min_price"),
                    "trail_pct": ra.get("trail_pct"),
                    "max_per_day": ra.get("max_per_day"), "min_score": ra.get("min_score"),
                    "feed": bot_cfg.get("data_feed"), "mode": bot_cfg.get("mode"),
                },
                "usage": _usage_totals(),
                "recent": jrn[-40:][::-1],
            })

        if path == "/api/memory":
            txt = MEMORY.read_text(encoding="utf-8", errors="replace") if MEMORY.exists() else ""
            return self._raw(txt.encode(), "text/markdown; charset=utf-8")

        if path == "/api/journal":
            q = urllib.parse.parse_qs(qs)
            day = (q.get("day", [""])[0] or "")[:10]
            entries = _read_jsonl(JOURNAL, 5000)
            days = sorted({str(e.get("ts", ""))[:10] for e in entries if e.get("ts")}, reverse=True)
            if day:
                entries = [e for e in entries if str(e.get("ts", "")).startswith(day)]
            else:
                today = time.strftime("%Y-%m-%d")
                day = today
                entries = [e for e in entries if str(e.get("ts", "")).startswith(today)]
            return self._json({"day": day, "days": days, "entries": entries})

        if path == "/api/bridge":
            return self._json({"enabled": BRIDGE, "model": BRIDGE_MODEL, **_bridge})

        if path == "/api/workbench/saved":
            # Now carries each board's panel list so the SAVED tab can render
            # real tiles (title + preview) instead of a bare dropdown of names.
            out = []
            if SAVED.exists():
                for d in sorted(SAVED.iterdir(), key=lambda p: p.stat().st_mtime,
                                reverse=True):
                    if not d.is_dir() or d.name == "_trash":
                        continue   # deleted pages live in _trash; not listed
                    panels = _panels_state(d, order="name")
                    out.append({
                        "name": d.name,
                        "panels": len(panels),
                        "ts": round(d.stat().st_mtime, 0),
                        "auto": d.name.startswith("_autosave-"),
                        "titles": [p["title"] for p in panels][:6],
                        "files": [{"name": p["name"], "title": p["title"],
                                   "size": p["size"], "mtime": p["mtime"]}
                                  for p in panels],
                    })
            return self._json({"saved": out})

        if path == "/api/saved/panel":
            # Serve ONE panel out of a saved board, so a report can be opened
            # and read without loading it over the live workbench.
            q = urllib.parse.parse_qs(qs)
            board = _safe_board_name(q.get("board", [""])[0])
            name = q.get("name", [""])[0]
            if not board or not name.endswith(".html"):
                return self._json({"error": "board and .html name required"}, 400)
            base = (SAVED / board).resolve()
            f = (base / name).resolve()
            if not str(base).startswith(str(SAVED.resolve())) \
                    or not str(f).startswith(str(base)) or not f.is_file():
                return self._json({"error": "not found"}, 404)
            return self._file(f, "text/html; charset=utf-8")

        if path == "/api/tts/profiles":
            # every voice Voicebox knows about, so you can pick yours by name
            try:
                return self._json({"profiles": _vb_profiles(),
                                   "using": _vb_profile.get("id"),
                                   "configured": _cfg.get("voicebox_profile")})
            except Exception as e:
                return self._json({"error": f"Voicebox unreachable: {str(e)[:100]}"}, 502)

        if path == "/api/tts/health":
            try:
                pid = _vb_pick_profile()
                return self._json({"ok": bool(pid), "profile_id": pid,
                                   "voice": _vb_profile.get("name"),
                                   "engine": _vb_profile.get("engine"),
                                   "hint": None if pid else "no voice profile in Voicebox"})
            except Exception as e:
                return self._json({"ok": False, "error": f"Voicebox unreachable: {str(e)[:100]}"})

        return self._json({"error": "not found"}, 404)

    # ---- POST ----
    def _stt(self, audio: bytes, parsed):
        """Speech-to-text relay: raw audio in, text out, via Voicebox
        POST /transcribe (multipart). Exists because WebView2 has no Web Speech
        API - MediaRecorder + this endpoint work identically in every shell."""
        if not audio:
            return self._json({"error": "empty audio"}, 400)
        q = urllib.parse.parse_qs(parsed.query)
        lang = (q.get("lang", ["en"])[0] or "en")[:8]
        model = str(_cfg.get("stt_model") or "")   # blank = Voicebox's default
        ctype = self.headers.get("Content-Type") or "application/octet-stream"
        ext = {"audio/webm": "webm", "audio/ogg": "ogg", "audio/wav": "wav",
               "audio/mp4": "mp4", "audio/mpeg": "mp3"}.get(ctype.split(";")[0].strip(), "webm")
        bnd = uuid.uuid4().hex
        parts = [(f'--{bnd}\r\nContent-Disposition: form-data; name="file"; '
                  f'filename="mic.{ext}"\r\nContent-Type: {ctype}\r\n\r\n').encode()
                 + audio + b"\r\n"]
        for name, val in (("language", lang), ("model", model)):
            if val:
                parts.append((f'--{bnd}\r\nContent-Disposition: form-data; '
                              f'name="{name}"\r\n\r\n{val}\r\n').encode())
        parts.append(f"--{bnd}--\r\n".encode())
        try:
            req = urllib.request.Request(
                f"{VOICEBOX}/transcribe", data=b"".join(parts), method="POST",
                headers={"Content-Type": f"multipart/form-data; boundary={bnd}"})
            with urllib.request.urlopen(req, timeout=90) as r:
                out = json.loads(r.read())
            return self._json({"ok": True, "text": str(out.get("text") or "").strip(),
                               "duration": out.get("duration")})
        except Exception as e:
            return self._json({"ok": False,
                               "error": f"transcribe failed: {str(e)[:140]}"}, 502)

    def _post(self):
        parsed = urllib.parse.urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        if length > 25_000_000:
            return self._json({"error": "body too large"}, 413)
        raw_body = self.rfile.read(length) if length else b""

        # /api/stt carries RAW AUDIO, not JSON - branch before the parse
        if parsed.path == "/api/stt":
            return self._stt(raw_body, parsed)

        try:
            body = json.loads(raw_body or b"{}")
        except Exception:
            return self._json({"error": "bad json"}, 400)

        if parsed.path == "/api/chat/send":
            text = str(body.get("text") or "").strip()
            if not text:
                return self._json({"error": "empty"}, 400)
            entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "role": "user", "text": text}
            with _chat_lock:
                with INBOX.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(entry) + "\n")
            _journal_log("chat", text[:160])
            return self._json({"ok": True})

        if parsed.path == "/api/shell/open":
            # External links open in the SYSTEM browser. Inside a webview an
            # unhandled target=_blank either does nothing or traps the user in
            # a chromeless window with no back button; in a plain browser the
            # frontend never calls this (it uses window.open as normal).
            url = str(body.get("url") or "").strip()
            if not url.lower().startswith(("http://", "https://")):
                return self._json({"error": "http(s) urls only"}, 400)
            try:
                webbrowser.open(url)
                return self._json({"ok": True})
            except Exception as e:
                return self._json({"ok": False, "error": str(e)[:120]}, 500)

        if parsed.path == "/api/scan-settings":
            # Applying these restarts the engine. That is the same hazard as
            # quitting: a restart mid-fill drops the in-process watcher that arms
            # the stop. So it refuses on exactly the same condition.
            safe, reasons = _shutdown_safety()
            if not safe and not bool(body.get("force")):
                return self._json({"ok": False, "blocked": True, "reasons": reasons,
                                   "hint": "an entry is working - applying this "
                                           "restarts the engine"}, 409)
            updates, errors = {}, []
            for k, v in (body.get("settings") or {}).items():
                if k not in SCAN_KEYS:
                    errors.append(f"{k} is not a scan setting")
                    continue
                ok, res = _coerce_scan(k, v)
                (errors.append(res) if not ok else updates.update({k: res}))
            if errors:
                return self._json({"ok": False, "errors": errors}, 400)
            if not updates:
                return self._json({"ok": False, "errors": ["nothing to change"]}, 400)
            try:
                _env_update(updates)
            except Exception as e:
                return self._json({"ok": False, "errors": [str(e)[:200]]}, 500)
            # The supervisor respawns it; killing is how you apply a .env change,
            # since config.py reads the file once at import.
            restarted = False
            if EMBEDDED:
                try:
                    _kill_engine_now()
                    restarted = True
                except Exception:
                    pass
            return self._json({"ok": True, "applied": updates, "restarted": restarted,
                               "note": "saved" + (" - engine restarting" if restarted
                                                  else " - restart the desk to apply")})

        if parsed.path == "/api/staged/clear":
            try:
                STAGED.unlink(missing_ok=True)
            except Exception as e:
                return self._json({"ok": False, "error": str(e)[:150]}, 500)
            return self._json({"ok": True})

        if parsed.path == "/api/file":
            name = str(body.get("name", ""))
            p = _editable_path(name)
            if not p:
                return self._json({"ok": False, "error": "not an editable file"}, 404)
            text = body.get("text")
            if not isinstance(text, str):
                return self._json({"ok": False, "error": "text must be a string"}, 400)
            if EDITABLE[name][1] == "json":
                # Validate BEFORE writing. A broken config.json does not fail at
                # save time, it fails at the next launch, when the editor that
                # broke it is no longer running.
                try:
                    json.loads(text)
                except Exception as e:
                    return self._json({"ok": False, "error": f"invalid JSON: {str(e)[:160]}"}, 400)
            try:
                _write_atomic(p, text)
            except Exception as e:
                return self._json({"ok": False, "error": str(e)[:200]}, 500)
            note = ""
            if name == "config.json":
                note = "saved - config.json is read at startup, so restart the desk to apply it"
            elif name in ("CLAUDE.md", "RULES.md", "memory.md", "PROMPTS.md"):
                note = "saved - the copilot picks this up on its next turn"
            return self._json({"ok": True, "bytes": len(text.encode("utf-8")), "note": note})

        if parsed.path == "/api/shell/quit":
            # Close the shell window cleanly (the tray-less "Quit"). Runs the
            # same path as clicking X: webview loop ends, server stops, the
            # engine tree is killed. No-op in a plain browser.
            #
            # REFUSES while an entry is working. The exit guarantee only holds
            # while this process is alive: the queue re-arms on boot, but a fill
            # that lands while we are dead sits at the broker with no stop until
            # someone reopens the app. Quitting then is the one action that still
            # reproduces the original naked-position bug, so it takes an explicit
            # override rather than a warning nobody reads.
            if not _qs_truthy(parsed.query, "force"):
                safe, reasons = _shutdown_safety()
                if not safe:
                    return self._json({"ok": False, "blocked": True, "reasons": reasons,
                                       "hint": "retry with ?force=1 to quit anyway"}, 409)
            hook = SHELL_HOOKS.get("quit")
            if not hook:
                return self._json({"ok": False, "hint": "no shell to quit"})
            threading.Timer(0.3, hook).start()   # let this response flush first
            return self._json({"ok": True})

        if parsed.path == "/api/shell/focus":
            # Single-instance flow: a second launch asks the FIRST process to
            # bring its window forward, then exits. No-op in a plain browser.
            hook = SHELL_HOOKS.get("focus")
            if not hook:
                return self._json({"ok": False, "hint": "no shell window to focus"})
            try:
                hook()
                return self._json({"ok": True})
            except Exception as e:
                return self._json({"ok": False, "error": str(e)[:120]}, 500)

        if parsed.path == "/api/setup/validate-keys":
            # Wizard step 2: validate against /v2/account BEFORE letting the
            # user continue, and detect the data feed in the same round trip.
            # Wrong-keys-typed-in is the most likely failure and it must not
            # surface 20 minutes later as an empty dashboard.
            envn = str(body.get("env") or "paper").lower()
            key, sec = _setup_key_pair(envn, body)
            acct, err = setup_core.check_keys(envn, key, sec)
            if not acct:
                return self._json({"ok": False, "error": err})
            feed = setup_core.detect_feed(key, sec)
            return self._json({"ok": True, "feed": feed,
                               "account": {"status": acct.get("status"),
                                           "equity": acct.get("equity"),
                                           "currency": acct.get("currency")}})

        if parsed.path == "/api/setup/save":
            envn = str(body.get("env") or "paper").lower()
            key, sec = _setup_key_pair(envn, body)
            mode = str(body.get("mode") or "research").lower()
            if envn not in ("paper", "live") or mode not in ("research", "manual", "auto"):
                return self._json({"ok": False, "error": "bad env or mode"}, 400)
            # THE EXIT-GUARANTEE GUARD. Saving kills the engine to apply the
            # new env - but a kill landing inside the order path's fill-poll
            # window (up to ~18s after a BUY submits, before the disk-backed
            # protect row exists) would leave a filled entry with NO exit and
            # no record of the intended trail. That is the VRM failure class.
            # So: if the broker shows a working BUY, refuse and let the user
            # retry after the fill instead of killing at an arbitrary instant.
            if EMBEDDED and BOT_PID.exists():
                try:
                    pid = int(BOT_PID.read_text().strip() or 0)
                except Exception:
                    pid = 0
                if pid and _pid_alive(pid):
                    try:
                        raw, code = _bot_get("broker/orders?status=open", timeout=8)
                        orders = json.loads(raw) if code == 200 else []
                        buys = [o for o in orders if isinstance(o, dict)
                                and str(o.get("side", "")).lower() == "buy"] \
                            if isinstance(orders, list) else []
                        if buys:
                            return self._json({"ok": False, "error":
                                f"engine is mid-order ({buys[0].get('symbol', '?')} buy "
                                "still working at the broker) - wait for the fill, "
                                "then save again"}, 409)
                    except Exception:
                        pass   # engine unreachable = no in-flight poll to race
            # Never write junk: re-validate server-side even if the UI already
            # did. One extra account call is cheap; a broken .env is not.
            acct, err = setup_core.check_keys(envn, key, sec)
            if not acct:
                return self._json({"ok": False, "error": err})
            feed = body.get("feed") or setup_core.detect_feed(key, sec)
            cur = setup_core.read_env()
            setup_core.apply_answers(
                cur, env_name=envn, key=key, sec=sec, feed=feed, mode=mode,
                webhook=str(body.get("webhook") or "").strip())
            setup_core.write_env(cur)
            _journal_log("note", f"setup saved ({envn}/{mode}, feed {feed or 'iex'})")
            # Apply: if the engine is up it is running the OLD env - kill it
            # and let the supervisor respawn with the new one. If it never
            # started (first run), the supervisor's wait-for-.env loop starts
            # it within seconds.
            restarted = False
            if EMBEDDED:
                try:
                    restarted = _kill_engine_now()
                except Exception:
                    pass
            return self._json({"ok": True, "feed": feed, "mode": mode,
                               "env": envn, "engine_restarted": restarted})

        if parsed.path == "/api/memory":
            txt = str(body.get("text") or "")[:8000]
            MEMORY.write_text(txt, encoding="utf-8")
            _journal_log("note", "memory updated")
            return self._json({"ok": True, "bytes": len(txt)})

        if parsed.path == "/api/journal/add":
            kind = str(body.get("kind") or "note")[:16]
            text = str(body.get("text") or "").strip()
            if not text:
                return self._json({"error": "empty"}, 400)
            _journal_log(kind, text)
            return self._json({"ok": True})

        if parsed.path == "/api/bridge/stop":
            p = _bridge_current.get("proc")
            if p and p.poll() is None:
                _bridge["stopping"] = True
                _kill_proc(p)
                _journal_log("note", "bridge turn stopped by user")
                return self._json({"ok": True, "stopped": True})
            return self._json({"ok": True, "stopped": False, "hint": "nothing running"})

        if parsed.path == "/api/workbench/clear":
            # wipe the live board (palette "clear board") - autosaved first, always
            auto = SAVED / f"_autosave-{time.strftime('%Y%m%d-%H%M%S')}"
            n = 0
            for f in PANELS.glob("*.html"):
                auto.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, auto / f.name)
                f.unlink()
                n += 1
            _journal_log("board", f"cleared board ({n} panels autosaved)")
            return self._json({"ok": True, "cleared": n,
                               "saved_as": auto.name if n else None})

        if parsed.path == "/api/workbench/save":
            name = _safe_board_name(body.get("name"))
            if not name:
                return self._json({"error": "name required"}, 400)
            dest = SAVED / name
            dest.mkdir(parents=True, exist_ok=True)
            n = 0
            for f in PANELS.glob("*.html"):
                shutil.copy2(f, dest / f.name)
                n += 1
            _journal_log("board", f"saved board '{name}' ({n} panels)")
            return self._json({"ok": True, "name": name, "panels": n})

        if parsed.path == "/api/workbench/save-panel":
            # Save ONE tile as its own page. The Ariel dossier belongs on its own
            # page, not stacked under three other cards on a shared board.
            src_name = str(body.get("panel") or "")
            if not src_name.endswith(".html"):
                return self._json({"error": "panel (.html) required"}, 400)
            src = (PANELS / src_name).resolve()
            if not str(src).startswith(str(PANELS.resolve())) or not src.is_file():
                return self._json({"error": "unknown panel"}, 404)
            # default the page name to the panel's own title
            fallback = src.stem
            try:
                head = src.read_text(encoding="utf-8", errors="replace")[:400]
                if "title:" in head:
                    fallback = head.split("title:", 1)[1].split("-->", 1)[0].strip() or fallback
            except Exception:
                pass
            name = _safe_board_name(body.get("name") or fallback)
            if not name:
                return self._json({"error": "name required"}, 400)
            dest = SAVED / name
            dest.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest / src.name)
            _journal_log("board", f"saved page '{name}' (1 panel: {src.name})")
            return self._json({"ok": True, "name": name, "panels": 1,
                               "panel": src.name})

        if parsed.path == "/api/workbench/delete-saved":
            # Delete a saved page. Nothing is unlinked - it moves to
            # saved-workbenches/_trash/<ts>-<name>/ so a misclick is recoverable
            # from the folder. The UI treats it as gone.
            name = _safe_board_name(body.get("name"))
            src = SAVED / (name or "")
            if not name or not src.is_dir():
                return self._json({"error": "unknown page"}, 404)
            trash = SAVED / "_trash"
            trash.mkdir(parents=True, exist_ok=True)
            dest = trash / f"{time.strftime('%Y%m%d-%H%M%S')}-{name}"
            try:
                shutil.move(str(src), str(dest))
            except Exception as e:
                return self._json({"error": f"could not delete: {str(e)[:160]}"}, 500)
            _journal_log("board", f"deleted page '{name}' (recoverable in _trash/{dest.name})")
            return self._json({"ok": True, "name": name, "trashed_as": dest.name})

        if parsed.path == "/api/panels/delete":
            # Remove ONE tile from the live workbench. Same deal: copied to
            # _trash first, then removed from panels/ so the grid drops it.
            pname = str(body.get("panel") or "")
            if not pname.endswith(".html"):
                return self._json({"error": "panel (.html) required"}, 400)
            f = (PANELS / pname).resolve()
            if not str(f).startswith(str(PANELS.resolve())) or not f.is_file():
                return self._json({"error": "unknown panel"}, 404)
            trash = SAVED / "_trash" / f"{time.strftime('%Y%m%d-%H%M%S')}-panel"
            trash.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(f, trash / f.name)
                f.unlink()
            except Exception as e:
                return self._json({"error": f"could not remove: {str(e)[:160]}"}, 500)
            _journal_log("board", f"removed panel '{pname}' (recoverable in _trash/{trash.name})")
            return self._json({"ok": True, "panel": pname, "trashed_as": trash.name})

        if parsed.path == "/api/workbench/load":
            name = _safe_board_name(body.get("name"))
            src = SAVED / (name or "")
            if not name or not src.is_dir():
                return self._json({"error": "unknown board"}, 404)
            # never destroy the live board silently: autosave it first
            auto = SAVED / f"_autosave-{time.strftime('%Y%m%d-%H%M%S')}"
            auto.mkdir(parents=True, exist_ok=True)
            for f in PANELS.glob("*.html"):
                shutil.copy2(f, auto / f.name)
                f.unlink()
            n = 0
            for f in src.glob("*.html"):
                shutil.copy2(f, PANELS / f.name)
                n += 1
            _journal_log("board", f"loaded board '{name}' ({n} panels)")
            return self._json({"ok": True, "loaded": name, "panels": n,
                               "previous_saved_as": auto.name})

        if parsed.path == "/api/tts":
            # Voicebox relay (same API the Interview Bot proved out): kokoro via
            # POST /generate/stream returns WAV bytes synchronously. The frontend
            # falls back to the browser voice when this errors.
            text = str(body.get("text") or "").strip()[:900]
            if not text:
                return self._json({"error": "empty"}, 400)
            try:
                pid = _vb_pick_profile(body.get("profile_id"))
                if not pid:
                    return self._json({"error": "no voice profile in Voicebox"}, 502)
                # Voicebox engines: qwen | qwen_custom_voice | luxtts | chatterbox |
                # chatterbox_turbo | tada | kokoro  (per its /openapi.json, default
                # "qwen"). A PRESET profile carries its own engine in preset_engine.
                # A CLONED profile carries none at all, and kokoro is preset-voices-
                # only, so naming kokoro on a clone is a guaranteed 400 - clones need
                # a cloning engine, "qwen" (Qwen3-TTS) being the default.
                known = _vb_profile.get("engine")
                shapes = ([known or "qwen", None, "chatterbox"] if _vb_profile.get("is_clone")
                          else [known or "kokoro", None])
                if known and known not in shapes:
                    shapes.insert(0, known)
                # None is a valid shape (omit the field), so probe with a sentinel.
                # Cache per profile: a preset wants engine="kokoro", a clone wants
                # the field left out entirely, so one shared answer would break one.
                cached = _VB_ENGINE_OK.get(pid, "\x00unset")
                if cached != "\x00unset" and cached in shapes:
                    shapes = [cached]
                last = None
                for eng in shapes:
                    p = {"text": text, "profile_id": pid, "language": "en"}
                    if eng:
                        p["engine"] = eng
                    req = urllib.request.Request(f"{VOICEBOX}/generate/stream",
                                                 data=json.dumps(p).encode(), method="POST",
                                                 headers={"Content-Type": "application/json"})
                    try:
                        # 45s, not 120: a preset answers in ~2s, and three
                        # 120s shape attempts back to back once held a reply
                        # hostage for six minutes while the UI sat mute
                        with urllib.request.urlopen(req, timeout=45) as r:
                            audio = r.read()
                            ctype = r.headers.get("Content-Type", "audio/wav")
                        if "json" in ctype:
                            last = "voicebox returned JSON not audio"
                            continue
                        _VB_ENGINE_OK[pid] = eng
                        return self._raw(audio, ctype)
                    except Exception as e:
                        last = str(e)[:120]
                raise RuntimeError(last or "no working engine shape")
            except Exception as e:
                _vb_profile["id"] = None  # re-probe next time
                return self._json({"error": f"voicebox failed: {str(e)[:160]}"}, 502)

        if parsed.path == "/api/bot/order":
            # human trade ticket -> bot /api/order (confirm gate lives on the bot).
            # Long timeout: a trailing-exit order polls its own fill (~20s).
            try:
                raw, code = _bot_post("order", body, timeout=60)
                try:
                    r = json.loads(raw)
                    if r.get("ok"):
                        tr = r.get("trail") or {}
                        _journal_log("order", f"{r.get('side')} {r.get('symbol')} "
                                     f"x{r.get('qty') or ('$' + str(body.get('notional')))} -> {r.get('status')}"
                                     + (f" + trail {tr.get('trail_percent')}% armed" if tr.get("armed") else
                                        (" - TRAIL NOT ARMED" if tr else "")))
                    else:
                        _journal_log("order", f"FAILED {body.get('side')} {body.get('symbol')}: {str(r.get('error'))[:120]}")
                except Exception:
                    pass
                return self._raw(raw, "application/json", code)
            except urllib.error.HTTPError as e:
                return self._raw(e.read(), "application/json", e.code)
            except Exception as e:
                return self._json({"ok": False, "error": f"bot unreachable: {str(e)[:120]}"}, 502)

        if parsed.path == "/api/schedule":
            # Turn scheduled scans on/off and set the interval. Manual by design:
            # nothing enables itself, and disabling is one click.
            s = _sched_load()
            if "enabled" in body:
                s["enabled"] = bool(body["enabled"])
            if "every_min" in body:
                try:
                    n = int(body["every_min"])
                except Exception:
                    return self._json({"ok": False, "error": "every_min must be a number"}, 400)
                if not (1 <= n <= 1440):
                    return self._json({"ok": False, "error": "every_min must be 1-1440"}, 400)
                s["every_min"] = n
            if "job" in body:
                job = str(body["job"])
                if job not in ("radar", "changed"):
                    return self._json({"ok": False, "error": f"unknown job {job!r}"}, 400)
                s["job"] = job
            if "market_hours_only" in body:
                s["market_hours_only"] = bool(body["market_hours_only"])
            s["next_run"] = (time.strftime("%Y-%m-%dT%H:%M:%S",
                                           time.localtime(time.time() + s["every_min"] * 60))
                             if s["enabled"] else None)
            with _sched_lock:
                _sched_save(s)
            _journal_log("note", f"scheduled {s['job']} scan "
                                 + (f"ENABLED every {s['every_min']}m"
                                    + (" (market hours only)" if s["market_hours_only"] else "")
                                    if s["enabled"] else "DISABLED"))
            s["runs"] = _sched_state["runs"][:10]
            return self._json({"ok": True, **s})

        if parsed.path == "/api/plan":
            # THE PLAN ENDPOINT. One call, two destinations:
            #   1. PAPER executes NOW, unconditionally.
            #   2. LIVE is written to staged-trade.json and waits for a human.
            #
            # This is the rule Dustin asked for on 2026-08-10, and it exists
            # because the live account is $1,000 - under the $2,000 Reg T
            # minimum - so it CANNOT short. Every short setup was therefore
            # unrecordable. Now the paper fill is the record and the live ticket
            # is optional.
            #
            # The asymmetry is deliberate and load-bearing: the leg that always
            # fires is the one that cannot lose money, and the leg that can lose
            # money still goes through the same file-bus staging as before. This
            # endpoint never places a live order. It cannot - it only writes a
            # file, exactly like the copilot does.
            symbol = str(body.get("symbol", "")).upper().strip()
            side = str(body.get("side", "buy")).lower()
            if not symbol:
                return self._json({"ok": False, "error": "symbol required"}, 400)
            if side not in ("buy", "sell"):
                return self._json({"ok": False, "error": "side must be buy or sell"}, 400)

            out = {"ok": True, "symbol": symbol, "side": side, "paper": None,
                   "staged": None}

            # --- leg 1: paper, always ---
            paper_body = {"symbol": symbol, "side": side,
                          "notional": body.get("notional"), "qty": body.get("qty"),
                          "exit_trail_pct": body.get("exit_trail_pct"),
                          "note": body.get("note") or ""}
            try:
                raw, code = _bot_post("paper/order", paper_body, timeout=60)
                out["paper"] = json.loads(raw)
            except urllib.error.HTTPError as e:
                try:
                    out["paper"] = json.loads(e.read())
                except Exception:
                    out["paper"] = {"ok": False, "error": f"paper HTTP {e.code}"}
            except Exception as e:
                out["paper"] = {"ok": False, "error": f"bot unreachable: {str(e)[:120]}"}

            p = out["paper"] or {}
            _journal_log("order", (
                f"PAPER {side} {symbol} "
                f"{('x' + str(p.get('qty'))) if p.get('qty') else ('$' + str(body.get('notional')))}"
                f" -> {p.get('status')}"
                + (f" + trail {(p.get('trail') or {}).get('trail_percent')}%"
                   if (p.get("trail") or {}).get("armed") else "")
            ) if p.get("ok") else f"PAPER FAILED {side} {symbol}: {str(p.get('error'))[:140]}")

            # --- leg 2: live, staged only ---
            if body.get("stage_live", True):
                ticket = {
                    "symbol": symbol, "side": side,
                    "notional": body.get("notional"), "qty": body.get("qty"),
                    "exit_trail_pct": body.get("exit_trail_pct"),
                    "why": body.get("note") or body.get("why") or "",
                    "source": "plan",
                    "paper_status": p.get("status") if p.get("ok") else "paper failed",
                    "ts": time.time(), "ttl_s": int(body.get("ttl_s") or 1800),
                }
                # Size notices ride ON the ticket. A warning printed in a log the
                # operator is not reading is not a warning. Advisory, never a
                # block: the ticket stages either way and he decides.
                try:
                    adv = _size_advisories(symbol, side, body)
                    if adv:
                        ticket["advisories"] = adv
                        out["advisories"] = adv
                        _journal_log("note", f"{symbol}: {adv[0]['message']}")
                except Exception as e:
                    ticket["advisories"] = [{"code": "advice_failed", "severity": "info",
                                             "message": f"could not compute size notice: {str(e)[:120]}"}]
                try:
                    STAGED.write_text(json.dumps(ticket, indent=2), encoding="utf-8")
                    out["staged"] = ticket
                    _journal_log("note", f"staged LIVE ticket {side} {symbol} "
                                         f"(awaiting your click, expires in "
                                         f"{ticket['ttl_s']//60}m)")
                except Exception as e:
                    out["staged"] = {"error": str(e)[:200]}

            return self._json(out, 200 if (out["paper"] or {}).get("ok") else 502)

        if parsed.path == "/api/bot/paper/protect":
            # arm a trailing stop on an EXISTING paper position. The paper twin
            # of /api/bot/protect, which did not exist, which is why a naked
            # paper entry could not be fixed from anywhere.
            try:
                raw, code = _bot_post("paper/protect", body, timeout=30)
                try:
                    r = json.loads(raw)
                    _journal_log("order", (f"PAPER PROTECT {r.get('symbol')} x{r.get('qty')} "
                                           f"trail {r.get('trail_percent')}% armed ({r.get('side')})")
                                 if r.get("ok") else
                                 f"PAPER PROTECT FAILED {body.get('symbol')}: {str(r.get('error'))[:120]}")
                except Exception:
                    pass
                return self._raw(raw, "application/json", code)
            except urllib.error.HTTPError as e:
                return self._raw(e.read(), "application/json", e.code)
            except Exception as e:
                return self._json({"ok": False, "error": f"bot unreachable: {str(e)[:120]}"}, 502)

        if parsed.path == "/api/bot/paper/order":
            # direct paper ticket, no live staging
            try:
                raw, code = _bot_post("paper/order", body, timeout=60)
                try:
                    r = json.loads(raw)
                    _journal_log("order", f"PAPER {r.get('side')} {r.get('symbol')} "
                                          f"-> {r.get('status')}" if r.get("ok")
                                 else f"PAPER FAILED {body.get('symbol')}: {str(r.get('error'))[:120]}")
                except Exception:
                    pass
                return self._raw(raw, "application/json", code)
            except urllib.error.HTTPError as e:
                return self._raw(e.read(), "application/json", e.code)
            except Exception as e:
                return self._json({"ok": False, "error": f"bot unreachable: {str(e)[:120]}"}, 502)

        if parsed.path in ("/api/tv/open", "/api/tv/shot"):
            # TradingView remote control. Optional: if tv.py or the debug browser
            # is missing, this fails loudly and nothing else in the desk cares.
            try:
                import tv
            except Exception as e:
                return self._json({"ok": False, "error": f"tv module unavailable: {e}"}, 501)
            if parsed.path == "/api/tv/open":
                sym = str(body.get("symbol", "")).strip()
                if not sym:
                    return self._json({"ok": False, "error": "symbol required"}, 400)
                r = tv.open_chart(sym, body.get("interval"))
                _journal_log("note", f"chart -> {sym}"
                             + (f" {body.get('interval')}" if body.get("interval") else "")
                             + ("" if r.get("ok") else f" (FAILED: {r.get('error')})"))
                return self._json(r, 200 if r.get("ok") else 502)
            name = time.strftime("tv-%Y%m%d-%H%M%S.png")
            r = tv.shot(str(SHOTS / name))
            if r.get("ok"):
                r["web_path"] = f"/api/shot?name={name}"
                _journal_log("note", f"chart screenshot {name}")
            return self._json(r, 200 if r.get("ok") else 502)

        if parsed.path == "/api/bot/protect":
            # arm a trailing stop on an EXISTING position (the VRM fix)
            try:
                raw, code = _bot_post("protect", body, timeout=30)
                try:
                    r = json.loads(raw)
                    _journal_log("order", (f"PROTECT {r.get('symbol')} x{r.get('qty')} "
                                           f"trail {r.get('trail_percent')}% armed")
                                 if r.get("ok") else
                                 f"PROTECT FAILED {body.get('symbol')}: {str(r.get('error'))[:120]}")
                except Exception:
                    pass
                return self._raw(raw, "application/json", code)
            except urllib.error.HTTPError as e:
                return self._raw(e.read(), "application/json", e.code)
            except Exception as e:
                return self._json({"ok": False, "error": f"bot unreachable: {str(e)[:120]}"}, 502)

        if parsed.path == "/api/bot/run/radar":
            try:
                raw, code = _bot_post("run/radar", {}, timeout=90)
                _journal_log("scan", "manual radar re-scan")
                return self._raw(raw, "application/json", code)
            except urllib.error.HTTPError as e:
                return self._raw(e.read(), "application/json", e.code)
            except Exception as e:
                return self._json({"ok": False, "error": f"bot unreachable: {str(e)[:120]}"}, 502)

        return self._json({"error": "not found"}, 404)


def build_server():
    """Everything main() did short of serve_forever(): dirs, the bind, the
    worker threads. Split out so shell.py can run the server on a thread and
    keep the GUI loop for itself. Raises SystemExit(2) on a busy port."""
    # The workspace. In a source run this is the repo and every mkdir is a no-op.
    # In a packaged run it is ~/MarketForge and this is where it gets built.
    WORK.mkdir(parents=True, exist_ok=True)
    BOT_HOME.mkdir(parents=True, exist_ok=True)
    (BOT_HOME / "data").mkdir(parents=True, exist_ok=True)
    PANELS.mkdir(exist_ok=True)
    SHOTS.mkdir(exist_ok=True)
    SAVED.mkdir(exist_ok=True)
    if WORK != ROOT:
        # Seed the workspace from the shipped copies, ONCE. Never overwrite: the
        # whole point is that an update cannot touch what the user has edited.
        # CLAUDE.md has to be here rather than in the program folder, because the
        # copilot runs with the workspace as its cwd and auto-loads it from there.
        for name in ("AGENTS.md", "CLAUDE.md", "PROMPTS.md", "config.json"):
            src, dst = resource_path(name), WORK / name
            if src.exists() and not dst.exists():
                dst.write_bytes(src.read_bytes())
        wel = resource_path("panels") / "00-welcome.html"
        if wel.exists() and not (PANELS / "00-welcome.html").exists():
            (PANELS / "00-welcome.html").write_bytes(wel.read_bytes())
        tpl = resource_path("bot") / ".env.template"
        if tpl.exists() and not (BOT_HOME / ".env.template").exists():
            (BOT_HOME / ".env.template").write_bytes(tpl.read_bytes())
    if not MEMORY.exists():
        MEMORY.write_text(DEFAULT_MEMORY, encoding="utf-8")
    # RULES.md is YOUR trading plan, so it is user data and never ships. Seed it
    # from the shipped template on first run, then leave it alone forever - an
    # update must never overwrite what someone wrote about how they trade.
    if not RULES.exists():
        tpl = resource_path("RULES.template.md")
        if tpl.exists():
            RULES.write_text(tpl.read_text(encoding="utf-8"), encoding="utf-8")
            print(f"  rules:   seeded {RULES.name} from the template - edit it, it is yours")
    for p in (INBOX, OUTBOX):
        if not p.exists():
            p.write_text("", encoding="utf-8")
    # Fail LOUDLY on a busy port. Silently landing on someone else's server is how
    # you end up staring at a paper account you thought was live.
    try:
        srv = Server(("127.0.0.1", PORT), Handler)
    except OSError as e:
        print(f"\n  !! Port {PORT} is already in use ({e.__class__.__name__}).")
        print(f"  !! Something is ALREADY serving http://localhost:{PORT} - probably the other")
        print("  !! Market Forge window. Close it, or set a different port:")
        print(f"  !!     set MF_PORT=8412  &&  python app.py\n")
        raise SystemExit(2)
    lane = "remote engine" if not EMBEDDED else "embedded engine"
    print(f"MARKET FORGE {VERSION} [{lane}]  ->  http://localhost:{PORT}")
    if EMBEDDED:
        threading.Thread(target=_bot_supervisor, daemon=True).start()
        print(f"  bot API: {BOT}  (EMBEDDED - engine runs in this process tree)")
    else:
        print(f"  bot API: {BOT}")

    # THE TAP, started here so nobody has to remember a second window. It waits a
    # few seconds for the engine's API to answer, because its symbol list starts
    # with the open positions and asking before the engine is up would reserve
    # nothing and silently watch indices only - which is precisely the class of
    # quiet-wrong-answer this whole thing keeps producing.
    _ok, _why = _tap_enabled()
    if _ok:
        def _tap_later():
            for _ in range(30):
                if TAP_STOP.is_set():
                    return
                try:
                    _bot_get("positions", timeout=5)
                    break
                except Exception:
                    time.sleep(1)
            _tap_supervisor()
        threading.Thread(target=_tap_later, daemon=True).start()
        print("  live tap: starting (auto-restarts when your positions change)")
    else:
        print(f"  live tap: OFF - {_why}")
        print("            prices will be 15 minutes behind; the data chip says so")
    if WORK != ROOT:
        print(f"  workspace: {WORK}  (your keys, plan, journal and boards live HERE,")
        print( "             not in the program folder - point your coding agent at it)")
    print(f"  panels:  {PANELS}  (your coding agent writes here; the WORKBENCH renders it live)")
    print("  chat:    chat-inbox.jsonl / chat-outbox.jsonl")
    threading.Thread(target=_state_writer, daemon=True).start()
    print(f"  state:   {STATE.name}  (live snapshot on disk, every 20s - lanes "
          f"that cannot reach localhost read this)")
    # Say plainly, on boot, whether an agent is present and WHAT breaks without
    # one. Before this you found out by noticing the radar had quietly stopped
    # producing scores, which is the worst possible way to learn it.
    _abin, _akind = _resolve_agent()
    if _abin:
        print(f"  agent:   {_akind} -> {_abin}")
    else:
        print("\n  " + "=" * 66)
        print("  NO CODING-AGENT CLI FOUND. The desk still runs. These do not:")
        print("    - the COPILOT tab (chat, panel building, day replays)")
        print("    - catalyst SCORES, so the radar alerts without triage")
        print("    - AUTO-ENTRIES, which require a score and so cannot fire")
        print("  Fix: install Claude Code or Codex and put it on PATH, or set")
        print("  AGENT_BIN=<full path> in bot/.env. Then restart the desk.")
        print("  Check with:  where claude    (PATH holds FOLDERS, not programs)")
        print("  " + "=" * 66 + "\n")
    if BRIDGE:
        threading.Thread(target=_bridge_loop, daemon=True).start()
        print(f"  bridge:  LIVE - new chat lines auto-run `claude -p` ({BRIDGE_MODEL}); replies are spoken")
    else:
        print("  bridge:  off (config.json bridge=false) - tell your CC window to check chat")
    return srv


def main():
    srv = build_server()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("bye")


if __name__ == "__main__":
    main()
