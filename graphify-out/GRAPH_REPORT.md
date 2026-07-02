# Graph Report - .  (2026-07-02)

## Corpus Check
- Corpus is ~8,450 words - fits in a single context window. You may not need a graph.

## Summary
- 163 nodes · 252 edges · 11 communities detected
- Extraction: 81% EXTRACTED · 19% INFERRED · 0% AMBIGUOUS · INFERRED: 47 edges (avg confidence: 0.79)
- Token cost: 1,850 input · 2,100 output

## Community Hubs (Navigation)
- [[_COMMUNITY_Bot Architecture & Config|Bot Architecture & Config]]
- [[_COMMUNITY_Portfolio Management|Portfolio Management]]
- [[_COMMUNITY_Trading Loop & Scheduler|Trading Loop & Scheduler]]
- [[_COMMUNITY_Technical Indicators|Technical Indicators]]
- [[_COMMUNITY_Self-Learning Engine|Self-Learning Engine]]
- [[_COMMUNITY_Strategy & Signal Scoring|Strategy & Signal Scoring]]
- [[_COMMUNITY_Market Data Fetcher|Market Data Fetcher]]
- [[_COMMUNITY_Trade Execution Logic|Trade Execution Logic]]
- [[_COMMUNITY_Web Dashboard|Web Dashboard]]
- [[_COMMUNITY_Global Config|Global Config]]
- [[_COMMUNITY_Package Init|Package Init]]

## God Nodes (most connected - your core abstractions)
1. `market_scan()` - 24 edges
2. `Portfolio` - 16 edges
3. `Model Trading Bot` - 16 edges
4. `Signal Scoring System (0–100, Seven Factors)` - 11 edges
5. `daily_summary()` - 10 edges
6. `compute_signals()` - 9 edges
7. `_connect()` - 8 edges
8. `Position` - 8 edges
9. `main()` - 7 edges
10. `detect_patterns()` - 7 edges

## Surprising Connections (you probably didn't know these)
- `Trading Bot – main entry point.  Runs the scan loop on a schedule during NSE mar` --uses--> `Portfolio`  [INFERRED]
  main.py → modules/portfolio.py
- `Called every SCAN_INTERVAL_MIN during market hours.` --uses--> `Portfolio`  [INFERRED]
  main.py → modules/portfolio.py
- `Called at 15:45 IST each weekday.` --uses--> `Portfolio`  [INFERRED]
  main.py → modules/portfolio.py
- `market_scan()` --calls--> `get_nifty_trend()`  [INFERRED]
  main.py → modules/data_fetcher.py
- `market_scan()` --calls--> `load_pattern_stats()`  [INFERRED]
  main.py → modules/learning.py

## Hyperedges (group relationships)
- **Signal-to-Trade Pipeline: indicators feed strategy scoring which triggers portfolio execution** — readme_indicators_py, readme_strategy_py, readme_portfolio_py, readme_trade_logger_py [INFERRED 0.90]
- **Self-Learning Feedback Loop: closed trades update pattern stats which adjust signal weights in next scan** — readme_trade_logger_py, readme_learning_py, readme_pattern_stats_json, readme_signal_scoring_system [INFERRED 0.88]
- **Dashboard aggregates portfolio state, trade history, and pattern stats for live display** — readme_dashboard_py, readme_portfolio_json, readme_trades_db, readme_pattern_stats_json [INFERRED 0.85]

## Communities

### Community 0 - "Bot Architecture & Config"
Cohesion: 0.08
Nodes (28): config.py (Tunable Parameters), dashboard.py (Flask Web Dashboard), modules/data_fetcher.py (Yahoo Finance Integration), modules/indicators.py (EMA, RSI, ATR, Support/Resistance, Patterns), main.py (Entry Point — Scheduler + Bot Loop), Model Trading Bot, data/portfolio.json (Portfolio State), modules/portfolio.py (Virtual Portfolio, JSON-Persisted) (+20 more)

### Community 1 - "Portfolio Management"
Cohesion: 0.14
Nodes (6): from_dict(), Portfolio, Position, Portfolio manager – tracks cash, open positions, and realised PnL. State is pers, Update trailing stops; return list of (symbol, new_stop) where stop moved., Ratchet up the stop loss when price makes new highs. Returns True if stop moved.

### Community 2 - "Trading Loop & Scheduler"
Cohesion: 0.18
Nodes (19): is_market_open(), Return True if the NSE is currently open for trading., daily_summary(), _ist_now(), main(), market_scan(), Trading Bot – main entry point.  Runs the scan loop on a schedule during NSE mar, Called at 15:45 IST each weekday. (+11 more)

### Community 3 - "Technical Indicators"
Cohesion: 0.15
Nodes (18): atr(), _body(), _candle_range(), compute_signals(), detect_patterns(), ema(), find_support_resistance(), _lower_wick() (+10 more)

### Community 4 - "Self-Learning Engine"
Cohesion: 0.15
Nodes (18): _append_to_file(), _generate_lessons(), load_pattern_stats(), Self-learning module.  The bot keeps two persistent artefacts:   1. pattern_stat, Write a daily market observation entry., Write a brief observation when a high-scoring stock is spotted., Rewrite lessons_learned.md based on current pattern stats., Return the current lessons file as a string (for logging/display). (+10 more)

### Community 5 - "Strategy & Signal Scoring"
Cohesion: 0.13
Nodes (18): Bullish Candlestick Patterns (Hammer, Engulfing, Doji, Morning Star, 0–15 pts weighted by win rate), EMA Bullish Stack (Price above all 3 EMAs, +15 pts), EMA 9/21 Crossover Signal (+20 pts), Entry Threshold (Score ≥ 60), Exit Conditions (Stop Loss, Target, EMA Death Cross, RSI Overbought + Bearish Pattern), modules/learning.py (Pattern Stats, Journal, Lessons), data/notes/lessons_learned.md (Auto-Generated Lessons), Nifty50 Trend Filter (+15 pts) (+10 more)

### Community 6 - "Market Data Fetcher"
Cohesion: 0.21
Nodes (13): _cached_download(), _download_with_retry(), get_current_price(), get_historical_data(), get_intraday_data(), get_multiple_prices(), get_nifty_trend(), Yahoo Finance data fetching with retry logic and a simple intraday cache. (+5 more)

### Community 7 - "Trade Execution Logic"
Cohesion: 0.22
Nodes (9): calculate_position_size(), generate_entry_signal(), generate_exit_signal(), Strategy engine – scores each stock and decides BUY / HOLD / SELL.  Scoring (0-1, Return (signal, score, reasons).     Only returns BUY if score >= MIN_CONFIDENCE, Check exit conditions for an open position.     Returns (signal, reason)., Risk-based position sizing:       risk_amount = portfolio_value * RISK_PER_TRADE, Score an entry opportunity.     Returns (score, reasons). (+1 more)

### Community 8 - "Web Dashboard"
Cohesion: 0.29
Nodes (3): create_app(), Flask dashboard — light mode, colorful charts (Chart.js via CDN), responsive lay, _start_dashboard()

### Community 9 - "Global Config"
Cohesion: 1.0
Nodes (0): 

### Community 10 - "Package Init"
Cohesion: 1.0
Nodes (0): 

## Knowledge Gaps
- **51 isolated node(s):** `Flask dashboard — light mode, colorful charts (Chart.js via CDN), responsive lay`, `Yahoo Finance data fetching with retry logic and a simple intraday cache.`, `Return daily OHLCV for the past `days` days.`, `Return 5-minute candles for today (market hours).`, `Return the latest close/price for a symbol.` (+46 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **Thin community `Global Config`** (1 nodes): `config.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Package Init`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `market_scan()` connect `Trading Loop & Scheduler` to `Portfolio Management`, `Technical Indicators`, `Self-Learning Engine`, `Market Data Fetcher`, `Trade Execution Logic`?**
  _High betweenness centrality (0.347) - this node is a cross-community bridge._
- **Why does `compute_signals()` connect `Technical Indicators` to `Trading Loop & Scheduler`?**
  _High betweenness centrality (0.141) - this node is a cross-community bridge._
- **Why does `Portfolio` connect `Portfolio Management` to `Trading Loop & Scheduler`?**
  _High betweenness centrality (0.060) - this node is a cross-community bridge._
- **Are the 20 inferred relationships involving `market_scan()` (e.g. with `is_market_open()` and `get_nifty_trend()`) actually correct?**
  _`market_scan()` has 20 INFERRED edges - model-reasoned connections that need verification._
- **Are the 4 inferred relationships involving `Portfolio` (e.g. with `Trading Bot – main entry point.  Runs the scan loop on a schedule during NSE mar` and `Called every SCAN_INTERVAL_MIN during market hours.`) actually correct?**
  _`Portfolio` has 4 INFERRED edges - model-reasoned connections that need verification._
- **Are the 7 inferred relationships involving `daily_summary()` (e.g. with `get_nifty_trend()` and `get_multiple_prices()`) actually correct?**
  _`daily_summary()` has 7 INFERRED edges - model-reasoned connections that need verification._
- **What connects `Flask dashboard — light mode, colorful charts (Chart.js via CDN), responsive lay`, `Yahoo Finance data fetching with retry logic and a simple intraday cache.`, `Return daily OHLCV for the past `days` days.` to the rest of the system?**
  _51 weakly-connected nodes found - possible documentation gaps or missing edges._