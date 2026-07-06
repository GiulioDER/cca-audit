"""Request model for the position sizer (demo).

All risk quantities are expressed in basis points (1 bp = 0.01% = 1/10_000).
"""
from pydantic import BaseModel, Field


class SizingRequest(BaseModel):
    """A validated request to size a position."""

    equity_usd: float = Field(gt=0, description="Account equity in USD.")
    price: float = Field(gt=0, description="Instrument price in USD; must be > 0.")
    risk_limit_bps: int = Field(
        ge=1, le=10_000, description="Max fraction of equity to risk, in bps (50 = 0.50%)."
    )
    stop_distance_bps: int = Field(
        ge=1, description="Distance from entry to stop, in bps; must be >= 1."
    )
