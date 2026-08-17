"""TradingView remote control, via Chrome DevTools Protocol.

WHY CHROME AND NOT THE DESKTOP APP: TradingView Desktop ships from the Microsoft
Store as a packaged app, so you cannot pass it --remote-debugging-port and its
install dir is ACL-locked. Chrome takes the flag happily, and driving a browser
through a documented protocol beats reverse-engineering someone's Electron
internals - it survives TradingView updates that would break a DOM-scraper.

WHAT THIS DOES NOT DO: no drawing tools, no indicator manipulation, no reading
their internal chart state. Symbol and interval travel in the URL (public and
stable), and screenshots come from CDP. That is the whole surface, on purpose.

STDLIB ONLY. CDP's HTTP half (/json/list, /json/new, /json/close) needs nothing
special; the interesting half (navigate in place, screenshot) needs a WebSocket,
so there is a ~70-line client below. If it fails for any reason we degrade to
close-and-reopen, which still gets you the right chart.

PORT 9223, NOT 9222 (changed 2026-08-16). The tradesdontlie/tradingview-mcp
bridge drives TradingView DESKTOP over CDP and its launch scripts hardcode
9222. Two CDP endpoints cannot share a port, and the failure is confusing
rather than loud: whichever process binds first owns it, and the second one
either fails to bind or - worse - silently attaches to the FIRST one's targets
and starts driving the wrong application. So the desktop app keeps 9222, which
its scripts assume, and this Chrome moved to 9223, which is one env var here.
Override with MF_TV_CDP.

Start the browser with run-tradingview.bat.
"""
from __future__ import annotations

import base64
import json
import os
import socket
import struct
import time
import urllib.parse
import urllib.request

CDP = os.environ.get("MF_TV_CDP", "http://127.0.0.1:9223").rstrip("/")
DEFAULT_EXCHANGE = os.environ.get("MF_TV_EXCHANGE", "")   # e.g. NASDAQ; blank = let TV resolve
# TradingView interval codes: 1,5,15,60,240 = minutes; D,W,M
INTERVALS = {"1m": "1", "5m": "5", "15m": "15", "30m": "30", "1h": "60",
             "4h": "240", "d": "D", "1d": "D", "daily": "D",
             "w": "W", "1w": "W", "weekly": "W", "m": "M", "monthly": "M"}


def _http(path, timeout=5):
    with urllib.request.urlopen(f"{CDP}{path}", timeout=timeout) as r:
        body = r.read().decode("utf-8", "replace")
    return json.loads(body) if body.strip().startswith(("{", "[")) else body


def status():
    """Is a debuggable Chrome there, and is TradingView open in it?"""
    try:
        ver = _http("/json/version")
        tabs = [t for t in _http("/json/list") if t.get("type") == "page"]
        tv = [t for t in tabs if "tradingview.com" in str(t.get("url", ""))]
        return {"ok": True, "browser": ver.get("Browser"), "tabs": len(tabs),
                "tradingview_tabs": len(tv),
                "current": (tv[0].get("url") if tv else None)}
    except Exception as e:
        return {"ok": False, "error": f"{e.__class__.__name__}: {str(e)[:160]}",
                "hint": "start Chrome with run-tradingview.bat"}


# ---------------------------------------------------------------- websocket
class _WS:
    """Minimal RFC6455 client - just enough for one CDP request/response.

    Deliberately not a general websocket library: no continuation frames, no
    extensions, no ping/pong handling beyond ignoring them. CDP replies to a
    single command in a single text frame, which is all we need."""

    def __init__(self, url, timeout=20):
        u = urllib.parse.urlparse(url)
        self.sock = socket.create_connection((u.hostname, u.port or 80), timeout=timeout)
        self.sock.settimeout(timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        path = u.path + (("?" + u.query) if u.query else "")
        self.sock.sendall((
            f"GET {path} HTTP/1.1\r\nHost: {u.hostname}:{u.port}\r\n"
            f"Upgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
        ).encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("handshake closed early")
            buf += chunk
        if b"101" not in buf.split(b"\r\n", 1)[0]:
            raise ConnectionError(f"no upgrade: {buf.split(chr(13).encode())[0][:80]}")
        self._rest = buf.split(b"\r\n\r\n", 1)[1]

    def _recv(self, n):
        while len(self._rest) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("socket closed")
            self._rest += chunk
        out, self._rest = self._rest[:n], self._rest[n:]
        return out

    def send(self, text):
        payload = text.encode()
        n = len(payload)
        hdr = b"\x81"                      # FIN + text opcode
        if n < 126:
            hdr += struct.pack("!B", n | 0x80)
        elif n < 1 << 16:
            hdr += struct.pack("!BH", 126 | 0x80, n)
        else:
            hdr += struct.pack("!BQ", 127 | 0x80, n)
        mask = os.urandom(4)               # client frames MUST be masked
        self.sock.sendall(hdr + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))

    def recv(self):
        b0, b1 = self._recv(2)
        ln = b1 & 0x7F
        if ln == 126:
            ln = struct.unpack("!H", self._recv(2))[0]
        elif ln == 127:
            ln = struct.unpack("!Q", self._recv(8))[0]
        data = self._recv(ln)
        if b1 & 0x80:                      # server frames are never masked, but be safe
            m = data[:4]
            data = bytes(c ^ m[i % 4] for i, c in enumerate(data[4:]))
        return (b0 & 0x0F), data

    def close(self):
        try:
            self.sock.close()
        except Exception:
            pass


def _cdp(ws_url, method, params=None, timeout=25):
    """One CDP command. Reads until the matching id comes back, skipping the
    event traffic CDP interleaves."""
    ws = _WS(ws_url, timeout)
    try:
        mid = int(time.time() * 1000) % 100000
        ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        deadline = time.time() + timeout
        while time.time() < deadline:
            op, data = ws.recv()
            if op == 8:
                raise ConnectionError("closed by browser")
            if op != 1:
                continue
            msg = json.loads(data.decode("utf-8", "replace"))
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(str(msg["error"])[:200])
                return msg.get("result", {})
        raise TimeoutError(f"{method} timed out")
    finally:
        ws.close()


# ---------------------------------------------------------------- actions
def chart_url(symbol, interval=None):
    sym = str(symbol).upper().strip()
    if DEFAULT_EXCHANGE and ":" not in sym:
        sym = f"{DEFAULT_EXCHANGE}:{sym}"
    q = {"symbol": sym}
    if interval:
        q["interval"] = INTERVALS.get(str(interval).lower(), str(interval).upper())
    return "https://www.tradingview.com/chart/?" + urllib.parse.urlencode(q)


def _tv_tab():
    for t in _http("/json/list"):
        if t.get("type") == "page" and "tradingview.com" in str(t.get("url", "")):
            return t
    return None


def open_chart(symbol, interval=None, focus=True):
    """Point the TradingView tab at a symbol. Navigates in place when the
    websocket works; otherwise closes and reopens (loses drawings, still lands
    on the right chart)."""
    url = chart_url(symbol, interval)
    tab = _tv_tab()
    if tab and tab.get("webSocketDebuggerUrl"):
        try:
            _cdp(tab["webSocketDebuggerUrl"], "Page.navigate", {"url": url})
            if focus:
                try:
                    _http(f"/json/activate/{tab['id']}")
                except Exception:
                    pass
            return {"ok": True, "url": url, "how": "navigated", "tab": tab["id"]}
        except Exception as e:
            fallback = f"{e.__class__.__name__}: {str(e)[:120]}"
    else:
        fallback = "no existing TradingView tab"
    try:
        if tab:
            _http(f"/json/close/{tab['id']}")
        new = _http("/json/new?" + urllib.parse.quote(url, safe=":/?=&"))
        return {"ok": True, "url": url, "how": "reopened", "note": fallback,
                "tab": (new or {}).get("id")}
    except Exception as e:
        return {"ok": False, "error": f"{e.__class__.__name__}: {str(e)[:160]}",
                "note": fallback, "url": url}


def shot(dest, settle=2.5):
    """PNG of the TradingView tab. This is the point of the whole module: it
    turns 'what does this chart look like' into a file an agent can actually
    read."""
    tab = _tv_tab()
    if not tab:
        return {"ok": False, "error": "no TradingView tab open"}
    ws = tab.get("webSocketDebuggerUrl")
    if not ws:
        return {"ok": False, "error": "tab exposes no websocket (is this a debug Chrome?)"}
    time.sleep(settle)                     # let the chart paint before capturing
    res = _cdp(ws, "Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False})
    data = base64.b64decode(res.get("data", ""))
    if len(data) < 1000:
        return {"ok": False, "error": "screenshot came back empty"}
    dest = os.fspath(dest)
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    with open(dest, "wb") as f:
        f.write(data)
    return {"ok": True, "path": dest, "bytes": len(data), "url": tab.get("url")}
