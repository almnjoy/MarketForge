"""Agentic Stock Bot - LOCAL dashboard server.

Pure Python stdlib (no pip installs, runs on any Python 3.9+). Serves the
dashboard UI, runs the bot engine in-process, and exposes the
two file buses Claude Code drives:

  panels/*.html      -> every file renders as a live card on the WORKBENCH tab
                        (sandboxed iframe; edit/add/delete = the page transforms)
  chat-inbox.jsonl   -> what Dustin says in the COPILOT tab (voice or typed)
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
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
PANELS = ROOT / "panels"
INBOX = ROOT / "chat-inbox.jsonl"
OUTBOX = ROOT / "chat-outbox.jsonl"
RULES = ROOT / "RULES.md"
SAVED = ROOT / "saved-workbenches"
MEMORY = ROOT / "memory.md"        # standing preferences the copilot honors
JOURNAL = ROOT / "journal.jsonl"   # the decision log Replay reconstructs from
SHOTS = ROOT / "tv-shots"          # TradingView captures - agents READ these

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
    _cfg.update(json.loads((ROOT / "config.json").read_text(encoding="utf-8")))
except Exception:
    pass
# EMBEDDED mode (the "friend edition"): MF_EMBEDDED=1 (or config "embedded": true)
# makes this server spawn the bot engine from bot/run_bot.py and point itself at
# it - one folder, one command, no server, no Docker.
EMBEDDED = os.environ.get("MF_EMBEDDED", "").strip() == "1" or bool(_cfg.get("embedded"))
if EMBEDDED:
    _cfg["bot_base"] = f"http://127.0.0.1:{int(_cfg.get('bot_port', 8796))}"
BOT = str(_cfg["bot_base"]).rstrip("/")
# Port: MF_PORT wins over config.json so the LIVE and PAPER desks can run side by
# side on different ports. They used to share 8410, which meant whichever started
# first owned the URL and the second silently served the wrong account.
PORT = int(os.environ.get("MF_PORT") or _cfg["port"])


BOT_PID = ROOT / "bot" / "data" / "engine.pid"


def _pid_alive(pid):
    try:
        if os.name == "nt":
            out = subprocess.run(["tasklist", "/FI", f"PID eq {int(pid)}"],
                                 capture_output=True, text=True, timeout=8).stdout
            return str(int(pid)) in out
        os.kill(int(pid), 0)
        return True
    except Exception:
        return False


def _kill_stale_engine():
    """Kill an engine left over from a previous run.

    Closing the console window sends CTRL_CLOSE_EVENT and Python gets ~5s, so
    atexit does NOT reliably fire on Windows - the engine can survive with no
    window attached. Two of those running at once means two radar schedulers on
    one brokerage account, which is exactly the double-entry problem we retired
    rocker to avoid. So: record the pid, and clean it up on the way IN as well
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
                               capture_output=True, timeout=10)
            else:
                os.kill(pid, 9)
            time.sleep(1)
        BOT_PID.unlink(missing_ok=True)
    except Exception as e:
        print(f"[embedded bot] stale-pid check failed: {e}")


def _bot_supervisor():
    """Keep the embedded bot engine alive: spawn, watch, restart with backoff."""
    import atexit
    _kill_stale_engine()
    backoff = 3
    while True:
        proc = subprocess.Popen([sys.executable, str(ROOT / "bot" / "run_bot.py")],
                                cwd=str(ROOT / "bot"))
        try:
            BOT_PID.parent.mkdir(parents=True, exist_ok=True)
            BOT_PID.write_text(str(proc.pid), encoding="utf-8")
        except Exception:
            pass
        atexit.register(lambda p=proc: p.poll() is None and _kill_proc(p))
        rc = proc.wait()
        print(f"[embedded bot] exited rc={rc}; restarting in {backoff}s "
              f"(check bot/.env if this loops)")
        time.sleep(backoff)
        backoff = min(60, backoff * 2)
VOICEBOX = str(_cfg["voicebox_url"]).rstrip("/")
_vb_profile = {"id": None}   # the app's default voice, resolved once from config
_VB_ENGINE_OK = {}           # profile_id -> the engine field shape Voicebox accepted
_STARTED_AT = time.time()    # process start, for the Admin tab's uptime
USAGE = ROOT / "usage.jsonl"  # measured copilot spend, one line per bridge turn
STATE = ROOT / "state.json"   # live snapshot ON DISK - see _state_writer()


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
BRIDGE_TIMEOUT = int(_cfg.get("bridge_timeout", 150))
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
                           capture_output=True, timeout=10)
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
  Dustin's execution lanes, not yours.

Recent conversation:
{history}

Newest message(s) from Dustin:
{new}"""


def _resolve_claude():
    override = os.environ.get("CLAUDE_BIN")
    if override:
        return override
    for name in ("claude", "claude.cmd", "claude.exe"):
        f = shutil.which(name)
        if f:
            return f
    for cand in (Path(os.environ.get("APPDATA", "x")) / "npm" / "claude.cmd",
                 Path.home() / ".local" / "bin" / "claude.exe",
                 Path.home() / ".local" / "bin" / "claude"):
        if cand.exists():
            return str(cand)
    return None


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
    bin_ = _resolve_claude()
    if not bin_:
        return "Bridge error: claude CLI not found. Set CLAUDE_BIN and restart app.py."
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
    argv = [bin_, "-p", "--model", BRIDGE_MODEL, "--dangerously-skip-permissions",
            "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
            "--no-session-persistence",
            "--output-format", "stream-json", "--verbose"]  # verbose is REQUIRED for stream-json in -p mode
    if bin_.lower().endswith((".cmd", ".bat")):
        argv = ["cmd", "/c"] + argv          # .cmd shims cannot be exec'd directly
    proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, text=True, cwd=str(ROOT),
                            encoding="utf-8", errors="replace",
                            start_new_session=(os.name != "nt"))  # own group -> killpg gets the tree
    _bridge_current["proc"] = proc
    killer = threading.Timer(BRIDGE_TIMEOUT, _kill_proc, args=(proc,))
    killer.start()
    try:
        proc.stdin.write(prompt)
        proc.stdin.close()
        text_parts, result_text = [], None
        for line in _iter_lines(proc):
            line = line.strip()
            if not line:
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
        killer.cancel()
        _bridge_current["proc"] = None
    if _bridge.pop("stopping", None):
        return "(stopped by you - say the word when you want me back on it)"
    if proc.returncode and proc.returncode != 0 and not (result_text or text_parts):
        return f"Bridge error (exit {proc.returncode}) - possibly timed out after {BRIDGE_TIMEOUT}s."
    return (result_text or " ".join(text_parts) or "").strip() or "(no reply)"


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
                reply = f"Bridge error: claude timed out after {BRIDGE_TIMEOUT}s."
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
           "regime"}          # market regime gate (read-only, additive)


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
      3. the first CLONED voice
      4. anything at all

    Cloned voices beat presets on purpose: sounding like *you* is the entire
    reason to run Voicebox instead of the browser's built-in speech. The old
    behaviour filtered to engine == "kokoro", which silently excluded every
    clone and always landed on a stock preset voice."""
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
        pick = next((p for p in profs if p["is_clone"]), None) or profs[0]
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
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path, qs = parsed.path, parsed.query

        if path in ("/", "/index.html"):
            return self._file(STATIC / "index.html", "text/html; charset=utf-8")
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
            days = sorted(sess.values(), key=lambda s: s["day"], reverse=True)
            if day:
                inbox = [m for m in inbox if str(m.get("ts", "")).startswith(day)]
                outbox = [m for m in outbox if str(m.get("ts", "")).startswith(day)]
            return self._json({"inbox": inbox, "outbox": outbox, "days": days,
                               "day": day, "today": time.strftime("%Y-%m-%d")})

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

        if path == "/api/meta":
            return self._json({"bot_base": BOT, "port": PORT, "root": str(ROOT),
                               "voicebox": VOICEBOX, "user": str(_cfg.get("user", "Dustin")),
                               "theme": str(_cfg.get("theme", "forge"))})

        if path == "/api/admin":
            # READ-ONLY inventory: what is running, on which model, from which files.
            # Deliberately cannot create or edit anything - this is a "show me what
            # is in place" screen, not a control panel. Secrets are NEVER returned,
            # only whether a file exists and how big it is.
            def finfo(p, label, what):
                try:
                    st = p.stat()
                    return {"label": label, "what": what, "path": str(p.relative_to(ROOT)),
                            "exists": True, "bytes": st.st_size,
                            "mtime": time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime))}
                except OSError:
                    return {"label": label, "what": what, "path": str(p.name),
                            "exists": False, "bytes": 0, "mtime": ""}

            bot_cfg = {}
            try:
                raw, code = _bot_get("config", timeout=6)
                if code == 200:
                    bot_cfg = json.loads(raw)
            except Exception:
                pass
            ra = (bot_cfg.get("radar_auto") or {}) if isinstance(bot_cfg, dict) else {}

            jrn = _read_jsonl(JOURNAL, 4000)
            kinds = {}
            for e in jrn:
                kinds[e.get("kind", "?")] = kinds.get(e.get("kind", "?"), 0) + 1

            lanes = [{
                "name": "Bridge (in-app copilot)", "runtime": "claude -p, headless",
                "model": BRIDGE_MODEL, "enabled": BRIDGE,
                "status": _bridge.get("status"), "turns": _bridge.get("turns", 0),
                "last_ms": _bridge.get("last_ms", 0), "timeout_s": BRIDGE_TIMEOUT,
                "binary": _resolve_claude() or "not found on PATH",
                "note": "One short turn per chat message. Builds panels, answers, speaks.",
            }, {
                "name": "Your terminal session", "runtime": "claude (interactive)",
                "model": "whatever /model says in that window", "enabled": None,
                "status": "external", "turns": None, "last_ms": None, "timeout_s": None,
                "binary": "", "note": "The deep-work lane. Market Forge cannot see or set its model.",
            }, {
                "name": "Catalyst scoring", "runtime": "OpenAI-compatible endpoint",
                "model": (bot_cfg.get("radar_llm_model") or "rules-only (no LLM)"),
                "enabled": bool(bot_cfg.get("radar_use_llm")),
                "status": "on" if bot_cfg.get("radar_use_llm") else "off",
                "turns": None, "last_ms": None, "timeout_s": None,
                "binary": str(bot_cfg.get("radar_llm_base") or ""),
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
                finfo(ROOT / "CLAUDE.md", "CLAUDE.md", "The copilot's brief and hard rules"),
                finfo(RULES, "RULES.md", "Your written trading plan"),
                finfo(MEMORY, "memory.md", "Standing orders injected into every turn"),
                finfo(ROOT / "PROMPTS.md", "PROMPTS.md", "Prompts that reliably work"),
                finfo(ROOT / "config.json", "config.json", "Ports, model, theme, Voicebox"),
                finfo(ROOT / "bot" / ".env", "bot/.env", "Your keys. Never displayed, never committed."),
                finfo(JOURNAL, "journal.jsonl", "Every scan, chat, order and board"),
                finfo(INBOX, "chat-inbox.jsonl", "What you said"),
                finfo(OUTBOX, "chat-outbox.jsonl", "What the copilot said"),
            ]

            return self._json({
                "runtime": {
                    "python": sys.version.split()[0], "platform": sys.platform,
                    "embedded": EMBEDDED, "port": PORT, "root": str(ROOT),
                    "bot_base": BOT,
                    "started": time.strftime("%Y-%m-%d %H:%M", time.localtime(_STARTED_AT)),
                    "uptime_s": int(time.time() - _STARTED_AT),
                },
                "lanes": lanes,
                "files": files,
                "counts": {
                    "panels": len(_panels_state()),
                    "boards": len([d for d in (ROOT / "saved-workbenches").glob("*") if d.is_dir()])
                              if (ROOT / "saved-workbenches").exists() else 0,
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
    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
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
                        with urllib.request.urlopen(req, timeout=120) as r:
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


def main():
    PANELS.mkdir(exist_ok=True)
    SHOTS.mkdir(exist_ok=True)
    SAVED.mkdir(exist_ok=True)
    if not MEMORY.exists():
        MEMORY.write_text(DEFAULT_MEMORY, encoding="utf-8")
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
    print(f"MARKET FORGE [{lane}]  ->  http://localhost:{PORT}")
    if EMBEDDED:
        threading.Thread(target=_bot_supervisor, daemon=True).start()
        print(f"  bot API: {BOT}  (EMBEDDED - engine runs in this process tree)")
    else:
        print(f"  bot API: {BOT}")
    print(f"  panels:  {PANELS}  (Claude Code writes here; the WORKBENCH renders it live)")
    print("  chat:    chat-inbox.jsonl / chat-outbox.jsonl")
    threading.Thread(target=_state_writer, daemon=True).start()
    print(f"  state:   {STATE.name}  (live snapshot on disk, every 20s - lanes "
          f"that cannot reach localhost read this)")
    if BRIDGE:
        threading.Thread(target=_bridge_loop, daemon=True).start()
        print(f"  bridge:  LIVE - new chat lines auto-run `claude -p` ({BRIDGE_MODEL}); replies are spoken")
    else:
        print("  bridge:  off (config.json bridge=false) - tell your CC window to check chat")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("bye")


if __name__ == "__main__":
    main()
