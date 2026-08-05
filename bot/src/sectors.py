"""Static symbol -> sector map for the sector-concentration gate (G4).

Alpaca's asset endpoint does not return GICS sectors, so we keep a small local
map for the default universe. Extend it as you add names; unknown symbols fall
into the 'unknown' bucket (which the sector cap still limits collectively).
"""
from __future__ import annotations

SECTOR_MAP = {
    "AAPL": "technology", "MSFT": "technology", "NVDA": "technology",
    "AVGO": "technology", "GOOGL": "communication", "META": "communication",
    "AMZN": "consumer_disc", "HD": "consumer_disc", "COST": "consumer_staples",
    "WMT": "consumer_staples", "PG": "consumer_staples", "KO": "consumer_staples",
    "PEP": "consumer_staples", "JPM": "financials", "V": "financials",
    "MA": "financials", "XOM": "energy", "CAT": "industrials",
    "UNH": "healthcare", "LLY": "healthcare",
}


def sector_for(symbol: str) -> str:
    return SECTOR_MAP.get(symbol.upper(), "unknown")
