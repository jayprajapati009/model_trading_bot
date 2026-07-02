import os

# ── Capital & Portfolio ────────────────────────────────────────────────────────
INITIAL_CAPITAL    = 100_000.0   # 1 Lakh INR
MAX_POSITION_PCT   = 0.15        # max 15% of portfolio in one stock
MAX_POSITIONS      = 8           # max concurrent open positions
RISK_PER_TRADE_PCT = 0.02        # risk 2% of current portfolio per trade

# ── Market hours (IST) ────────────────────────────────────────────────────────
TIMEZONE           = "Asia/Kolkata"
MARKET_OPEN        = (9, 15)     # (hour, minute)
MARKET_CLOSE       = (15, 30)
SCAN_INTERVAL_MIN  = 15          # scan every N minutes during market hours

# ── Watchlist (NSE stocks + Nifty50 index) ────────────────────────────────────
WATCHLIST = [
    "RELIANCE.NS", "TCS.NS",     "INFY.NS",      "HDFCBANK.NS",
    "ICICIBANK.NS","SBIN.NS",    "BAJFINANCE.NS", "WIPRO.NS",
    "AXISBANK.NS", "KOTAKBANK.NS","HINDUNILVR.NS","ITC.NS",
    "BHARTIARTL.NS","SUNPHARMA.NS","MARUTI.NS",   "TITAN.NS",
    "ASIANPAINT.NS","ULTRACEMCO.NS","TECHM.NS",   "HCLTECH.NS",
]
NIFTY_SYMBOL = "^NSEI"

# ── Technical indicator parameters ────────────────────────────────────────────
EMA_FAST         = 9
EMA_SLOW         = 21
EMA_TREND        = 50
RSI_PERIOD       = 14
RSI_OVERSOLD     = 35
RSI_OVERBOUGHT   = 65
ATR_PERIOD       = 14
VOLUME_AVG_PERIOD= 20

# ── Risk management ───────────────────────────────────────────────────────────
STOP_LOSS_PCT       = 0.03    # initial stop 3% below entry
TARGET_PCT          = 0.06    # profit target 6% above entry  (2:1 R:R)
TRAILING_STOP_PCT   = 0.015   # trailing stop activates once price up 3%

# ── Strategy thresholds ───────────────────────────────────────────────────────
MIN_CONFIDENCE      = 60      # minimum score (0-100) to open a position
LOOKBACK_DAYS       = 90      # days of history to fetch for analysis
SUPPORT_LOOKBACK    = 20      # candles to detect support/resistance

# ── File paths ────────────────────────────────────────────────────────────────
BASE_DIR            = os.path.dirname(os.path.abspath(__file__))
DATA_DIR            = os.path.join(BASE_DIR, "data")
NOTES_DIR           = os.path.join(DATA_DIR, "notes")
LOGS_DIR            = os.path.join(BASE_DIR, "logs")

PORTFOLIO_FILE      = os.path.join(DATA_DIR, "portfolio.json")
DB_FILE             = os.path.join(DATA_DIR, "trades.db")
PATTERN_STATS_FILE  = os.path.join(DATA_DIR, "pattern_stats.json")
JOURNAL_FILE        = os.path.join(NOTES_DIR, "trading_journal.md")
LESSONS_FILE        = os.path.join(NOTES_DIR, "lessons_learned.md")
