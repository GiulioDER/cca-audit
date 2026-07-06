"""Application risk settings (PRE-EXISTING — not changed by this PR).

The per-trade risk-budget cap and other risk limits are defined here and consumed
across the sizing code.
"""

RISK_LIMITS = {
    "max_risk_budget_usd": 50_000.0,  # hard cap on the per-trade risk budget
    "max_leverage": 20.0,
}
