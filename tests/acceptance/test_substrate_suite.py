import re

import pytest

from cca_checks import property_check as pcheck
from cca_checks.property_check import run_properties

pytest.importorskip("hypothesis", reason="numeric extra not installed")
pytest.importorskip("mpmath", reason="numeric extra not installed")

UNSTABLE = "tests/fixtures/substrate/props_unstable.py"
STABLE = "tests/fixtures/substrate/props_stable.py"
SIGN_TRAP = "tests/fixtures/substrate/props_sign_trap.py"


def test_cancellation_is_confirmed_with_a_falsifying_example():
    v = run_properties("SUB-ACC-1", UNSTABLE)
    assert v.verdict == "CONFIRMED"
    assert v.source == "hypothesis"
    # Not a fixed literal: Hypothesis's banner wording depends on the
    # installed version (see cca_checks/property_check.py:_BANNER).
    assert re.search(pcheck._BANNER, v.evidence)
    assert "substrate_agrees" in v.evidence


def test_confirmation_is_reproducible():
    a = run_properties("SUB-ACC-1", UNSTABLE)
    b = run_properties("SUB-ACC-1", UNSTABLE)
    assert a.evidence == b.evidence


def test_the_stable_variant_is_not_confirmed():
    v = run_properties("SUB-ACC-2", STABLE)
    assert v.verdict == "UNCERTAIN"
    assert v.verdict != "CONFIRMED"
    assert "no counterexample" in v.evidence


def test_sign_error_is_structurally_invisible_to_this_layer():
    # The blindness probe, end to end. A CONFIRMED here would mean the check is
    # reporting divergence where the two substrates genuinely agree.
    v = run_properties("SUB-ACC-3", SIGN_TRAP)
    assert v.verdict == "UNCERTAIN"
    assert v.verdict != "CONFIRMED"
    assert "no counterexample" in v.evidence
