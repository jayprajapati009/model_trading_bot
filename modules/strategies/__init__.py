"""Strategy registry — all active strategies are instantiated here."""

from modules.strategies.ema_momentum import EmaMomentumStrategy
from modules.strategies.stage_two import StageTwoStrategy
from modules.strategies.fibonacci import FibonacciStrategy

STRATEGIES = {
    s.name: s for s in (
        EmaMomentumStrategy(),
        StageTwoStrategy(),
        FibonacciStrategy(),
    )
}


def get_strategy(name: str):
    """Return strategy by name; falls back to ema_momentum for legacy positions."""
    return STRATEGIES.get(name, STRATEGIES["ema_momentum"])
