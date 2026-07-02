"""
Technical analysis: EMAs, RSI, ATR, support/resistance, candlestick patterns.
All functions accept a pandas DataFrame with columns: open, high, low, close, volume.
"""

import numpy as np
import pandas as pd
import logging

import config

logger = logging.getLogger(__name__)


# ── Core indicators ───────────────────────────────────────────────────────────

def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain  = delta.clip(lower=0)
    loss  = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=period - 1, adjust=False).mean()
    avg_loss = loss.ewm(com=period - 1, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, prev_close = df["high"], df["low"], df["close"].shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low  - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(com=period - 1, adjust=False).mean()


def volume_ratio(df: pd.DataFrame, period: int = 20) -> pd.Series:
    """Current volume as a ratio of the N-period average volume."""
    avg = df["volume"].rolling(period).mean()
    return df["volume"] / avg.replace(0, np.nan)


# ── Support / Resistance ──────────────────────────────────────────────────────

def find_support_resistance(df: pd.DataFrame,
                             lookback: int = None) -> tuple[float, float]:
    """
    Return (support, resistance) using swing highs/lows over the last
    `lookback` candles.
    """
    lookback = lookback or config.SUPPORT_LOOKBACK
    window = df.tail(lookback)
    support    = float(window["low"].min())
    resistance = float(window["high"].max())
    return support, resistance


def near_support(price: float, support: float, tolerance: float = 0.015) -> bool:
    """True if price is within `tolerance` (default 1.5%) above support."""
    return support * (1 - tolerance) <= price <= support * (1 + tolerance)


def near_resistance(price: float, resistance: float,
                    tolerance: float = 0.015) -> bool:
    return resistance * (1 - tolerance) <= price <= resistance * (1 + tolerance)


# ── Candlestick patterns ──────────────────────────────────────────────────────

def _body(row: pd.Series) -> float:
    return abs(row["close"] - row["open"])


def _upper_wick(row: pd.Series) -> float:
    return row["high"] - max(row["open"], row["close"])


def _lower_wick(row: pd.Series) -> float:
    return min(row["open"], row["close"]) - row["low"]


def _candle_range(row: pd.Series) -> float:
    return row["high"] - row["low"]


def detect_patterns(df: pd.DataFrame) -> list[str]:
    """
    Detect candlestick patterns in the last 3 candles.
    Returns a list of pattern names found.
    """
    if len(df) < 3:
        return []

    patterns: list[str] = []
    c0 = df.iloc[-1]   # latest candle
    c1 = df.iloc[-2]   # previous candle
    c2 = df.iloc[-3]

    r0 = _candle_range(c0)
    if r0 == 0:
        return patterns

    b0 = _body(c0)
    lw0 = _lower_wick(c0)
    uw0 = _upper_wick(c0)

    bullish0 = c0["close"] > c0["open"]
    bearish0 = c0["close"] < c0["open"]
    bullish1 = c1["close"] > c1["open"]
    bearish1 = c1["close"] < c1["open"]

    # Doji
    if b0 <= r0 * 0.1:
        patterns.append("doji")

    # Hammer (bullish reversal) – small body at top, long lower wick
    if (lw0 >= b0 * 2) and (uw0 <= b0 * 0.5) and (b0 >= r0 * 0.1):
        patterns.append("hammer")

    # Inverted Hammer
    if (uw0 >= b0 * 2) and (lw0 <= b0 * 0.5) and (b0 >= r0 * 0.1):
        patterns.append("inverted_hammer")

    # Shooting Star (bearish) – inverted hammer after uptrend
    if bearish0 and (uw0 >= b0 * 2) and (lw0 <= b0 * 0.5):
        patterns.append("shooting_star")

    # Bullish Engulfing
    if (bullish0 and bearish1 and
            c0["open"] < c1["close"] and c0["close"] > c1["open"]):
        patterns.append("bullish_engulfing")

    # Bearish Engulfing
    if (bearish0 and bullish1 and
            c0["open"] > c1["close"] and c0["close"] < c1["open"]):
        patterns.append("bearish_engulfing")

    # Bullish Marubozu (strong bull candle, small wicks)
    if bullish0 and b0 >= r0 * 0.8:
        patterns.append("bullish_marubozu")

    # Morning Star (3-candle bullish reversal)
    if (bearish1 and _body(c1) >= _candle_range(c1) * 0.5 and
            _body(c0) <= _candle_range(c0) * 0.3 and  # middle is small
            bullish0):
        patterns.append("morning_star")

    # Evening Star (3-candle bearish reversal)
    if (bullish1 and _body(c1) >= _candle_range(c1) * 0.5 and
            _body(c0) <= _candle_range(c0) * 0.3 and
            bearish0):
        patterns.append("evening_star")

    return patterns


BULLISH_PATTERNS = {
    "hammer", "inverted_hammer", "bullish_engulfing",
    "bullish_marubozu", "morning_star",
}
BEARISH_PATTERNS = {
    "shooting_star", "bearish_engulfing", "evening_star",
}


# ── Full signal snapshot ──────────────────────────────────────────────────────

def compute_signals(df: pd.DataFrame) -> dict:
    """
    Given OHLCV history, return a dict of computed values:
    ema9, ema21, ema50, rsi, atr, volume_ratio,
    support, resistance, patterns, current_price
    """
    close = df["close"]

    e9  = ema(close, config.EMA_FAST)
    e21 = ema(close, config.EMA_SLOW)
    e50 = ema(close, config.EMA_TREND)
    r   = rsi(close, config.RSI_PERIOD)
    a   = atr(df, config.ATR_PERIOD)
    vr  = volume_ratio(df, config.VOLUME_AVG_PERIOD)
    sup, res = find_support_resistance(df)
    pats = detect_patterns(df)

    price = float(close.iloc[-1])

    return {
        "price":        price,
        "ema9":         float(e9.iloc[-1]),
        "ema21":        float(e21.iloc[-1]),
        "ema50":        float(e50.iloc[-1]),
        "ema9_prev":    float(e9.iloc[-2]),
        "ema21_prev":   float(e21.iloc[-2]),
        "rsi":          float(r.iloc[-1]),
        "atr":          float(a.iloc[-1]),
        "volume_ratio": float(vr.iloc[-1]) if not np.isnan(vr.iloc[-1]) else 1.0,
        "support":      sup,
        "resistance":   res,
        "patterns":     pats,
    }
