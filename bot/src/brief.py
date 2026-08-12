"""The "what changed" brief. Deterministic first, model second.

What it is
----------
A short periodic read of the desk: regime, your positions, what the radar found
since last time, and whether anything is unprotected. Written to journal.jsonl so
it lands on the JOURNAL tab and in the RADAR > Brief pane.

The design rule that matters
----------------------------
**The facts are computed in code. The model only writes the sentence.**

A brief that asks a model "what changed?" and lets it read the raw state will
occasionally invent a change, and a fabricated alert about your own money is
worse than no brief. So `collect()` produces a factual diff with no model
involved, and the model is handed that diff and asked only to phrase it. If the
model is unavailable the brief still works - it just reads like a machine, which
is the correct failure mode.

It also refuses to say anything when nothing changed, because a brief that fires
every 5 minutes saying "no change" trains you to ignore it, and then it is
useless on the day something does change.
"""
from __future__ import annotations

import json
import time

import config

STATE_PATH = config.DATA_DIR / "brief-state.json"
OUT_PATH = config.DATA_DIR / "brief-latest.json"


def _load(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def collect(client, conn, cfg=config):
    """The factual diff since the last brief. No model, no opinions."""
    prev = _load(STATE_PATH, {})
    now = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S")}
    changes, facts = [], {}

    # --- regime -------------------------------------------------------------
    try:
        import regime
        r = regime.read(client)
        now["regime"] = r.get("regime")
        facts["regime"] = {"label": r.get("label"), "note": r.get("note"),
                           "short_posture": r.get("short_posture"),
                           "distribution_days": r.get("max_distribution_days")}
        if prev.get("regime") and prev["regime"] != now["regime"]:
            changes.append({"kind": "regime", "severity": "high",
                            "text": f"Regime moved {prev['regime'].upper()} -> "
                                    f"{str(now['regime']).upper()}. {r.get('note')}"})
        if r.get("follow_through_broken"):
            changes.append({"kind": "regime", "severity": "high",
                            "text": f"Follow-through day FAILED "
                                    f"({', '.join(str(d) for d in r['follow_through_broken'])}): "
                                    f"a confirmed rally lost the low that defined it."})
    except Exception as e:
        facts["regime"] = {"error": str(e)[:120]}

    # --- positions + protection ---------------------------------------------
    try:
        import api as _api
        pos = client.list_positions() or []
        now["positions"] = sorted(p["symbol"] for p in pos)
        facts["positions"] = [{"symbol": p["symbol"], "qty": p["qty"],
                               "pl": round(p["unrealized_pl_cents"] / 100, 2)}
                              for p in pos]
        gone = set(prev.get("positions") or []) - set(now["positions"])
        new = set(now["positions"]) - set(prev.get("positions") or [])
        if gone:
            changes.append({"kind": "position", "severity": "high",
                            "text": f"Closed since last brief: {', '.join(sorted(gone))}. "
                                    f"A stop may have fired."})
        if new:
            changes.append({"kind": "position", "severity": "medium",
                            "text": f"New position(s): {', '.join(sorted(new))}."})

        naked = _api.unprotected_positions(client)
        facts["unprotected"] = naked
        if naked:
            changes.append({"kind": "risk", "severity": "high",
                            "text": "UNPROTECTED: " + ", ".join(
                                f"{u['symbol']} ({u['side']}, needs a {u['needs']})"
                                for u in naked)})
    except Exception as e:
        facts["positions_error"] = str(e)[:120]

    # --- radar delta --------------------------------------------------------
    try:
        import db as _db
        rows = _db.recent_alerts(conn, limit=40) or []
        syms = [r["symbol"] for r in rows]
        now["alerts"] = sorted(syms)
        fresh = [r for r in rows if r["symbol"] not in set(prev.get("alerts") or [])]
        facts["new_alerts"] = [{"symbol": r["symbol"], "score": r["score"],
                                "pct": r["pct"], "why": r["why"]}
                               for r in fresh[:8]]
        strong = [r for r in fresh
                  if (r["score"] or 0) >= getattr(cfg, "RADAR_LLM_MIN_SCORE", 60)]
        if strong:
            changes.append({"kind": "radar", "severity": "medium",
                            "text": "New catalysts: " + ", ".join(
                                f"{r['symbol']} ({r['score']})" for r in strong[:6])})
    except Exception as e:
        facts["radar_error"] = str(e)[:120]

    # --- live tap -----------------------------------------------------------
    try:
        import stream
        live = stream.read_live() or {}
        facts["live_symbols"] = len(live)
        if not live:
            facts["live_note"] = ("no live tap - every price here is >=15 min old "
                                  "on the free plan")
    except Exception:
        pass

    return {"now": now, "changes": changes, "facts": facts,
            "quiet": not changes, "prev_ts": prev.get("ts")}


def phrase(diff, cfg=config):
    """Hand the model the FACTS and ask only for the sentence. Never the raw state."""
    if diff["quiet"]:
        return None
    bullets = "\n".join(f"- [{c['severity']}] {c['text']}" for c in diff["changes"])
    try:
        import llm
        prompt = (
            "You write a 2-3 sentence trading-desk brief. You are given the ONLY "
            "facts you may use. Do not add tickers, numbers or events that are "
            "not listed. Do not speculate about causes. Do not give advice. If "
            "the facts are thin, be brief.\n\nFACTS:\n" + bullets
        )
        out = llm.complete(prompt) if hasattr(llm, "complete") else None
        if out and len(out.strip()) > 10:
            return out.strip()
    except Exception:
        pass
    return bullets          # machine voice beats a wrong sentence


def run(client, conn, cfg=config):
    diff = collect(client, conn, cfg)
    text = phrase(diff, cfg)
    payload = {"ts": diff["now"]["ts"], "quiet": diff["quiet"],
               "text": text, "changes": diff["changes"], "facts": diff["facts"]}
    try:
        OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        OUT_PATH.write_text(json.dumps(payload, indent=1), encoding="utf-8")
        STATE_PATH.write_text(json.dumps(diff["now"]), encoding="utf-8")
    except Exception as e:
        print(f"[brief] write failed: {e}")
    return payload


if __name__ == "__main__":
    import db
    from alpaca_client import AlpacaClient
    conn = db.connect()
    db.init_db(conn)
    p = run(AlpacaClient(), conn)
    print(p["text"] or "no change since the last brief")
