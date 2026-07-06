"""Sizing service boundary.

PRE-EXISTING module; this PR rewires it to delegate the math to the new
`sizer.position_size` (previously the sizing was computed inline here).
"""
from schemas import SizingRequest
from sizer import position_size


def size_position(payload: dict) -> float:
    req = SizingRequest(**payload)  # validates at the boundary: price > 0, stop_distance_bps >= 1
    return position_size(
        req.equity_usd, req.price, req.risk_limit_bps, req.stop_distance_bps
    )
