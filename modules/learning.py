"""
Self-learning module.

The bot keeps two persistent artefacts:
  1. pattern_stats.json  – win/loss/avg_return per pattern name.
                           Used by strategy.py to weight pattern signals.
  2. trading_journal.md  – human-readable daily observations.
  3. lessons_learned.md  – distilled rules that emerged from trade analysis.

After every closed trade the bot:
  • updates pattern_stats
  • appends a trade note to the journal
  • rewrites lessons_learned when a pattern crosses a confidence threshold
"""

import json
import logging
import os
from datetime import datetime
from typing import Any

import config

logger = logging.getLogger(__name__)


# ── Pattern stats ─────────────────────────────────────────────────────────────

def load_pattern_stats() -> dict:
    if not os.path.exists(config.PATTERN_STATS_FILE):
        return {}
    try:
        with open(config.PATTERN_STATS_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_pattern_stats(stats: dict):
    os.makedirs(os.path.dirname(config.PATTERN_STATS_FILE), exist_ok=True)
    with open(config.PATTERN_STATS_FILE, "w") as f:
        json.dump(stats, f, indent=2)


def record_trade_outcome(signals_used: list[str], pnl: float,
                         pnl_pct: float):
    """
    Called after a trade closes.
    Updates win/loss counts and running average return for each signal.
    """
    stats  = load_pattern_stats()
    is_win = pnl > 0

    for sig in signals_used:
        entry = stats.setdefault(sig, {
            "wins": 0, "losses": 0, "total_pnl": 0.0,
            "total_pnl_pct": 0.0, "win_rate": 0.5, "avg_return": 0.0,
        })
        if is_win:
            entry["wins"] += 1
        else:
            entry["losses"] += 1
        entry["total_pnl"]     += pnl
        entry["total_pnl_pct"] += pnl_pct
        total = entry["wins"] + entry["losses"]
        entry["win_rate"]   = round(entry["wins"] / total, 3)
        entry["avg_return"] = round(entry["total_pnl_pct"] / total, 2)

    _save_pattern_stats(stats)
    logger.debug("Pattern stats updated for signals: %s", signals_used)


# ── Journal ───────────────────────────────────────────────────────────────────

def _append_to_file(path: str, text: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a") as f:
        f.write(text + "\n")


def write_trade_note(symbol: str, direction: str, entry_price: float,
                     exit_price: float, pnl: float, pnl_pct: float,
                     exit_reason: str, signals_used: list[str]):
    """Append a trade closure note to the trading journal."""
    outcome = "WIN" if pnl > 0 else "LOSS"
    date_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    note = f"""
### {outcome} | {symbol} | {date_str}
- Direction : {direction}
- Entry     : ₹{entry_price:.2f}
- Exit      : ₹{exit_price:.2f}  ({exit_reason})
- PnL       : ₹{pnl:.2f}  ({pnl_pct:.2f}%)
- Signals   : {", ".join(signals_used)}
"""
    _append_to_file(config.JOURNAL_FILE, note)
    logger.debug("Trade note written for %s", symbol)


def write_daily_observation(market_trend: str, portfolio_stats: dict,
                             notable_events: list[str]):
    """Write a daily market observation entry."""
    date_str = datetime.now().strftime("%Y-%m-%d")
    lines = [
        f"\n## Daily Observation – {date_str}",
        f"- Market trend (Nifty50): **{market_trend}**",
        f"- NAV          : ₹{portfolio_stats.get('nav', 0):.2f}",
        f"- Realised PnL : ₹{portfolio_stats.get('realised_pnl', 0):.2f}",
        f"- Unrealised   : ₹{portfolio_stats.get('unrealised_pnl', 0):.2f}",
        f"- Return       : {portfolio_stats.get('total_return_pct', 0):.2f}%",
        f"- Open positions: {portfolio_stats.get('open_positions', 0)}",
    ]
    if notable_events:
        lines.append("- Notable events:")
        lines += [f"  - {e}" for e in notable_events]
    _append_to_file(config.JOURNAL_FILE, "\n".join(lines))


def write_scan_observation(symbol: str, price: float, score: int,
                            signal: str, reasons: list[str]):
    """Write a brief observation when a high-scoring stock is spotted."""
    if score < config.MIN_CONFIDENCE - 10:
        return   # only log near-misses and above
    date_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    note = (f"\n**SCAN** {date_str} | {symbol} | ₹{price:.2f} | "
            f"score={score} | {signal} | {'; '.join(reasons[:3])}")
    _append_to_file(config.JOURNAL_FILE, note)


# ── Lessons learned ───────────────────────────────────────────────────────────

def _generate_lessons(stats: dict) -> str:
    lines = [
        "# Lessons Learned",
        f"_Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M')}_",
        "",
        "Patterns with >= 5 trades are listed below.",
        "",
    ]
    qualified = {k: v for k, v in stats.items()
                 if v.get("wins", 0) + v.get("losses", 0) >= 5}
    if not qualified:
        lines.append("_Not enough data yet. Keep trading!_")
        return "\n".join(lines)

    # Sort by win rate desc
    for sig, v in sorted(qualified.items(), key=lambda x: -x[1]["win_rate"]):
        total  = v["wins"] + v["losses"]
        wr     = v["win_rate"] * 100
        avg_r  = v["avg_return"]
        verdict = "✓ RELIABLE" if wr >= 60 else ("⚠ MIXED" if wr >= 45 else "✗ AVOID")
        lines.append(
            f"- **{sig}** | {total} trades | win rate {wr:.0f}% | "
            f"avg return {avg_r:.1f}% | {verdict}"
        )
        if wr >= 60:
            lines.append(f"  → Boost confidence when this signal appears.")
        elif wr < 45:
            lines.append(f"  → Require additional confirmation; reduce weight.")

    return "\n".join(lines)


def refresh_lessons():
    """Rewrite lessons_learned.md based on current pattern stats."""
    stats   = load_pattern_stats()
    content = _generate_lessons(stats)
    os.makedirs(os.path.dirname(config.LESSONS_FILE), exist_ok=True)
    with open(config.LESSONS_FILE, "w") as f:
        f.write(content)
    logger.info("Lessons updated: %s", config.LESSONS_FILE)


# ── Summary helpers ───────────────────────────────────────────────────────────

def read_lessons() -> str:
    """Return the current lessons file as a string (for logging/display)."""
    if not os.path.exists(config.LESSONS_FILE):
        return "No lessons yet."
    with open(config.LESSONS_FILE) as f:
        return f.read()


def summarise_pattern_stats() -> list[dict]:
    stats = load_pattern_stats()
    rows  = []
    for sig, v in stats.items():
        total = v.get("wins", 0) + v.get("losses", 0)
        rows.append({
            "signal":    sig,
            "trades":    total,
            "win_rate":  f"{v.get('win_rate', 0)*100:.0f}%",
            "avg_return": f"{v.get('avg_return', 0):.1f}%",
        })
    return sorted(rows, key=lambda x: -int(x["trades"]))
