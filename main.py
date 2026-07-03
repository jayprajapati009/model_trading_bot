"""
Trading Bot – main entry point (multi-strategy).

Jobs:
  • market_scan       – every SCAN_INTERVAL_MIN during NSE hours; runs every
                        strategy's exits then entries within its capital bucket
  • stage2_scan       – daily 08:45 IST; scans Nifty 500, builds the Stage 2
                        shortlist for intraday entry timing
  • daily_summary     – 15:45 IST weekdays
  • weekly_review     – Friday 16:00 IST; auto-tunes parameters (auto mode)
                        and reallocates capital between strategies

All state is persisted; safe to restart at any time.
"""

import logging
import os
import signal
import sys
import threading
from datetime import datetime

import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

import config
from modules import data_fetcher, learning, trade_logger, tuner
from modules import params as param_store
from modules.portfolio import Portfolio
from modules.regime import detect_regime
from modules.strategies import STRATEGIES, get_strategy
from dashboard import create_app

# ── Logging setup ─────────────────────────────────────────────────────────────

os.makedirs(config.LOGS_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s – %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(config.LOGS_DIR, "trading_bot.log")),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("main")

_portfolio: Portfolio | None = None
_open_trade_ids: dict[str, int] = {}   # symbol -> DB trade id
_IST = pytz.timezone(config.TIMEZONE)


def _ist_now() -> datetime:
    return datetime.now(_IST)


# ── Core scan cycle ───────────────────────────────────────────────────────────

def market_scan():
    if not data_fetcher.is_market_open():
        logger.debug("Market closed – skipping scan")
        return

    logger.info("── Market scan started %s ──", _ist_now().strftime("%H:%M:%S"))

    market_trend  = data_fetcher.get_nifty_trend()
    pattern_stats = learning.load_pattern_stats()
    allocations   = param_store.get_allocations(list(STRATEGIES.keys()))
    prices: dict[str, float] = {}

    # Context shared by all strategies
    nifty = data_fetcher.get_historical_data(config.NIFTY_SYMBOL, days=400)
    nifty_ret_63 = None
    if not nifty.empty and len(nifty) > 63:
        nifty_ret_63 = float(nifty["close"].iloc[-1] / nifty["close"].iloc[-63] - 1)
    ctx = {
        "market_trend":  market_trend,
        "pattern_stats": pattern_stats,
        "nifty_ret_63":  nifty_ret_63,
    }

    # ── 1. Exits first (per-position, using its own strategy's logic) ─────────
    for sym, pos in list(_portfolio.positions.items()):
        strat = get_strategy(pos.strategy)
        df = data_fetcher.get_historical_data(sym, days=strat.lookback_days)
        if df.empty:
            continue
        prices[sym] = float(df["close"].iloc[-1])

        sig, reason = strat.exit_signal(sym, df, pos, strat.params())
        if sig == "SELL":
            result = _portfolio.close_position(sym, prices[sym], reason)
            if result:
                tid = _open_trade_ids.pop(sym, None)
                if tid:
                    trade_logger.log_trade_closed(
                        tid, result["exit_price"], result["pnl"],
                        result["pnl_pct"], reason)
                learning.record_trade_outcome(
                    result["signals_used"], result["pnl"], result["pnl_pct"])
                learning.write_trade_note(
                    sym, "LONG", result["entry_price"], result["exit_price"],
                    result["pnl"], result["pnl_pct"], reason,
                    result["signals_used"])
                learning.refresh_lessons()
                logger.info("EXIT [%s] %s | PnL ₹%.2f (%.2f%%) | %s",
                            pos.strategy, sym, result["pnl"],
                            result["pnl_pct"], reason)

    # ── 2. Trailing stops ─────────────────────────────────────────────────────
    for sym, new_stop in _portfolio.update_trailing_stops(prices):
        logger.info("Trailing stop updated for %s → %.2f", sym, new_stop)

    # ── 3. Entries per strategy, within its capital allocation ────────────────
    nav = _portfolio.net_value(prices)
    for strat in STRATEGIES.values():
        p = strat.params()
        allocation = allocations.get(strat.name, 1.0 / len(STRATEGIES))
        bucket_capital = nav * allocation

        for sym in strat.entry_universe():
            if sym in _portfolio.positions:
                continue

            df = data_fetcher.get_historical_data(sym, days=strat.lookback_days)
            if df.empty or len(df) < 55:
                continue

            try:
                sig, score, reasons, plan = strat.entry_signal(sym, df, ctx, p)
            except Exception as exc:
                logger.warning("%s entry_signal failed for %s: %s",
                               strat.name, sym, exc)
                continue

            price = float(df["close"].iloc[-1])
            prices[sym] = price
            trade_logger.log_scan(sym, price, score, sig, reasons, strat.name)
            learning.write_scan_observation(sym, price, score,
                                            f"{sig} [{strat.label}]", reasons)

            if sig != "BUY":
                continue

            from modules.indicators import atr as atr_fn
            atr_val = float(atr_fn(df, config.ATR_PERIOD).iloc[-1])
            qty, sl, tgt = strat.position_size(bucket_capital, price, atr_val,
                                               p, plan)
            pos = _portfolio.open_position(
                sym, price, qty, sl, tgt, reasons,
                strategy=strat.name, allocation=allocation)
            if pos:
                tid = trade_logger.log_trade_opened(
                    sym, price, qty, reasons, score,
                    strategy=strat.name,
                    config_version=strat.config_version())
                _open_trade_ids[sym] = tid
                logger.info("ENTER [%s] %s | qty=%d @ ₹%.2f | SL=%.2f | "
                            "TGT=%.2f | score=%d",
                            strat.name, sym, qty, price, sl, tgt, score)

    stats = _portfolio.stats(prices)
    logger.info("Portfolio → NAV ₹%.2f | Total PnL ₹%.2f (%.2f%%)",
                stats["nav"], stats["total_pnl"], stats["total_return_pct"])


# ── Stage 2 daily scan ────────────────────────────────────────────────────────

def stage2_scan():
    logger.info("── Stage 2 daily universe scan ──")
    try:
        shortlist = STRATEGIES["stage_two"].build_shortlist()
        if shortlist:
            top = ", ".join(f"{r['symbol']}({r['score']})" for r in shortlist[:5])
            logger.info("Stage 2 top candidates: %s", top)
    except Exception as exc:
        logger.error("Stage 2 scan failed: %s", exc)


# ── Daily summary ─────────────────────────────────────────────────────────────

def daily_summary():
    logger.info("── Daily summary ──")
    market_trend = data_fetcher.get_nifty_trend()
    prices = data_fetcher.get_multiple_prices(list(_portfolio.positions.keys()))
    stats  = _portfolio.stats(prices)
    perf   = trade_logger.get_performance_stats()
    date_s = _ist_now().strftime("%Y-%m-%d")

    events = [f"Market regime: {detect_regime()}"]
    if perf.get("total_trades", 0):
        events.append(f"All-time: {perf['total_trades']} trades, "
                      f"win rate {perf['win_rate_pct']}%, "
                      f"profit factor {perf['profit_factor']}")
    for name in STRATEGIES:
        sp = trade_logger.get_strategy_performance(name)
        if sp["trades"]:
            events.append(f"{name}: {sp['trades']} trades, "
                          f"expectancy ₹{sp['expectancy']}, "
                          f"win rate {sp['win_rate']}%")

    learning.write_daily_observation(market_trend, stats, events)
    learning.refresh_lessons()
    trade_logger.save_daily_summary(
        date_s, stats["nav"], stats["realised_pnl"],
        stats["unrealised_pnl"], stats["open_positions"],
        notes="; ".join(events))
    logger.info("Daily summary saved for %s", date_s)


# ── Weekly tuner ──────────────────────────────────────────────────────────────

def weekly_review():
    try:
        tuner.weekly_review()
    except Exception as exc:
        logger.error("Weekly review failed: %s", exc)


# ── Dashboard thread ──────────────────────────────────────────────────────────

def _start_dashboard():
    dash_port = int(os.environ.get("DASHBOARD_PORT", 8080))
    app = create_app(_portfolio, _open_trade_ids)
    logger.info("Dashboard starting on port %d", dash_port)
    app.run(host="0.0.0.0", port=dash_port, debug=False, use_reloader=False)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    global _portfolio

    logger.info("=" * 60)
    logger.info("Trading Bot starting up (strategies: %s)",
                ", ".join(STRATEGIES.keys()))
    logger.info("=" * 60)

    trade_logger.init_db()
    _portfolio = Portfolio()

    for row in trade_logger.get_open_trades():
        _open_trade_ids[row["symbol"]] = row["id"]
    if _open_trade_ids:
        logger.info("Restored %d open trade IDs from DB", len(_open_trade_ids))

    stats = _portfolio.stats()
    alloc = param_store.get_allocations(list(STRATEGIES.keys()))
    logger.info("Portfolio: NAV ₹%.2f | Open: %d | Allocations: %s",
                stats["nav"], stats["open_positions"],
                {k: f"{v:.0%}" for k, v in alloc.items()})

    dash_thread = threading.Thread(target=_start_dashboard, daemon=True)
    dash_thread.start()

    scheduler = BackgroundScheduler(timezone=_IST)
    scheduler.add_job(market_scan,
                      IntervalTrigger(minutes=config.SCAN_INTERVAL_MIN, timezone=_IST),
                      id="market_scan", coalesce=True, max_instances=1)
    scheduler.add_job(stage2_scan,
                      CronTrigger(day_of_week="mon-fri", hour=8, minute=45, timezone=_IST),
                      id="stage2_scan", coalesce=True, max_instances=1)
    scheduler.add_job(daily_summary,
                      CronTrigger(day_of_week="mon-fri", hour=15, minute=45, timezone=_IST),
                      id="daily_summary", coalesce=True)
    scheduler.add_job(weekly_review,
                      CronTrigger(day_of_week="fri", hour=16, minute=0, timezone=_IST),
                      id="weekly_review", coalesce=True)
    scheduler.start()
    logger.info("Scheduler started. Scan every %d min.", config.SCAN_INTERVAL_MIN)

    # Build Stage 2 shortlist at startup if missing/stale (runs in background)
    if not STRATEGIES["stage_two"].entry_universe():
        threading.Thread(target=stage2_scan, daemon=True).start()

    if data_fetcher.is_market_open():
        market_scan()

    def _shutdown(signum, frame):
        logger.info("Shutdown signal received – stopping scheduler")
        scheduler.shutdown(wait=False)
        sys.exit(0)

    signal.signal(signal.SIGINT,  _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    import time
    while True:
        time.sleep(60)


if __name__ == "__main__":
    main()
