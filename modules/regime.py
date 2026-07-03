"""
Market regime detection.

Classifies the current market as a combination of:
  trend      – bullish / bearish / sideways   (Nifty vs 50-EMA and 200-SMA)
  volatility – calm / volatile                (ATR percentile over the past year)

The regime string (e.g. "bullish/calm") is stamped on every parameter change
and trade so the tuner can learn which settings work in which conditions.
"""

import logging

import config
from modules import data_fetcher
from modules.indicators import atr

logger = logging.getLogger(__name__)


def detect_regime() -> str:
    df = data_fetcher.get_historical_data(config.NIFTY_SYMBOL, days=365)
    if df.empty or len(df) < 60:
        return "unknown/unknown"

    close = df["close"]
    ema50  = close.ewm(span=50, adjust=False).mean().iloc[-1]
    sma200 = close.rolling(min(200, len(close))).mean().iloc[-1]
    price  = close.iloc[-1]

    if price > ema50 and price > sma200:
        trend = "bullish"
    elif price < ema50 and price < sma200:
        trend = "bearish"
    else:
        trend = "sideways"

    # Volatility: today's ATR as percentile of the past year
    atr_series = atr(df, config.ATR_PERIOD).dropna()
    if len(atr_series) > 20:
        current = atr_series.iloc[-1]
        pct = (atr_series < current).mean()
        vol = "volatile" if pct > 0.80 else "calm"
    else:
        vol = "calm"

    regime = f"{trend}/{vol}"
    logger.debug("Market regime: %s", regime)
    return regime
