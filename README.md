# Model Trading Bot

A self-learning model portfolio bot for Indian NSE stocks. No real money is used — it simulates trades on a virtual ₹1,00,000 portfolio using live prices from Yahoo Finance, runs 24/7 on a Digital Ocean droplet, and gets smarter over time by tracking which signals actually work.

---

## Features

- **Virtual portfolio** — ₹1 Lakh starting capital, tracks cash, positions, realised + unrealised PnL
- **Live NSE prices** — fetches OHLCV data from Yahoo Finance (`.NS` symbols)
- **Technical analysis** — EMA 9/21/50, RSI, ATR, support/resistance, candlestick patterns (Hammer, Engulfing, Doji, Morning Star, etc.)
- **Risk management** — 2% risk per trade, 3% stop loss, 6% target (2:1 R:R), auto trailing stop
- **Self-learning** — tracks win rate per signal in `data/pattern_stats.json`; writes a daily trading journal and distills lessons over time
- **Web dashboard** — Flask app on port 8080 with live portfolio, trade history, pattern stats, and journal
- **Always-on deployment** — runs as a systemd service on a Digital Ocean droplet, auto-restarts on crash

---

## Architecture

```
trading_bot/
├── main.py              # Entry point — scheduler + bot loop
├── config.py            # All tunable parameters
├── dashboard.py         # Flask web dashboard
├── requirements.txt
├── setup.sh             # One-shot droplet installer
└── modules/
    ├── data_fetcher.py  # Yahoo Finance integration
    ├── indicators.py    # EMA, RSI, ATR, support/resistance, patterns
    ├── strategy.py      # Signal scoring (0–100), position sizing, exit signals
    ├── portfolio.py     # Virtual portfolio (JSON-persisted, survives restarts)
    ├── trade_logger.py  # SQLite trade log
    └── learning.py      # Pattern stats, journal, lessons file
```

### Runtime data (gitignored, created on first run)
```
data/
├── portfolio.json        # Current portfolio state
├── trades.db             # Full trade history (SQLite)
├── pattern_stats.json    # Per-signal win rate tracking
└── notes/
    ├── trading_journal.md  # Daily observations + trade notes
    └── lessons_learned.md  # Auto-generated from pattern stats
logs/
└── trading_bot.log
```

---

## Strategy

Entry signals are scored 0–100 across seven factors:

| Factor | Points |
|--------|--------|
| Fresh EMA 9/21 crossover | +20 |
| Price above all three EMAs (bullish stack) | +15 |
| RSI in momentum zone (40–60) | +15 |
| Nifty50 trend is bullish | +15 |
| Bullish candlestick pattern (weighted by past win rate) | 0–15 |
| Near support level | +10 |
| Volume spike (>1.5× average) | +10 |

Deductions: overbought RSI (−20), price below 50 EMA (−15), bearish pattern (−10).

A position opens when score ≥ 60. Exits on stop loss, profit target, EMA death cross, or RSI overbought + bearish pattern.

---

## Self-Learning

After every closed trade, the bot updates the win rate for each signal that triggered the entry. The next scan reads these stats and adjusts confidence weights — patterns with historically higher win rates boost the score more.

Every evening at 15:45 IST a daily summary is written. Once a signal has ≥5 trades, it appears in `lessons_learned.md` with a `RELIABLE / MIXED / AVOID` verdict.

---

## Deploy on Digital Ocean

### 1. Clone the repo on your droplet

```bash
git clone https://github.com/jayprajapati009/model_trading_bot.git
cd model_trading_bot
```

### 2. Run the setup script (as root)

```bash
bash setup.sh
```

This installs Python dependencies into a venv, creates the data directories, registers a systemd service, and starts the bot.

### 3. Check it's running

```bash
systemctl status trading_bot
journalctl -u trading_bot -f     # live logs
```

---

## Dashboard

The bot serves a web dashboard on port **8080**.

**Access via SSH tunnel from your laptop or phone:**

```bash
ssh -L 8080:localhost:8080 root@<YOUR_DROPLET_IP>
```

Then open **http://localhost:8080** in your browser.

| Route | Description |
|-------|-------------|
| `/` | Portfolio overview — NAV, PnL, open positions |
| `/trades` | Full closed trade history |
| `/patterns` | Per-signal win rates + lessons learned |
| `/journal` | Last 200 lines of the trading journal |
| `/api/stats` | JSON snapshot (for integrations) |

The dashboard auto-refreshes every 60 seconds.

---

## Configuration

All parameters live in `config.py`:

```python
INITIAL_CAPITAL    = 100_000   # starting cash (INR)
MAX_POSITIONS      = 8         # max concurrent open positions
RISK_PER_TRADE_PCT = 0.02      # risk 2% of portfolio per trade
STOP_LOSS_PCT      = 0.03      # 3% hard stop
TARGET_PCT         = 0.06      # 6% profit target
MIN_CONFIDENCE     = 60        # minimum score to open a position
SCAN_INTERVAL_MIN  = 15        # scan frequency during market hours
```

Watchlist (20 large-cap NSE stocks + Nifty50) is also in `config.py`.

---

## Useful Commands

```bash
# Restart the bot
systemctl restart trading_bot

# Stop the bot
systemctl stop trading_bot

# Pull latest code and restart
git pull && systemctl restart trading_bot

# Read today's journal
cat data/notes/trading_journal.md | tail -50

# View current lessons
cat data/notes/lessons_learned.md
```

---

## Watchlist

20 large-cap NSE stocks across sectors:

`RELIANCE` · `TCS` · `INFY` · `HDFCBANK` · `ICICIBANK` · `SBIN` · `BAJFINANCE` · `WIPRO` · `AXISBANK` · `KOTAKBANK` · `HINDUNILVR` · `ITC` · `BHARTIARTL` · `SUNPHARMA` · `MARUTI` · `TITAN` · `ASIANPAINT` · `ULTRACEMCO` · `TECHM` · `HCLTECH`

---

## Disclaimer

This is a **model portfolio only**. No real trades are placed and no real money is at risk. It is intended for learning and strategy development purposes.
