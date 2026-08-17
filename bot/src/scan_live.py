#!/usr/bin/env python3
"""LIVE LANE scanner - the catalyst radar, rebuilt on scanner_core.

This is `radar.py`'s scan, with every lane-agnostic mechanic moved out to
`scanner_core` and only the LONG-LANE GATES left behind. Behaviour is unchanged and
`test_scan_live_parity.py` proves it against the original on identical input.

WHAT LIVES HERE (lane-specific, belongs to the swing brain):
  - the movers feed and its thresholds (min move %, price floor)
  - the authoritative move-math re-check
  - the dollar-volume floor, feed-scaled
  - the LLM catalyst curation and the discipline note
  - auto-execute, which only this lane has

WHAT DOES NOT (shared, in scanner_core):
  - the per-lane scan lock, the scan log, candidate dedupe
  - the leveraged-wrapper test, filer-first ranking, discord

Awareness only. It never places an order except through the explicit auto-execute
path, which is off by default and gated on the same breaker the dashboard shows.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone

import config
import db
import scanner_core as sc
from llm import classify

LANE = sc.Lane("live", legacy_scanlog="scan-log.json")


def discipline_note(price, cfg=config):
    trail = price * (1 - cfg.RADAR_TRAIL_PCT)
    return (f"If you play it: size tiny, scale some out into strength, trail a stop "
            f"~{cfg.RADAR_TRAIL_PCT:.0%} (~${trail:,.2f}). Don't round-trip a winner. "
            f"IPOs / low-float names are the highest-risk version.")


def fresh_news(news, cfg=config):
    """Drop headlines too old to be today's catalyst. Returns (kept, dropped_n).

    The scorer is asked "does this news explain this move", and it answers yes to
    a perfectly good story from four years ago. On 2026-08-17 SIC came back 85 and
    "signal" on Sun Capital's OCTOBER 2021 $14.50 buyout - a deal that closed, on a
    ticker that has not traded since, printing at $14.49 because that IS the deal
    price. A stale headline does not read as stale, it reads as a catalyst.

    An item with NO parseable timestamp is KEPT. Unknown age is not evidence of
    staleness, and dropping it would silently blind the scorer on any feed whose
    date field is shaped differently.
    """
    days = int(getattr(cfg, "RADAR_MAX_HEADLINE_AGE_DAYS", 0) or 0)
    if days <= 0 or not news:
        return list(news or []), 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    kept, dropped = [], 0
    for n in news:
        raw = str(n.get("created_at") or n.get("at") or n.get("updated_at") or "")
        when = None
        if raw:
            try:
                when = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                if when.tzinfo is None:
                    when = when.replace(tzinfo=timezone.utc)
            except Exception:
                when = None
        if when is not None and when < cutoff:
            dropped += 1
            continue
        kept.append(n)
    return kept, dropped


def feed_session_is_stale(client):
    """Is the movers feed serving a session that has already CLOSED?

    Returns (stale, session_date). The screener endpoint always returns the CURRENT
    session's movers - and before the bell "current" means the last completed one.
    Pre-market, on weekends and on holidays every candidate's latest trade IS that
    session's closing print, so the move math measures a close against itself, reads
    0.0%, and the whole board gets skipped as "the screener number was stale". On
    2026-08-17 that killed 18 of 20 candidates and the scan reported a quiet tape.

    After the close is NOT stale: the feed is that day's real session and the math
    works. So the test is "market shut AND today has no daily bar yet", not "shut".

    Fails OPEN - a clock hiccup must never be able to silence a scan.
    """
    try:
        if (client.get_clock() or {}).get("is_open"):
            return False, ""
        bars = client.get_daily_bars("SPY", limit=1) or []
        last = str(bars[-1].get("t", ""))[:10] if bars else ""
    except Exception:
        return False, ""
    if last and last != datetime.now().strftime("%Y-%m-%d"):
        return True, last
    return False, ""


def verify_pct(client, sym, price, screener_pct, cfg=config):
    """Authoritative move math. The screener's percent_change AND price are UNTRUSTED:
    par-priced SPAC/warrant math, unadjusted reverse splits and stale after-hours
    prints have all produced fake triple-digit movers (AMIX read +466% while the live
    quote was down on the day).

    Returns (pct, price, is_ipo, verified). On a data gap the screener numbers pass
    through with verified=False so the alert can SAY it is unverified; fewer than 2
    real daily bars is a genuine IPO, flagged rather than corrected.

    OUR number can be the stale one. When the latest trade is not NEWER than the last
    completed session, `cur` and `prev` are the same closing print and the answer is
    0.0% - not a verified flat tape, an unanswerable question. That case now returns
    verified=False instead of a confident zero.
    """
    cur, cur_day = price, ""
    try:
        getter = getattr(client, "get_latest_trade", None)
        if getter:
            t = getter(sym) or {}
            if t.get("price_cents"):
                cur = t["price_cents"] / 100.0
                cur_day = str(t.get("at", ""))[:10]
        else:
            lp = client.get_latest_price(sym)
            if lp:
                cur = lp / 100.0
    except Exception:
        pass
    try:
        bars = client.get_daily_bars(sym, limit=5)
        rows = [(str(b.get("t", ""))[:10], b["c"]) for b in bars if b.get("c")]
        if len(rows) < 2:
            return screener_pct, cur, True, False
        today = datetime.now().strftime("%Y-%m-%d")
        last_day = rows[-1][0]
        if last_day == today:
            prev_cents = rows[-2][1]
        else:
            prev_cents = rows[-1][1]
            if cur_day and cur_day <= last_day:
                return screener_pct, cur, False, False
        prev = prev_cents / 100.0
        if prev > 0 and cur > 0:
            return round((cur - prev) / prev * 100.0, 2), cur, False, True
        return screener_pct, cur, False, False
    except Exception:
        return screener_pct, cur, False, False


def scan(client, conn, cfg=config, autotrade=None):
    """One pass of the live lane. Returns the alert list, ranked."""
    stale, session = feed_session_is_stale(client)
    if stale and not getattr(cfg, "RADAR_SCAN_CLOSED_MARKET", False):
        msg = (f"movers feed is still {session}'s completed session - the bell has not "
               f"rung, so every 'mover' is that session's close measured against "
               f"itself. Nothing to scan until the open.")
        print(f"[live] {msg}")
        log = sc.ScanLog(LANE)
        log.skip("(feed)", msg)
        log.write()
        return []

    try:
        movers = client.get_movers(top=cfg.RADAR_TOP_N)
    except Exception as e:
        print(f"live: movers unavailable ({str(e)[:120]})")
        return []

    held, ks_ok = set(), False
    if cfg.RADAR_AUTO_EXECUTE and autotrade:
        try:
            held = {p.get("symbol") for p in client.list_positions()}
        except Exception:
            held = set()
        ks_ok = autotrade.killswitch_ok(client, conn)

    buzz = {}
    if cfg.RADAR_REDDIT_ENABLED:
        try:
            import reddit
            buzz = reddit.mention_map(client, cfg)
        except Exception as e:
            print(f"live: reddit buzz unavailable ({str(e)[:80]})")

    log = sc.ScanLog(LANE)
    alerts = []

    gainers, dropped = sc.dedupe_candidates(movers.get("gainers", []))
    if dropped:
        print(f"[live] movers feed carried {dropped} duplicate symbol(s); deduped")

    for g in gainers:
        sym = g.get("symbol")
        pct = float(g.get("percent_change", 0) or 0)
        price = float(g.get("price", 0) or 0)
        if not sym:
            continue
        if pct < cfg.RADAR_MIN_MOVE_PCT:
            log.skip(sym, f"move {pct:.1f}% under the {cfg.RADAR_MIN_MOVE_PCT:.0f}% bar",
                     pct=pct)
            continue
        if price * 100 < cfg.RADAR_MIN_PRICE_CENTS:
            log.skip(sym, f"${price:.2f} under the "
                          f"${cfg.RADAR_MIN_PRICE_CENTS/100:.2f} price floor",
                     pct=pct, price=price)
            continue
        if db.alert_exists_today(conn, sym, "gainer"):
            log.skip(sym, "already alerted today", pct=pct, price=price)
            continue

        if getattr(cfg, "RADAR_SKIP_LEVERAGED", True):
            nm = (client.get_asset(sym) or {}).get("name", "")
            if sc.is_leveraged_wrapper(nm):
                log.skip(sym, f"leveraged wrapper: {nm[:60].upper()}",
                         pct=pct, price=price)
                continue

        pct, price, is_ipo, verified = verify_pct(client, sym, price, pct, cfg)
        if verified and pct < cfg.RADAR_MIN_MOVE_PCT:
            log.skip(sym, f"verified move is only {pct:.1f}% - the screener number "
                          f"was stale or split-skewed", pct=pct, price=price)
            continue

        # ONE bar fetch, feeding two gates. It used to live inside `if floor:`,
        # which meant the history question was only ever asked when a liquidity
        # floor happened to be set.
        try:
            bars = client.get_daily_bars(sym, limit=25)
        except Exception:
            bars = None

        # NO history at all. Not an IPO - an instrument this lane cannot evaluate.
        # This is also what closed the hole below: with no bars there is no dollar
        # volume, so `adv` came back None and the floor let the name through.
        if not bars and getattr(cfg, "RADAR_REQUIRE_BARS", True):
            log.skip(sym, "no daily bar history - no ATR, no stop reference and no "
                          "dollar volume, so the liquidity floor cannot judge it "
                          "(dead ticker, warrant or untradable class)",
                     pct=pct, price=price)
            continue

        floor = getattr(cfg, "RADAR_MIN_DOLLAR_VOLUME", 0)
        adv = None
        if floor:
            try:
                import signals
                adv = signals.avg_dollar_volume(bars) if bars else None
            except Exception:
                adv = None
            if adv is not None and adv < floor:
                log.skip(sym, f"${adv/1e6:.2f}M avg daily dollar volume, under the "
                              f"${floor/1e6:.0f}M floor", pct=pct, price=price,
                         dollar_volume=round(adv))
                continue

        # CLAIM BEFORE THE LLM CALL. The scan lock makes overlap impossible; this
        # makes it harmless if it happens anyway (a cron and a manual run racing).
        claimed = {"ok": False}

        def _claim():
            db.record_alert(conn, symbol=sym, kind="gainer", pct=pct,
                            price_cents=int(price * 100), headline="", url="",
                            note="scoring...", score=None, verdict="pending",
                            catalyst_type="", why="")
            claimed["ok"] = True

        def _score():
            headlines, url, stale = [], "", 0
            try:
                news = client.get_news(symbols=[sym], limit=4)
                news, stale = fresh_news(news, cfg)
                headlines = [n.get("headline", "") for n in news if n.get("headline")]
                if news:
                    url = news[0].get("url", "") or ""
            except Exception:
                pass
            rb = buzz.get(sym)
            if rb:
                for bp in rb.get("posts", [])[:2]:
                    headlines.append(f"[Reddit r/{bp['sub']} {bp['score']}pts] {bp['title']}")
            return classify(sym, pct, price, headlines, cfg), headlines, url, rb, stale

        def _finalize(res):
            verdict, headlines, url, rb, stale = res
            score = verdict["score"] if verdict else None
            vlabel = verdict["verdict"] if verdict else ""
            ctype = verdict["catalyst_type"] if verdict else ""
            why = verdict["why"] if verdict else ""
            headline = headlines[0] if headlines else ""

            note = discipline_note(price, cfg)
            if is_ipo:
                note = ("IPO / first sessions: the % is vs the first available close, "
                        "not a normal prior day. Treat the number as unreliable. ") + note
            elif not verified:
                note = ("Move math UNVERIFIED (bars/latest unavailable): % and price are "
                        "screener-reported and may be stale or split-skewed. ") + note
            if stale:
                note = (f"{stale} headline(s) withheld from the scorer as older than "
                        f"{cfg.RADAR_MAX_HEADLINE_AGE_DAYS} days. If the verdict below "
                        f"reads thin, that is why - there is no fresh news explaining "
                        f"this move. ") + note
            if rb:
                note = f"Reddit buzz: {rb['mentions']} mention(s) in hot posts right now. " + note

            if ks_ok and autotrade:
                autotrade.maybe(client, conn, sym, price, score, vlabel, held, cfg,
                                verified=verified, is_ipo=is_ipo)

            db.update_alert_scoring(conn, symbol=sym, kind="gainer", headline=headline,
                                    url=url, note=note, score=score, verdict=vlabel,
                                    catalyst_type=ctype, why=why, pct=pct,
                                    price_cents=int(price * 100))
            log.alert(sym, why or ctype or "scored", pct=pct, price=price, score=score,
                      verdict=vlabel, catalyst_type=ctype,
                      dollar_volume=round(adv) if adv else None)

            supply = {}
            if getattr(cfg, "ANNOTATE_SUPPLY", True):
                try:
                    import fundamentals
                    supply = fundamentals.annotate(sym)
                except Exception as e:
                    supply = {"supply_class": "unknown",
                              "note": f"supply lookup failed: {str(e)[:60]}"}

            row = {"symbol": sym, "pct": pct, "price": price, "score": score,
                   "verdict": vlabel, "catalyst_type": ctype, "why": why,
                   "shares_outstanding": supply.get("shares_outstanding"),
                   "shares_millions": supply.get("shares_millions"),
                   "supply_class": supply.get("supply_class", "unknown"),
                   "shares_as_of": supply.get("shares_as_of")}
            alerts.append(row)

            tag = f" [{score} {vlabel}]" if score is not None else ""
            print(f"[LIVE] {sym} +{pct:.1f}% @ ${price:,.2f}{tag}"
                  + (f"  {why or headline}" if (why or headline) else ""))

            push = (score is None) or (vlabel == "signal" and score >= cfg.RADAR_LLM_MIN_SCORE)
            if cfg.RADAR_DISCORD_WEBHOOK and push:
                msg = f"**{sym}** +{pct:.1f}% @ ${price:,.2f}"
                if score is not None:
                    msg += f"  (score {score} - {ctype or vlabel})"
                if why:
                    msg += f"\n{why}"
                msg += f"\n{note}"
                if url:
                    msg += f"\n{url}"
                sc.post_discord(cfg.RADAR_DISCORD_WEBHOOK, msg)
            return row

        sc.claim_then_score(_claim, _score, _finalize,
                            on_claim_fail=lambda e: log.skip(sym, f"could not claim: {str(e)[:70]}"))

    ranked = sc.rank_companies_first(alerts)
    log.write()
    return ranked


def main():
    argparse.ArgumentParser(description="Live-lane catalyst scan (awareness only).").parse_args()
    conn = db.connect()
    db.init_db(conn)
    from alpaca_client import AlpacaClient
    client = AlpacaClient()
    print(config.env_banner())
    print(config.feed_banner())
    try:
        with sc.ScanLock(LANE):
            import autotrade as at
            alerts = scan(client, conn, autotrade=at if config.RADAR_AUTO_EXECUTE else None)
    except sc.ScanBusy as e:
        # Exit 0: a refused duplicate is correct behaviour, not a failure.
        print(f"live: {e}")
        return
    except ImportError:
        with sc.ScanLock(LANE):
            alerts = scan(client, conn)
    print(f"live: {len(alerts)} new alert(s) (min move {config.RADAR_MIN_MOVE_PCT:.0f}%).")


if __name__ == "__main__":
    main()
