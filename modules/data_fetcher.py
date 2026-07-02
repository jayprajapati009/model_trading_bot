"""Yahoo Finance data fetching with retry logic and a simple intraday cache."""

import time
import logging
from datetime import datetime, date

import pytz
import pandas as pd
import yfinance as yf

import config

logger = logging.getLogger(__name__)

_cache: dict[str, tuple[float, pd.DataFrame]] = {}
_CACHE_TTL = 300  # seconds


def _evict_cache() -> None:
    """Drop expired entries so DataFrames don't accumulate in memory indefinitely."""
    cutoff = time.time() - _CACHE_TTL
    expired = [k for k, (ts, _) in _cache.items() if ts < cutoff]
    for k in expired:
        del _cache[k]


def _cached_download(symbol: str, period: str, interval: str) -> pd.DataFrame:
    key = f"{symbol}|{period}|{interval}"
    ts, df = _cache.get(key, (0, pd.DataFrame()))
    if time.time() - ts < _CACHE_TTL and not df.empty:
        return df
    _evict_cache()  # prune stale entries before adding a new one
    df = _download_with_retry(symbol, period, interval)
    _cache[key] = (time.time(), df)
    return df


def _download_with_retry(symbol: str, period: str, interval: str,
                          retries: int = 3) -> pd.DataFrame:
    for attempt in range(retries):
        try:
            df = yf.download(symbol, period=period, interval=interval,
                             progress=False, auto_adjust=True)
            if not df.empty:
                # yfinance >= 0.2.x returns MultiIndex columns when group_by='column'
                if isinstance(df.columns, pd.MultiIndex):
                    df = df.xs(symbol, axis=1, level=1)
                df.columns = [c.lower() for c in df.columns]
                df = df.dropna(subset=["close"])
                return df
        except Exception as exc:
            logger.warning("Download attempt %d for %s failed: %s",
                           attempt + 1, symbol, exc)
            time.sleep(2 ** attempt)
    logger.error("All download attempts failed for %s", symbol)
    return pd.DataFrame()


def get_historical_data(symbol: str, days: int = 90) -> pd.DataFrame:
    """Return daily OHLCV for the past `days` days."""
    period = f"{days}d"
    return _cached_download(symbol, period, "1d")


def get_intraday_data(symbol: str) -> pd.DataFrame:
    """Return 5-minute candles for today (market hours)."""
    return _cached_download(symbol, "1d", "5m")


def get_current_price(symbol: str) -> float | None:
    """Return the latest close/price for a symbol."""
    try:
        ticker = yf.Ticker(symbol)
        info = ticker.fast_info
        price = getattr(info, "last_price", None) or getattr(info, "previous_close", None)
        if price:
            return float(price)
        # Fallback: last row of 1-day 1m data
        df = _download_with_retry(symbol, "1d", "1m")
        if not df.empty:
            return float(df["close"].iloc[-1])
    except Exception as exc:
        logger.warning("Could not fetch price for %s: %s", symbol, exc)
    return None


def get_multiple_prices(symbols: list[str]) -> dict[str, float]:
    """Bulk-fetch latest prices for a list of symbols."""
    prices: dict[str, float] = {}
    for sym in symbols:
        p = get_current_price(sym)
        if p is not None:
            prices[sym] = p
    return prices


def is_market_open() -> bool:
    """Return True if the NSE is currently open for trading."""
    tz = pytz.timezone(config.TIMEZONE)
    now = datetime.now(tz)
    if now.weekday() >= 5:  # Saturday / Sunday
        return False
    open_time  = now.replace(hour=config.MARKET_OPEN[0],  minute=config.MARKET_OPEN[1],  second=0, microsecond=0)
    close_time = now.replace(hour=config.MARKET_CLOSE[0], minute=config.MARKET_CLOSE[1], second=0, microsecond=0)
    return open_time <= now <= close_time


def get_nifty_trend() -> str:
    """
    Return 'bullish', 'bearish', or 'neutral' based on Nifty50 vs its 50-EMA.
    Used as a broad market filter.
    """
    df = get_historical_data(config.NIFTY_SYMBOL, days=90)
    if df.empty or len(df) < 50:
        return "neutral"
    ema50 = df["close"].ewm(span=50, adjust=False).mean()
    last_close = df["close"].iloc[-1]
    last_ema   = ema50.iloc[-1]
    if last_close > last_ema * 1.005:
        return "bullish"
    if last_close < last_ema * 0.995:
        return "bearish"
    return "neutral"
