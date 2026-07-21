import os
import sys

from hypothesis import given, strategies as st

sys.path.insert(0, os.path.dirname(__file__))

from cca_checks.hypo import cca_settings              # noqa: E402
from cca_checks.properties import assert_monotonic_in  # noqa: E402
from growth import expected_log_growth               # noqa: E402


@cca_settings
@given(
    mu=st.floats(-0.5, 0.5),
    vol=st.floats(0.01, 1.0),
    t=st.floats(0.01, 5.0),
)
def test_growth_decreases_with_volatility(mu, vol, t):
    assert_monotonic_in(expected_log_growth, (mu, vol, t), index=1,
                        direction="decreasing", delta=0.1)
