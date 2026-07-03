"""
SQLite-backed trade logger.
Records every trade with full context so the bot (and the human) can review it.
"""

import json
import logging
import os
import sqlite3
from datetime import datetime

import config

logger = logging.getLogger(__name__)


def _connect() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(config.DB_FILE), exist_ok=True)
    conn = sqlite3.connect(config.DB_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")   # dashboard reads don't block bot writes
    conn.execute("PRAGMA synchronous=NORMAL") # safe but faster than FULL
    return conn


def init_db():
    with _connect() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS trades (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol        TEXT NOT NULL,
                direction     TEXT NOT NULL DEFAULT 'LONG',
                entry_date    TEXT,
                entry_price   REAL,
                exit_date     TEXT,
                exit_price    REAL,
                quantity      INTEGER,
                pnl           REAL,
                pnl_pct       REAL,
                exit_reason   TEXT,
                signals_used  TEXT,
                score         INTEGER,
                status        TEXT DEFAULT 'OPEN'
            );

            CREATE TABLE IF NOT EXISTS scan_log (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                ts        TEXT,
                symbol    TEXT,
                price     REAL,
                score     INTEGER,
                signal    TEXT,
                reasons   TEXT
            );

            CREATE TABLE IF NOT EXISTS daily_summary (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                date         TEXT UNIQUE,
                nav          REAL,
                realised_pnl REAL,
                unrealised_pnl REAL,
                open_positions INTEGER,
                notes        TEXT
            );

            CREATE TABLE IF NOT EXISTS exam_log (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                date           TEXT,
                symbol         TEXT,
                day_change_pct REAL,
                category       TEXT,
                score          INTEGER,
                strategy       TEXT,
                notes          TEXT
            );

            CREATE TABLE IF NOT EXISTS param_history (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                ts             TEXT,
                strategy       TEXT,
                mode           TEXT,
                source         TEXT,
                regime         TEXT,
                config_version INTEGER,
                params         TEXT,
                reason         TEXT
            );
        """)
        # Migrations for multi-strategy support (safe to re-run)
        for stmt in (
            "ALTER TABLE trades ADD COLUMN strategy TEXT DEFAULT 'ema_momentum'",
            "ALTER TABLE trades ADD COLUMN config_version INTEGER DEFAULT 0",
            "ALTER TABLE scan_log ADD COLUMN strategy TEXT DEFAULT 'ema_momentum'",
        ):
            try:
                conn.execute(stmt)
            except sqlite3.OperationalError:
                pass   # column already exists
    logger.debug("Database initialised at %s", config.DB_FILE)


def log_trade_opened(symbol: str, entry_price: float, quantity: int,
                     signals_used: list[str], score: int,
                     strategy: str = "ema_momentum",
                     config_version: int = 0) -> int:
    with _connect() as conn:
        cur = conn.execute(
            """INSERT INTO trades (symbol, entry_date, entry_price, quantity,
               signals_used, score, status, strategy, config_version)
               VALUES (?, ?, ?, ?, ?, ?, 'OPEN', ?, ?)""",
            (symbol, datetime.now().isoformat(), entry_price,
             quantity, json.dumps(signals_used), score, strategy, config_version),
        )
        return cur.lastrowid


def log_trade_closed(trade_id: int, exit_price: float, pnl: float,
                     pnl_pct: float, exit_reason: str):
    with _connect() as conn:
        conn.execute(
            """UPDATE trades SET exit_date=?, exit_price=?, pnl=?, pnl_pct=?,
               exit_reason=?, status='CLOSED'
               WHERE id=?""",
            (datetime.now().isoformat(), exit_price, pnl,
             pnl_pct, exit_reason, trade_id),
        )


def log_scan(symbol: str, price: float, score: int,
             signal: str, reasons: list[str],
             strategy: str = "ema_momentum"):
    with _connect() as conn:
        conn.execute(
            "INSERT INTO scan_log (ts, symbol, price, score, signal, reasons, strategy) VALUES (?,?,?,?,?,?,?)",
            (datetime.now().isoformat(), symbol, price, score,
             signal, "; ".join(reasons), strategy),
        )


def log_param_change(strategy: str, mode: str, source: str, regime: str,
                     config_version: int, params: dict, reason: str):
    """Record every parameter/allocation change for future self-tuning analysis."""
    with _connect() as conn:
        conn.execute(
            """INSERT INTO param_history
               (ts, strategy, mode, source, regime, config_version, params, reason)
               VALUES (?,?,?,?,?,?,?,?)""",
            (datetime.now().isoformat(), strategy, mode, source, regime,
             config_version, json.dumps(params), reason),
        )


def get_scans_for_date(date_s: str) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT symbol, score, signal, reasons, strategy FROM scan_log "
            "WHERE ts LIKE ?", (f"{date_s}%",)
        ).fetchall()
    return [dict(r) for r in rows]


def get_trades_opened_on(date_s: str) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM trades WHERE entry_date LIKE ?", (f"{date_s}%",)
        ).fetchall()
    return [dict(r) for r in rows]


def get_trades_closed_on(date_s: str) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM trades WHERE status='CLOSED' AND exit_date LIKE ?",
            (f"{date_s}%",)
        ).fetchall()
    return [dict(r) for r in rows]


def log_exam_rows(rows: list[dict]):
    if not rows:
        return
    with _connect() as conn:
        conn.executemany(
            """INSERT INTO exam_log
               (date, symbol, day_change_pct, category, score, strategy, notes)
               VALUES (:date, :symbol, :day_change_pct, :category, :score,
                       :strategy, :notes)""",
            rows,
        )


def get_exam_summary(days: int = 30) -> list[dict]:
    """Category counts per day — the tuner's window into decision quality."""
    with _connect() as conn:
        rows = conn.execute(
            """SELECT date, category, COUNT(*) AS n, AVG(day_change_pct) AS avg_chg
               FROM exam_log
               WHERE date >= date('now', ?)
               GROUP BY date, category ORDER BY date DESC""",
            (f"-{int(days)} days",)
        ).fetchall()
    return [dict(r) for r in rows]


def get_param_history(limit: int = 50) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM param_history ORDER BY ts DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_strategy_performance(strategy: str, config_version: int | None = None,
                             last_n: int | None = None) -> dict:
    """Aggregate stats for one strategy (optionally one config version)."""
    q = "SELECT pnl, pnl_pct FROM trades WHERE status='CLOSED' AND strategy=?"
    args: list = [strategy]
    if config_version is not None:
        q += " AND config_version=?"
        args.append(config_version)
    q += " ORDER BY exit_date DESC"
    if last_n:
        q += f" LIMIT {int(last_n)}"
    with _connect() as conn:
        rows = conn.execute(q, args).fetchall()
    pnls = [r["pnl"] for r in rows if r["pnl"] is not None]
    if not pnls:
        return {"trades": 0, "expectancy": 0.0, "win_rate": 0.0, "total_pnl": 0.0}
    wins = [p for p in pnls if p > 0]
    return {
        "trades":     len(pnls),
        "expectancy": round(sum(pnls) / len(pnls), 2),   # avg PnL per trade
        "win_rate":   round(len(wins) / len(pnls) * 100, 1),
        "total_pnl":  round(sum(pnls), 2),
    }


def save_daily_summary(date_str: str, nav: float, realised_pnl: float,
                       unrealised_pnl: float, open_positions: int,
                       notes: str = ""):
    with _connect() as conn:
        conn.execute(
            """INSERT INTO daily_summary
               (date, nav, realised_pnl, unrealised_pnl, open_positions, notes)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(date) DO UPDATE SET
                 nav=excluded.nav, realised_pnl=excluded.realised_pnl,
                 unrealised_pnl=excluded.unrealised_pnl,
                 open_positions=excluded.open_positions, notes=excluded.notes""",
            (date_str, nav, realised_pnl, unrealised_pnl, open_positions, notes),
        )


def get_all_closed_trades() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM trades WHERE status='CLOSED' ORDER BY exit_date DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def get_open_trades() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM trades WHERE status='OPEN'"
        ).fetchall()
    return [dict(r) for r in rows]


def get_performance_stats() -> dict:
    # Aggregate entirely in SQL — never loads individual trade rows into Python.
    with _connect() as conn:
        r = conn.execute("""
            SELECT
                COUNT(*)                                          AS total,
                SUM(pnl > 0)                                     AS wins,
                SUM(pnl <= 0)                                    AS losses,
                COALESCE(SUM(pnl), 0)                            AS total_pnl,
                COALESCE(AVG(CASE WHEN pnl > 0 THEN pnl END), 0) AS avg_win,
                COALESCE(AVG(CASE WHEN pnl <= 0 THEN pnl END), 0) AS avg_loss,
                COALESCE(SUM(CASE WHEN pnl > 0  THEN pnl ELSE 0 END), 0) AS gross_profit,
                COALESCE(SUM(CASE WHEN pnl <= 0 THEN pnl ELSE 0 END), 0) AS gross_loss,
                COALESCE(MAX(pnl), 0)                            AS best_trade,
                COALESCE(MIN(pnl), 0)                            AS worst_trade
            FROM trades WHERE status='CLOSED'
        """).fetchone()

    total = r["total"] or 0
    if total == 0:
        return {"total_trades": 0}

    wins, losses    = r["wins"] or 0, r["losses"] or 0
    gross_loss      = r["gross_loss"] or 0
    profit_factor   = abs(r["gross_profit"] / gross_loss) if gross_loss != 0 else float("inf")
    return {
        "total_trades":   total,
        "winning_trades": wins,
        "losing_trades":  losses,
        "win_rate_pct":   round(wins / total * 100, 1),
        "total_pnl":      round(r["total_pnl"], 2),
        "avg_win":        round(r["avg_win"], 2),
        "avg_loss":       round(r["avg_loss"], 2),
        "profit_factor":  round(profit_factor, 2),
        "best_trade":     round(r["best_trade"], 2),
        "worst_trade":    round(r["worst_trade"], 2),
    }
