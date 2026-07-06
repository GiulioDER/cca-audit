"""Position sizing from a basis-point risk budget (demo feature).

Feature PR: size a position so that hitting the stop loses at most the risk budget,
with the risk limit and stop distance both expressed in basis points.
"""
from model import SizingRequest

BPS_PER_UNIT = 10_000  # 1.0 == 10_000 basis points


def position_size(req: SizingRequest) -> float:
    """Return the position size (in units) for a sizing request.

    risk_budget   = equity * risk_limit      (risk_limit in bps)
    per_unit_risk = price  * stop_distance    (stop_distance in bps)
    size          = risk_budget / per_unit_risk
    """
    risk_budget = req.equity_usd * (req.risk_limit_bps / 100)          # scale bps -> fraction
    per_unit_risk = req.price * (req.stop_distance_bps / BPS_PER_UNIT)  # scale bps -> fraction
    return risk_budget / per_unit_risk
