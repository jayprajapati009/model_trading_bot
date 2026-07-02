"""
Flask dashboard — light mode, colorful charts (Chart.js via CDN), responsive layout.

Routes:
  GET /             – main dashboard (portfolio overview + charts)
  GET /trades       – closed trade history
  GET /patterns     – pattern performance stats
  GET /journal      – last 200 lines of trading journal
  GET /api/stats    – JSON portfolio snapshot
  GET /api/history  – daily NAV history for charting
"""

import json
import os
from datetime import datetime

from flask import Flask, jsonify, render_template_string

import config
from modules import data_fetcher, learning, trade_logger


# ── Shared assets ─────────────────────────────────────────────────────────────

_FONTS = '<link rel="preconnect" href="https://fonts.googleapis.com"><link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">'

_BASE_CSS = """
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  :root {
    --bg: #f5f7fa; --surface: #ffffff; --border: #e2e8f0;
    --text: #1a202c; --muted: #718096; --accent: #4f46e5;
    --green: #059669; --green-bg: #ecfdf5; --green-border: #a7f3d0;
    --red: #dc2626;   --red-bg: #fef2f2;   --red-border: #fecaca;
    --blue: #2563eb;  --blue-bg: #eff6ff;  --blue-border: #bfdbfe;
    --amber: #d97706; --amber-bg: #fffbeb; --amber-border: #fde68a;
    --purple: #7c3aed; --purple-bg: #f5f3ff; --purple-border: #ddd6fe;
    --card-shadow: 0 1px 3px rgba(0,0,0,.08), 0 1px 2px rgba(0,0,0,.05);
    --card-shadow-hover: 0 4px 12px rgba(0,0,0,.12);
  }
  body { font-family: 'Inter', sans-serif; background: var(--bg); color: var(--text); min-height: 100vh; }

  /* ── Nav ── */
  nav {
    background: var(--surface); border-bottom: 1px solid var(--border);
    padding: 0 28px; display: flex; align-items: center; gap: 0;
    height: 60px; position: sticky; top: 0; z-index: 100;
    box-shadow: 0 1px 3px rgba(0,0,0,.06);
  }
  .nav-brand { font-weight: 700; font-size: 17px; color: var(--text); margin-right: 32px; display: flex; align-items: center; gap: 8px; }
  .nav-brand span { font-size: 22px; }
  nav a { color: var(--muted); text-decoration: none; font-size: 14px; font-weight: 500;
          padding: 4px 12px; border-radius: 6px; transition: all .15s; }
  nav a:hover, nav a.active { background: var(--blue-bg); color: var(--blue); }
  .nav-right { margin-left: auto; display: flex; align-items: center; gap: 12px; font-size: 13px; color: var(--muted); }

  /* ── Layout ── */
  .container { max-width: 1280px; margin: 0 auto; padding: 28px 24px; }
  .page-header { margin-bottom: 24px; }
  .page-header h1 { font-size: 22px; font-weight: 700; }
  .page-header p { font-size: 14px; color: var(--muted); margin-top: 4px; }

  /* ── Cards ── */
  .cards-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 16px; margin-bottom: 28px; }
  .card {
    background: var(--surface); border: 1px solid var(--border);
    border-radius: 12px; padding: 20px;
    box-shadow: var(--card-shadow); transition: box-shadow .2s;
  }
  .card:hover { box-shadow: var(--card-shadow-hover); }
  .card-label { font-size: 12px; font-weight: 600; color: var(--muted); text-transform: uppercase; letter-spacing: .6px; margin-bottom: 8px; }
  .card-value { font-size: 28px; font-weight: 700; line-height: 1; }
  .card-sub { font-size: 13px; color: var(--muted); margin-top: 6px; }
  .card.green  { border-left: 4px solid var(--green);  background: linear-gradient(135deg,#fff 60%,var(--green-bg)); }
  .card.red    { border-left: 4px solid var(--red);    background: linear-gradient(135deg,#fff 60%,var(--red-bg)); }
  .card.blue   { border-left: 4px solid var(--blue);   background: linear-gradient(135deg,#fff 60%,var(--blue-bg)); }
  .card.purple { border-left: 4px solid var(--purple); background: linear-gradient(135deg,#fff 60%,var(--purple-bg)); }
  .card.amber  { border-left: 4px solid var(--amber);  background: linear-gradient(135deg,#fff 60%,var(--amber-bg)); }
  .text-green  { color: var(--green); }
  .text-red    { color: var(--red); }
  .text-blue   { color: var(--blue); }
  .text-purple { color: var(--purple); }
  .text-amber  { color: var(--amber); }

  /* ── Charts row ── */
  .charts-row { display: grid; grid-template-columns: 2fr 1fr; gap: 20px; margin-bottom: 28px; }
  @media(max-width:900px) { .charts-row { grid-template-columns: 1fr; } }
  .chart-card { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 20px; box-shadow: var(--card-shadow); }
  .chart-title { font-size: 15px; font-weight: 600; margin-bottom: 16px; display: flex; align-items: center; gap: 8px; }
  .chart-title .dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; }

  /* ── Tables ── */
  .section { margin-bottom: 32px; }
  .section-title { font-size: 16px; font-weight: 700; margin-bottom: 14px; display: flex; align-items: center; gap: 8px; }
  .table-wrap { overflow-x: auto; border-radius: 12px; border: 1px solid var(--border); box-shadow: var(--card-shadow); }
  table { width: 100%; border-collapse: collapse; background: var(--surface); font-size: 14px; }
  th { background: #f8fafc; color: var(--muted); font-size: 11px; font-weight: 700; text-transform: uppercase;
       letter-spacing: .6px; padding: 11px 16px; text-align: left; border-bottom: 1px solid var(--border); }
  td { padding: 12px 16px; border-bottom: 1px solid #f1f5f9; }
  tr:last-child td { border-bottom: none; }
  tbody tr:hover td { background: #f8fafc; }

  /* ── Badges ── */
  .badge { display: inline-flex; align-items: center; gap: 4px; padding: 3px 10px; border-radius: 20px; font-size: 12px; font-weight: 600; }
  .badge.green  { background: var(--green-bg);  color: var(--green);  border: 1px solid var(--green-border); }
  .badge.red    { background: var(--red-bg);    color: var(--red);    border: 1px solid var(--red-border); }
  .badge.blue   { background: var(--blue-bg);   color: var(--blue);   border: 1px solid var(--blue-border); }
  .badge.amber  { background: var(--amber-bg);  color: var(--amber);  border: 1px solid var(--amber-border); }
  .badge.purple { background: var(--purple-bg); color: var(--purple); border: 1px solid var(--purple-border); }

  /* ── Status bar ── */
  .status-bar { display: flex; align-items: center; gap: 16px; margin-bottom: 24px; flex-wrap: wrap; }
  .status-bar .ts { font-size: 13px; color: var(--muted); }
  .pulse { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
  .pulse.open  { background: var(--green); box-shadow: 0 0 0 2px var(--green-bg); animation: pulse 2s infinite; }
  .pulse.closed { background: #cbd5e1; }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.5} }

  /* ── Progress bars ── */
  .progress-wrap { margin-top: 8px; }
  .progress-bar { height: 6px; border-radius: 3px; background: #e2e8f0; overflow: hidden; }
  .progress-fill { height: 100%; border-radius: 3px; transition: width .6s ease; }

  /* ── Journal ── */
  pre { background: #f8fafc; border: 1px solid var(--border); border-radius: 10px;
        padding: 18px; font-size: 13px; line-height: 1.7; overflow-x: auto; white-space: pre-wrap;
        color: var(--text); font-family: 'JetBrains Mono', 'Fira Code', monospace; }

  /* ── Empty state ── */
  .empty { text-align: center; padding: 40px; color: var(--muted); }
  .empty .icon { font-size: 40px; margin-bottom: 12px; }
  .empty p { font-size: 14px; }

  /* ── Responsive ── */
  @media(max-width:640px) {
    .cards-grid { grid-template-columns: 1fr 1fr; }
    .card-value { font-size: 22px; }
  }
</style>
"""

def _nav(active: int) -> str:
    links = ['Dashboard', 'Trades', 'Patterns', 'Journal']
    hrefs = ['/', '/trades', '/patterns', '/journal']
    items = ""
    for i, (label, href) in enumerate(zip(links, hrefs)):
        cls = ' class="active"' if i == active else ""
        items += f'<a href="{href}"{cls}>{label}</a>'
    return (
        '<nav>'
        '<div class="nav-brand"><span>📈</span> TradingBot</div>'
        + items +
        '<div class="nav-right" id="clock"></div>'
        '</nav>'
        '<script>'
        '(function(){'
        'var el=document.getElementById("clock");'
        'if(el){setInterval(function(){el.textContent=new Date().toLocaleTimeString("en-IN",{timeZone:"Asia/Kolkata"})+" IST";},1000);}'
        '})();'
        '</script>'
    )


def _pnl_color(v: float) -> str:
    return 'green' if v >= 0 else 'red'


def _pnl_sign(v: float) -> str:
    return f'+₹{v:,.2f}' if v >= 0 else f'-₹{abs(v):,.2f}'


# ── Index / Dashboard ─────────────────────────────────────────────────────────

_INDEX_TMPL = """<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Trading Bot · Dashboard</title>
{{ fonts|safe }}{{ css|safe }}
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
</head><body>
{{ nav|safe }}
<div class="container">

  <!-- Status bar -->
  <div class="status-bar">
    <span class="ts">⏱ {{ ts }}</span>
    <span class="badge {{ 'green' if market_open else 'amber' }}">
      <span class="pulse {{ 'open' if market_open else 'closed' }}"></span>
      Market {{ 'OPEN' if market_open else 'CLOSED' }}
    </span>
    <span class="badge blue">Nifty50 {{ market_trend|upper }}</span>
    <span class="ts" style="margin-left:auto">Auto-refresh in <span id="countdown">60</span>s</span>
  </div>

  <!-- KPI cards -->
  <div class="cards-grid">
    <div class="card blue">
      <div class="card-label">Net Asset Value</div>
      <div class="card-value text-blue">₹{{ "{:,.0f}".format(stats.nav) }}</div>
      <div class="card-sub">Initial ₹1,00,000</div>
      <div class="progress-wrap">
        <div class="progress-bar"><div class="progress-fill" style="width:{{ [[stats.nav/100000*100,0]|max, 100]|min }}%;background:var(--blue)"></div></div>
      </div>
    </div>

    <div class="card {{ 'green' if stats.total_return_pct >= 0 else 'red' }}">
      <div class="card-label">Total Return</div>
      <div class="card-value text-{{ 'green' if stats.total_return_pct >= 0 else 'red' }}">
        {{ '+' if stats.total_return_pct >= 0 else '' }}{{ stats.total_return_pct }}%
      </div>
      <div class="card-sub">Since inception</div>
    </div>

    <div class="card {{ 'green' if stats.realised_pnl >= 0 else 'red' }}">
      <div class="card-label">Realised PnL</div>
      <div class="card-value text-{{ 'green' if stats.realised_pnl >= 0 else 'red' }}">
        {{ '+' if stats.realised_pnl >= 0 else '' }}₹{{ "{:,.0f}".format(stats.realised_pnl) }}
      </div>
      <div class="card-sub">Closed trades</div>
    </div>

    <div class="card {{ 'green' if stats.unrealised_pnl >= 0 else 'red' }}">
      <div class="card-label">Unrealised PnL</div>
      <div class="card-value text-{{ 'green' if stats.unrealised_pnl >= 0 else 'red' }}">
        {{ '+' if stats.unrealised_pnl >= 0 else '' }}₹{{ "{:,.0f}".format(stats.unrealised_pnl) }}
      </div>
      <div class="card-sub">Open positions</div>
    </div>

    <div class="card amber">
      <div class="card-label">Cash Available</div>
      <div class="card-value text-amber">₹{{ "{:,.0f}".format(stats.cash) }}</div>
      <div class="card-sub">{{ "{:.1f}".format(stats.cash / stats.nav * 100) }}% of NAV</div>
    </div>

    <div class="card purple">
      <div class="card-label">Open Positions</div>
      <div class="card-value text-purple">{{ stats.open_positions }}</div>
      <div class="card-sub">Max 8 allowed</div>
    </div>
  </div>

  <!-- Charts row -->
  <div class="charts-row">
    <div class="chart-card">
      <div class="chart-title">
        <span class="dot" style="background:#4f46e5"></span>
        Portfolio NAV vs Baseline (₹1L)
      </div>
      <canvas id="navChart" height="90"></canvas>
    </div>
    <div class="chart-card">
      <div class="chart-title">
        <span class="dot" style="background:#059669"></span>
        Win / Loss Distribution
      </div>
      <canvas id="winLossChart" height="180"></canvas>
    </div>
  </div>

  <!-- Performance stats row -->
  <div class="charts-row" style="grid-template-columns:1fr 1fr 1fr">
    <div class="chart-card" style="text-align:center">
      <div class="chart-title" style="justify-content:center"><span class="dot" style="background:#d97706"></span>Win Rate</div>
      <div style="font-size:48px;font-weight:800;color:var(--{{ 'green' if perf.get('win_rate_pct',0) >= 50 else 'red' }})">
        {{ perf.get('win_rate_pct','–') }}%
      </div>
      <div style="font-size:13px;color:var(--muted);margin-top:4px">
        {{ perf.get('winning_trades',0) }}W · {{ perf.get('losing_trades',0) }}L of {{ perf.get('total_trades',0) }} trades
      </div>
    </div>
    <div class="chart-card" style="text-align:center">
      <div class="chart-title" style="justify-content:center"><span class="dot" style="background:#2563eb"></span>Profit Factor</div>
      <div style="font-size:48px;font-weight:800;color:var(--{{ 'green' if perf.get('profit_factor',0) >= 1 else 'red' }})">
        {{ perf.get('profit_factor','–') }}
      </div>
      <div style="font-size:13px;color:var(--muted);margin-top:4px">Gross profit / Gross loss</div>
    </div>
    <div class="chart-card" style="text-align:center">
      <div class="chart-title" style="justify-content:center"><span class="dot" style="background:#7c3aed"></span>Avg Win vs Avg Loss</div>
      <div style="font-size:22px;font-weight:700;color:var(--green);margin-top:4px">+₹{{ perf.get('avg_win','–') }}</div>
      <div style="font-size:13px;color:var(--muted)">avg win</div>
      <div style="font-size:22px;font-weight:700;color:var(--red);margin-top:8px">-₹{{ "{:.2f}".format(perf.get('avg_loss',0)|abs) if perf.get('avg_loss') else '–' }}</div>
      <div style="font-size:13px;color:var(--muted)">avg loss</div>
    </div>
  </div>

  <!-- Open Positions table -->
  <div class="section">
    <div class="section-title">📂 Open Positions</div>
    {% if positions %}
    <div class="table-wrap">
    <table>
      <thead><tr>
        <th>Symbol</th><th>Qty</th><th>Entry</th><th>Current</th>
        <th>Stop Loss</th><th>Target</th><th>PnL (₹)</th><th>PnL (%)</th><th>Since</th>
      </tr></thead>
      <tbody>
      {% for p in positions %}
      <tr>
        <td><strong>{{ p.symbol.replace('.NS','') }}</strong><br><span style="font-size:11px;color:var(--muted)">NSE</span></td>
        <td>{{ p.qty }}</td>
        <td>₹{{ "{:,.2f}".format(p.entry) }}</td>
        <td><strong>₹{{ "{:,.2f}".format(p.current) }}</strong></td>
        <td><span style="color:var(--red)">₹{{ "{:,.2f}".format(p.stop_loss) }}</span></td>
        <td><span style="color:var(--green)">₹{{ "{:,.2f}".format(p.target) }}</span></td>
        <td class="text-{{ 'green' if p.pnl >= 0 else 'red' }}"><strong>{{ '+' if p.pnl >= 0 else '' }}{{ "{:,.2f}".format(p.pnl) }}</strong></td>
        <td>
          <span class="badge {{ 'green' if p.pnl_pct >= 0 else 'red' }}">
            {{ '+' if p.pnl_pct >= 0 else '' }}{{ p.pnl_pct }}%
          </span>
        </td>
        <td style="font-size:12px;color:var(--muted)">{{ p.entry_date }}</td>
      </tr>
      {% endfor %}
      </tbody>
    </table>
    </div>
    {% else %}
    <div class="empty"><div class="icon">📭</div><p>No open positions. Bot is watching the market.</p></div>
    {% endif %}
  </div>

</div>

<script>
// Countdown
var secs = 60;
setInterval(function(){
  secs--;
  var el = document.getElementById('countdown');
  if(el) el.textContent = secs;
  if(secs <= 0) location.reload();
}, 1000);

// NAV chart
fetch('/api/history').then(r=>r.json()).then(function(h){
  var ctx = document.getElementById('navChart').getContext('2d');
  var labels = h.dates || [];
  var navs = h.navs || [];
  var baseline = labels.map(function(){ return 100000; });
  new Chart(ctx, {
    type: 'line',
    data: {
      labels: labels,
      datasets: [
        {
          label: 'NAV',
          data: navs,
          borderColor: '#4f46e5',
          backgroundColor: 'rgba(79,70,229,0.08)',
          fill: true,
          tension: 0.4,
          pointRadius: navs.length > 30 ? 0 : 3,
          borderWidth: 2.5,
        },
        {
          label: 'Baseline ₹1L',
          data: baseline,
          borderColor: '#cbd5e1',
          borderDash: [5, 4],
          borderWidth: 1.5,
          pointRadius: 0,
          fill: false,
        }
      ]
    },
    options: {
      responsive: true,
      plugins: {
        legend: { position: 'top', labels: { boxWidth: 12, font: { size: 12 } } },
        tooltip: {
          callbacks: {
            label: function(ctx){ return ' ₹' + ctx.parsed.y.toLocaleString('en-IN', {maximumFractionDigits:0}); }
          }
        }
      },
      scales: {
        x: { grid: { display: false }, ticks: { font: { size: 11 }, maxTicksLimit: 8 } },
        y: {
          grid: { color: '#f1f5f9' },
          ticks: {
            font: { size: 11 },
            callback: function(v){ return '₹' + (v/1000).toFixed(0) + 'k'; }
          }
        }
      }
    }
  });
});

// Win/Loss donut
(function(){
  var wins = {{ perf.get('winning_trades', 0) }};
  var losses = {{ perf.get('losing_trades', 0) }};
  var ctx = document.getElementById('winLossChart').getContext('2d');
  if(wins === 0 && losses === 0){
    ctx.font = '14px Inter';
    ctx.fillStyle = '#94a3b8';
    ctx.textAlign = 'center';
    ctx.fillText('No trades yet', ctx.canvas.width/2, ctx.canvas.height/2);
    return;
  }
  new Chart(ctx, {
    type: 'doughnut',
    data: {
      labels: ['Winning', 'Losing'],
      datasets: [{
        data: [wins, losses],
        backgroundColor: ['#10b981', '#ef4444'],
        borderColor: ['#fff','#fff'],
        borderWidth: 3,
        hoverOffset: 6,
      }]
    },
    options: {
      cutout: '68%',
      plugins: {
        legend: { position: 'bottom', labels: { boxWidth: 12, font: { size: 13 } } },
        tooltip: {
          callbacks: {
            label: function(ctx){
              var total = wins + losses;
              var pct = total > 0 ? (ctx.parsed/total*100).toFixed(1) : 0;
              return ' ' + ctx.label + ': ' + ctx.parsed + ' (' + pct + '%)';
            }
          }
        }
      }
    }
  });
})();
</script>
</body></html>
"""

# ── Trades page ───────────────────────────────────────────────────────────────

_TRADES_TMPL = """<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Trading Bot · Trade History</title>
{{ fonts|safe }}{{ css|safe }}
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
</head><body>
{{ nav|safe }}
<div class="container">
  <div class="page-header">
    <h1>Trade History</h1>
    <p>All closed positions with entry / exit details and PnL breakdown.</p>
  </div>

  {% if trades %}
  <!-- Cumulative PnL chart -->
  <div class="chart-card" style="margin-bottom:28px">
    <div class="chart-title"><span class="dot" style="background:#4f46e5"></span>Cumulative PnL Over Trades</div>
    <canvas id="cumPnlChart" height="70"></canvas>
  </div>

  <div class="section">
    <div class="table-wrap">
    <table>
      <thead><tr>
        <th>#</th><th>Symbol</th><th>Entry Date</th><th>Exit Date</th>
        <th>Qty</th><th>Entry</th><th>Exit</th><th>PnL (₹)</th><th>PnL (%)</th><th>Exit Reason</th>
      </tr></thead>
      <tbody>
      {% for t in trades %}
      <tr>
        <td style="color:var(--muted);font-size:12px">{{ t.id }}</td>
        <td><strong>{{ t.symbol.replace('.NS','') }}</strong></td>
        <td style="font-size:12px;color:var(--muted)">{{ t.entry_date[:10] if t.entry_date else '–' }}</td>
        <td style="font-size:12px;color:var(--muted)">{{ t.exit_date[:10] if t.exit_date else '–' }}</td>
        <td>{{ t.quantity }}</td>
        <td>₹{{ "{:,.2f}".format(t.entry_price) }}</td>
        <td>₹{{ "{:,.2f}".format(t.exit_price) }}</td>
        <td class="text-{{ 'green' if t.pnl >= 0 else 'red' }}"><strong>{{ '+' if t.pnl >= 0 else '' }}{{ "{:,.2f}".format(t.pnl) }}</strong></td>
        <td><span class="badge {{ 'green' if t.pnl_pct >= 0 else 'red' }}">{{ '+' if t.pnl_pct >= 0 else '' }}{{ "{:.2f}".format(t.pnl_pct) }}%</span></td>
        <td style="font-size:12px;color:var(--muted)">{{ t.exit_reason }}</td>
      </tr>
      {% endfor %}
      </tbody>
    </table>
    </div>
  </div>

  <script>
  var pnls = [{% for t in trades|reverse %}{{ t.pnl }},{% endfor %}];
  var cum = []; var running = 0;
  pnls.forEach(function(p){ running += p; cum.push(running); });
  var labels = pnls.map(function(_,i){ return 'Trade '+(i+1); });
  var colors = cum.map(function(v){ return v >= 0 ? 'rgba(16,185,129,0.15)' : 'rgba(239,68,68,0.15)'; });
  var borderColors = cum.map(function(v){ return v >= 0 ? '#10b981' : '#ef4444'; });
  new Chart(document.getElementById('cumPnlChart').getContext('2d'), {
    type: 'bar',
    data: {
      labels: labels,
      datasets: [{
        label: 'Cumulative PnL',
        data: cum,
        backgroundColor: colors,
        borderColor: borderColors,
        borderWidth: 2,
        borderRadius: 4,
      }]
    },
    options: {
      responsive: true,
      plugins: { legend: { display: false }, tooltip: { callbacks: { label: function(c){ return ' ₹' + c.parsed.y.toFixed(2); } } } },
      scales: {
        x: { display: false },
        y: { grid: { color: '#f1f5f9' }, ticks: { font: { size: 11 }, callback: function(v){ return '₹'+v.toFixed(0); } } }
      }
    }
  });
  </script>
  {% else %}
  <div class="empty"><div class="icon">📊</div><p>No closed trades yet. The bot will log completed trades here.</p></div>
  {% endif %}
</div>
</body></html>
"""

# ── Patterns page ─────────────────────────────────────────────────────────────

_PATTERNS_TMPL = """<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Trading Bot · Pattern Stats</title>
{{ fonts|safe }}{{ css|safe }}
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
</head><body>
{{ nav|safe }}
<div class="container">
  <div class="page-header">
    <h1>Pattern & Signal Performance</h1>
    <p>The bot tracks which signals led to wins or losses and adjusts its confidence weights over time.</p>
  </div>

  {% if rows %}
  <div class="chart-card" style="margin-bottom:28px">
    <div class="chart-title"><span class="dot" style="background:#4f46e5"></span>Win Rate by Signal</div>
    <canvas id="patternChart" height="80"></canvas>
  </div>

  <div class="section">
    <div class="table-wrap">
    <table>
      <thead><tr><th>Signal</th><th>Trades</th><th>Win Rate</th><th>Avg Return</th><th>Verdict</th></tr></thead>
      <tbody>
      {% for r in rows %}
      <tr>
        <td><strong>{{ r.signal.replace('_',' ').title() }}</strong></td>
        <td>{{ r.trades }}</td>
        <td>
          <div style="display:flex;align-items:center;gap:10px">
            <div class="progress-bar" style="width:80px">
              <div class="progress-fill" style="width:{{ r.win_rate.replace('%','') }}%;background:{{ '#10b981' if r.win_rate.replace('%','')|float >= 50 else '#ef4444' }}"></div>
            </div>
            <span class="text-{{ 'green' if r.win_rate.replace('%','')|float >= 50 else 'red' }}">{{ r.win_rate }}</span>
          </div>
        </td>
        <td class="text-{{ 'green' if r.avg_return.replace('%','')|float >= 0 else 'red' }}">{{ r.avg_return }}</td>
        <td>
          {% set wr = r.win_rate.replace('%','')|float %}
          {% if wr >= 60 %}<span class="badge green">✓ Reliable</span>
          {% elif wr >= 45 %}<span class="badge amber">⚠ Mixed</span>
          {% else %}<span class="badge red">✗ Avoid</span>{% endif %}
        </td>
      </tr>
      {% endfor %}
      </tbody>
    </table>
    </div>
  </div>

  <script>
  var labels = [{% for r in rows %}'{{ r.signal.replace("_"," ")|title }}',{% endfor %}];
  var wrs = [{% for r in rows %}{{ r.win_rate.replace('%','') }},{% endfor %}];
  var colors = wrs.map(function(w){ return w>=60?'rgba(16,185,129,0.8)':w>=45?'rgba(217,119,6,0.8)':'rgba(239,68,68,0.8)'; });
  new Chart(document.getElementById('patternChart').getContext('2d'), {
    type: 'bar',
    data: {
      labels: labels,
      datasets: [{
        label: 'Win Rate %',
        data: wrs,
        backgroundColor: colors,
        borderRadius: 6,
        borderSkipped: false,
      }]
    },
    options: {
      indexAxis: 'y',
      responsive: true,
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { label: function(c){ return ' Win rate: '+c.parsed.x+'%'; } } },
        annotation: {}
      },
      scales: {
        x: { min: 0, max: 100, grid: { color: '#f1f5f9' },
             ticks: { callback: function(v){ return v+'%'; }, font: { size: 11 } } },
        y: { grid: { display: false }, ticks: { font: { size: 12 } } }
      }
    }
  });
  </script>
  {% else %}
  <div class="empty"><div class="icon">🔬</div><p>No pattern data yet. Close some trades to see signal performance.</p></div>
  {% endif %}

  <div class="section">
    <div class="section-title">📝 Lessons Learned</div>
    <pre>{{ lessons }}</pre>
  </div>
</div>
</body></html>
"""

# ── Journal page ──────────────────────────────────────────────────────────────

_JOURNAL_TMPL = """<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Trading Bot · Journal</title>
{{ fonts|safe }}{{ css|safe }}
</head><body>
{{ nav|safe }}
<div class="container">
  <div class="page-header">
    <h1>Trading Journal</h1>
    <p>Daily observations, trade notes, and market commentary written by the bot.</p>
  </div>
  <div class="section">
    <pre>{{ content if content else 'No journal entries yet. The bot will start writing after its first trade.' }}</pre>
  </div>
</div>
</body></html>
"""


# ── App factory ───────────────────────────────────────────────────────────────

def create_app(portfolio, open_trade_ids: dict):
    app = Flask(__name__)

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
            fonts=_FONTS, css=_BASE_CSS, nav=_nav(0),
            ts=datetime.now().strftime("%d %b %Y, %I:%M %p"),
            market_open=market_open, market_trend=market_trend,
            stats=stats, positions=positions, perf=perf,
        )

    @app.route("/trades")
    def trades():
        closed = trade_logger.get_all_closed_trades()
        return render_template_string(
            _TRADES_TMPL, fonts=_FONTS, css=_BASE_CSS, nav=_nav(1), trades=closed
        )

    @app.route("/patterns")
    def patterns():
        rows    = learning.summarise_pattern_stats()
        lessons = learning.read_lessons()
        return render_template_string(
            _PATTERNS_TMPL, fonts=_FONTS, css=_BASE_CSS, nav=_nav(2),
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
            _JOURNAL_TMPL, fonts=_FONTS, css=_BASE_CSS, nav=_nav(3), content=content
        )

    @app.route("/api/stats")
    def api_stats():
        prices = data_fetcher.get_multiple_prices(list(portfolio.positions.keys()))
        return jsonify({
            "portfolio":   portfolio.stats(prices),
            "positions":   portfolio.position_summary(prices),
            "performance": trade_logger.get_performance_stats(),
            "market_open": data_fetcher.is_market_open(),
            "market_trend": data_fetcher.get_nifty_trend(),
            "timestamp":   datetime.now().isoformat(),
        })

    @app.route("/api/history")
    def api_history():
        """Return daily NAV history for the chart from trade_logger daily_summary table."""
        try:
            from modules.trade_logger import _connect
            with _connect() as conn:
                rows = conn.execute(
                    "SELECT date, nav FROM daily_summary ORDER BY date ASC LIMIT 180"
                ).fetchall()
            dates = [r[0] for r in rows]
            navs  = [r[1] for r in rows]
        except Exception:
            dates, navs = [], []

        # If no history yet, return current NAV as single point
        if not dates:
            prices = data_fetcher.get_multiple_prices(list(portfolio.positions.keys()))
            stats  = portfolio.stats(prices)
            dates  = [datetime.now().strftime("%Y-%m-%d")]
            navs   = [stats["nav"]]

        return jsonify({"dates": dates, "navs": navs})

    return app
