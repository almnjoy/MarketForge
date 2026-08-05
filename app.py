"""Agentic Stock Bot - LOCAL dashboard server.

Pure Python stdlib (no pip installs, runs on any Python 3.9+). Serves the
dashboard UI, proxies the rocker stock-bot API over the LAN, and exposes the
two file buses Claude Code drives:

  panels/*.html      -> every file renders as a live card on the WORKBENCH tab
                        (sandboxed iframe; edit/add/delete = the page transforms)
  chat-inbox.jsonl   -> what Dustin says in the COPILOT tab (voice or typed)
  chat-outbox.jsonl  -> what Claude Code answers; the tab renders + speaks it

Run:  python app.py   (or run.bat)  ->  http://localhost:8410
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

_cfg = {"bot_base": "http://10.20.20.100:8796", "port": 8410,
        "voicebox_url": "http://127.0.0.1:17493"}
try:
    _cfg.update(json.loads((ROOT / "config.json").read_text(encoding="utf-8")))
except Exception:
    pass
# EMBEDDED mode (the "friend edition"): MF_EMBEDDED=1 (or config "embedded": true)
# makes this server spawn the bot engine from bot/run_bot.py and point itself at
# it - one folder, one command, no rocker, no Docker.
EMBEDDED = os.environ.get("MF_EMBEDDED", "").strip() == "1" or bool(_cfg.get("embedded"))
if EMBEDDED:
    _cfg["bot_base"] = f"http://127.0.0.1:{int(_cfg.get('bot_port', 8796))}"
BOT = str(_cfg["bot_base"]).rstrip("/")
PORT = int(_cfg["port"])


def _bot_supervisor():
    """Keep the embedded bot engine alive: spawn, watch, restart with backoff."""
    import atexit
    backoff = 3
    while True:
        proc = subprocess.Popen([sys.executable, str(ROOT / "bot" / "run_bot.py")],
                                cwd=str(ROOT / "bot"))
        atexit.register(lambda p=proc: p.poll() is None and p.kill())
        rc = proc.wait()
        print(f"[embedded bot] exited rc={rc}; restarting in {backoff}s "
              f"(check bot/.env if this loops)")
        time.sleep(backoff)
        backoff = min(60, backoff * 2)
VOICEBOX = str(_cfg["voicebox_url"]).rstrip("/")
_vb_profile = {"id": None}  # cached first kokoro profile (API shape per the Interview Bot)

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
  the bot over http://{bot}/api/* (status, radar, reddit, bars, news, spark). When an answer
  deserves a panel, build it and say so in a sentence.
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
    prompt = BRIDGE_PROMPT.format(bot=BOT.split("//", 1)[-1], memory=mem or "(empty)",
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
           "config", "log", "spark", "bars", "news"}


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


def _vb_first_profile():
    """First Kokoro-capable Voicebox profile (engine reported as preset_engine
    for presets, default_engine for clones - same filter the Interview Bot uses)."""
    if _vb_profile["id"]:
        return _vb_profile["id"]
    req = urllib.request.Request(f"{VOICEBOX}/profiles", headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=8) as r:
        data = json.loads(r.read())
    items = data if isinstance(data, list) else data.get("profiles", [])
    for p in items:
        if (p.get("preset_engine") or p.get("default_engine") or "").lower() == "kokoro":
            pid = p.get("id") or p.get("profile_id")
            if pid:
                _vb_profile["id"] = pid
                return pid
    return None


def _panels_state(root=None):
    """[{name, title, size, mtime}] sorted by filename. Title from a leading
    <!-- title: X --> comment; size from <!-- size: full|wide|tall --> (layout
    hint: full = whole row, wide = double width, tall = extra height)."""
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
                    if cand in ("full", "wide", "tall"):
                        size = cand
            except Exception:
                pass
            items.append({"name": f.name, "title": title, "size": size,
                          "mtime": round(f.stat().st_mtime, 2)})
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
            return self._json({"inbox": _read_jsonl(INBOX), "outbox": _read_jsonl(OUTBOX)})

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

        if path == "/api/meta":
            return self._json({"bot_base": BOT, "port": PORT, "root": str(ROOT),
                               "voicebox": VOICEBOX, "user": str(_cfg.get("user", "Dustin"))})

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
            out = []
            if SAVED.exists():
                for d in sorted(SAVED.iterdir(), reverse=True):
                    if d.is_dir():
                        n = len(list(d.glob("*.html")))
                        out.append({"name": d.name, "panels": n,
                                    "ts": round(d.stat().st_mtime, 0)})
            return self._json({"saved": out})

        if path == "/api/tts/health":
            try:
                pid = _vb_first_profile()
                return self._json({"ok": bool(pid), "profile_id": pid,
                                   "hint": None if pid else "no Kokoro voice in Voicebox"})
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
                pid = _vb_first_profile()
                if not pid:
                    return self._json({"error": "no Kokoro voice profile in Voicebox"}, 502)
                payload = json.dumps({"text": text, "profile_id": pid,
                                      "engine": "kokoro", "language": "en"}).encode()
                req = urllib.request.Request(f"{VOICEBOX}/generate/stream", data=payload,
                                             method="POST",
                                             headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=120) as r:
                    audio = r.read()
                    ctype = r.headers.get("Content-Type", "audio/wav")
                if "json" in ctype:
                    return self._json({"error": "voicebox returned JSON not audio"}, 502)
                return self._raw(audio, ctype)
            except Exception as e:
                _vb_profile["id"] = None  # re-probe next time
                return self._json({"error": f"voicebox failed: {str(e)[:120]}"}, 502)

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
    SAVED.mkdir(exist_ok=True)
    if not MEMORY.exists():
        MEMORY.write_text(DEFAULT_MEMORY, encoding="utf-8")
    for p in (INBOX, OUTBOX):
        if not p.exists():
            p.write_text("", encoding="utf-8")
    srv = Server(("127.0.0.1", PORT), Handler)
    print(f"MARKET FORGE  ->  http://localhost:{PORT}")
    if EMBEDDED:
        threading.Thread(target=_bot_supervisor, daemon=True).start()
        print(f"  bot API: {BOT}  (EMBEDDED - engine runs in this process tree)")
    else:
        print(f"  bot API: {BOT}")
    print(f"  panels:  {PANELS}  (Claude Code writes here; the WORKBENCH renders it live)")
    print("  chat:    chat-inbox.jsonl / chat-outbox.jsonl")
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
