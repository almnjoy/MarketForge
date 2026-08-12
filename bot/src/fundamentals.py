"""Share count from SEC EDGAR. No API key, no new dependency, no scraping.

Why this exists
---------------
Ross Cameron's framing, and it is a good one: a catalyst is DEMAND, and the share
count is SUPPLY. The same headline on a 12 million share company and on a 2
billion share company are not the same event. The radar scores catalysts and
knows nothing about supply, so it cannot tell those two apart.

READ THIS BEFORE YOU TRUST THE NUMBER
-------------------------------------
**This is SHARES OUTSTANDING, not FLOAT.** They are different and the difference
is exactly where low-float trading lives:

    float = shares outstanding - insider/restricted/closely-held shares

For a company where insiders hold 40%, the tradeable float is far smaller than
this number, and the "low float squeeze" everyone talks about is a float
phenomenon, not an outstanding-shares phenomenon. Real float needs a paid
provider. Calling this "float" would be the kind of quiet inaccuracy that gets
someone hurt, so every name in this module says `shares_outstanding`.

What it IS good for: order of magnitude. It cleanly separates a 10M-share
microcap from a 2B-share megacap, which is most of the value, and it is free and
authoritative (it is the company's own filing).

Source: SEC EDGAR
  - ticker -> CIK   https://www.sec.gov/files/company_tickers.json
  - shares          https://data.sec.gov/api/xbrl/companyconcept/CIK##########/
                    dei/EntityCommonStockSharesOutstanding.json

SEC requires a descriptive User-Agent with contact info and rate-limits to 10
requests/second. Both are honoured below. Everything is cached to disk because
share counts change quarterly at most, and a scan must never be gated on a
network call to a government website.
"""
from __future__ import annotations

import json
import time

import requests

import config

CIK_MAP_PATH = config.DATA_DIR / "sec-cik-map.json"
SHARES_PATH = config.DATA_DIR / "sec-shares.json"

# SEC asks for a real contact string. Overridable so someone else's build is not
# making requests under Dustin's name.
UA = (getattr(config, "SEC_USER_AGENT", None)
      or "MarketForge research (duallema@outlook.com)")

CIK_TTL = 30 * 86400        # ticker->CIK map: new listings are rare
SHARES_TTL = 30 * 86400     # share counts move quarterly at most
_MIN_GAP = 0.12             # ~8 req/s, under the SEC's 10/s limit
_last_call = [0.0]


def _get(url):
    gap = _MIN_GAP - (time.time() - _last_call[0])
    if gap > 0:
        time.sleep(gap)
    _last_call[0] = time.time()
    r = requests.get(url, headers={"User-Agent": UA,
                                   "Accept-Encoding": "gzip, deflate"},
                     timeout=15)
    r.raise_for_status()
    return r.json()


def _load(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(path, obj):
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(obj), encoding="utf-8")
    except Exception as e:
        print(f"[fundamentals] cache write failed: {e}")


def cik_for(symbol):
    """CIK (zero-padded to 10) for a ticker, or None. Cached ~30 days."""
    symbol = symbol.upper().strip()
    cache = _load(CIK_MAP_PATH)
    if cache.get("_fetched", 0) + CIK_TTL > time.time() and cache.get("map"):
        return cache["map"].get(symbol)
    try:
        raw = _get("https://www.sec.gov/files/company_tickers.json")
    except Exception as e:
        print(f"[fundamentals] CIK map fetch failed: {str(e)[:120]}")
        return (cache.get("map") or {}).get(symbol)   # stale beats nothing
    m = {v["ticker"].upper(): str(v["cik_str"]).zfill(10) for v in raw.values()}
    _save(CIK_MAP_PATH, {"_fetched": time.time(), "map": m})
    return m.get(symbol)


def shares_outstanding(symbol, max_age_s=SHARES_TTL):
    """{shares, as_of, source} or None. Never raises, never blocks a scan.

    Returns the most recent EntityCommonStockSharesOutstanding the company has
    filed. `as_of` is the filing's own date, not fetch time, because a share
    count from a filing 8 months ago is a different thing from a fresh one and
    the caller deserves to know which it got.
    """
    symbol = symbol.upper().strip()
    cache = _load(SHARES_PATH)
    hit = cache.get(symbol)
    if hit and hit.get("_fetched", 0) + max_age_s > time.time():
        return hit if hit.get("shares") else None

    cik = cik_for(symbol)
    if not cik:
        return None
    try:
        d = _get(f"https://data.sec.gov/api/xbrl/companyconcept/CIK{cik}/dei/"
                 f"EntityCommonStockSharesOutstanding.json")
    except Exception as e:
        print(f"[fundamentals] {symbol}: {str(e)[:100]}")
        return hit if (hit and hit.get("shares")) else None

    best = None
    for unit_rows in (d.get("units") or {}).values():
        for row in unit_rows:
            end = row.get("end") or ""
            val = row.get("val")
            if val and (best is None or end > best["as_of"]):
                best = {"shares": int(val), "as_of": end,
                        "form": row.get("form"), "source": "SEC EDGAR"}
    rec = best or {"shares": None, "as_of": None, "source": "SEC EDGAR"}
    rec["_fetched"] = time.time()
    cache[symbol] = rec
    _save(SHARES_PATH, cache)
    return rec if rec.get("shares") else None


def supply_class(shares):
    """Bucket a share count. Deliberately coarse - the input is an approximation
    of float, so a precise-looking threshold would be false precision."""
    if not shares:
        return "unknown"
    m = shares / 1e6
    if m < 20:
        return "micro"      # a real catalyst here moves price violently
    if m < 75:
        return "small"
    if m < 300:
        return "mid"
    if m < 2000:
        return "large"
    return "mega"           # a headline is a rounding error


def annotate(symbol):
    """Everything the scan wants about supply, in one call. Always a dict."""
    rec = shares_outstanding(symbol)
    if not rec:
        return {"shares_outstanding": None, "supply_class": "unknown",
                "shares_as_of": None,
                "note": "no SEC share count (foreign issuer, ETF, or not filed)"}
    return {
        "shares_outstanding": rec["shares"],
        "shares_millions": round(rec["shares"] / 1e6, 1),
        "supply_class": supply_class(rec["shares"]),
        "shares_as_of": rec.get("as_of"),
        "note": "SEC shares OUTSTANDING, not free float",
    }


if __name__ == "__main__":
    import sys
    for s in (sys.argv[1:] or ["RPD", "HQI", "NVDA", "AAPL", "FF"]):
        a = annotate(s)
        n = a.get("shares_millions")
        print(f"{s:<6} {str(n) + 'M' if n else '--':>10}  {a['supply_class']:<8}"
              f"  as of {a.get('shares_as_of')}")
