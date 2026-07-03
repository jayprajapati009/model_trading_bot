"""
Stock universe management.

Fetches the Nifty 500 constituent list from NSE (cached for 7 days).
Falls back to a built-in list of ~100 liquid large/mid caps if NSE is
unreachable.
"""

import csv
import io
import json
import logging
import os
import time

import requests

import config

logger = logging.getLogger(__name__)

_CACHE_TTL = 7 * 24 * 3600   # refresh weekly

# Fallback: liquid Nifty-100-ish names (used only if the NSE fetch fails)
_FALLBACK = [
    "RELIANCE", "TCS", "HDFCBANK", "ICICIBANK", "INFY", "BHARTIARTL", "SBIN",
    "LICI", "ITC", "HINDUNILVR", "LT", "BAJFINANCE", "HCLTECH", "MARUTI",
    "SUNPHARMA", "ADANIENT", "KOTAKBANK", "TITAN", "ONGC", "TATAMOTORS",
    "NTPC", "AXISBANK", "DMART", "ADANIGREEN", "ADANIPORTS", "ULTRACEMCO",
    "ASIANPAINT", "COALINDIA", "BAJAJFINSV", "POWERGRID", "M&M", "TATASTEEL",
    "WIPRO", "IOC", "JIOFIN", "DLF", "JSWSTEEL", "HDFCLIFE", "SIEMENS",
    "VBL", "ZOMATO", "PIDILITIND", "GRASIM", "SBILIFE", "BEL", "LTIM",
    "TRENT", "PNB", "INDUSINDBK", "BANKBARODA", "HINDALCO", "HAL", "TECHM",
    "AMBUJACEM", "INDIGO", "CIPLA", "GAIL", "BAJAJ-AUTO", "TATAPOWER",
    "MARICO", "EICHERMOT", "BPCL", "BRITANNIA", "GODREJCP", "DIVISLAB",
    "DRREDDY", "HAVELLS", "SHRIRAMFIN", "APOLLOHOSP", "CHOLAFIN", "DABUR",
    "TORNTPHARM", "MANKIND", "HEROMOTOCO", "SHREECEM", "IDBI", "BOSCHLTD",
    "JINDALSTEL", "CANBK", "NESTLEIND", "VEDL", "ZYDUSLIFE", "UNIONBANK",
    "LODHA", "NAUKRI", "PFC", "ICICIPRULI", "TVSMOTOR", "BERGEPAINT",
    "POLYCAB", "MUTHOOTFIN", "SRF", "SAIL", "MOTHERSON", "IRCTC", "PAGEIND",
    "ABB", "CGPOWER", "TIINDIA", "COLPAL", "PERSISTENT",
]


def get_nifty500() -> list[str]:
    """Return Nifty 500 symbols with .NS suffix (cached weekly)."""
    # Serve from cache if fresh
    if os.path.exists(config.UNIVERSE_CACHE_FILE):
        try:
            with open(config.UNIVERSE_CACHE_FILE) as f:
                cached = json.load(f)
            if time.time() - cached.get("fetched_at", 0) < _CACHE_TTL:
                return cached["symbols"]
        except Exception:
            pass

    symbols = _fetch_from_nse()
    if not symbols:
        logger.warning("NSE fetch failed – using fallback universe (%d symbols)",
                       len(_FALLBACK))
        symbols = [f"{s}.NS" for s in _FALLBACK]

    os.makedirs(os.path.dirname(config.UNIVERSE_CACHE_FILE), exist_ok=True)
    with open(config.UNIVERSE_CACHE_FILE, "w") as f:
        json.dump({"fetched_at": time.time(), "symbols": symbols}, f)
    return symbols


def _fetch_from_nse() -> list[str]:
    try:
        resp = requests.get(
            config.NIFTY500_CSV_URL,
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=20,
        )
        resp.raise_for_status()
        reader = csv.DictReader(io.StringIO(resp.text))
        symbols = [f"{row['Symbol'].strip()}.NS" for row in reader
                   if row.get("Symbol")]
        if len(symbols) > 100:
            logger.info("Fetched %d Nifty 500 symbols from NSE", len(symbols))
            return symbols
    except Exception as exc:
        logger.warning("Nifty 500 fetch failed: %s", exc)
    return []
