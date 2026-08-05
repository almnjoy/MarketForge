"""Tiny OpenAI-compatible chat client for the radar's catalyst curation.

Defaults to the homelab Ollama on llmhub (free, private, no key). Point
RADAR_LLM_BASE_URL at OpenRouter / an Anthropic proxy / etc. to swap models.
Returns None on ANY failure so the radar degrades gracefully to rules-only.

The model triages (real catalyst vs noise); it never sizes or predicts price.
"""
from __future__ import annotations

import json
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


def classify(symbol, pct, price, headlines, cfg=config):
    base = (cfg.RADAR_LLM_BASE_URL or "").rstrip("/")
    if not base or not cfg.RADAR_USE_LLM:
        return None
    hl = "\n".join(f"- {h}" for h in headlines[:4]) or "- (no headlines found)"
    user = (
        f"Symbol: {symbol}\nMove: +{pct:.1f}% today\nPrice: ${price:,.2f}\n"
        f"Recent headlines:\n{hl}\n\n"
        'Output JSON exactly: {"score": <0-100 how strong and tradeable the catalyst is>, '
        '"verdict": "signal" or "noise", "catalyst_type": "<short label>", '
        '"why": "<one short sentence>"}'
    )
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
