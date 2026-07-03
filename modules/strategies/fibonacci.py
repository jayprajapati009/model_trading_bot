"""
Fibonacci retracement strategy.

In an uptrend (price above 50 EMA):
  1. Find the most recent significant up-leg (swing low → swing high,
     minimum leg size configurable).
  2. Wait for a pullback into the 38.2%–61.8% retracement zone.
  3. Require a reversal cue (bullish candlestick pattern, or RSI recovering
     from the pullback) before entering.
  4. Stop below the 78.6% level; target the prior swing high
     (or the 127.2% extension when configured).
"""

import logging

import pandas as pd

import config
from modules.indicators import compute_signals, BULLISH_PATTERNS
from modules.params import ParamSpec
from modules.strategies.base import Strategy

logger = logging.getLogger(__name__)


class FibonacciStrategy(Strategy):
    name  = "fibonacci"
    label = "Fibonacci Retracement"
    description = ("Buys pullbacks into the 38.2–61.8% Fibonacci retracement "
                   "zone of the last significant up-leg in an uptrend, with a "
                   "reversal cue. Stop below the 78.6% level, target the "
                   "prior high or 127% extension.")
    lookback_days = 120

    param_specs = [
        ParamSpec("min_score",          "Entry threshold (score)", 60, 45, 80, 5,
                  "Minimum score to enter on a retracement"),
        ParamSpec("swing_lookback",     "Swing lookback (days)",   60, 20, 90, 5,
                  "Window used to find the swing low → high leg"),
        ParamSpec("min_leg_pct",        "Min leg size %",          8.0, 4.0, 20.0, 1.0,
                  "Up-leg must be at least this % to be tradeable"),
        ParamSpec("zone_low",           "Zone start (fib %)",      38.2, 23.6, 50.0, 1.0,
                  "Shallow edge of the buy zone"),
        ParamSpec("zone_high",          "Zone end (fib %)",        61.8, 50.0, 78.6, 1.0,
                  "Deep edge of the buy zone"),
        ParamSpec("target_extension",   "Target (fib %)",          100.0, 100.0, 161.8, 27.2,
                  "100 = prior swing high, 127.2/161.8 = extensions"),
        ParamSpec("risk_per_trade_pct", "Risk per trade %",        2.0, 0.5, 4.0, 0.5,
                  "Portfolio % risked if the stop is hit"),
    ]

    def entry_universe(self) -> list[str]:
        return config.WATCHLIST

    # ── Swing detection ───────────────────────────────────────────────────────

    def _find_leg(self, df: pd.DataFrame, p: dict) -> dict | None:
        """Find the last significant swing low → swing high up-leg."""
        window = df.tail(int(p["swing_lookback"]))
        if len(window) < 10:
            return None

        hi_idx = window["high"].idxmax()
        hi = float(window.loc[hi_idx, "high"])

        before_high = window.loc[:hi_idx]
        if len(before_high) < 3:
            return None
        lo_idx = before_high["low"].idxmin()
        lo = float(before_high.loc[lo_idx, "low"])

        leg = hi - lo
        if lo <= 0 or (leg / lo) * 100 < p["min_leg_pct"]:
            return None
        return {"low": lo, "high": hi, "leg": leg,
                "high_idx": hi_idx, "low_idx": lo_idx}

    # ── Entry ─────────────────────────────────────────────────────────────────

    def entry_signal(self, symbol, df, ctx, p):
        sigs  = compute_signals(df)
        price = sigs["price"]
        score, reasons = 0, []

        # Uptrend filter — retracements are only buyable in uptrends
        if price < sigs["ema50"]:
            return "HOLD", 0, ["not in uptrend (below 50 EMA)"], None

        leg = self._find_leg(df, p)
        if leg is None:
            return "HOLD", 0, ["no significant up-leg found"], None

        # Where is price inside the retracement?
        retr_pct = (leg["high"] - price) / leg["leg"] * 100
        if not (p["zone_low"] <= retr_pct <= p["zone_high"]):
            return "HOLD", 10, [f"outside fib zone (retracement {retr_pct:.1f}%)"], None

        score += 40
        reasons.append(f"in fib buy zone: {retr_pct:.1f}% retracement of "
                       f"{leg['low']:.2f}→{leg['high']:.2f} leg")

        # Golden pocket bonus (58.6–65% retracement is the classic sweet spot)
        if 58.6 <= retr_pct <= 65.0:
            score += 10
            reasons.append("golden pocket (61.8% zone)")

        # Reversal cues
        bull_pats = [x for x in sigs.get("patterns", []) if x in BULLISH_PATTERNS]
        if bull_pats:
            score += 20
            reasons.append(f"reversal pattern: {', '.join(bull_pats)}")

        rsi = sigs["rsi"]
        if 35 <= rsi <= 55:
            score += 15
            reasons.append(f"RSI reset by pullback ({rsi:.1f})")

        if sigs["volume_ratio"] < 0.8:
            score += 10
            reasons.append("pullback on drying volume (healthy)")

        if ctx.get("market_trend") == "bullish":
            score += 5
            reasons.append("Nifty50 bullish")

        score = min(score, 100)
        if score < p["min_score"]:
            return "HOLD", score, reasons, None

        # Fib-based stop & target plan
        stop   = leg["high"] - leg["leg"] * 0.786
        ext    = p["target_extension"] / 100
        target = leg["low"] + leg["leg"] * ext
        plan = {"stop": round(stop, 2), "target": round(target, 2)}
        reasons.append(f"stop @ 78.6% ({plan['stop']}), target @ {p['target_extension']:.0f}% ({plan['target']})")
        return "BUY", score, reasons, plan

    # ── Exit ──────────────────────────────────────────────────────────────────

    def exit_signal(self, symbol, df, pos, p):
        price = float(df["close"].iloc[-1])
        if price <= pos.stop_loss:
            return "SELL", f"fib stop (78.6%) hit @ {price:.2f}"
        if price >= pos.target:
            return "SELL", f"fib target hit @ {price:.2f}"
        # Structure break: close below 50 EMA invalidates the uptrend premise
        sigs = compute_signals(df)
        if price < sigs["ema50"] * 0.99:
            return "SELL", "uptrend broken (closed below 50 EMA)"
        return "HOLD", "riding the retracement bounce"
