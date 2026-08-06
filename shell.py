"""Market Forge - native desktop shell (pywebview + WebView2).

This is the entry point of MarketForge.exe. It does three jobs:

1. FROZEN CHILD DISPATCH (the infinite-loop fix). app.py and the engine spawn
   helpers as [sys.executable, "<script>.py"]. Under PyInstaller sys.executable
   IS this exe, so without the shim every engine spawn would relaunch the whole
   app forever. The shim recognizes the two scripts this app legitimately runs
   as children - bot/run_bot.py and bot/src/radar.py - and emulates
   `python <script>` for them from the files shipped next to the exe.

   Why dispatch instead of running the engine in-process: the engine is a child
   ON PURPOSE. The supervisor's crash-restart, the orphan pid-kill on startup,
   and the timeout-killable radar scan are all process-level safety properties;
   folding the engine into this process would silently delete all three. The
   subprocess model stays; only argv interpretation changes when frozen.

2. SINGLE INSTANCE. If the port already serves Market Forge, ask that process
   to focus its window and exit. Double-clicking the icon twice must not start
   a second server (or worse, a second radar scheduler).

3. THE WINDOW. Start the existing stdlib server on a thread, open one WebView2
   window on it, and on window close shut everything down - including the
   engine child (atexit in app.py, backstopped by the stale-pid kill on the
   next start).

app.py stays stdlib-only and never imports pywebview; it only exposes
SHELL/SHELL_HOOKS for this file to fill in. The frontend never knows which
shell it is in - it feature-detects via /api/shell (see docs/NATIVE-SHELL-PLAN.md).

Dev run:  python shell.py   (window, no freeze needed - MF_PORT/MF_BOT_PORT honored)
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.request
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))

# Scripts the exe may be asked to run as its own children. Anything else on
# argv is a mistake - refuse loudly rather than boot a second desk.
_CHILD_SCRIPTS = ("run_bot.py", "radar.py")


def _dispatch_child():
    """Emulate `python <script>.py` when the frozen exe is called that way."""
    if not FROZEN or len(sys.argv) < 2:
        return
    tgt = sys.argv[1]
    if not str(tgt).lower().endswith(".py"):
        return
    p = Path(tgt).resolve()
    if p.name not in _CHILD_SCRIPTS:
        try:
            print(f"[shell] refusing unknown child script: {tgt}", file=sys.stderr)
        except Exception:
            pass
        sys.exit(2)
    # The scripts live on disk next to the exe and import siblings by folder
    # (run_bot.py inserts bot/src itself; radar.py needs bot/src on the path).
    sys.path.insert(0, str(p.parent))
    sys.argv = sys.argv[1:]           # argv[0] = the script, as python would
    import runpy
    runpy.run_path(str(p), run_name="__main__")
    sys.exit(0)


_dispatch_child()


def _open_log():
    """A windowed exe has no console: print() would vanish (or worse). Give the
    MAIN process a real log file. Engine output goes to logs/engine.log via the
    supervisor - dispatch children keep the handles their parent gave them,
    which is why this runs only after _dispatch_child()."""
    if not FROZEN:
        return
    logs = Path(sys.executable).resolve().parent / "logs"
    logs.mkdir(exist_ok=True)
    lp = logs / "marketforge.log"
    mode = "w" if (lp.exists() and lp.stat().st_size > 5_000_000) else "a"
    f = open(lp, mode, buffering=1, encoding="utf-8", errors="replace")
    sys.stdout = sys.stderr = f
    print(f"\n=== Market Forge shell start {time.strftime('%Y-%m-%d %H:%M:%S')} ===")


_open_log()


def _existing_desk(port: int) -> bool:
    """Is the port already serving THIS app (not just any server)?"""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/meta", timeout=2.5) as r:
            return json.load(r).get("app") == "marketforge"
    except Exception:
        return False


def _msgbox(text: str, title="Market Forge"):
    print(f"[shell] {title}: {text}")
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, text, title, 0x10)  # MB_ICONERROR
        except Exception:
            pass


def main():
    os.environ.setdefault("MF_EMBEDDED", "1")   # the exe is the friend edition
    import app as desk

    if _existing_desk(desk.PORT):
        # Second launch: focus the first window instead of a second server.
        print(f"[shell] Market Forge already running on :{desk.PORT} - focusing it")
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{desk.PORT}/api/shell/focus", data=b"{}",
                method="POST", headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=3) as r:
                ok = json.load(r).get("ok")
        except Exception:
            ok = False
        if not ok:
            # The running desk has no window to raise (browser lane) - at least
            # put the user in front of it.
            import webbrowser
            webbrowser.open(f"http://127.0.0.1:{desk.PORT}")
        sys.exit(0)

    try:
        srv = desk.build_server()
    except SystemExit:
        _msgbox(f"Port {desk.PORT} is already in use by another program.\n"
                f"Close it, or set MF_PORT to a free port and relaunch.")
        raise

    desk.SHELL.update({"shell": "pywebview", "can_focus": True})
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    import webview   # imported late: dispatch children must never pay for it

    win = webview.create_window(
        "Market Forge", f"http://127.0.0.1:{desk.PORT}",
        width=1600, height=1000, min_size=(1100, 700),
        background_color="#0a0d13",   # forge --bg: no white flash before paint
        text_select=True)

    def _focus():
        for op in (win.restore, win.show):
            try:
                op()
            except Exception:
                pass
        try:                    # pull to front, then release always-on-top
            win.on_top = True
            win.on_top = False
        except Exception:
            pass

    desk.SHELL_HOOKS["focus"] = _focus
    desk.SHELL_HOOKS["quit"] = lambda: win.destroy()

    # private_mode=False + a storage dir: without it WebView2 runs incognito
    # and localStorage (theme pick, chart symbol, splash cache) dies per run.
    webview.start(private_mode=False,
                  storage_path=str(desk.ROOT / "webview-data"))

    # window closed (X, or /api/shell/quit): stop serving, kill the engine
    # SYNCHRONOUSLY, then exit. atexit is not reliable on this path (proven in
    # the dev test: the shell exited clean and left a live engine) - the
    # startup stale-pid sweep stays as the backstop, not the mechanism.
    print("[shell] window closed - shutting down")
    try:
        srv.shutdown()
    except Exception:
        pass
    try:
        desk.stop_engine()
    except Exception:
        pass
    sys.exit(0)


if __name__ == "__main__":
    main()
