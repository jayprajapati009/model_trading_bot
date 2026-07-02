"""
Trading Bot – main entry point.

Runs the scan loop on a schedule during NSE market hours (9:15–15:30 IST).
Performs a daily summary at 15:45 IST.
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
from modules import data_fetcher, indicators, learning, portfolio, strategy, trade_logger
from modules.portfolio import Portfolio
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

# ── Globals ───────────────────────────────────────────────────────────────────

_portfolio: Portfolio | None = None
_open_trade_ids: dict[str, int] = {}   # symbol -> DB trade id
_IST = pytz.timezone(config.TIMEZONE)


def _ist_now() -> datetime:
    return datetime.now(_IST)


# ── Core scan cycle ───────────────────────────────────────────────────────────

def market_scan():
    """Called every SCAN_INTERVAL_MIN during market hours."""
    if not data_fetcher.is_market_open():
        logger.debug("Market closed – skipping scan")
        return

    logger.info("── Market scan started %s ──", _ist_now().strftime("%H:%M:%S"))

    market_trend = data_fetcher.get_nifty_trend()
    pattern_stats = learning.load_pattern_stats()
    prices: dict[str, float] = {}

    # ── 1. Check exits first ──────────────────────────────────────────────────
    for sym, pos in list(_portfolio.positions.items()):
        df = data_fetcher.get_historical_data(sym, days=config.LOOKBACK_DAYS)
        if df.empty:
            continue
        sigs = indicators.compute_signals(df)
        prices[sym] = sigs["price"]

        sig, reason = strategy.generate_exit_signal(
            sym, sigs, pos.entry_price, pos.stop_loss, pos.target
        )
        if sig == "SELL":
            result = _portfolio.close_position(sym, sigs["price"], reason)
            if result:
                tid = _open_trade_ids.pop(sym, None)
                if tid:
                    trade_logger.log_trade_closed(
                        tid, result["exit_price"], result["pnl"],
                        result["pnl_pct"], reason,
                    )
                learning.record_trade_outcome(
                    result["signals_used"], result["pnl"], result["pnl_pct"]
                )
                learning.write_trade_note(
                    sym, "LONG", result["entry_price"], result["exit_price"],
                    result["pnl"], result["pnl_pct"], reason, result["signals_used"]
                )
                learning.refresh_lessons()
                logger.info("EXIT %s | PnL ₹%.2f (%.2f%%) | %s",
                            sym, result["pnl"], result["pnl_pct"], reason)

    # ── 2. Update trailing stops ──────────────────────────────────────────────
    moved = _portfolio.update_trailing_stops(prices)
    for sym, new_stop in moved:
        logger.info("Trailing stop updated for %s → %.2f", sym, new_stop)

    # ── 3. Scan for new entries ───────────────────────────────────────────────
    nav = _portfolio.net_value(prices)
    for sym in config.WATCHLIST:
        if sym in _portfolio.positions:
            continue   # already holding

        df = data_fetcher.get_historical_data(sym, days=config.LOOKBACK_DAYS)
        if df.empty or len(df) < 55:
            continue

        sigs  = indicators.compute_signals(df)
        price = sigs["price"]
        prices[sym] = price

        sig, score, reasons = strategy.generate_entry_signal(
            sym, sigs, market_trend,
            has_position=(sym in _portfolio.positions),
            pattern_stats=pattern_stats,
        )

        trade_logger.log_scan(sym, price, score, sig, reasons)
        learning.write_scan_observation(sym, price, score, sig, reasons)

        if sig == "BUY":
            qty, sl, tgt = strategy.calculate_position_size(nav, price, sigs["atr"])
            pos = _portfolio.open_position(sym, price, qty, sl, tgt, reasons)
            if pos:
                tid = trade_logger.log_trade_opened(sym, price, qty, reasons, score)
                _open_trade_ids[sym] = tid
                logger.info("ENTER %s | qty=%d @ ₹%.2f | SL=%.2f | TGT=%.2f | score=%d",
                            sym, qty, price, sl, tgt, score)

    stats = _portfolio.stats(prices)
    logger.info("Portfolio → NAV ₹%.2f | Total PnL ₹%.2f (%.2f%%)",
                stats["nav"], stats["total_pnl"], stats["total_return_pct"])


# ── Daily summary ─────────────────────────────────────────────────────────────

def daily_summary():
    """Called at 15:45 IST each weekday."""
    logger.info("── Daily summary ──")
    market_trend = data_fetcher.get_nifty_trend()
    prices = data_fetcher.get_multiple_prices(list(_portfolio.positions.keys()))
    stats  = _portfolio.stats(prices)

    perf   = trade_logger.get_performance_stats()
    date_s = _ist_now().strftime("%Y-%m-%d")

    events = []
    if perf.get("total_trades", 0):
        events.append(f"All-time: {perf['total_trades']} trades, "
                      f"win rate {perf['win_rate_pct']}%, "
                      f"profit factor {perf['profit_factor']}")

    learning.write_daily_observation(market_trend, stats, events)
    learning.refresh_lessons()

    trade_logger.save_daily_summary(
        date_s, stats["nav"], stats["realised_pnl"],
        stats["unrealised_pnl"], stats["open_positions"],
        notes="; ".join(events),
    )
    logger.info("Daily summary saved for %s", date_s)


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
    logger.info("Trading Bot starting up")
    logger.info("=" * 60)

    # Init
    trade_logger.init_db()
    _portfolio = Portfolio()

    # Restore open trade IDs from DB so restarts track correctly
    for row in trade_logger.get_open_trades():
        _open_trade_ids[row["symbol"]] = row["id"]
    if _open_trade_ids:
        logger.info("Restored %d open trade IDs from DB", len(_open_trade_ids))

    stats = _portfolio.stats()
    logger.info("Portfolio: NAV ₹%.2f | Open positions: %d",
                stats["nav"], stats["open_positions"])

    # Start web dashboard in background thread
    dash_thread = threading.Thread(target=_start_dashboard, daemon=True)
    dash_thread.start()

    # Scheduler
    scheduler = BackgroundScheduler(timezone=_IST)

    # Market scan – every SCAN_INTERVAL_MIN during weekdays
    scheduler.add_job(
        market_scan,
        trigger=IntervalTrigger(minutes=config.SCAN_INTERVAL_MIN, timezone=_IST),
        id="market_scan",
        coalesce=True,
        max_instances=1,
    )

    # Daily summary – 15:45 IST weekdays
    scheduler.add_job(
        daily_summary,
        trigger=CronTrigger(day_of_week="mon-fri", hour=15, minute=45,
                            timezone=_IST),
        id="daily_summary",
        coalesce=True,
    )

    scheduler.start()
    logger.info("Scheduler started. Scan every %d min. Press Ctrl+C to stop.",
                config.SCAN_INTERVAL_MIN)

    # Run one scan immediately at startup if market is open
    if data_fetcher.is_market_open():
        market_scan()

    # Graceful shutdown
    def _shutdown(signum, frame):
        logger.info("Shutdown signal received – stopping scheduler")
        scheduler.shutdown(wait=False)
        sys.exit(0)

    signal.signal(signal.SIGINT,  _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    # Keep main thread alive
    import time
    while True:
        time.sleep(60)


if __name__ == "__main__":
    main()
