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


def _agent_bin():
    """The local coding-agent CLI, if any. CLAUDE_BIN overrides; claude first
    (mirrors the dashboard bridge's resolution)."""
    override = os.environ.get("CLAUDE_BIN")
    if override:
        return override
    for name in ("claude", "claude.cmd", "claude.exe"):
        f = shutil.which(name)
        if f:
            return f
    return None


def _classify_agent(user, cfg):
    """One headless agent turn. The fast flags (--tools \"\" + empty MCP config)
    skip the tool/plugin boot that eats most of the latency - seconds, not
    minutes. input= gives the child a real stdin (windowed-build safe)."""
    bin_ = _agent_bin()
    if not bin_:
        return None
    argv = [bin_, "-p", "--model", cfg.RADAR_AGENT_MODEL, "--tools", "",
            "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
            "--no-session-persistence", "--output-format", "text"]
    if bin_.lower().endswith((".cmd", ".bat")):
        argv = ["cmd", "/c"] + argv          # .cmd shims cannot be exec'd directly
    kw = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    try:
        p = subprocess.run(argv, input=SYSTEM + "\n\n" + user, text=True,
                           encoding="utf-8", errors="replace", capture_output=True,
                           timeout=cfg.RADAR_AGENT_TIMEOUT, **kw)
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
        if _agent_bin():
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
