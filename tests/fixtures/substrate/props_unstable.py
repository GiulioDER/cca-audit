import os
import sys

from hypothesis import given, strategies as st

sys.path.insert(0, os.path.dirname(__file__))

from cca_checks.hypo import cca_settings              # noqa: E402
from cca_checks.substrate import assert_substrate_agrees  # noqa: E402
from targets import unstable                          # noqa: E402


@cca_settings
@given(x=st.floats(1e-9, 1e-6))
def test_float64_matches_the_reference(x):
    assert_substrate_agrees(unstable, (x,))
