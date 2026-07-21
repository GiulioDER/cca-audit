import pytest

from cca_checks.property_check import run_properties

pytest.importorskip("hypothesis", reason="numeric extra not installed")

VIOLATED = "tests/fixtures/numeric/props_violated.py"
FIXED = "tests/fixtures/numeric/props_fixed.py"
HOLD = "tests/fixtures/numeric/props_hold.py"


def test_sign_trap_is_confirmed_with_a_falsifying_example():
    v = run_properties("NUM-ACC-1", VIOLATED)
    assert v.verdict == "CONFIRMED"
    assert v.source == "hypothesis"
    assert "Falsifying example" in v.evidence
    assert "monotonic" in v.evidence


def test_the_corrected_twin_is_not_confirmed():
    # The same property against the fixed implementation. A checker that
    # confirmed both would be discriminating nothing.
    v = run_properties("NUM-ACC-3", FIXED)
    assert v.verdict == "UNCERTAIN"
    assert v.verdict != "CONFIRMED"


def test_confirmation_is_reproducible():
    # derandomize=True: the same audit must yield the same artifact.
    a = run_properties("NUM-ACC-1", VIOLATED)
    b = run_properties("NUM-ACC-1", VIOLATED)
    assert a.evidence == b.evidence


def test_a_property_that_holds_on_buggy_code_never_refutes():
    # The honest blindness case. The defect is real and still present; this
    # property simply cannot see it. UNCERTAIN, never FALSE_POSITIVE.
    v = run_properties("NUM-ACC-2", HOLD)
    assert v.verdict == "UNCERTAIN"
    assert v.verdict != "FALSE_POSITIVE"
    assert "no counterexample" in v.evidence
