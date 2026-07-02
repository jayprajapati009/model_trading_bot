"""
Flask dashboard for the trading bot.
Accessible at http://<droplet-ip>:8080  (or via SSH port forward).

Routes:
  GET /             – main dashboard (portfolio overview)
  GET /trades       – closed trade history
  GET /patterns     – pattern performance stats
  GET /journal      – last 100 lines of trading journal
  GET /api/stats    – JSON portfolio snapshot (for polling)
"""

import json
import os
from datetime import datetime
from functools import lru_cache

from flask import Flask, jsonify, render_template_string

import config
from modules import data_fetcher, learning, trade_logger


# ── HTML templates ────────────────────────────────────────────────────────────

_BASE_CSS = """
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: 'Segoe UI', Arial, sans-serif; background: #0d1117; color: #c9d1d9; }
  nav { background: #161b22; border-bottom: 1px solid #30363d; padding: 12px 24px;
        display: flex; align-items: center; gap: 24px; }
  nav a { color: #58a6ff; text-decoration: none; font-size: 14px; }
  nav a:hover { text-decoration: underline; }
  nav .brand { font-weight: 700; font-size: 18px; color: #f0f6fc; margin-right: auto; }
  .container { max-width: 1200px; margin: 0 auto; padding: 24px; }
  h2 { color: #f0f6fc; margin-bottom: 16px; font-size: 18px; }
  .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 16px; margin-bottom: 28px; }
  .card { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 16px; }
  .card .label { font-size: 12px; color: #8b949e; margin-bottom: 6px; text-transform: uppercase; letter-spacing: 0.5px; }
  .card .value { font-size: 24px; font-weight: 700; color: #f0f6fc; }
  .card .value.green { color: #3fb950; }
  .card .value.red { color: #f85149; }
  table { width: 100%; border-collapse: collapse; background: #161b22;
          border: 1px solid #30363d; border-radius: 8px; overflow: hidden; }
  th { background: #21262d; color: #8b949e; font-size: 12px; text-transform: uppercase;
       letter-spacing: 0.5px; padding: 10px 14px; text-align: left; }
  td { padding: 10px 14px; border-top: 1px solid #21262d; font-size: 14px; }
  tr:hover td { background: #1c2128; }
  .badge { display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 12px; font-weight: 600; }
  .badge.green { background: #1a4731; color: #3fb950; }
  .badge.red { background: #3d1f1f; color: #f85149; }
  .badge.blue { background: #1a3050; color: #58a6ff; }
  .section { margin-bottom: 32px; }
  pre { background: #161b22; border: 1px solid #30363d; border-radius: 8px;
        padding: 16px; font-size: 13px; overflow-x: auto; white-space: pre-wrap; }
  .ts { font-size: 12px; color: #8b949e; margin-bottom: 16px; }
</style>
"""

_NAV = """
<nav>
  <span class="brand">📈 Trading Bot</span>
  <a href="/">Dashboard</a>
  <a href="/trades">Trade History</a>
  <a href="/patterns">Pattern Stats</a>
  <a href="/journal">Journal</a>
</nav>
"""

_INDEX_TMPL = """
<!DOCTYPE html><html><head><title>Trading Bot</title>{{ css|safe }}
<meta http-equiv="refresh" content="60">
</head><body>
{{ nav|safe }}
<div class="container">
  <p class="ts">Last updated: {{ ts }} &nbsp;|&nbsp; Auto-refresh every 60s &nbsp;|&nbsp;
     Market: <span class="badge {{ 'green' if market_open else 'red' }}">
     {{ 'OPEN' if market_open else 'CLOSED' }}</span>
     &nbsp;Nifty trend: <span class="badge blue">{{ market_trend }}</span>
  </p>

  <div class="section">
    <h2>Portfolio Overview</h2>
    <div class="cards">
      <div class="card">
        <div class="label">Net Asset Value</div>
        <div class="value">₹{{ stats.nav | int }}</div>
      </div>
      <div class="card">
        <div class="label">Cash</div>
        <div class="value">₹{{ stats.cash | int }}</div>
      </div>
      <div class="card">
        <div class="label">Realised PnL</div>
        <div class="value {{ 'green' if stats.realised_pnl >= 0 else 'red' }}">
          ₹{{ stats.realised_pnl | int }}</div>
      </div>
      <div class="card">
        <div class="label">Unrealised PnL</div>
        <div class="value {{ 'green' if stats.unrealised_pnl >= 0 else 'red' }}">
          ₹{{ stats.unrealised_pnl | int }}</div>
      </div>
      <div class="card">
        <div class="label">Total Return</div>
        <div class="value {{ 'green' if stats.total_return_pct >= 0 else 'red' }}">
          {{ stats.total_return_pct }}%</div>
      </div>
      <div class="card">
        <div class="label">Open Positions</div>
        <div class="value">{{ stats.open_positions }}</div>
      </div>
    </div>
  </div>

  <div class="section">
    <h2>Open Positions</h2>
    {% if positions %}
    <table>
      <tr><th>Symbol</th><th>Qty</th><th>Entry</th><th>Current</th>
          <th>Stop Loss</th><th>Target</th><th>PnL (₹)</th><th>PnL (%)</th><th>Since</th></tr>
      {% for p in positions %}
      <tr>
        <td><strong>{{ p.symbol }}</strong></td>
        <td>{{ p.qty }}</td>
        <td>₹{{ p.entry }}</td>
        <td>₹{{ p.current }}</td>
        <td>₹{{ p.stop_loss }}</td>
        <td>₹{{ p.target }}</td>
        <td class="{{ 'green' if p.pnl >= 0 else 'red' }}">{{ p.pnl }}</td>
        <td class="{{ 'green' if p.pnl_pct >= 0 else 'red' }}">{{ p.pnl_pct }}%</td>
        <td>{{ p.entry_date }}</td>
      </tr>
      {% endfor %}
    </table>
    {% else %}
    <p style="color:#8b949e">No open positions.</p>
    {% endif %}
  </div>

  <div class="section">
    <h2>Performance Stats</h2>
    <div class="cards">
      <div class="card"><div class="label">Total Trades</div>
        <div class="value">{{ perf.get('total_trades', 0) }}</div></div>
      <div class="card"><div class="label">Win Rate</div>
        <div class="value {{ 'green' if perf.get('win_rate_pct',0) >= 50 else 'red' }}">
          {{ perf.get('win_rate_pct', '-') }}%</div></div>
      <div class="card"><div class="label">Profit Factor</div>
        <div class="value">{{ perf.get('profit_factor', '-') }}</div></div>
      <div class="card"><div class="label">Total PnL</div>
        <div class="value {{ 'green' if perf.get('total_pnl',0) >= 0 else 'red' }}">
          ₹{{ perf.get('total_pnl', 0) }}</div></div>
      <div class="card"><div class="label">Avg Win</div>
        <div class="value green">₹{{ perf.get('avg_win', '-') }}</div></div>
      <div class="card"><div class="label">Avg Loss</div>
        <div class="value red">₹{{ perf.get('avg_loss', '-') }}</div></div>
    </div>
  </div>
</div>
</body></html>
"""

_TRADES_TMPL = """
<!DOCTYPE html><html><head><title>Trade History</title>{{ css|safe }}</head><body>
{{ nav|safe }}
<div class="container">
  <div class="section">
    <h2>Closed Trades</h2>
    {% if trades %}
    <table>
      <tr><th>#</th><th>Symbol</th><th>Entry</th><th>Exit</th>
          <th>Qty</th><th>PnL (₹)</th><th>PnL (%)</th><th>Exit Reason</th><th>Entry Date</th></tr>
      {% for t in trades %}
      <tr>
        <td>{{ t.id }}</td>
        <td><strong>{{ t.symbol }}</strong></td>
        <td>₹{{ "%.2f"|format(t.entry_price) }}</td>
        <td>₹{{ "%.2f"|format(t.exit_price) }}</td>
        <td>{{ t.quantity }}</td>
        <td class="{{ 'green' if t.pnl >= 0 else 'red' }}">{{ "%.2f"|format(t.pnl) }}</td>
        <td class="{{ 'green' if t.pnl_pct >= 0 else 'red' }}">{{ "%.2f"|format(t.pnl_pct) }}%</td>
        <td>{{ t.exit_reason }}</td>
        <td>{{ t.entry_date[:10] if t.entry_date else '' }}</td>
      </tr>
      {% endfor %}
    </table>
    {% else %}
    <p style="color:#8b949e">No closed trades yet.</p>
    {% endif %}
  </div>
</div>
</body></html>
"""

_PATTERNS_TMPL = """
<!DOCTYPE html><html><head><title>Pattern Stats</title>{{ css|safe }}</head><body>
{{ nav|safe }}
<div class="container">
  <div class="section">
    <h2>Pattern / Signal Performance</h2>
    {% if rows %}
    <table>
      <tr><th>Signal</th><th>Trades</th><th>Win Rate</th><th>Avg Return</th></tr>
      {% for r in rows %}
      <tr>
        <td>{{ r.signal }}</td>
        <td>{{ r.trades }}</td>
        <td class="{{ 'green' if r.win_rate|int >= 50 else 'red' }}">{{ r.win_rate }}</td>
        <td class="{{ 'green' if r.avg_return|float >= 0 else 'red' }}">{{ r.avg_return }}</td>
      </tr>
      {% endfor %}
    </table>
    {% else %}
    <p style="color:#8b949e">No pattern data yet. Close some trades first.</p>
    {% endif %}
  </div>
  <div class="section">
    <h2>Lessons Learned</h2>
    <pre>{{ lessons }}</pre>
  </div>
</div>
</body></html>
"""

_JOURNAL_TMPL = """
<!DOCTYPE html><html><head><title>Journal</title>{{ css|safe }}</head><body>
{{ nav|safe }}
<div class="container">
  <div class="section">
    <h2>Trading Journal (last 200 lines)</h2>
    <pre>{{ content }}</pre>
  </div>
</div>
</body></html>
"""


# ── App factory ───────────────────────────────────────────────────────────────

def create_app(portfolio, open_trade_ids: dict):
    app = Flask(__name__)
    app.config["TEMPLATES_AUTO_RELOAD"] = True

    @app.route("/")
    def index():
        market_open  = data_fetcher.is_market_open()
        market_trend = data_fetcher.get_nifty_trend()
        prices = data_fetcher.get_multiple_prices(list(portfolio.positions.keys()))
        stats  = portfolio.stats(prices)
        positions = portfolio.position_summary(prices)
        perf   = trade_logger.get_performance_stats()
        return render_template_string(
            _INDEX_TMPL,
            css=_BASE_CSS, nav=_NAV,
            ts=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            market_open=market_open,
            market_trend=market_trend,
            stats=stats,
            positions=positions,
            perf=perf,
        )

    @app.route("/trades")
    def trades():
        closed = trade_logger.get_all_closed_trades()
        return render_template_string(
            _TRADES_TMPL, css=_BASE_CSS, nav=_NAV, trades=closed
        )

    @app.route("/patterns")
    def patterns():
        rows    = learning.summarise_pattern_stats()
        lessons = learning.read_lessons()
        return render_template_string(
            _PATTERNS_TMPL, css=_BASE_CSS, nav=_NAV,
            rows=rows, lessons=lessons
        )

    @app.route("/journal")
    def journal():
        content = ""
        if os.path.exists(config.JOURNAL_FILE):
            with open(config.JOURNAL_FILE) as f:
                lines = f.readlines()
            content = "".join(lines[-200:])
        return render_template_string(
            _JOURNAL_TMPL, css=_BASE_CSS, nav=_NAV, content=content
        )

    @app.route("/api/stats")
    def api_stats():
        prices = data_fetcher.get_multiple_prices(list(portfolio.positions.keys()))
        return jsonify({
            "portfolio":  portfolio.stats(prices),
            "positions":  portfolio.position_summary(prices),
            "performance": trade_logger.get_performance_stats(),
            "market_open": data_fetcher.is_market_open(),
            "market_trend": data_fetcher.get_nifty_trend(),
            "timestamp": datetime.now().isoformat(),
        })

    return app
