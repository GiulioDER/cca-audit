"""A sign trap and its corrected twin.

`expected_log_growth` carries the defect this feature exists to catch: the
variance term enters with the wrong sign. The expression is well formed and the
names are right; only the meaning is inverted, which is exactly what reads as
correct on review.
"""


def expected_log_growth(mu: float, vol: float, t: float) -> float:
    """BUGGY: variance should drag growth down, not push it up."""
    return (mu + 0.5 * vol ** 2) * t


def expected_log_growth_fixed(mu: float, vol: float, t: float) -> float:
    """CORRECT: variance drag reduces expected log growth."""
    return (mu - 0.5 * vol ** 2) * t
