"""
EMA Momentum strategy — the original bot strategy, now fully parameterised.

Scores 0-100 across seven factors (EMA crossover, EMA stack, RSI zone,
support proximity, volume spike, candlestick patterns, Nifty trend).
"""

import pandas as pd

import config
from modules.indicators import compute_signals, BULLISH_PATTERNS, BEARISH_PATTERNS
from modules.params import ParamSpec
from modules.strategies.base import Strategy


class EmaMomentumStrategy(Strategy):
    name  = "ema_momentum"
    label = "EMA Momentum"
    description = ("Scores stocks on EMA 9/21/50 alignment, RSI, support, "
                   "volume and candlestick patterns. Buys when the combined "
                   "score clears the confidence threshold.")
    lookback_days = 90

    param_specs = [
        ParamSpec("min_confidence",     "Entry threshold (score)", 60, 45, 75, 5,
                  "Minimum 0-100 score required to open a position"),
        ParamSpec("stop_loss_pct",      "Stop loss %",             3.0, 1.5, 6.0, 0.5,
                  "Initial stop distance below entry"),
        ParamSpec("reward_risk_ratio",  "Reward:risk ratio",       2.0, 1.0, 4.0, 0.5,
                  "Target distance as multiple of stop distance"),
        ParamSpec("risk_per_trade_pct", "Risk per trade %",        2.0, 0.5, 4.0, 0.5,
                  "Portfolio % risked if the stop is hit"),
        ParamSpec("rsi_overbought",     "RSI overbought",          65, 55, 80, 5,
                  "RSI above this subtracts 20 points"),
        ParamSpec("volume_spike",       "Volume spike ratio",      1.5, 1.2, 3.0, 0.1,
                  "Volume vs 20-day average that counts as a spike"),
    ]

    def entry_universe(self) -> list[str]:
        return config.WATCHLIST

    def entry_signal(self, symbol, df, ctx, p):
        sigs = compute_signals(df)
        score, reasons = self._score(sigs, ctx, p)
        signal = "BUY" if score >= p["min_confidence"] else "HOLD"
        return signal, score, reasons, None

    def _score(self, s: dict, ctx: dict, p: dict) -> tuple[int, list[str]]:
        score, reasons = 0, []
        pattern_stats = ctx.get("pattern_stats", {})
        market_trend  = ctx.get("market_trend", "neutral")

        cross_up = s["ema9"] > s["ema21"] and s["ema9_prev"] <= s["ema21_prev"]
        if cross_up:
            score += 20; reasons.append("fresh EMA 9/21 crossover")
        elif s["ema9"] > s["ema21"]:
            score += 10; reasons.append("EMA9 > EMA21")

        price = s["price"]
        if price > s["ema9"] > s["ema21"] > s["ema50"]:
            score += 15; reasons.append("price above all EMAs (bullish stack)")
        elif price < s["ema50"]:
            score -= 15; reasons.append("[−] price below 50 EMA (downtrend)")

        rsi = s["rsi"]
        if 40 <= rsi <= 60:
            score += 15; reasons.append(f"RSI in momentum zone ({rsi:.1f})")
        elif rsi > p["rsi_overbought"]:
            score -= 20; reasons.append(f"[−] RSI overbought ({rsi:.1f})")
        elif rsi < config.RSI_OVERSOLD:
            score += 5; reasons.append(f"RSI oversold bounce potential ({rsi:.1f})")

        if s["support"] > 0:
            pct = (price - s["support"]) / s["support"]
            if 0 <= pct <= 0.02:
                score += 10; reasons.append(f"near support {s['support']:.2f}")

        vr = s["volume_ratio"]
        if vr >= p["volume_spike"]:
            score += 10; reasons.append(f"volume spike {vr:.1f}×")
        elif vr >= 1.2:
            score += 5; reasons.append(f"above-avg volume {vr:.1f}×")

        for pat in s.get("patterns", []):
            if pat in BULLISH_PATTERNS:
                wr = pattern_stats.get(pat, {}).get("win_rate", 0.5)
                score += int(15 * wr)
                reasons.append(f"bullish pattern: {pat} (win rate {wr:.0%})")
            elif pat in BEARISH_PATTERNS:
                score -= 10; reasons.append(f"[−] bearish pattern: {pat}")

        if market_trend == "bullish":
            score += 15; reasons.append("Nifty50 bullish")
        elif market_trend == "bearish":
            score -= 10; reasons.append("[−] Nifty50 bearish – caution")

        return max(0, min(score, 100)), reasons

    def exit_signal(self, symbol, df, pos, p):
        s = compute_signals(df)
        price = s["price"]
        if price <= pos.stop_loss:
            return "SELL", f"stop loss hit @ {price:.2f}"
        if price >= pos.target:
            return "SELL", f"target hit @ {price:.2f}"
        if s["ema9"] < s["ema21"] and s["ema9_prev"] >= s["ema21_prev"]:
            return "SELL", "EMA death cross (9 crossed below 21)"
        pats = s.get("patterns", [])
        if s["rsi"] > 70 and any(x in BEARISH_PATTERNS for x in pats):
            return "SELL", f"RSI overbought + bearish pattern {pats}"
        return "HOLD", "no exit signal"
