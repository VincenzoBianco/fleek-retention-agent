"""Expected-value maths — the one place £ prizes and risk-adjusted EV are computed.

Extracted from plays._decide so there is a single source of truth shared by:
  - the deterministic decision path (plays.decide, the fallback), and
  - the agent's `expected_value` tool (retention_agent/tools.py).

This is a deliberate guardrail in the agentic design: the *agent* chooses the
categorical decision (segment, play, feature); the *money* is always computed
here, deterministically, from the same priors in config.py. That keeps the queue
"one honest number across three prizes" — an LLM never invents a £ figure, so
plays stay comparable and the ranking can't be talked into a different order.

Each function returns (prize_gmv, expected_value, prize_type). `prize_gmv` is the
descriptive headline; `expected_value` is the comparable, risk-adjusted number the
queue ranks on. Rounding matches the original inline maths exactly.
"""
from __future__ import annotations

from . import config
from .models import Account


def reengage_prize(a: Account) -> tuple[float, float, str]:
    """At-risk = FORWARD exposure (lost run-rate x 6mo, capped at window GMV),
    NOT lifetime GMV. EV discounts by the win-back rate."""
    prior_monthly = sum(a.monthly_gmv[:3]) / 3
    recent_monthly = sum(a.monthly_gmv[-3:]) / 3
    lost_monthly = max(0.0, prior_monthly - recent_monthly)
    at_risk = round(min(lost_monthly * 6, a.gmv_total_6m), 0)
    ev = round(config.SAVE_RATE * at_risk, 0)
    return at_risk, ev, "GMV at risk (fwd)"


def reengage_lost_monthly(a: Account) -> float:
    """The monthly run-rate drop — used for the human-readable reason string."""
    return max(0.0, sum(a.monthly_gmv[:3]) / 3 - sum(a.monthly_gmv[-3:]) / 3)


def onboard_prize(a: Account) -> tuple[float, float, str]:
    """Ranked on RAMP POTENTIAL (value of activating), not tiny current spend."""
    ramp = round(max(config.EARLY_SUCCESS_GMV_TARGET - a.gmv_total_6m,
                     config.EARLY_SUCCESS_GMV_TARGET * 0.3), 0)
    ev = round(config.ONBOARD_ACTIVATION_RATE * ramp, 0)
    return ramp, ev, "ramp potential"


def migrate_prize(a: Account, warm: bool | None = None) -> tuple[float, float, str]:
    """Prize is the GMV riding on a human (exposure, NOT at-risk). EV = modest
    expansion on the converted spend, discounted by the conversion rate. Warm/cold
    is a fact of the transaction tier (hybrid = warm), so EV stays a pure function
    of the account even under the agent."""
    if warm is None:
        warm = a.transaction_mode == "hybrid"
    on_a_human = round(a.gmv_total_6m * a.broker_reliance / 100, 0)
    convert = config.CONVERT_RATE_WARM if warm else config.CONVERT_RATE_COLD
    ev = round(convert * on_a_human * config.MIGRATION_EXPANSION, 0)
    return on_a_human, ev, "GMV on a human"


def grow_prize(a: Account, feature: str) -> tuple[float, float, str]:
    """Modelled uplift — a conservative slice of the engagement premium. Here the
    prize IS the EV (uplift already carries its own conservatism), matching the
    original code where priority == expected_value == uplift."""
    pct = config.GROWTH_UPLIFT_PCT.get(feature, min(config.GROWTH_UPLIFT_PCT.values()))
    uplift = round(a.gmv_total_6m * pct, 0)
    return uplift, uplift, "modelled uplift"


def prize_for(a: Account, play: str, feature: str | None = None) -> tuple[float, float, str]:
    """Dispatch on play name. Used by the `expected_value` agent tool so the agent
    can compare the £ of, say, reengage vs migrate for the same account."""
    if play == "reengage":
        return reengage_prize(a)
    if play == "onboard":
        return onboard_prize(a)
    if play == "migrate_to_selfserve":
        return migrate_prize(a)
    if play == "grow_selfserve":
        return grow_prize(a, feature or "chat")
    return 0.0, 0.0, ""
