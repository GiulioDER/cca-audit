from settings import RISK_CAP_USD  # the "PR" line an auditor might flag as undefined

def cap(x: float) -> float:
    return min(x, RISK_CAP_USD)
