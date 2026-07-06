"""Tests for the position sizer (demo).

Note: this suite only checks that the size is positive and finite — it does NOT
assert the magnitude. That is deliberate: it mirrors the very common real-world case
where a feature ships with a smoke test that passes even though the sizing math is
100x off. CCA catches the magnitude bug by reasoning, not via a failing test.
"""
from model import SizingRequest
from sizer import position_size


def test_position_size_is_positive():
    req = SizingRequest(
        equity_usd=100_000,
        price=50.0,
        risk_limit_bps=50,       # intended: risk 0.50% of equity
        stop_distance_bps=200,   # intended: 2.00% stop
    )
    size = position_size(req)
    assert size > 0
