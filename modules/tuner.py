"""
Weekly auto-tuner.

Runs every Friday after market close. Two jobs:

1. PARAMETER TUNING (only for strategies in 'auto' mode)
   - Score the current config version on its closed trades (expectancy =
     average PnL per trade). Requires TUNE_MIN_TRADES before acting.
   - ROLLBACK: if expectancy dropped below TUNE_ROLLBACK_DROP × the previous
     version's expectancy, revert to the previous version's params.
   - Otherwise HILL-CLIMB: adjust ONE parameter, one step, chosen by simple
     performance heuristics:
       • win rate < 45%          → raise the entry threshold (be pickier)
       • win rate > 65%, few trades → lower the entry threshold (trade more)
       • avg loss > avg win      → tighten stop / raise reward:risk
   - Every decision is logged to param_history with the market regime, so a
     future version of the tuner can learn regime→param relationships from
     its own audit trail.

2. CAPITAL REALLOCATION (always, since the user chose algo-decided split)
   - weight_i ∝ smoothed expectancy of strategy i over its last 20 trades
   - bounded [ALLOC_MIN, ALLOC_MAX], max shift ALLOC_MAX_SHIFT per week
   - strategies with no trades yet keep their current weight (no punishment
     for being new)
"""

import json
import logging

import config
from modules import params as param_store
from modules import trade_logger
from modules.regime import detect_regime
from modules.strategies import STRATEGIES

logger = logging.getLogger(__name__)


def weekly_review():
    regime = detect_regime()
    logger.info("── Weekly tuner review (regime: %s) ──", regime)

    for strat in STRATEGIES.values():
        _tune_strategy(strat, regime)

    _reallocate_capital(regime)
    logger.info("Weekly review complete")


# ── Parameter tuning ──────────────────────────────────────────────────────────

def _tune_strategy(strat, regime: str):
    name = strat.name
    mode = strat.mode()

    version = strat.config_version()
    perf = trade_logger.get_strategy_performance(name, config_version=version)

    # Always log a performance snapshot so history accumulates in every mode
    trade_logger.log_param_change(
        strategy=name, mode=mode, source="tuner_snapshot", regime=regime,
        config_version=version, params=strat.params(),
        reason=(f"weekly snapshot: {perf['trades']} trades, "
                f"expectancy ₹{perf['expectancy']}, win rate {perf['win_rate']}%"),
    )

    if mode != "auto":
        logger.info("%s: mode=%s — snapshot logged, no tuning", name, mode)
        return

    if perf["trades"] < config.TUNE_MIN_TRADES:
        logger.info("%s: only %d trades under v%d — need %d before tuning",
                    name, perf["trades"], version, config.TUNE_MIN_TRADES)
        return

    # Rollback check against the previous config version
    prev_perf = trade_logger.get_strategy_performance(name, config_version=version - 1)
    if (prev_perf["trades"] >= config.TUNE_MIN_TRADES
            and prev_perf["expectancy"] > 0
            and perf["expectancy"] < prev_perf["expectancy"] * config.TUNE_ROLLBACK_DROP):
        _rollback(strat, regime, perf, prev_perf)
        return

    _hill_climb(strat, regime, perf)


def _rollback(strat, regime, perf, prev_perf):
    """Revert to the previous version's params (found in param_history)."""
    history = trade_logger.get_param_history(limit=200)
    prev_version = strat.config_version() - 1
    prev_entry = next(
        (h for h in history
         if h["strategy"] == strat.name and h["config_version"] == prev_version
         and h["source"] != "tuner_snapshot"),
        None,
    )
    if not prev_entry:
        logger.warning("%s: rollback wanted but v%d params not in history",
                       strat.name, prev_version)
        return
    prev_params = json.loads(prev_entry["params"])
    param_store.set_params(
        strat.name, strat.param_specs, "auto", prev_params,
        source="tuner_rollback", regime=regime,
        reason=(f"expectancy ₹{perf['expectancy']} < 80% of previous "
                f"₹{prev_perf['expectancy']} — reverting to v{prev_version}"),
    )
    logger.info("%s: ROLLED BACK to v%d params", strat.name, prev_version)


# Round-robin state: which param to try next, persisted in the params file
def _next_param_index(strat) -> int:
    import os
    key = f"_tune_idx_{strat.name}"
    store = param_store._load_store()
    idx = store.get(key, 0)
    store[key] = (idx + 1) % len(strat.param_specs)
    param_store._save_store(store)
    return idx


def _hill_climb(strat, regime, perf):
    """Adjust one parameter one step based on performance heuristics."""
    current = strat.params()
    specs   = {s.key: s for s in strat.param_specs}
    win_rate = perf["win_rate"]

    chosen_key, direction, why = None, 0, ""

    threshold_key = ("min_confidence" if "min_confidence" in specs
                     else "min_score" if "min_score" in specs else None)

    if threshold_key and win_rate < 45:
        chosen_key, direction = threshold_key, +1
        why = f"win rate {win_rate}% < 45% — raising entry threshold to be pickier"
    elif threshold_key and win_rate > 65 and perf["trades"] < 15:
        chosen_key, direction = threshold_key, -1
        why = f"win rate {win_rate}% > 65% with few trades — lowering threshold to trade more"
    elif "reward_risk_ratio" in specs and perf["expectancy"] < 0:
        chosen_key, direction = "reward_risk_ratio", +1
        why = f"negative expectancy ₹{perf['expectancy']} — widening reward:risk"
    else:
        # No strong signal: explore round-robin, nudging toward default
        idx  = _next_param_index(strat)
        spec = strat.param_specs[idx]
        chosen_key = spec.key
        direction  = 1 if current[spec.key] < spec.default else -1
        if current[spec.key] == spec.default:
            direction = 1 if win_rate >= 50 else -1
        why = (f"exploration step on {spec.key} "
               f"(win rate {win_rate}%, expectancy ₹{perf['expectancy']})")

    spec = specs[chosen_key]
    new_value = spec.clamp(current[chosen_key] + direction * spec.step)
    if new_value == current[chosen_key]:
        logger.info("%s: %s already at rail (%s) — no change",
                    strat.name, chosen_key, new_value)
        return

    new_params = dict(current)
    new_params[chosen_key] = new_value
    param_store.set_params(
        strat.name, strat.param_specs, "auto", new_params,
        source="tuner_hillclimb", regime=regime,
        reason=f"{why}: {chosen_key} {current[chosen_key]} → {new_value}",
    )
    logger.info("%s: tuned %s %s → %s (%s)",
                strat.name, chosen_key, current[chosen_key], new_value, why)


# ── Capital reallocation ──────────────────────────────────────────────────────

def _reallocate_capital(regime: str):
    names   = list(STRATEGIES.keys())
    current = param_store.get_allocations(names)

    # Score = expectancy over last 20 trades; None if no trades yet
    scores: dict[str, float | None] = {}
    for name in names:
        perf = trade_logger.get_strategy_performance(name, last_n=20)
        scores[name] = perf["expectancy"] if perf["trades"] >= 3 else None

    with_data = {k: v for k, v in scores.items() if v is not None}
    if not with_data:
        logger.info("Allocation: no strategy has enough trades — keeping %s", current)
        return

    # Convert expectancy to positive weights (shift so worst gets a floor)
    min_exp = min(with_data.values())
    raw = {k: (v - min_exp + 1.0) for k, v in with_data.items()}
    total_raw = sum(raw.values())
    data_target = {k: v / total_raw for k, v in raw.items()}

    # Strategies without data keep their current weight; scale the rest
    reserved = sum(current[k] for k in names if scores[k] is None)
    available = 1.0 - reserved

    target = {}
    for k in names:
        if scores[k] is None:
            target[k] = current[k]
        else:
            target[k] = data_target[k] * available

    # Smooth: cap the weekly shift, then clamp to [min, max] and renormalise
    new_alloc = {}
    for k in names:
        shift = max(-config.ALLOC_MAX_SHIFT,
                    min(config.ALLOC_MAX_SHIFT, target[k] - current[k]))
        new_alloc[k] = max(config.ALLOC_MIN,
                           min(config.ALLOC_MAX, current[k] + shift))
    total = sum(new_alloc.values())
    new_alloc = {k: v / total for k, v in new_alloc.items()}

    changed = any(abs(new_alloc[k] - current[k]) > 0.01 for k in names)
    if not changed:
        logger.info("Allocation: no meaningful change — keeping %s", current)
        return

    reason = "; ".join(
        f"{k}: expectancy ₹{scores[k]}" if scores[k] is not None else f"{k}: no data"
        for k in names
    )
    param_store.set_allocations(new_alloc, source="tuner_realloc",
                                regime=regime, reason=reason)
