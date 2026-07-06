"""Boundary request schema (PRE-EXISTING — not changed by this PR).

This is the trust boundary: every sizing request is validated here before any
downstream code runs. `price > 0` and `stop_distance_bps >= 1` are enforced.
"""
from pydantic import BaseModel, Field


class SizingRequest(BaseModel):
    equity_usd: float = Field(gt=0, description="Account equity in USD.")
    price: float = Field(gt=0, description="Instrument price in USD; must be > 0.")
    risk_limit_bps: int = Field(ge=1, le=10_000, description="Max equity to risk, in bps.")
    stop_distance_bps: int = Field(ge=1, description="Stop distance in bps; must be >= 1.")
