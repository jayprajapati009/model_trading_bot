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
        """)
    logger.debug("Database initialised at %s", config.DB_FILE)


def log_trade_opened(symbol: str, entry_price: float, quantity: int,
                     signals_used: list[str], score: int) -> int:
    with _connect() as conn:
        cur = conn.execute(
            """INSERT INTO trades (symbol, entry_date, entry_price, quantity,
               signals_used, score, status)
               VALUES (?, ?, ?, ?, ?, ?, 'OPEN')""",
            (symbol, datetime.now().isoformat(), entry_price,
             quantity, json.dumps(signals_used), score),
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
             signal: str, reasons: list[str]):
    with _connect() as conn:
        conn.execute(
            "INSERT INTO scan_log (ts, symbol, price, score, signal, reasons) VALUES (?,?,?,?,?,?)",
            (datetime.now().isoformat(), symbol, price, score,
             signal, "; ".join(reasons)),
        )


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
