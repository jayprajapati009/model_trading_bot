"""
End-of-day learning examination.

After market close the bot grades its own homework:

  1. Fetches the day's % change for every stock in the combined universe
     (watchlist + Nifty 500 / Stage 2 universe).
  2. Validates today's decisions:
       • entries — is the position up or down vs entry?
       • exits   — did the stock keep rising after we sold (exited early)
                   or fall (exited well)?
  3. Missed opportunities — stocks that moved ≥ MISS_THRESHOLD% today which
     the bot scanned but did NOT buy, including WHICH factor blocked them.
  4. Good avoids — stocks the bot skipped that dropped ≥ MISS_THRESHOLD%
     (skipping was the right call; learning needs both sides).
  5. Score calibration — average day change per score bucket, i.e. "do our
     high scores actually predict bigger moves?"

Everything is written to the exam_log table (structured, for the tuner to
mine later) and to the trading journal (human-readable).
"""

import logging
import time
from collections import Counter
from datetime import datetime

import yfinance as yf

import config
from modules import learning, trade_logger
from modules.universe import get_nifty500

logger = logging.getLogger(__name__)

MISS_THRESHOLD = 2.0   # day % move that counts as a missed opportunity / good avoid


# ── Day change fetch ──────────────────────────────────────────────────────────

def _fetch_day_changes(symbols: list[str]) -> dict[str, float]:
    """Last close vs previous close, % — batched to be gentle on Yahoo."""
    changes: dict[str, float] = {}
    for i in range(0, len(symbols), 50):
        batch = symbols[i:i + 50]
        try:
            raw = yf.download(batch, period="5d", interval="1d",
                              progress=False, auto_adjust=True,
                              group_by="ticker", threads=True)
        except Exception as exc:
            logger.warning("Exam batch %d download failed: %s", i // 50, exc)
            continue
        for sym in batch:
            try:
                df = raw[sym] if len(batch) > 1 else raw
                closes = df["Close"].dropna()
                if len(closes) >= 2:
                    changes[sym] = round(
                        (closes.iloc[-1] / closes.iloc[-2] - 1) * 100, 2)
            except Exception:
                continue
        time.sleep(1)
    return changes


# ── Main examination ──────────────────────────────────────────────────────────

def daily_examination():
    date_s = datetime.now().strftime("%Y-%m-%d")
    logger.info("── Daily learning examination for %s ──", date_s)

    symbols = list(dict.fromkeys(config.WATCHLIST + get_nifty500()))
    changes = _fetch_day_changes(symbols)
    if not changes:
        logger.error("Exam aborted: could not fetch any day changes")
        return

    # Best scan per symbol today (highest score across strategies/scans)
    best: dict[str, dict] = {}
    for r in trade_logger.get_scans_for_date(date_s):
        if r["symbol"] not in best or r["score"] > best[r["symbol"]]["score"]:
            best[r["symbol"]] = r

    opened = trade_logger.get_trades_opened_on(date_s)
    closed = trade_logger.get_trades_closed_on(date_s)
    held_syms = {t["symbol"] for t in opened}

    exam_rows: list[dict] = []

    # ── Missed opportunities & good avoids ────────────────────────────────────
    missed, avoided = [], []
    for sym, chg in changes.items():
        if sym in held_syms:
            continue
        scan = best.get(sym)
        if chg >= MISS_THRESHOLD:
            missed.append({"symbol": sym, "chg": chg, "scan": scan})
            exam_rows.append({
                "date": date_s, "symbol": sym, "day_change_pct": chg,
                "category": "missed",
                "score": scan["score"] if scan else None,
                "strategy": scan["strategy"] if scan else None,
                "notes": scan["reasons"] if scan else "never scanned",
            })
        elif chg <= -MISS_THRESHOLD and scan:
            avoided.append({"symbol": sym, "chg": chg, "scan": scan})
            exam_rows.append({
                "date": date_s, "symbol": sym, "day_change_pct": chg,
                "category": "good_avoid",
                "score": scan["score"], "strategy": scan["strategy"],
                "notes": scan["reasons"],
            })
    missed.sort(key=lambda m: -m["chg"])
    avoided.sort(key=lambda m: m["chg"])

    # Which factor blocked the biggest missed movers most often?
    blockers = Counter()
    for m in missed:
        if m["scan"]:
            for reason in (m["scan"]["reasons"] or "").split("; "):
                if reason.startswith("[−]"):
                    blockers[reason] += 1

    # ── Validate today's entries ──────────────────────────────────────────────
    entry_checks = []
    for t in opened:
        chg = changes.get(t["symbol"])
        if chg is None:
            continue
        close_vs_entry = None
        # approximate: compare day close direction with our entry
        verdict = "up on day" if chg > 0 else "down on day"
        entry_checks.append({"symbol": t["symbol"], "strategy": t["strategy"],
                             "day_chg": chg, "verdict": verdict})
        exam_rows.append({
            "date": date_s, "symbol": t["symbol"], "day_change_pct": chg,
            "category": "entry_check", "score": t["score"],
            "strategy": t["strategy"],
            "notes": f"entered @ {t['entry_price']}; stock {verdict}",
        })

    # ── Validate today's exits ────────────────────────────────────────────────
    exit_checks = []
    for t in closed:
        chg = changes.get(t["symbol"])
        if chg is None:
            continue
        # crude but useful: if the stock still rose ≥1% on the day after a
        # non-target exit, we probably left money on the table
        early = chg >= 1.0 and "target" not in (t["exit_reason"] or "")
        verdict = "exited early (kept rising)" if early else "exit validated"
        exit_checks.append({"symbol": t["symbol"], "reason": t["exit_reason"],
                            "pnl": t["pnl"], "day_chg": chg, "verdict": verdict})
        exam_rows.append({
            "date": date_s, "symbol": t["symbol"], "day_change_pct": chg,
            "category": "exit_check", "score": t["score"],
            "strategy": t["strategy"],
            "notes": f"{t['exit_reason']} pnl={t['pnl']}; {verdict}",
        })

    # ── Score calibration: avg day change per score bucket ────────────────────
    buckets: dict[str, list[float]] = {"0-19": [], "20-39": [], "40-59": [], "60+": []}
    for sym, scan in best.items():
        chg = changes.get(sym)
        if chg is None:
            continue
        s = scan["score"]
        key = "0-19" if s < 20 else "20-39" if s < 40 else "40-59" if s < 60 else "60+"
        buckets[key].append(chg)
    calibration = {k: (round(sum(v) / len(v), 2) if v else None, len(v))
                   for k, v in buckets.items()}

    trade_logger.log_exam_rows(exam_rows)
    _write_journal(date_s, changes, missed, avoided, blockers,
                   entry_checks, exit_checks, calibration)

    logger.info("Exam done: %d symbols, %d missed, %d good avoids, "
                "%d entries checked, %d exits checked",
                len(changes), len(missed), len(avoided),
                len(entry_checks), len(exit_checks))


# ── Journal output ────────────────────────────────────────────────────────────

def _write_journal(date_s, changes, missed, avoided, blockers,
                   entry_checks, exit_checks, calibration):
    lines = [f"\n## 🎓 Learning Examination – {date_s}",
             f"Universe examined: {len(changes)} stocks"]

    movers = sorted(changes.items(), key=lambda kv: -kv[1])[:5]
    lines.append("\n**Top movers today:** " +
                 ", ".join(f"{s.replace('.NS','')} {c:+.1f}%" for s, c in movers))

    if entry_checks:
        lines.append("\n**Entries taken:**")
        for e in entry_checks:
            lines.append(f"- {e['symbol']} [{e['strategy']}] — {e['verdict']} ({e['day_chg']:+.1f}%)")

    if exit_checks:
        lines.append("\n**Exits taken:**")
        for e in exit_checks:
            lines.append(f"- {e['symbol']} — {e['reason']} | pnl ₹{e['pnl']} | {e['verdict']}")

    if missed:
        lines.append(f"\n**Missed opportunities (moved ≥{MISS_THRESHOLD}%, we held back):**")
        for m in missed[:8]:
            scan = m["scan"]
            if scan:
                lines.append(f"- {m['symbol']} {m['chg']:+.1f}% — our best score {scan['score']} "
                             f"[{scan['strategy']}]")
            else:
                lines.append(f"- {m['symbol']} {m['chg']:+.1f}% — never scanned (not in any universe)")
        if blockers:
            top = blockers.most_common(3)
            lines.append("- **Most common blockers among missed movers:** " +
                         "; ".join(f"{r} ({n}×)" for r, n in top))

    if avoided:
        lines.append(f"\n**Good avoids (skipped and they fell ≥{MISS_THRESHOLD}%):**")
        for a in avoided[:5]:
            lines.append(f"- {a['symbol']} {a['chg']:+.1f}% — scored only {a['scan']['score']}, correctly skipped")

    lines.append("\n**Score calibration (avg day move per score bucket):**")
    for bucket, (avg, n) in calibration.items():
        if n:
            lines.append(f"- score {bucket}: avg {avg:+.2f}% across {n} stocks")
    lines.append("_If higher buckets don't show higher averages, the scoring "
                 "weights need re-examination._")

    learning._append_to_file(config.JOURNAL_FILE, "\n".join(lines))
