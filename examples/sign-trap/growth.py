"""A sign trap: the expression is well formed, the names are right, the meaning is inverted."""


def expected_log_growth(mu: float, vol: float, t: float) -> float:
    """Expected log growth over horizon t.

    Variance drag should REDUCE expected log growth. This returns the opposite.
    """
    return (mu + 0.5 * vol ** 2) * t
