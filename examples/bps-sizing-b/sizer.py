"""Position sizing (NEW in this PR — extracted from service.size_position).

Sizes a position from a basis-point risk limit and stop distance so that hitting
the stop loses at most the risk budget.
"""

BPS_PER_UNIT = 10_000  # 1.0 == 10_000 basis points


def position_size(equity_usd, price, risk_limit_bps, stop_distance_bps):
    risk_budget = equity_usd * (risk_limit_bps / 100)          # scale bps -> fraction
    per_unit_risk = price * (stop_distance_bps / BPS_PER_UNIT)  # scale bps -> fraction
    return risk_budget / per_unit_risk
