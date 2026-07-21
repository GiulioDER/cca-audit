import os
import sys

from hypothesis import given, strategies as st

sys.path.insert(0, os.path.dirname(__file__))

from cca_checks.hypo import cca_settings      # noqa: E402
from cca_checks.properties import assert_limit  # noqa: E402
from drift import expected_log_growth        # noqa: E402


@cca_settings
@given(mu=st.floats(-0.5, 0.5), t=st.floats(0.01, 5.0))
def test_limit_at_zero_volatility(mu, t):
    # This property HOLDS on the buggy function: at vol == 0 the flipped term
    # vanishes, so the defect is invisible here. Keeping it proves the checker
    # returns UNCERTAIN rather than pretending a clean run refutes the finding.
    assert_limit(expected_log_growth, (mu, 0.5, t), index=1,
                 approaching=0.0, expected=mu * t)
