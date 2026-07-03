"""
Stage 2 strategy — Stan Weinstein stage analysis.

A stock is in Stage 2 (advancing phase) when:
  • price is above its 150-day (~30-week) moving average
  • that MA is rising
  • price is near its 52-week high (breakout, not a bounce)
  • relative strength vs Nifty is positive
  • the breakout came on above-average volume

A daily pre-market scan of the Nifty 500 builds a shortlist of the
highest-scoring Stage 2 candidates; intraday scans only evaluate the
shortlist for entry timing.

Exit: Weinstein-style — sell when price closes below the (rising) 150-day MA,
plus the standard stop loss / trailing protections.
"""

import json
import logging
import os
import time
from datetime import datetime

import pandas as pd

import config
from modules import data_fetcher
from modules.indicators import atr
from modules.params import ParamSpec
from modules.strategies.base import Strategy

logger = logging.getLogger(__name__)

_SHORTLIST_TTL = 20 * 3600   # rebuild daily


class StageTwoStrategy(Strategy):
    name  = "stage_two"
    label = "Stage 2 Breakout"
    description = ("Weinstein stage analysis: scans the Nifty 500 daily for "
                   "stocks breaking into Stage 2 (advancing phase) above a "
                   "rising 30-week MA on strong volume with market-beating "
                   "relative strength.")
    lookback_days = 400   # need ~1 year for 52-week high + 150-day MA

    param_specs = [
        ParamSpec("min_score",          "Entry threshold (score)", 60, 45, 80, 5,
                  "Minimum Stage 2 score to buy"),
        ParamSpec("ma_period",          "Stage MA period (days)",  150, 100, 200, 10,
                  "The Weinstein 30-week MA equivalent in trading days"),
        ParamSpec("near_high_pct",      "Near 52w-high window %",  5.0, 2.0, 15.0, 1.0,
                  "Price must be within this % of its 52-week high"),
        ParamSpec("breakout_volume",    "Breakout volume ratio",   1.5, 1.0, 3.0, 0.1,
                  "Recent volume vs 50-day average required for breakout"),
        ParamSpec("stop_loss_pct",      "Stop loss %",             5.0, 3.0, 10.0, 0.5,
                  "Stage 2 needs wider stops than momentum scalps"),
        ParamSpec("risk_per_trade_pct", "Risk per trade %",        2.0, 0.5, 4.0, 0.5,
                  "Portfolio % risked if the stop is hit"),
        ParamSpec("reward_risk_ratio",  "Reward:risk ratio",       3.0, 1.5, 5.0, 0.5,
                  "Stage 2 trends run long — wider targets"),
        ParamSpec("shortlist_size",     "Shortlist size",          20, 5, 40, 5,
                  "How many top-scoring Stage 2 stocks to track intraday"),
    ]

    # ── Universe: daily shortlist of Nifty 500 ───────────────────────────────

    def entry_universe(self) -> list[str]:
        shortlist = self._load_shortlist()
        return [row["symbol"] for row in shortlist]

    def _load_shortlist(self) -> list[dict]:
        path = config.STAGE2_SHORTLIST_FILE
        if os.path.exists(path):
            try:
                with open(path) as f:
                    data = json.load(f)
                if time.time() - data.get("built_at", 0) < _SHORTLIST_TTL:
                    return data.get("shortlist", [])
            except Exception:
                pass
        return []

    def build_shortlist(self) -> list[dict]:
        """
        Daily pre-market job: scan Nifty 500, score every stock, save the
        top N Stage 2 candidates. Uses batched downloads to be gentle on
        Yahoo Finance.
        """
        from modules.universe import get_nifty500
        import yfinance as yf

        p = self.params()
        symbols = get_nifty500()
        logger.info("Stage 2 scan: %d symbols in universe", len(symbols))

        nifty = data_fetcher.get_historical_data(config.NIFTY_SYMBOL, days=400)
        nifty_ret_63 = None
        if not nifty.empty and len(nifty) > 63:
            nifty_ret_63 = nifty["close"].iloc[-1] / nifty["close"].iloc[-63] - 1

        scored: list[dict] = []
        batch_size = 50
        for i in range(0, len(symbols), batch_size):
            batch = symbols[i:i + batch_size]
            try:
                raw = yf.download(batch, period="400d", interval="1d",
                                  progress=False, auto_adjust=True,
                                  group_by="ticker", threads=True)
            except Exception as exc:
                logger.warning("Stage 2 batch %d failed: %s", i // batch_size, exc)
                continue

            for sym in batch:
                try:
                    df = raw[sym].dropna(subset=["Close"]) if len(batch) > 1 else raw.dropna(subset=["Close"])
                    df.columns = [c.lower() for c in df.columns]
                    result = self._score_stage2(df, p, nifty_ret_63)
                    if result and result["score"] >= p["min_score"] - 10:
                        result["symbol"] = sym
                        scored.append(result)
                except Exception:
                    continue
            time.sleep(1)   # rate-limit courtesy between batches

        scored.sort(key=lambda r: -r["score"])
        shortlist = scored[:int(p["shortlist_size"])]

        os.makedirs(os.path.dirname(config.STAGE2_SHORTLIST_FILE), exist_ok=True)
        with open(config.STAGE2_SHORTLIST_FILE, "w") as f:
            json.dump({
                "built_at": time.time(),
                "built_date": datetime.now().strftime("%Y-%m-%d"),
                "universe_size": len(symbols),
                "candidates_found": len(scored),
                "shortlist": shortlist,
            }, f, indent=2)
        logger.info("Stage 2 shortlist built: %d candidates, top %d kept",
                    len(scored), len(shortlist))
        return shortlist

    def _score_stage2(self, df: pd.DataFrame, p: dict,
                      nifty_ret_63: float | None) -> dict | None:
        ma_period = int(p["ma_period"])
        if len(df) < ma_period + 25:
            return None

        close  = df["close"]
        price  = float(close.iloc[-1])
        ma     = close.rolling(ma_period).mean()
        ma_now = float(ma.iloc[-1])
        ma_then = float(ma.iloc[-21])   # MA one month ago

        score, reasons = 0, []

        # Gate: must be above the stage MA — otherwise not Stage 2 at all
        if price <= ma_now:
            return None
        score += 25
        reasons.append(f"price above {ma_period}d MA")

        # Rising MA
        if ma_now > ma_then:
            slope_pct = (ma_now / ma_then - 1) * 100
            score += 15
            reasons.append(f"MA rising ({slope_pct:.1f}%/month)")

        # Near 52-week high (breakout territory, not a dead-cat bounce)
        high_52w = float(df["high"].tail(250).max())
        dist = (high_52w - price) / high_52w * 100
        if dist <= p["near_high_pct"]:
            score += 20
            reasons.append(f"within {dist:.1f}% of 52w high")

        # Volume confirmation: last 5 days vs 50-day average
        vol50 = df["volume"].tail(50).mean()
        vol5  = df["volume"].tail(5).mean()
        if vol50 > 0 and vol5 / vol50 >= p["breakout_volume"]:
            score += 15
            reasons.append(f"breakout volume {vol5/vol50:.1f}×")

        # Relative strength vs Nifty (63-day return comparison)
        if nifty_ret_63 is not None and len(close) > 63:
            stock_ret = price / float(close.iloc[-63]) - 1
            if stock_ret > nifty_ret_63:
                score += 15
                reasons.append(f"outperforming Nifty ({stock_ret:.1%} vs {nifty_ret_63:.1%})")

        # Higher lows over the last 50 days (uptrend structure)
        lows = df["low"].tail(50)
        first_half, second_half = lows.iloc[:25].min(), lows.iloc[25:].min()
        if second_half > first_half:
            score += 10
            reasons.append("higher lows structure")

        return {"score": min(score, 100), "reasons": reasons,
                "price": price, "ma": round(ma_now, 2)}

    # ── Entry / exit ──────────────────────────────────────────────────────────

    def entry_signal(self, symbol, df, ctx, p):
        result = self._score_stage2(df, p, ctx.get("nifty_ret_63"))
        if result is None:
            return "HOLD", 0, ["not in Stage 2 (below stage MA or insufficient data)"], None
        score, reasons = result["score"], result["reasons"]
        signal = "BUY" if score >= p["min_score"] else "HOLD"
        return signal, score, reasons, None

    def exit_signal(self, symbol, df, pos, p):
        price = float(df["close"].iloc[-1])
        if price <= pos.stop_loss:
            return "SELL", f"stop loss hit @ {price:.2f}"
        if price >= pos.target:
            return "SELL", f"target hit @ {price:.2f}"
        # Weinstein exit: close below the stage MA ends Stage 2
        ma_period = int(p["ma_period"])
        if len(df) >= ma_period:
            ma_now = float(df["close"].rolling(ma_period).mean().iloc[-1])
            if price < ma_now:
                return "SELL", f"closed below {ma_period}d MA (Stage 2 over)"
        return "HOLD", "still in Stage 2"
