"""
Strategy engine – scores each stock and decides BUY / HOLD / SELL.

Scoring (0-100):
  +20  EMA crossover (9 > 21, was 9 <= 21 previous candle)
  +15  Price above all three EMAs (9, 21, 50)
  +15  RSI between 40 and 60 (momentum zone, not overbought)
  +10  Near support level
  +10  Volume spike (> 1.5× average)
  +15  Bullish candlestick pattern
  +15  Broad market (Nifty50) is bullish

Deductions:
  -20  RSI > 65 (overbought)
  -15  Price below 50 EMA (downtrend)
  -10  Bearish candlestick pattern

A score >= MIN_CONFIDENCE triggers a BUY signal.

Exit signals:
  • Stop loss hit
  • Target hit
  • EMA death cross (9 EMA crosses below 21 EMA)
  • RSI overbought (> 70) with bearish pattern
"""

import logging
from typing import Literal

import config
from modules.indicators import BULLISH_PATTERNS, BEARISH_PATTERNS

logger = logging.getLogger(__name__)

Signal = Literal["BUY", "SELL", "HOLD"]


def score_entry(signals: dict, market_trend: str,
                pattern_stats: dict) -> tuple[int, list[str]]:
    """
    Score an entry opportunity.
    Returns (score, reasons).
    """
    score   = 0
    reasons = []

    # ── EMA crossover (fresh) ────────────────────────────────────────────────
    cross_up = (signals["ema9"] > signals["ema21"] and
                signals["ema9_prev"] <= signals["ema21_prev"])
    if cross_up:
        score += 20
        reasons.append("fresh EMA 9/21 crossover")
    elif signals["ema9"] > signals["ema21"]:
        score += 10
        reasons.append("EMA9 > EMA21")

    # ── Price vs EMAs ────────────────────────────────────────────────────────
    price = signals["price"]
    above_all = price > signals["ema9"] > signals["ema21"] > signals["ema50"]
    if above_all:
        score += 15
        reasons.append("price above all EMAs (bullish stack)")
    elif price < signals["ema50"]:
        score -= 15
        reasons.append("[−] price below 50 EMA (downtrend)")

    # ── RSI ──────────────────────────────────────────────────────────────────
    rsi = signals["rsi"]
    if 40 <= rsi <= 60:
        score += 15
        reasons.append(f"RSI in momentum zone ({rsi:.1f})")
    elif rsi > config.RSI_OVERBOUGHT:
        score -= 20
        reasons.append(f"[−] RSI overbought ({rsi:.1f})")
    elif rsi < config.RSI_OVERSOLD:
        score += 5
        reasons.append(f"RSI oversold bounce potential ({rsi:.1f})")

    # ── Support ──────────────────────────────────────────────────────────────
    if signals["support"] > 0:
        pct_from_support = (price - signals["support"]) / signals["support"]
        if 0 <= pct_from_support <= 0.02:
            score += 10
            reasons.append(f"near support {signals['support']:.2f}")

    # ── Volume ───────────────────────────────────────────────────────────────
    vr = signals["volume_ratio"]
    if vr >= 1.5:
        score += 10
        reasons.append(f"volume spike {vr:.1f}×")
    elif vr >= 1.2:
        score += 5
        reasons.append(f"above-avg volume {vr:.1f}×")

    # ── Candlestick patterns ─────────────────────────────────────────────────
    pats = signals.get("patterns", [])
    bull_pats = [p for p in pats if p in BULLISH_PATTERNS]
    bear_pats = [p for p in pats if p in BEARISH_PATTERNS]

    for pat in bull_pats:
        # weight by historical performance if available
        stats = pattern_stats.get(pat, {})
        win_rate = stats.get("win_rate", 0.5)
        boost = int(15 * win_rate)  # 0-15 based on past win rate
        score += boost
        reasons.append(f"bullish pattern: {pat} (win rate {win_rate:.0%})")

    for pat in bear_pats:
        score -= 10
        reasons.append(f"[−] bearish pattern: {pat}")

    # ── Market trend filter ──────────────────────────────────────────────────
    if market_trend == "bullish":
        score += 15
        reasons.append("Nifty50 bullish")
    elif market_trend == "bearish":
        score -= 10
        reasons.append("[−] Nifty50 bearish – caution")

    return max(0, min(score, 100)), reasons


def generate_entry_signal(symbol: str, signals: dict, market_trend: str,
                           has_position: bool,
                           pattern_stats: dict) -> tuple[Signal, int, list[str]]:
    """
    Return (signal, score, reasons).
    Only returns BUY if score >= MIN_CONFIDENCE and no existing position.
    """
    if has_position:
        return "HOLD", 0, ["already in position"]

    score, reasons = score_entry(signals, market_trend, pattern_stats)
    if score >= config.MIN_CONFIDENCE:
        return "BUY", score, reasons
    return "HOLD", score, reasons


def generate_exit_signal(symbol: str, signals: dict,
                          pos_entry: float, pos_stop: float,
                          pos_target: float) -> tuple[Signal, str]:
    """
    Check exit conditions for an open position.
    Returns (signal, reason).
    """
    price = signals["price"]

    # Hard stop loss
    if price <= pos_stop:
        return "SELL", f"stop loss hit @ {price:.2f}"

    # Target reached
    if price >= pos_target:
        return "SELL", f"target hit @ {price:.2f}"

    # EMA death cross
    death_cross = (signals["ema9"] < signals["ema21"] and
                   signals["ema9_prev"] >= signals["ema21_prev"])
    if death_cross:
        return "SELL", "EMA death cross (9 crossed below 21)"

    # Overbought + bearish pattern
    pats = signals.get("patterns", [])
    if signals["rsi"] > 70 and any(p in BEARISH_PATTERNS for p in pats):
        return "SELL", f"RSI overbought + bearish pattern {pats}"

    return "HOLD", "no exit signal"


def calculate_position_size(portfolio_value: float, price: float,
                             atr: float) -> tuple[int, float, float]:
    """
    Risk-based position sizing:
      risk_amount = portfolio_value * RISK_PER_TRADE_PCT
      stop_distance = max(ATR * 1.5, STOP_LOSS_PCT * price)
      quantity = risk_amount / stop_distance
    Returns (quantity, stop_loss_price, target_price).
    """
    risk_amount    = portfolio_value * config.RISK_PER_TRADE_PCT
    stop_distance  = max(atr * 1.5, price * config.STOP_LOSS_PCT)
    quantity       = max(1, int(risk_amount / stop_distance))

    # cap by max position size
    max_qty = int((portfolio_value * config.MAX_POSITION_PCT) / price)
    quantity = min(quantity, max_qty)

    stop_loss = round(price - stop_distance, 2)
    target    = round(price + stop_distance * 2, 2)   # 2:1 R:R
    return quantity, stop_loss, target
