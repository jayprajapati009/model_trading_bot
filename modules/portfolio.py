"""
Portfolio manager – tracks cash, open positions, and realised PnL.
State is persisted to a JSON file so the bot survives restarts.
"""

import json
import logging
import os
from datetime import datetime
from typing import Optional

import config

logger = logging.getLogger(__name__)


class Position:
    def __init__(self, symbol: str, entry_price: float, quantity: int,
                 stop_loss: float, target: float, entry_date: str,
                 signals_used: list[str], strategy: str = "ema_momentum",
                 leverage: float = 1.0):
        self.symbol       = symbol
        self.entry_price  = entry_price
        self.quantity     = quantity
        self.stop_loss    = stop_loss
        self.target       = target
        self.entry_date   = entry_date
        self.signals_used = signals_used
        self.strategy     = strategy
        self.leverage     = leverage          # >1 simulates futures margin
        self.high_since_entry = entry_price   # tracks highest price for trailing stop

    def to_dict(self) -> dict:
        return self.__dict__.copy()

    @classmethod
    def from_dict(cls, d: dict) -> "Position":
        pos = cls.__new__(cls)
        pos.__dict__.update(d)
        # Legacy positions saved before multi-strategy support
        if not hasattr(pos, "strategy"):
            pos.strategy = "ema_momentum"
        if not hasattr(pos, "leverage"):
            pos.leverage = 1.0
        return pos

    @property
    def margin(self) -> float:
        """Cash actually blocked for this position (notional / leverage)."""
        return self.entry_price * self.quantity / self.leverage

    def unrealised_pnl(self, current_price: float) -> float:
        return (current_price - self.entry_price) * self.quantity

    def unrealised_pct(self, current_price: float) -> float:
        # return on the capital deployed — leverage multiplies it both ways
        return (current_price - self.entry_price) / self.entry_price * 100 * self.leverage

    def current_value(self, current_price: float) -> float:
        # margin blocked + open PnL; for leverage 1 this equals price × qty
        return self.margin + self.unrealised_pnl(current_price)

    def update_trailing_stop(self, current_price: float) -> bool:
        """Ratchet up the stop loss when price makes new highs. Returns True if stop moved."""
        if current_price > self.high_since_entry:
            self.high_since_entry = current_price
            # activate trailing stop once price is up by half the target
            if current_price >= self.entry_price * (1 + config.TARGET_PCT / 2):
                new_stop = current_price * (1 - config.TRAILING_STOP_PCT)
                if new_stop > self.stop_loss:
                    self.stop_loss = round(new_stop, 2)
                    return True
        return False


class Portfolio:
    def __init__(self):
        self.cash: float = config.INITIAL_CAPITAL
        self.positions: dict[str, Position] = {}
        self.realised_pnl: float = 0.0
        self._load()

    # ── Persistence ───────────────────────────────────────────────────────────

    def _load(self):
        if not os.path.exists(config.PORTFOLIO_FILE):
            self._save()
            return
        try:
            with open(config.PORTFOLIO_FILE) as f:
                data = json.load(f)
            self.cash         = data.get("cash", config.INITIAL_CAPITAL)
            self.realised_pnl = data.get("realised_pnl", 0.0)
            self.positions    = {
                sym: Position.from_dict(pd)
                for sym, pd in data.get("positions", {}).items()
            }
            logger.info("Portfolio loaded: cash=%.2f, positions=%d",
                        self.cash, len(self.positions))
        except Exception as exc:
            logger.error("Failed to load portfolio: %s. Starting fresh.", exc)
            self._save()

    def _save(self):
        os.makedirs(os.path.dirname(config.PORTFOLIO_FILE), exist_ok=True)
        data = {
            "cash":         self.cash,
            "realised_pnl": self.realised_pnl,
            "positions":    {sym: p.to_dict() for sym, p in self.positions.items()},
            "last_updated": datetime.now().isoformat(),
        }
        with open(config.PORTFOLIO_FILE, "w") as f:
            json.dump(data, f, indent=2)

    # ── Trade execution ───────────────────────────────────────────────────────

    def strategy_exposure(self, strategy: str) -> float:
        """Capital (margin) currently blocked by one strategy."""
        return sum(p.margin
                   for p in self.positions.values() if p.strategy == strategy)

    def gross_exposure(self) -> float:
        """Total notional across positions — what leverage actually rides on."""
        return sum(p.entry_price * p.quantity for p in self.positions.values())

    def can_open_position(self, price: float, quantity: int,
                          strategy: str | None = None,
                          allocation: float | None = None,
                          leverage: float = 1.0) -> tuple[bool, str]:
        notional = price * quantity
        margin   = notional / leverage
        if len(self.positions) >= config.MAX_POSITIONS:
            return False, f"Max positions ({config.MAX_POSITIONS}) reached"
        if margin > self.cash:
            return False, f"Insufficient cash: need margin {margin:.2f}, have {self.cash:.2f}"
        if margin > self.net_value() * config.MAX_POSITION_PCT:
            return False, f"Position too large (margin >{config.MAX_POSITION_PCT:.0%} of portfolio)"
        if self.gross_exposure() + notional > self.net_value() * config.MAX_GROSS_EXPOSURE:
            return False, (f"Gross exposure cap hit "
                           f"({config.MAX_GROSS_EXPOSURE:.1f}× NAV)")
        if strategy and allocation is not None:
            budget = self.net_value() * allocation
            if self.strategy_exposure(strategy) + margin > budget * 1.05:
                return False, (f"{strategy} allocation exhausted "
                               f"(budget ₹{budget:.0f})")
        return True, "ok"

    def open_position(self, symbol: str, price: float, quantity: int,
                      stop_loss: float, target: float,
                      signals_used: list[str],
                      strategy: str = "ema_momentum",
                      allocation: float | None = None,
                      leverage: float = 1.0) -> Optional[Position]:
        if symbol in self.positions:
            logger.warning("Already have a position in %s", symbol)
            return None
        ok, reason = self.can_open_position(price, quantity, strategy,
                                            allocation, leverage)
        if not ok:
            logger.info("Cannot open %s: %s", symbol, reason)
            return None

        self.cash -= price * quantity / leverage   # block margin only
        pos = Position(
            symbol=symbol,
            entry_price=price,
            quantity=quantity,
            stop_loss=stop_loss,
            target=target,
            entry_date=datetime.now().isoformat(),
            signals_used=signals_used,
            strategy=strategy,
            leverage=leverage,
        )
        self.positions[symbol] = pos
        self._save()
        logger.info("OPENED %s: qty=%d @ %.2f | SL=%.2f | TGT=%.2f | lev=%.0f×",
                    symbol, quantity, price, stop_loss, target, leverage)
        return pos

    def close_position(self, symbol: str, price: float,
                       reason: str = "manual") -> Optional[dict]:
        if symbol not in self.positions:
            logger.warning("No position in %s to close", symbol)
            return None

        pos = self.positions.pop(symbol)
        pnl      = (price - pos.entry_price) * pos.quantity
        # pnl_pct is return on the margin deployed — what a futures trader sees
        pnl_pct  = (price - pos.entry_price) / pos.entry_price * 100 * pos.leverage

        self.cash         += pos.margin + pnl   # release margin + settle PnL
        self.realised_pnl += pnl
        self._save()

        result = {
            "symbol":      symbol,
            "entry_price": pos.entry_price,
            "exit_price":  price,
            "quantity":    pos.quantity,
            "pnl":         round(pnl, 2),
            "pnl_pct":     round(pnl_pct, 2),
            "reason":      reason,
            "entry_date":  pos.entry_date,
            "exit_date":   datetime.now().isoformat(),
            "signals_used": pos.signals_used,
            "strategy":    pos.strategy,
            "leverage":    pos.leverage,
        }
        logger.info("CLOSED %s: pnl=%.2f (%.2f%%) reason=%s",
                    symbol, pnl, pnl_pct, reason)
        return result

    def update_trailing_stops(self, prices: dict[str, float]) -> list[tuple[str, float]]:
        """Update trailing stops; return list of (symbol, new_stop) where stop moved."""
        moved = []
        for sym, pos in self.positions.items():
            price = prices.get(sym)
            if price and pos.update_trailing_stop(price):
                moved.append((sym, pos.stop_loss))
        if moved:
            self._save()
        return moved

    # ── Stats ─────────────────────────────────────────────────────────────────

    def net_value(self, prices: dict[str, float] | None = None) -> float:
        if prices is None:
            prices = {}
        positions_value = sum(
            pos.current_value(prices.get(sym, pos.entry_price))
            for sym, pos in self.positions.items()
        )
        return self.cash + positions_value

    def unrealised_pnl(self, prices: dict[str, float]) -> float:
        return sum(
            pos.unrealised_pnl(prices.get(sym, pos.entry_price))
            for sym, pos in self.positions.items()
        )

    def stats(self, prices: dict[str, float] | None = None) -> dict:
        prices = prices or {}
        upnl   = self.unrealised_pnl(prices)
        nav    = self.net_value(prices)
        return {
            "cash":           round(self.cash, 2),
            "nav":            round(nav, 2),
            "realised_pnl":   round(self.realised_pnl, 2),
            "unrealised_pnl": round(upnl, 2),
            "total_pnl":      round(self.realised_pnl + upnl, 2),
            "total_return_pct": round((nav - config.INITIAL_CAPITAL) / config.INITIAL_CAPITAL * 100, 2),
            "open_positions": len(self.positions),
            "initial_capital": config.INITIAL_CAPITAL,
        }

    def position_summary(self, prices: dict[str, float] | None = None) -> list[dict]:
        prices = prices or {}
        rows = []
        for sym, pos in self.positions.items():
            cp = prices.get(sym, pos.entry_price)
            rows.append({
                "symbol":     sym,
                "qty":        pos.quantity,
                "entry":      round(pos.entry_price, 2),
                "current":    round(cp, 2),
                "stop_loss":  round(pos.stop_loss, 2),
                "target":     round(pos.target, 2),
                "pnl":        round(pos.unrealised_pnl(cp), 2),
                "pnl_pct":    round(pos.unrealised_pct(cp), 2),
                "entry_date": pos.entry_date[:10],
                "strategy":   pos.strategy,
                "leverage":   pos.leverage,
            })
        return rows
