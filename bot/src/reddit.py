"""Retail buzz radar: what tickers Reddit's hot pages are pushing right now.

The rationale: reddit is still a fast read on retail attention, catching news and
retail holds early, so fold it into the radar. Pulls hot posts from the
configured subs over www.reddit.com's public Atom RSS (the ONLY reddit surface that serves real
data from this network as of 2026-08: old.reddit and every .json endpoint
return an HTML shield; www hot.rss with a full Chrome UA passes), extracts
ticker mentions ($ABC anywhere; bare 2-5
letter uppercase tokens from TITLES only, minus a noise blocklist), validates
candidates against Alpaca (must have a real latest trade), and ranks by
mention count weighted by hot-page rank (RSS carries no vote counts).

Cached to data/reddit_trending.json for RADAR_REDDIT_CACHE_SECS (default 10
min) so dashboard polls and radar runs never hammer reddit.
"""
from __future__ import annotations

import json
import math
import re
import time
import urllib.request
import xml.etree.ElementTree as ET
from html import unescape

import config

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
CACHE_PATH = config.DATA_DIR / "reddit_trending.json"

# Bare-token noise. $-prefixed mentions bypass this on purpose ($NOW is a claim,
# NOW in a sentence is a word). Some real tickers (ALL, ON, BIG...) are
# sacrificed from bare matching to keep the false-positive rate sane.
BLOCKLIST = {
    "THE", "AND", "FOR", "ARE", "ALL", "NOT", "NEW", "NOW", "GET", "OUT", "DAY",
    "WEEK", "YOLO", "WSB", "DD", "CEO", "CFO", "COO", "FDA", "SEC", "ETF", "IPO",
    "AI", "EPS", "PT", "ATH", "OTM", "ITM", "LOL", "IMO", "TLDR", "EDIT", "USA",
    "USD", "GDP", "CPI", "FED", "FOMC", "ER", "PM", "AH", "EOD", "EOW", "HODL",
    "MOON", "APES", "APE", "GAIN", "LOSS", "PUT", "PUTS", "CALL", "CALLS", "BUY",
    "SELL", "HOLD", "LONG", "SHORT", "BULL", "BEAR", "RED", "GREEN", "UP", "DOWN",
    "BIG", "HUGE", "NEXT", "LAST", "JUST", "LIKE", "THIS", "THAT", "WITH", "FROM",
    "HAVE", "WILL", "WHAT", "WHEN", "YOUR", "THEY", "BEEN", "MORE", "SOME",
    "ONLY", "OVER", "INTO", "THAN", "THEM", "WERE", "ANY", "CAN", "HAS", "HAD",
    "WHO", "WHY", "HOW", "TODAY", "ON", "OFF", "NO", "YES", "IT", "IS", "BE",
    "TO", "OF", "IN", "AT", "MY", "WE", "US", "SO", "DO", "IF", "OR", "AN",
    "AS", "GO", "ME", "AM", "PSA", "IRA", "OTC", "API", "CEO", "IV", "ROI",
    "YTD", "IMHO", "FYI", "PSA", "RIP", "ELI", "AMA", "TOS", "MOASS", "FOMO",
}

_D = re.compile(r"\$([A-Za-z]{1,5})\b")
_B = re.compile(r"\b([A-Z]{2,5})\b")


ATOM = "{http://www.w3.org/2005/Atom}"


def _fetch_sub(sub, limit=40):
    url = f"https://www.reddit.com/r/{sub}/hot.rss?limit={limit}"
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept": "application/atom+xml, text/xml, */*"})
    with urllib.request.urlopen(req, timeout=12) as r:
        raw = r.read()
    root = ET.fromstring(raw)
    posts = []
    entries = root.findall(f"{ATOM}entry")
    for i, e in enumerate(entries):
        title = e.findtext(f"{ATOM}title") or ""
        link = ""
        for ln in e.findall(f"{ATOM}link"):
            link = ln.get("href") or link
        body = unescape(e.findtext(f"{ATOM}content") or "")
        text = re.sub(r"<[^>]+>", " ", body)[:2500]
        posts.append({
            "sub": sub,
            "title": title,
            "selftext": text,
            "score": max(1, len(entries) - i),  # hot-rank proxy (RSS has no votes)
            "rank": i + 1,
            "url": link,
        })
    return posts


def _extract(posts):
    agg = {}
    for p in posts:
        syms = {m.group(1).upper() for m in _D.finditer(p["title"] + " " + p["selftext"])}
        syms |= {m.group(1) for m in _B.finditer(p["title"])} - BLOCKLIST
        syms = {s for s in syms if len(s) >= 2 and s not in BLOCKLIST or (len(s) >= 2 and s in {m.group(1).upper() for m in _D.finditer(p['title'] + ' ' + p['selftext'])})}
        for s in syms:
            a = agg.setdefault(s, {"symbol": s, "mentions": 0, "weight": 0.0, "posts": []})
            a["mentions"] += 1
            a["weight"] += 1.0 + math.log10(max(p["score"], 1))
            a["posts"].append({"sub": p["sub"], "score": p["score"], "rank": p.get("rank"), "title": p["title"][:140], "url": p["url"]})
    for a in agg.values():
        a["posts"].sort(key=lambda x: x["score"], reverse=True)
        a["posts"] = a["posts"][:3]
    return agg


def _load_store():
    try:
        st = json.loads(CACHE_PATH.read_text())
        if isinstance(st.get("subs"), dict):
            return st
    except Exception:
        pass
    return {"subs": {}}


def get_trending_cached(client, cfg=config):
    """Round-robin refresh: each call refreshes AT MOST the stalest sub (ONE
    reddit request) and merges it with the stored posts of the others -
    reddit's shield trips on burst requests, so never fetch several subs
    back-to-back. A rendered-payload cache (120s) absorbs the dashboard's 30s
    polling so neither reddit nor Alpaca gets hammered."""
    store = _load_store()
    now = time.time()
    payload = store.get("payload")
    if payload and now - float(store.get("payload_at", 0)) < 120:
        return payload

    subs = store["subs"]
    for k in list(subs):
        if k not in cfg.RADAR_REDDIT_SUBS:
            del subs[k]
    stalest, age = None, -1.0
    for sub in cfg.RADAR_REDDIT_SUBS:
        a = now - float(subs.get(sub, {}).get("fetched", 0))
        if a > age:
            stalest, age = sub, a
    if stalest and age > cfg.RADAR_REDDIT_CACHE_SECS:
        try:
            subs[stalest] = {"fetched": now, "posts": _fetch_sub(stalest)}
        except Exception:
            rec = subs.setdefault(stalest, {"fetched": 0, "posts": []})
            rec["fetched"] = now - cfg.RADAR_REDDIT_CACHE_SECS + 120  # retry in ~2 min

    posts, subs_ok = [], []
    for sub in cfg.RADAR_REDDIT_SUBS:
        rec = subs.get(sub) or {}
        if rec.get("posts"):
            posts.extend(rec["posts"])
            subs_ok.append(sub)

    agg = _extract(posts)
    ranked = sorted(agg.values(), key=lambda a: a["weight"], reverse=True)[:20]
    trending = []
    for a in ranked:
        try:
            lp = client.get_latest_price(a["symbol"])
            price = lp / 100.0 if lp else None
        except Exception:
            continue  # not a real tradeable symbol -> drop
        trending.append({
            "symbol": a["symbol"], "mentions": a["mentions"],
            "weight": round(a["weight"], 1), "price": price,
            "top_post": a["posts"][0] if a["posts"] else None,
            "posts": a["posts"],
        })
        if len(trending) >= 12:
            break

    payload = {"generated": now, "subs": subs_ok, "trending": trending}
    store["payload"], store["payload_at"] = payload, now
    try:
        CACHE_PATH.write_text(json.dumps(store))
    except Exception:
        pass
    return payload


def mention_map(client, cfg=config):
    """{SYM: {mentions, weight, posts[:2]}} for the radar's buzz lookups."""
    data = get_trending_cached(client, cfg)
    return {t["symbol"]: t for t in data.get("trending", [])}
