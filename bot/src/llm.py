"""Catalyst scoring for the radar: signal vs noise, 0-100.

Two providers, picked by RADAR_LLM_PROVIDER:
  agent  - the local coding-agent CLI (claude; CLAUDE_BIN overrides). The SAME
           optional dependency that powers the copilot seat, so scoring needs
           no Ollama and no extra install. Default model: haiku (cheap, fast).
  openai - any OpenAI-compatible endpoint (Ollama, OpenRouter, a proxy).
  auto   - agent when a CLI is on PATH, else the endpoint if configured.

Returns None on ANY failure so the radar degrades gracefully to alert-only -
and auto-entries need scores, so no scorer also means no auto entries.

The model triages (real catalyst vs noise); it never sizes or predicts price.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import urllib.request

import config

SYSTEM = (
    "You are a terse trading catalyst screener. Given a stock that moved a lot today "
    "and its recent news headlines, decide if the move is driven by a REAL, durable "
    "catalyst (M&A, earnings beat or raised guidance, FDA/regulatory approval, a major "
    "contract or partnership, index inclusion, notable analyst action, or an IPO) versus "
    "NOISE (low-float pump, technical bounce, a vague 'stocks moving' roundup, or no "
    "clear news). Reply with ONLY a compact JSON object, no prose, no markdown."
)


# ---------------------------------------------------------------------------
# Which coding agent is installed. Two supported, and the differences are not
# cosmetic: they take different flags AND deliver the prompt differently.
#
#   claude - prompt on STDIN, flags that skip the tool/plugin boot (that boot is
#            most of the latency: seconds instead of minutes).
#   codex  - prompt as an ARGUMENT. `codex exec` blocks forever waiting on stdin
#            if you leave it open, so stdin is closed and the prompt is argv.
#
# AGENT_BIN pins an exact binary, AGENT_KIND pins which dialect to speak. Both
# default to auto-detect. AGENT_ARGS lets you override the flags entirely
# without touching code, because a CLI's flags are not ours to guarantee.
# ---------------------------------------------------------------------------
AGENT_SPECS = {
    "claude": {
        "names": ("claude", "claude.cmd", "claude.exe"),
        "args": ["-p", "--model", "{model}", "--tools", "", "--strict-mcp-config",
                 "--mcp-config", '{"mcpServers":{}}', "--no-session-persistence",
                 "--output-format", "text"],
        "prompt": "stdin",
    },
    "codex": {
        "names": ("codex", "codex.cmd", "codex.exe"),
        "args": ["exec", "--model", "{model}", "--skip-git-repo-check", "{prompt}"],
        "prompt": "argv",
    },
}


def _agent_bin(kind=None):
    """(binary, kind) for the local coding-agent CLI, or (None, None).

    AGENT_BIN wins. CLAUDE_BIN is still honoured so nobody's existing .env
    breaks - it just means "claude" unless AGENT_KIND says otherwise.
    """
    want = (kind or os.environ.get("AGENT_KIND", "auto") or "auto").lower()
    override = os.environ.get("AGENT_BIN") or os.environ.get("CLAUDE_BIN")
    if override:
        k = want if want in AGENT_SPECS else _kind_from_path(override)
        return override, k
    order = [want] if want in AGENT_SPECS else list(AGENT_SPECS)
    for k in order:
        for name in AGENT_SPECS[k]["names"]:
            f = shutil.which(name)
            if f:
                return f, k
    return None, None


def _kind_from_path(path):
    """Guess the dialect from a binary name. Defaults to claude, which is what
    every pre-existing CLAUDE_BIN in the wild points at."""
    low = str(path).lower()
    for k in AGENT_SPECS:
        if k in low:
            return k
    return "claude"


def _agent_argv(bin_, kind, model, prompt):
    """Build argv for this agent, and decide what goes on stdin."""
    spec = AGENT_SPECS.get(kind) or AGENT_SPECS["claude"]
    raw = os.environ.get("AGENT_ARGS")
    args = raw.split() if raw else spec["args"]
    argv, stdin_text = [bin_], None
    for a in args:
        if a == "{prompt}":
            argv.append(prompt)
        else:
            argv.append(a.replace("{model}", str(model)))
    if spec["prompt"] == "stdin":
        stdin_text = prompt
    if bin_.lower().endswith((".cmd", ".bat")):
        argv = ["cmd", "/c"] + argv          # .cmd shims cannot be exec'd directly
    return argv, stdin_text


def _classify_agent(user, cfg):
    """One headless agent turn, in whichever dialect is installed."""
    bin_, kind = _agent_bin()
    if not bin_:
        return None
    model = cfg.RADAR_AGENT_MODEL if kind == "claude" else os.environ.get(
        "AGENT_MODEL", cfg.RADAR_AGENT_MODEL)
    argv, stdin_text = _agent_argv(bin_, kind, model, SYSTEM + "\n\n" + user)
    kw = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    try:
        p = subprocess.run(argv, input=stdin_text, text=True,
                           encoding="utf-8", errors="replace", capture_output=True,
                           timeout=cfg.RADAR_AGENT_TIMEOUT,
                           # No stdin text means CLOSE stdin. Leaving it open is
                           # how `codex exec` hangs until the timeout kills it.
                           **({} if stdin_text is not None
                              else {"stdin": subprocess.DEVNULL}), **kw)
        if p.returncode != 0:
            return None
        return _parse(p.stdout or "")
    except Exception:
        return None


def classify(symbol, pct, price, headlines, cfg=config):
    if not cfg.RADAR_USE_LLM:
        return None
    hl = "\n".join(f"- {h}" for h in headlines[:4]) or "- (no headlines found)"
    user = (
        f"Symbol: {symbol}\nMove: +{pct:.1f}% today\nPrice: ${price:,.2f}\n"
        f"Recent headlines:\n{hl}\n\n"
        'Output JSON exactly: {"score": <0-100 how strong and tradeable the catalyst is>, '
        '"verdict": "signal" or "noise", "catalyst_type": "<short label>", '
        '"why": "<one short sentence>"}'
    )
    provider = getattr(cfg, "RADAR_LLM_PROVIDER", "auto")
    if provider in ("auto", "agent"):
        # _agent_bin() returns a TUPLE now. `if _agent_bin():` would be true even
        # when it is (None, None), which would route every scan into an agent
        # that does not exist and silently return no scores.
        if _agent_bin()[0]:
            # a failed call returns None = alert-only for this mover; do NOT
            # fall through to the endpoint mid-scan or scores mix providers
            return _classify_agent(user, cfg)
        if provider == "agent":
            return None
    return _classify_openai(user, cfg)


def _classify_openai(user, cfg):
    base = (cfg.RADAR_LLM_BASE_URL or "").rstrip("/")
    if not base:
        return None
    body = json.dumps({
        "model": cfg.RADAR_LLM_MODEL,
        "messages": [{"role": "system", "content": SYSTEM},
                     {"role": "user", "content": user}],
        "temperature": 0, "stream": False,
    }).encode()
    headers = {"Content-Type": "application/json"}
    if cfg.RADAR_LLM_API_KEY:
        headers["Authorization"] = f"Bearer {cfg.RADAR_LLM_API_KEY}"
    try:
        req = urllib.request.Request(base + "/chat/completions", data=body, headers=headers)
        resp = urllib.request.urlopen(req, timeout=cfg.RADAR_LLM_TIMEOUT)
        data = json.loads(resp.read())
        return _parse(data["choices"][0]["message"]["content"])
    except Exception:
        return None


def _parse(content):
    s, e = content.find("{"), content.rfind("}")
    if s < 0 or e < 0:
        return None
    try:
        obj = json.loads(content[s:e + 1])
    except Exception:
        return None
    try:
        score = int(float(obj.get("score", 0)))
    except (TypeError, ValueError):
        score = 0
    return {
        "score": max(0, min(100, score)),
        "verdict": (obj.get("verdict") or "noise").lower().strip(),
        "catalyst_type": str(obj.get("catalyst_type", ""))[:40],
        "why": str(obj.get("why", ""))[:200],
    }
