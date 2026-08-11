"""Plain-language risk notices. PURE (no I/O), so the tests hammer it.

Why this exists
---------------
The gates in risk.py were binary: a trade either passed all nine or it was not
staged, and the operator saw `FAIL ['G3_position_cap']`. That is a machine
telling a human "no" in a language he did not ask to learn, about his own money,
in an account he is deliberately running concentrated.

Dustin's instruction, 2026-08-11: keep the math, drop the hard blocks, say the
number out loud instead.

    "hey if you make this trade with only 5k in your account, each share is
     approx 500 putting you at 25% of the overall account. But it's staged and
     ready for your review!"

So a cap becomes a sentence, and the ticket still stages. He decides.

What stays HARD
---------------
Not everything. Three things are correctness, not preference, and no amount of
conviction makes them a good idea:

  - **G5 breakers.** The kill switch. If the daily-loss or drawdown limit has
    tripped, the answer is stop trading today, not size down.
  - **G9 fat-finger.** qty <= 0, notional under the floor, a limit price miles
    from the reference. These are typos and bugs, not opinions.
  - **G1 signal validity.** A "trade" with no stop, or a stop above the entry on
    a long, is not a trade. There is nothing to stage.

Everything else - position cap, sector cap, concurrency, bankroll, wash sale,
LLM confidence - becomes a notice.
"""
from __future__ import annotations

# Gates that remain hard blocks no matter what RISK_MODE says.
HARD_GATES = ("G1_signal", "G5_breakers", "G9_fat_finger")

# Ordered worst-first so a caller can take advisories[0] as "the headline".
SEVERITY_ORDER = ("danger", "caution", "info")


def _pct(n, d):
    return (n / d) if d else 0.0


def _money(cents):
    return f"${cents / 100:,.2f}"


# ==========================================================================
# THE TWO CONSTRAINTS
#
# `position % of account = risk% / stop distance %` is not a bug and the two
# rules are not really in tension. They answer DIFFERENT questions and the app
# never said which:
#
#   RISK_PER_TRADE_PCT  ->  "what do I lose if the stop WORKS?"
#   MAX_POSITION_PCT    ->  "what do I lose if the stop DOESN'T?"
#
# A stop only works if there is a price between you and it. Gaps, halts and
# reopens skip straight over. So a 5% stop on a 20% position is 1% of the account
# at risk in the normal case and 6% if it gaps down 30% overnight. That second
# number is what the position cap is actually protecting, and it is invisible if
# you only look at stop risk.
#
# Which means the cap should not be a hand-picked 10%. It should be DERIVED:
#
#   MAX_POSITION_PCT = MAX_GAP_LOSS_PCT / ASSUMED_GAP_PCT
#
# "I will not lose more than 3% of the account to a 30% overnight gap" gives you
# exactly 10%. That is where the number came from; now it is written down and
# the two rules stop contradicting each other.
#
# Everything below is expressed in R-multiples and percentages. At $10 or at $10M
# the analysis is byte-for-byte identical, which is the point.
# ==========================================================================

def derived_position_cap(cfg):
    """The position cap implied by the gap tolerance, or the literal cap."""
    gap = getattr(cfg, "ASSUMED_GAP_PCT", 0.0)
    max_gap_loss = getattr(cfg, "MAX_GAP_LOSS_PCT", 0.0)
    if gap and max_gap_loss:
        return max_gap_loss / gap
    return cfg.MAX_POSITION_PCT


def sizing_math(entry_cents, stop_cents, account_cents, cfg):
    """The full picture behind one position size. Pure, scale-invariant.

    Returns percentages and R-multiples, never a verdict. `binding` names which
    constraint actually decided the size, which is the thing that was never
    surfaced and the reason the cap "kept biting" for no visible reason.
    """
    if not entry_cents or not account_cents or entry_cents <= 0:
        return None
    risk_pct = cfg.RISK_PER_TRADE_PCT
    cap_pct = derived_position_cap(cfg)
    stop_dist = (entry_cents - stop_cents) if stop_cents else 0
    stop_pct = _pct(stop_dist, entry_cents)

    # Size the risk budget wants, as a fraction of the account.
    want_pct = (risk_pct / stop_pct) if stop_pct > 0 else float("inf")
    binding = "risk" if want_pct <= cap_pct else "position_cap"
    final_pct = min(want_pct, cap_pct)

    # When the cap binds you are risking LESS than your stated risk per trade.
    # Nobody ever tells you that either.
    effective_risk_pct = final_pct * stop_pct if stop_pct > 0 else 0.0

    # The stop distance at which the two constraints agree. Wider than this and
    # risk binds; tighter and the cap binds.
    crossover_stop_pct = (risk_pct / cap_pct) if cap_pct else None

    gap = getattr(cfg, "ASSUMED_GAP_PCT", 0.30)
    return {
        "risk_pct": risk_pct,
        "cap_pct": cap_pct,
        "cap_is_derived": bool(getattr(cfg, "ASSUMED_GAP_PCT", 0)
                               and getattr(cfg, "MAX_GAP_LOSS_PCT", 0)),
        "stop_pct": stop_pct,
        "wanted_position_pct": want_pct,
        "final_position_pct": final_pct,
        "binding": binding,
        "effective_risk_pct": effective_risk_pct,
        "r_cents": int(risk_pct * account_cents),
        "crossover_stop_pct": crossover_stop_pct,
        # what the position costs if the stop is skipped entirely
        "gap_loss_pct": final_pct * gap,
        "assumed_gap_pct": gap,
    }


def sizing_explainer(m):
    """One paragraph naming the binding constraint. Scale-free by construction."""
    if not m:
        return None
    if m["stop_pct"] <= 0:
        return None
    stop = f"{m['stop_pct']:.1%}"
    if m["binding"] == "risk":
        return (f"Stop is {stop} away, so risking {m['risk_pct']:.1%} wants "
                f"{m['wanted_position_pct']:.1%} of the account. Under your "
                f"{m['cap_pct']:.1%} position cap, so RISK is what sized this. "
                f"A {m['assumed_gap_pct']:.0%} gap through the stop would cost "
                f"{m['gap_loss_pct']:.1%} of the account.")
    return (f"Stop is only {stop} away, so risking {m['risk_pct']:.1%} would want "
            f"{m['wanted_position_pct']:.1%} of the account. The "
            f"{m['cap_pct']:.1%} position cap binds instead, which means you are "
            f"actually risking {m['effective_risk_pct']:.2%}, not "
            f"{m['risk_pct']:.1%}. The cap is not being fussy: at "
            f"{m['final_position_pct']:.1%} a {m['assumed_gap_pct']:.0%} gap "
            f"costs {m['gap_loss_pct']:.1%} of the account, and a tight stop does "
            f"nothing about gaps. Stops wider than "
            f"{m['crossover_stop_pct']:.1%} are risk-sized instead.")


def position_notice(*, symbol, qty, entry_cents, stop_cents, account_cents):
    """The sentence Dustin asked for, built from the actual numbers.

    Dollars AND percentages, always. The dollars are what he sees in the account;
    the percentages are the part that reads the same at $10 or at $10M, which is
    the analysis he actually wants to carry forward.
    """
    if not qty or not entry_cents or not account_cents:
        return None
    notional = qty * entry_cents
    share_pct = _pct(notional, account_cents)
    bits = [
        f"{_money(account_cents)} account.",
        f"{qty:g} share{'s' if qty != 1 else ''} of {symbol} at "
        f"{_money(entry_cents)} = {_money(notional)}, "
        f"{share_pct:.0%} of the account.",
    ]
    if stop_cents and 0 < stop_cents < entry_cents:
        risked = qty * (entry_cents - stop_cents)
        risk_pct = _pct(risked, account_cents)
        bits.append(f"Your stop at {_money(stop_cents)} risks {_money(risked)} "
                    f"= {risk_pct:.2%} of the account (1R).")
    return " ".join(bits)


def advise(ctx, gates, notional_cents, qty, cfg):
    """Turn failing soft gates into readable notices.

    `gates` is the dict evaluate() already built. This does not re-derive any
    risk math - it explains the math that was already done, which is the only
    way the two can never disagree.

    Returns a list of {code, severity, message}, worst first.
    """
    out = []
    bankroll = ctx.get("bankroll_cents", 0) or 0
    account = ctx.get("current_equity_cents") or bankroll
    entry = ctx.get("entry_cents") or 0
    sym = ctx.get("symbol", "?")

    # The headline sizing sentence. Always present when there is a size, whether
    # or not anything is over a limit - the number is useful either way.
    note = position_notice(symbol=sym, qty=qty, entry_cents=entry,
                           stop_cents=ctx.get("stop_cents"),
                           account_cents=account)
    m = sizing_math(entry, ctx.get("stop_cents"), account, cfg)
    if note:
        share_pct = _pct(qty * entry, account)
        cap = derived_position_cap(cfg)
        over = share_pct > cap
        out.append({
            "code": "sizing",
            "severity": "caution" if over else "info",
            "message": note + (
                f" That is over your {cap:.0%} per-position guideline."
                if over else ""),
        })
        # Name the binding constraint. This is the number that was invisible:
        # "the cap kept biting" had no explanation attached to it anywhere.
        exp = sizing_explainer(m)
        if exp:
            out.append({
                "code": "sizing_math",
                "severity": "info",
                "message": exp,
            })

    if not gates.get("G3_position_cap", True) and notional_cents > 0:
        cap = derived_position_cap(cfg)
        why = (f" (that cap is {getattr(cfg, 'MAX_GAP_LOSS_PCT', 0):.0%} max loss "
               f"to a {getattr(cfg, 'ASSUMED_GAP_PCT', 0):.0%} gap)"
               if m and m.get("cap_is_derived") else "")
        out.append({
            "code": "G3_position_cap",
            "severity": "caution",
            "message": f"Position is {_pct(notional_cents, bankroll):.0%} of the "
                       f"{_money(bankroll)} bankroll, over the {cap:.0%} cap{why}.",
        })

    if not gates.get("G4_sector_cap", True):
        exposure = ctx.get("sector_exposure_cents", 0) + notional_cents
        out.append({
            "code": "G4_sector_cap",
            "severity": "caution",
            "message": f"This puts {_pct(exposure, bankroll):.0%} of the bankroll in "
                       f"'{ctx.get('sector')}', over the {cfg.MAX_SECTOR_PCT:.0%} "
                       f"sector guideline. Concentration cuts both ways.",
        })

    if not gates.get("G6_concurrency", True):
        out.append({
            "code": "G6_concurrency",
            "severity": "info",
            "message": f"You would have {ctx.get('open_positions', 0) + 1} positions "
                       f"open, past your {cfg.MAX_POSITIONS} guideline. More names "
                       f"means less attention per name.",
        })

    if not gates.get("G7_bankroll_cap", True):
        committed = ctx.get("bot_committed_cents", 0)
        out.append({
            "code": "G7_bankroll_cap",
            "severity": "danger",
            "message": f"This exceeds the bot's {_money(bankroll)} bankroll: "
                       f"{_money(committed)} already committed plus "
                       f"{_money(notional_cents)} here. You would be spending "
                       f"money the bot was not walled off to use.",
        })

    if not gates.get("G8_wash_sale", True):
        out.append({
            "code": "G8_wash_sale",
            "severity": "danger",
            "message": f"{sym} sold at a loss inside the last "
                       f"{cfg.WASH_SALE_COOLDOWN_DAYS} days. Buying back now can "
                       f"disallow that loss for taxes.",
        })

    if not gates.get("G2_confidence", True):
        out.append({
            "code": "G2_confidence",
            "severity": "info",
            "message": f"The analysis layer is not confident "
                       f"(confidence={ctx.get('confidence')!r}, "
                       f"critique={ctx.get('critique_verdict')!r}). That is a "
                       f"signal-quality note, not a risk limit.",
        })

    return sorted(out, key=lambda a: SEVERITY_ORDER.index(a["severity"]))


def blocking_gates(gates, cfg):
    """Which failing gates actually stop the ticket.

    In advisory mode (the default) only HARD_GATES block. In strict mode every
    gate blocks, which is the old behavior, kept so it can be turned back on
    without editing code.
    """
    failed = [g for g, ok in gates.items() if not ok]
    if getattr(cfg, "RISK_MODE", "advisory") == "strict":
        return failed
    return [g for g in failed if g in HARD_GATES]


def headline(advisories):
    """One line for a notification or a log. None when there is nothing to say."""
    if not advisories:
        return None
    worst = advisories[0]
    return f"[{worst['severity']}] {worst['message']}"
