"""Risk gates + position sizing. THIS FILE DECIDES MONEY.

The LLM never sizes positions; every numeric decision lives here. Split into a
PURE core (no I/O, unit-tested hard) and an I/O shell (CLI that loads the
screen's candidates + the LLM analysis/critique, reads the live account, and
stages orders for human-gated execution).

Gates (all must pass; a G5 trip HALTS the whole cycle):
  G1 signal        candidate action == 'buy' with a valid entry/stop (stop < entry)
  G2 confidence    analysis confidence >= medium AND critique verdict pass/revise
  G3 position cap  notional <= MAX_POSITION_PCT of bankroll
  G4 sector cap    sector exposure + notional <= MAX_SECTOR_PCT of bankroll
  G5 breakers      daily loss < 5%, drawdown < 15%, else HALT (kill-switch)
  G6 concurrency   open positions < MAX_POSITIONS
  G7 bankroll cap  committed + notional <= bankroll (walls bot off from manual $)
  G8 wash-sale     symbol not inside the loss-exit cooldown window
  G9 fat-finger    qty > 0, notional in band, limit within MAX_LIMIT_DEVIATION

Sizing is RISK-BASED (fixed-fractional off the stop distance), capped by the
position cap; quarter-Kelly is only an optional extra cap. Whole shares unless
FRACTIONAL. All money is integer cents.
"""
from __future__ import annotations

import argparse
import json
import math

import advice
import config
import db
import portfolio
from alpaca_client import AlpacaClient
from sectors import sector_for


# ==========================================================================
# PURE CORE  (no network, no disk; this is what the tests hammer)
# ==========================================================================
def kelly_cap_shares(entry_cents, stop_cents, target_cents, p_win, bankroll_cents, cfg=config):
    """Optional quarter-Kelly notional cap, expressed in shares. Needs a target
    and a win probability from the analysis layer; returns None if unusable."""
    if not target_cents or p_win is None:
        return None
    risk = entry_cents - stop_cents
    reward = target_cents - entry_cents
    if risk <= 0 or reward <= 0:
        return 0.0
    R = reward / risk
    f_full = (p_win * (R + 1) - 1) / R
    if f_full <= 0:
        return 0.0
    frac = cfg.KELLY_FRACTION * f_full
    return (frac * bankroll_cents) / entry_cents


def position_size(entry_cents, stop_cents, bankroll_cents, cfg=config,
                  target_cents=None, p_win=None):
    """Risk-based sizing. Returns (qty, notional_cents).

    dollars risked to the stop = RISK_PER_TRADE_PCT * bankroll
    per-share risk            = entry - stop
    raw shares                = risk / per-share risk

    In STRICT mode that is then clamped to MAX_POSITION_PCT of bankroll. In
    ADVISORY mode (the default) it is not - the concentration is reported by
    advice.advise() instead. Quarter-Kelly still applies in both when enabled.
    """
    per_share_risk = entry_cents - stop_cents
    if per_share_risk <= 0 or entry_cents <= 0:
        return 0, 0
    risk_budget_cents = cfg.RISK_PER_TRADE_PCT * bankroll_cents
    raw = risk_budget_cents / per_share_risk

    # MAX_POSITION_PCT was clamped here unconditionally, which meant G3 could
    # almost never fail: sizing had already made the position legal before the
    # gate looked at it. Advisory mode would then have been decoration - the bot
    # would still never PROPOSE more than 10%.
    #
    # In advisory mode the risk math decides the size (you still only risk
    # RISK_PER_TRADE_PCT to the stop) and the concentration is reported as a
    # notice. A tight stop on an expensive share legitimately wants a large
    # notional; that is the case Dustin asked to be told about rather than
    # silently shrunk.
    qty = raw
    if getattr(cfg, "RISK_MODE", "advisory") == "strict":
        cap_pct = advice.derived_position_cap(cfg)
        qty = min(raw, (cap_pct * bankroll_cents) / entry_cents)

    if cfg.USE_KELLY_CAP:
        kc = kelly_cap_shares(entry_cents, stop_cents, target_cents, p_win, bankroll_cents, cfg)
        if kc is not None:
            qty = min(qty, kc)

    if cfg.FRACTIONAL:
        qty = math.floor(qty * 1000) / 1000.0  # 3dp
    else:
        qty = math.floor(qty)

    if qty <= 0:
        return 0, 0
    notional = int(round(qty * entry_cents))
    return qty, notional


def breaker_state(open_equity_cents, current_equity_cents, peak_equity_cents, cfg=config):
    """Daily-loss (vs day open) and drawdown (vs all-time peak) kill-switch."""
    daily_loss_pct = 0.0
    if open_equity_cents and open_equity_cents > 0:
        daily_loss_pct = max(0.0, (open_equity_cents - current_equity_cents) / open_equity_cents)
    peak = max(peak_equity_cents or 0, current_equity_cents)
    drawdown_pct = max(0.0, (peak - current_equity_cents) / peak) if peak > 0 else 0.0
    tripped_daily = daily_loss_pct >= cfg.MAX_DAILY_LOSS_PCT
    tripped_dd = drawdown_pct >= cfg.MAX_DRAWDOWN_PCT
    return {
        "daily_loss_pct": daily_loss_pct,
        "drawdown_pct": drawdown_pct,
        "tripped_daily": tripped_daily,
        "tripped_drawdown": tripped_dd,
        "tripped": tripped_daily or tripped_dd,
    }


def evaluate(ctx, cfg=config):
    """Pure gate evaluation for ONE candidate.

    ctx keys: symbol, sector, action, confidence, critique_verdict, entry_cents,
    stop_cents, target_cents(optional), p_win(optional), reference_price_cents,
    bankroll_cents, bot_committed_cents, sector_exposure_cents, open_positions,
    in_wash_cooldown(bool), open_equity_cents, current_equity_cents,
    peak_equity_cents.

    Returns {symbol, staged, gates:{...}, order|None, reasons:[...], halt}.
    """
    gates, reasons = {}, []
    sym = ctx["symbol"]

    # G5 breakers first: a trip halts the whole cycle.
    bs = breaker_state(ctx["open_equity_cents"], ctx["current_equity_cents"],
                       ctx["peak_equity_cents"], cfg)
    gates["G5_breakers"] = not bs["tripped"]
    if bs["tripped"]:
        which = "daily-loss" if bs["tripped_daily"] else "drawdown"
        reasons.append(f"G5 HALT ({which}): daily_loss={bs['daily_loss_pct']:.1%} "
                       f"drawdown={bs['drawdown_pct']:.1%}")
        return {"symbol": sym, "staged": False, "gates": gates, "order": None,
                "reasons": reasons, "halt": True}

    entry = ctx.get("entry_cents")
    stop = ctx.get("stop_cents")

    # G1 signal validity
    g1 = (ctx.get("action") == "buy" and entry and stop and 0 < stop < entry)
    gates["G1_signal"] = bool(g1)
    if not g1:
        reasons.append(f"G1: not a valid buy signal (action={ctx.get('action')}, "
                       f"entry={entry}, stop={stop})")

    # G2 confidence + critique verdict
    conf_ok = ctx.get("confidence") in ("high", "medium")
    crit_ok = ctx.get("critique_verdict") in ("pass", "revise")
    gates["G2_confidence"] = conf_ok and crit_ok
    if not conf_ok:
        reasons.append(f"G2: confidence {ctx.get('confidence')!r} < medium")
    if not crit_ok:
        reasons.append(f"G2: critique verdict {ctx.get('critique_verdict')!r} not pass/revise")

    # G6 concurrency
    gates["G6_concurrency"] = ctx["open_positions"] < cfg.MAX_POSITIONS
    if ctx["open_positions"] >= cfg.MAX_POSITIONS:
        reasons.append(f"G6: open positions {ctx['open_positions']} >= {cfg.MAX_POSITIONS}")

    # G8 wash-sale cooldown
    gates["G8_wash_sale"] = not ctx.get("in_wash_cooldown", False)
    if ctx.get("in_wash_cooldown", False):
        reasons.append(f"G8: {sym} in wash-sale cooldown ({cfg.WASH_SALE_COOLDOWN_DAYS}d after a loss exit)")

    # Sizing (needs a valid signal to price against)
    bankroll = ctx.get("bankroll_cents", 0)
    qty, notional = (0, 0)
    if g1:
        qty, notional = position_size(entry, stop, bankroll, cfg,
                                      target_cents=ctx.get("target_cents"),
                                      p_win=ctx.get("p_win"))

    # G3 position cap (derived from the gap tolerance, see advice.py)
    cap_pct = advice.derived_position_cap(cfg)
    gates["G3_position_cap"] = notional <= cap_pct * bankroll + 1e-6 and notional > 0
    if notional <= 0:
        reasons.append("G3: computed size is 0 (risk budget too small for the stop distance)")
    elif notional > cap_pct * bankroll + 1e-6:
        reasons.append("G3: notional exceeds position cap")

    # G4 sector cap
    new_sector = ctx.get("sector_exposure_cents", 0) + notional
    gates["G4_sector_cap"] = new_sector <= cfg.MAX_SECTOR_PCT * bankroll + 1e-6
    if not gates["G4_sector_cap"]:
        reasons.append(f"G4: sector '{ctx.get('sector')}' exposure would exceed "
                       f"{cfg.MAX_SECTOR_PCT:.0%} of bankroll")

    # G7 bankroll cap
    committed = ctx.get("bot_committed_cents", 0)
    gates["G7_bankroll_cap"] = (committed + notional) <= bankroll + 1e-6 and notional > 0
    if notional > 0 and (committed + notional) > bankroll + 1e-6:
        reasons.append(f"G7: would exceed ${bankroll/100:,.0f} bankroll "
                       f"(committed ${committed/100:,.0f})")

    # G9 fat-finger sanity
    ref = ctx.get("reference_price_cents") or entry or 0
    limit = entry
    g9 = True
    if qty <= 0:
        g9 = False
        reasons.append("G9: qty <= 0")
    if notional < cfg.MIN_ORDER_NOTIONAL_CENTS:
        g9 = False
        reasons.append(f"G9: notional ${notional/100:.2f} below ${cfg.MIN_ORDER_NOTIONAL_CENTS/100:.0f} floor")
    if ref and limit and abs(limit - ref) / ref > cfg.MAX_LIMIT_DEVIATION_PCT:
        g9 = False
        reasons.append(f"G9: limit strays >{cfg.MAX_LIMIT_DEVIATION_PCT:.0%} from reference")
    gates["G9_fat_finger"] = g9

    # ADVISORY MODE (default, set 2026-08-11).
    #
    # A failing soft gate no longer kills the ticket. It becomes a sentence, the
    # trade still stages, and the operator decides. Only G1/G5/G9 block, because
    # those are correctness (no stop, kill switch tripped, fat finger) rather
    # than a preference about concentration. Set RISK_MODE=strict to restore the
    # old all-nine behavior without touching code.
    all_pass = all(gates.values())
    blocking = advice.blocking_gates(gates, cfg)
    staged = not blocking
    advisories = advice.advise(ctx, gates, notional, qty, cfg) if not blocking else []

    order = None
    if staged and qty > 0 and entry and stop:
        order = {
            "symbol": sym,
            "sector": ctx.get("sector", "unknown"),
            "side": "buy",
            "qty": qty,
            "limit_price_cents": int(entry),
            "stop_hint_cents": int(stop),
            "notional_cents": int(notional),
            "confidence": ctx.get("confidence"),
            "reason": ctx.get("signal_reason", "trend_pullback_reclaim"),
            "env": cfg.STOCK_ENV,
            # Carried ON the ticket so the thing the operator clicks is the same
            # object that knows what is unusual about it.
            "advisories": advisories,
            "soft_gates_failed": [g for g, ok in gates.items()
                                  if not ok and g not in advice.HARD_GATES],
        }
    return {"symbol": sym, "staged": staged, "all_gates_pass": all_pass,
            "gates": gates, "blocking": blocking, "order": order,
            "advisories": advisories, "reasons": reasons, "halt": False}


# ==========================================================================
# I/O SHELL  (CLI: loads files, hits the API, stages orders)
# ==========================================================================
def _load_json(path):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _candidates_by_symbol():
    data = _load_json(config.CANDIDATES_PATH) or []
    return {c["symbol"]: c for c in data}


def bot_deployed(conn):
    """The bot's own committed capital + per-sector exposure + open count, from
    the orders IT placed. Manual positions are excluded (bankroll sandbox)."""
    total, by_sector, symbols = 0, {}, set()
    for r in db.bot_open_symbols(conn, config.STOCK_ENV):
        px = r["limit_price_cents"] or 0
        cost = int(r["qty"] * px)
        total += cost
        symbols.add(r["symbol"])
        sec = sector_for(r["symbol"])
        by_sector[sec] = by_sector.get(sec, 0) + cost
    return {"committed": total, "by_sector": by_sector, "open": len(symbols)}


def run_symbol(symbol, conn, acct, bot, cooldown, client, cfg=config):
    cand = _candidates_by_symbol().get(symbol, {})
    analysis = _load_json(config.ANALYSIS_DIR / f"{symbol}.json") or {}
    critique = _load_json(config.ANALYSIS_DIR / f"{symbol}.critique.json") or {}

    entry = cand.get("entry_cents")
    ref = None
    try:
        ref = client.get_latest_price(symbol)
    except Exception:
        ref = entry

    current_equity = acct["cash_cents"] + acct["positions_value_cents"]
    open_equity = db.open_equity_today(conn) or current_equity
    peak = db.peak_equity(conn) or current_equity
    sector = cand.get("sector") or sector_for(symbol)

    ctx = {
        "symbol": symbol,
        "sector": sector,
        "action": cand.get("action", "hold"),
        "confidence": analysis.get("confidence", "low" if config.REQUIRE_LLM_ANALYSIS else "high"),
        "critique_verdict": critique.get("verdict", None if config.REQUIRE_LLM_ANALYSIS else "pass"),
        "entry_cents": entry,
        "stop_cents": cand.get("stop_cents"),
        "target_cents": analysis.get("target_cents"),
        "p_win": analysis.get("p_win"),
        "reference_price_cents": ref,
        # sizing + G3/G4/G7 use the bot bankroll and its OWN exposure
        "bankroll_cents": cfg.BOT_BANKROLL_CENTS,
        "bot_committed_cents": bot["committed"],
        "sector_exposure_cents": bot["by_sector"].get(sector, 0),
        "open_positions": bot["open"],
        "in_wash_cooldown": symbol in cooldown,
        "signal_reason": cand.get("reason"),
        # breakers (G5) use real account equity
        "open_equity_cents": open_equity,
        "current_equity_cents": current_equity,
        "peak_equity_cents": peak,
    }
    return evaluate(ctx, cfg)


def _write_staged(orders):
    config.STAGED_ORDERS_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.STAGED_ORDERS_PATH.write_text(json.dumps(orders, indent=2))


def _merge_staged(new_orders):
    existing = _load_json(config.STAGED_ORDERS_PATH) or []
    by_symbol = {o["symbol"]: o for o in existing}
    for o in new_orders:
        by_symbol[o["symbol"]] = o
    _write_staged(list(by_symbol.values()))


def _print_report(results, staged):
    print(config.env_banner())
    print(f"risk mode: {getattr(config, 'RISK_MODE', 'advisory').upper()}"
          + ("  (caps are notices, not blocks)"
             if getattr(config, "RISK_MODE", "advisory") != "strict" else ""))
    print(f"{'symbol':<8}{'entry':>9}{'stop':>9}{'qty':>8}  verdict")
    for r in results:
        o = r["order"] or {}
        entry = o.get("limit_price_cents")
        stop = o.get("stop_hint_cents")
        qty = o.get("qty")
        if r["staged"]:
            verdict = "STAGED" if r.get("all_gates_pass") else "STAGED (with notes)"
        else:
            verdict = f"BLOCKED {r.get('blocking') or []}"
        e = f"{entry/100:.2f}" if entry else "-"
        s = f"{stop/100:.2f}" if stop else "-"
        print(f"{r['symbol']:<8}{e:>9}{s:>9}{str(qty or '-'):>8}  {verdict}")
        # The whole point: say the number out loud, next to the ticket.
        for a in r.get("advisories") or []:
            print(f"          - [{a['severity']}] {a['message']}")
    print(f"\nStaged {len(staged)} order(s) -> {config.STAGED_ORDERS_PATH}")
    if staged:
        print("Review data/staged_orders.json, then run execute.py (human-gated) to place them.")


def main():
    ap = argparse.ArgumentParser(description="Risk gate + sizing; stages orders.")
    ap.add_argument("--symbol", help="evaluate a single symbol; default = all candidates")
    args = ap.parse_args()

    conn = db.connect()
    db.init_db(conn)
    client = AlpacaClient()

    try:
        acct = portfolio.account_snapshot(client)
        db.record_equity(conn, env=config.STOCK_ENV, cash_cents=acct["cash_cents"],
                         positions_value_cents=acct["positions_value_cents"])
    except Exception as e:
        print(f"WARN: could not read account ({e}); aborting to avoid trading blind.")
        return

    cands = _candidates_by_symbol()
    symbols = [args.symbol] if args.symbol else list(cands.keys())
    if not symbols:
        print("No candidates to evaluate. Run screen.py first.")
        return

    bot = bot_deployed(conn)
    cooldown = db.symbols_in_wash_cooldown(conn, config.WASH_SALE_COOLDOWN_DAYS)
    print(f"bankroll ${config.BOT_BANKROLL_CENTS/100:,.0f}  committed "
          f"${bot['committed']/100:,.2f}  open {bot['open']}  cooldown {sorted(cooldown)}")

    staged, results = [], []
    for sym in symbols:
        r = run_symbol(sym, conn, acct, bot, cooldown, client)
        results.append(r)
        if r.get("halt"):
            print("=" * 60)
            print("CIRCUIT BREAKER TRIPPED - halting cycle, staging nothing.")
            for reason in r["reasons"]:
                print("  " + reason)
            _write_staged([])
            return
        if r["staged"]:
            o = r["order"]
            staged.append(o)
            bot["committed"] += o["notional_cents"]
            bot["by_sector"][o["sector"]] = bot["by_sector"].get(o["sector"], 0) + o["notional_cents"]
            bot["open"] += 1

    _merge_staged(staged)
    _print_report(results, staged)


if __name__ == "__main__":
    main()
