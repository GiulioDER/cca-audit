import re

import pytest

from cca_checks import property_check as pcheck
from cca_checks.property_check import run_properties

pytest.importorskip("hypothesis", reason="numeric extra not installed")
pytest.importorskip("mpmath", reason="numeric extra not installed")

UNSTABLE = "tests/fixtures/substrate/props_unstable.py"
STABLE = "tests/fixtures/substrate/props_stable.py"
SIGN_TRAP = "tests/fixtures/substrate/props_sign_trap.py"
CROSS_MODULE = "tests/fixtures/substrate/props_cross_module.py"
DEFAULT_BINDING = "tests/fixtures/substrate/props_default_binding.py"
CLOSURE_BINDING = "tests/fixtures/substrate/props_closure_binding.py"
STATEFUL = "tests/fixtures/substrate/props_stateful.py"


@pytest.mark.parametrize(
    "finding_id,path,verdict,evidence",
    [
        ("SUB-MATRIX-1", UNSTABLE, "CONFIRMED", "substrate_agrees"),
        ("SUB-MATRIX-2", STABLE, "UNCERTAIN", "no counterexample"),
        ("SUB-MATRIX-3", SIGN_TRAP, "UNCERTAIN", "no counterexample"),
        ("SUB-MATRIX-4", CROSS_MODULE, "UNCERTAIN", "substrate_lost"),
        ("SUB-MATRIX-5", DEFAULT_BINDING, "UNCERTAIN", "substrate_lost"),
        ("SUB-MATRIX-6", CLOSURE_BINDING, "UNCERTAIN", "substrate_lost"),
        ("SUB-MATRIX-7", STATEFUL, "UNCERTAIN", "nondeterministic replay"),
    ],
)
def test_scalar_oracle_capability_matrix(finding_id, path, verdict, evidence):
    result = run_properties(finding_id, path)
    assert result.verdict == verdict
    assert evidence in result.evidence
    if verdict == "UNCERTAIN":
        assert "PROPERTY substrate_agrees violated" not in result.evidence


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


@pytest.mark.parametrize("path", [CROSS_MODULE, STATEFUL])
def test_fail_closed_evidence_is_reproducible(path):
    a = run_properties("SUB-ACC-FAIL", path)
    b = run_properties("SUB-ACC-FAIL", path)
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
