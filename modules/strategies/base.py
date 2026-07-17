"""
Strategy base class.

A strategy declares:
  • name / label
  • param_specs  – tunable parameters with rails (min/max/step)
  • entry_universe() – symbols it scans for entries
  • entry_signal()   – (signal, score, reasons, plan) for a candidate symbol
  • exit_signal()    – (signal, reason) for an open position

`plan` lets a strategy override stop/target placement (e.g. Fibonacci levels);
when None, risk-based defaults from its params are used.
"""

from abc import ABC, abstractmethod

import pandas as pd

from modules.params import ParamSpec, get_active_params, get_mode, get_config_version


class Strategy(ABC):
    name: str = "base"
    label: str = "Base"
    description: str = ""
    lookback_days: int = 90

    param_specs: list[ParamSpec] = []

    # ── Param resolution ──────────────────────────────────────────────────────

    def params(self) -> dict:
        return get_active_params(self.name, self.param_specs)

    def mode(self) -> str:
        return get_mode(self.name)

    def config_version(self) -> int:
        return get_config_version(self.name)

    # ── Interface ─────────────────────────────────────────────────────────────

    @abstractmethod
    def entry_universe(self) -> list[str]:
        """Symbols this strategy scans for new entries."""

    @abstractmethod
    def entry_signal(self, symbol: str, df: pd.DataFrame, ctx: dict,
                     p: dict) -> tuple[str, int, list[str], dict | None]:
        """Return (signal 'BUY'|'HOLD', score 0-100, reasons, plan|None)."""

    @abstractmethod
    def exit_signal(self, symbol: str, df: pd.DataFrame, pos,
                    p: dict) -> tuple[str, str]:
        """Return (signal 'SELL'|'HOLD', reason)."""

    # ── Shared position sizing ────────────────────────────────────────────────

    def position_size(self, capital: float, price: float, atr_val: float,
                      p: dict, plan: dict | None = None) -> tuple[int, float, float]:
        """
        Risk-based sizing within this strategy's capital bucket.
        Returns (quantity, stop_loss, target). plan overrides stop/target.
        """
        stop_pct  = p.get("stop_loss_pct", 3.0) / 100
        risk_pct  = p.get("risk_per_trade_pct", 2.0) / 100
        rr        = p.get("reward_risk_ratio", 2.0)

        if plan and plan.get("stop"):
            stop_loss = plan["stop"]
            stop_distance = max(price - stop_loss, price * 0.005)
        else:
            stop_distance = max(atr_val * 1.5, price * stop_pct)
            stop_loss = round(price - stop_distance, 2)

        if plan and plan.get("target"):
            target = plan["target"]
        else:
            target = round(price + stop_distance * rr, 2)

        risk_amount = capital * risk_pct
        quantity = max(1, int(risk_amount / stop_distance))
        # leverage raises buying power (margin trading); risk per share is
        # unchanged, so the risk-based qty still caps the actual risk taken
        leverage = p.get("leverage", 1.0)
        max_qty  = max(1, int(capital * leverage / price))
        return min(quantity, max_qty), round(stop_loss, 2), round(target, 2)
