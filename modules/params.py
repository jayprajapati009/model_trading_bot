"""
Strategy parameter store.

Each strategy has three modes:
  default – built-in defaults from its ParamSpec list
  manual  – user-set values from the dashboard (validated against rails)
  auto    – values the weekly tuner adjusts on its own

Every change (user or auto) is logged to the param_history table with the
market regime at the time, so future tuning can correlate parameter choices
with market conditions.
"""

import json
import logging
import os
import threading
from dataclasses import dataclass, asdict
from datetime import datetime

import config

logger = logging.getLogger(__name__)

_lock = threading.Lock()   # dashboard thread + scheduler thread both write

MODES = ("default", "manual", "auto")


@dataclass
class ParamSpec:
    key: str
    label: str
    default: float
    min: float
    max: float
    step: float
    description: str = ""

    def clamp(self, value: float) -> float:
        v = max(self.min, min(self.max, float(value)))
        # snap to step grid to keep the search space discrete for the tuner
        steps = round((v - self.min) / self.step)
        return round(self.min + steps * self.step, 6)


def _load_store() -> dict:
    if not os.path.exists(config.STRATEGY_PARAMS_FILE):
        return {}
    try:
        with open(config.STRATEGY_PARAMS_FILE) as f:
            return json.load(f)
    except Exception as exc:
        logger.error("Failed to load strategy params: %s", exc)
        return {}


def _save_store(store: dict):
    os.makedirs(os.path.dirname(config.STRATEGY_PARAMS_FILE), exist_ok=True)
    with open(config.STRATEGY_PARAMS_FILE, "w") as f:
        json.dump(store, f, indent=2)


def get_mode(strategy: str) -> str:
    store = _load_store()
    return store.get(strategy, {}).get("mode", "default")


def get_config_version(strategy: str) -> int:
    store = _load_store()
    return store.get(strategy, {}).get("config_version", 0)


def get_active_params(strategy: str, specs: list[ParamSpec]) -> dict:
    """Resolve the effective parameter dict for a strategy based on its mode."""
    store = _load_store()
    entry = store.get(strategy, {})
    mode  = entry.get("mode", "default")
    defaults = {s.key: s.default for s in specs}
    if mode == "default":
        return defaults
    overrides = entry.get(mode, {})   # 'manual' or 'auto' dict
    spec_map  = {s.key: s for s in specs}
    resolved  = dict(defaults)
    for k, v in overrides.items():
        if k in spec_map:
            resolved[k] = spec_map[k].clamp(v)
    return resolved


def set_params(strategy: str, specs: list[ParamSpec], mode: str,
               values: dict | None, source: str, reason: str,
               regime: str) -> dict:
    """
    Update mode and/or values for a strategy. `values` apply to the given
    mode's bucket (manual or auto). Returns the new active params.
    Logs to param_history.
    """
    if mode not in MODES:
        raise ValueError(f"invalid mode {mode!r}")

    spec_map = {s.key: s for s in specs}
    with _lock:
        store = _load_store()
        entry = store.setdefault(strategy, {})
        entry["mode"] = mode
        if values and mode in ("manual", "auto"):
            bucket = entry.setdefault(mode, {})
            for k, v in values.items():
                if k in spec_map:
                    bucket[k] = spec_map[k].clamp(v)
        entry["config_version"] = entry.get("config_version", 0) + 1
        entry["updated_at"] = datetime.now().isoformat()
        _save_store(store)

    active = get_active_params(strategy, specs)

    # Log to DB (import here to avoid circular import at module load)
    from modules import trade_logger
    trade_logger.log_param_change(
        strategy=strategy, mode=mode, source=source, regime=regime,
        config_version=entry["config_version"],
        params=active, reason=reason,
    )
    logger.info("Params updated: %s mode=%s v%d by %s (%s)",
                strategy, mode, entry["config_version"], source, reason)
    return active


# ── Capital allocations ───────────────────────────────────────────────────────

def get_allocations(strategy_names: list[str]) -> dict[str, float]:
    """Return capital weight per strategy; defaults to equal split."""
    store = _load_store()
    alloc = store.get("_allocations", {})
    missing = [s for s in strategy_names if s not in alloc]
    if missing:
        equal = 1.0 / len(strategy_names)
        alloc = {s: alloc.get(s, equal) for s in strategy_names}
        total = sum(alloc.values())
        alloc = {s: w / total for s, w in alloc.items()}
    return alloc


def set_allocations(alloc: dict[str, float], source: str, reason: str,
                    regime: str):
    with _lock:
        store = _load_store()
        store["_allocations"] = {s: round(w, 4) for s, w in alloc.items()}
        store["_allocations_updated_at"] = datetime.now().isoformat()
        _save_store(store)
    from modules import trade_logger
    trade_logger.log_param_change(
        strategy="_allocations", mode="auto", source=source, regime=regime,
        config_version=0, params=alloc, reason=reason,
    )
    logger.info("Allocations updated by %s: %s (%s)", source, alloc, reason)
