"""
Momentum Rider — aggressive relative-strength strategy with simulated
futures leverage.

The classical momentum factor ("buy the best performers of the last
6 months"), executed with pullback timing:

  • daily pre-market scan ranks the Nifty 500 by 6-month return and
    shortlists the top leaders
  • intraday, a leader is bought when it trades CLOSE to its 10-week EMA
    (50-day EMA) — pullback entries into strength, never chasing spikes
  • churn: exit when price closes below the 10-week EMA or the ranking
    return turns negative, and rotate into the next leader

`leverage` simulates NSE stock-futures margin: only notional/leverage of
cash is blocked, but PnL accrues on the FULL notional — in both
directions. A 4% adverse move at 3× leverage is a 12% hit on the margin.
"""

import json
import logging
import os
import time
from datetime import datetime

import pandas as pd

import config
from modules.params import ParamSpec
from modules.strategies.base import Strategy

logger = logging.getLogger(__name__)

_SHORTLIST_TTL = 20 * 3600   # rebuild daily


class MomentumRiderStrategy(Strategy):
    name  = "momentum_rider"
    label = "Momentum Rider"
    description = ("Ranks the Nifty 500 by 6-month relative strength and buys "
                   "the leaders on pullbacks to the 10-week EMA. Optional "
                   "simulated futures leverage multiplies PnL on the margin "
                   "blocked — gains AND losses. The aggressive bucket.")
    lookback_days = 300   # 126-day ranking window + EMA warm-up

    param_specs = [
        ParamSpec("min_score",          "Entry threshold (score)", 60, 45, 80, 5,
                  "Minimum momentum score to buy"),
        ParamSpec("momentum_days",      "Momentum window (days)",  126, 63, 252, 21,
                  "Return window for relative-strength ranking (126 ≈ 6 months)"),
        ParamSpec("min_rank_return",    "Min ranking return %",    20.0, 5.0, 50.0, 5.0,
                  "A stock must be up at least this % over the window to count as a leader"),
        ParamSpec("max_ext_pct",        "Max % above 10-week EMA", 6.0, 2.0, 15.0, 1.0,
                  "Buy only close to the 10-week EMA — extended stocks wait for a pullback"),
        ParamSpec("shortlist_size",     "Shortlist size",          15, 5, 30, 5,
                  "How many momentum leaders to track intraday"),
        ParamSpec("stop_loss_pct",      "Stop loss %",             4.0, 2.0, 8.0, 0.5,
                  "Leaders swing hard — wider stop than scalps"),
        ParamSpec("reward_risk_ratio",  "Reward:risk ratio",       2.5, 1.5, 5.0, 0.5,
                  "Momentum winners run — let them"),
        ParamSpec("risk_per_trade_pct", "Risk per trade %",        3.0, 0.5, 5.0, 0.5,
                  "The aggressive bucket risks more per trade"),
        ParamSpec("leverage",           "Futures leverage ×",      2, 1, 5, 1,
                  "Simulated margin: PnL on leverage × the cash blocked — losses multiply too"),
    ]

    # ── Universe: daily shortlist of momentum leaders ─────────────────────────

    def entry_universe(self) -> list[str]:
        return [row["symbol"] for row in self._load_shortlist()]

    def _load_shortlist(self) -> list[dict]:
        path = config.MOMENTUM_SHORTLIST_FILE
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
        """Daily pre-market job: rank the Nifty 500 by momentum-window return."""
        from modules.universe import get_nifty500
        import yfinance as yf

        p = self.params()
        symbols = get_nifty500()
        logger.info("Momentum scan: %d symbols in universe", len(symbols))

        # Neutral market context — the ranking must not depend on the day's mood
        ctx = {"market_trend": "neutral"}

        scored: list[dict] = []
        batch_size = 50
        for i in range(0, len(symbols), batch_size):
            batch = symbols[i:i + batch_size]
            try:
                raw = yf.download(batch, period=f"{self.lookback_days}d",
                                  interval="1d", progress=False,
                                  auto_adjust=True, group_by="ticker",
                                  threads=True)
            except Exception as exc:
                logger.warning("Momentum batch %d failed: %s", i // batch_size, exc)
                continue

            for sym in batch:
                try:
                    df = raw[sym].dropna(subset=["Close"]) if len(batch) > 1 else raw.dropna(subset=["Close"])
                    df.columns = [c.lower() for c in df.columns]
                    result = self._score_momentum(df, p, ctx)
                    if result:
                        result["symbol"] = sym
                        scored.append(result)
                except Exception:
                    continue
            time.sleep(1)   # rate-limit courtesy between batches

        # Rank purely by the window return — that IS the momentum factor
        scored.sort(key=lambda r: -r["rank_return"])
        shortlist = scored[:int(p["shortlist_size"])]

        os.makedirs(os.path.dirname(config.MOMENTUM_SHORTLIST_FILE), exist_ok=True)
        with open(config.MOMENTUM_SHORTLIST_FILE, "w") as f:
            json.dump({
                "built_at": time.time(),
                "built_date": datetime.now().strftime("%Y-%m-%d"),
                "universe_size": len(symbols),
                "candidates_found": len(scored),
                "shortlist": shortlist,
            }, f, indent=2)
        logger.info("Momentum shortlist built: %d leaders found, top %d kept",
                    len(scored), len(shortlist))
        return shortlist

    def _score_momentum(self, df: pd.DataFrame, p: dict, ctx: dict) -> dict | None:
        mdays = int(p["momentum_days"])
        if len(df) < mdays + 10:
            return None

        close = df["close"]
        price = float(close.iloc[-1])
        rank_return = (price / float(close.iloc[-mdays]) - 1) * 100

        # Gate: only leaders qualify at all
        if rank_return < p["min_rank_return"]:
            return None

        ema50 = close.ewm(span=50, adjust=False).mean()
        ema_now, ema_then = float(ema50.iloc[-1]), float(ema50.iloc[-21])

        score, reasons = 0, []

        if rank_return >= 2 * p["min_rank_return"]:
            score += 30
            reasons.append(f"{mdays}d return {rank_return:+.0f}% (top-tier momentum)")
        else:
            score += 20
            reasons.append(f"{mdays}d return {rank_return:+.0f}%")

        if price > ema_now and ema_now > ema_then:
            score += 15
            reasons.append("above rising 10-week EMA")
        elif price < ema_now:
            score -= 20
            reasons.append("[−] below 10-week EMA (momentum broken)")

        # The "buy close to 10WEMA" rule — pullback entry, not a chase
        ext = (price - ema_now) / ema_now * 100
        if 0 <= ext <= p["max_ext_pct"]:
            score += 25
            reasons.append(f"close to 10-week EMA (+{ext:.1f}%)")
        elif ext > p["max_ext_pct"]:
            score -= 10
            reasons.append(f"[−] extended {ext:.1f}% above 10-week EMA — wait for pullback")

        if len(close) > 21:
            ret_21 = (price / float(close.iloc[-21]) - 1) * 100
            if ret_21 > 0:
                score += 10
                reasons.append(f"1-month return {ret_21:+.1f}%")

        vol20 = float(df["volume"].tail(20).mean())
        vol5  = float(df["volume"].tail(5).mean())
        if vol20 > 0 and vol5 / vol20 >= 1.2:
            score += 10
            reasons.append(f"volume {vol5 / vol20:.1f}× average")

        trend = ctx.get("market_trend", "neutral")
        if trend == "bullish":
            score += 10
            reasons.append("Nifty bullish")
        elif trend == "bearish":
            score -= 15
            reasons.append("[−] Nifty bearish — leveraged longs are dangerous here")

        return {"score": max(0, min(score, 100)), "reasons": reasons,
                "rank_return": round(rank_return, 1), "price": price,
                "ema": round(ema_now, 2)}

    # ── Entry / exit ──────────────────────────────────────────────────────────

    def entry_signal(self, symbol, df, ctx, p):
        result = self._score_momentum(df, p, ctx)
        if result is None:
            return "HOLD", 0, [f"not a momentum leader (<{p['min_rank_return']:.0f}% "
                               f"over {int(p['momentum_days'])}d)"], None
        score, reasons = result["score"], result["reasons"]
        signal = "BUY" if score >= p["min_score"] else "HOLD"
        return signal, score, reasons, None

    def exit_signal(self, symbol, df, pos, p):
        close = df["close"]
        price = float(close.iloc[-1])
        if price <= pos.stop_loss:
            return "SELL", f"stop loss hit @ {price:.2f}"
        if price >= pos.target:
            return "SELL", f"target hit @ {price:.2f}"

        ema_now = float(close.ewm(span=50, adjust=False).mean().iloc[-1])
        if price < ema_now * 0.99:
            return "SELL", "closed below 10-week EMA — churn to the next leader"

        mdays = int(p["momentum_days"])
        if len(close) > mdays and price / float(close.iloc[-mdays]) - 1 < 0:
            return "SELL", f"{mdays}d momentum turned negative"

        return "HOLD", "still a leader"
