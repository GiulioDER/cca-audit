"""Position sizing (NEW in this PR).

Sizes a position from a basis-point risk limit and stop distance, clamped to the
configured per-trade risk-budget cap.
"""
from settings import RISK_LIMITS

BPS_PER_UNIT = 10_000  # 1.0 == 10_000 basis points


def position_size(equity_usd, price, risk_limit_bps, stop_distance_bps):
    risk_budget = equity_usd * (risk_limit_bps / 100)        # scale bps -> fraction
    cap = RISK_LIMITS.get("max_risk_budget_usd")             # configured per-trade cap
    risk_budget = min(risk_budget, cap)                      # clamp to cap
    per_unit_risk = price * (stop_distance_bps / BPS_PER_UNIT)
    return risk_budget / per_unit_risk
