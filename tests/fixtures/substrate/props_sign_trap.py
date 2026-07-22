import os
import sys

from hypothesis import given, strategies as st

sys.path.insert(0, os.path.dirname(__file__))

from cca_checks.hypo import cca_settings              # noqa: E402
from cca_checks.substrate import assert_substrate_agrees  # noqa: E402
from targets import sign_trap                         # noqa: E402


@cca_settings
@given(
    mu=st.floats(-0.5, 0.5),
    vol=st.floats(0.01, 1.0),
    t=st.floats(0.01, 5.0),
)
def test_float64_matches_the_reference(mu, vol, t):
    # The sign defect is real and present. Both substrates compute the same wrong
    # formula, so this passes — and must.
    assert_substrate_agrees(sign_trap, (mu, vol, t))
