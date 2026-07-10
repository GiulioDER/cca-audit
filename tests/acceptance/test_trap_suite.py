import shutil
import pytest
from pathlib import Path

from cca_checks.claim import Claim
from cca_checks.pyright_check import (
    NULLABILITY_RULES,
    TYPE_RULES,
    run_pyright,
    verdict_for_claim,
    verdict_for_definedness,
)
from cca_checks.repro_runner import run_repro

pyright_missing = shutil.which("pyright") is None

@pytest.mark.skipif(pyright_missing, reason="pyright not installed")
def test_defined_symbol_is_dropped_by_pyright():
    path = "examples/bps-sizing/definedness_trap/service.py"
    claim = Claim("ENV-1", path, 1, "definedness", "RISK_CAP_USD undefined")
    v = verdict_for_definedness(claim, run_pyright(path))
    assert v.verdict == "FALSE_POSITIVE" and "pyright" in v.source

def test_guarded_div_by_zero_is_escalated_not_refuted():
    # a repro that passes (guard holds) must yield UNCERTAIN, never FALSE_POSITIVE
    v = run_repro("BUG-1", "tests/fixtures/passes_fixture.py", "ZeroDivisionError")
    assert v.verdict == "UNCERTAIN"


FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def _settle(fixture_name, line, claim_type, rules):
    path = str(FIXTURES / fixture_name)
    claim = Claim(fixture_name, path, line, claim_type)
    return verdict_for_claim(claim, run_pyright(path), rules)


@pytest.mark.skipif(shutil.which("pyright") is None, reason="pyright not on PATH")
def test_guarded_optional_access_is_refuted_with_an_artifact():
    """The marquee case: 'possible null dereference' on a value guarded three lines up."""
    v = _settle("guarded_optional.py", 11, "nullability", NULLABILITY_RULES)
    assert v.verdict == "FALSE_POSITIVE"
    assert v.source == "pyright"
    assert v.evidence.strip()


@pytest.mark.skipif(shutil.which("pyright") is None, reason="pyright not on PATH")
def test_unguarded_optional_access_is_confirmed():
    v = _settle("unguarded_optional.py", 9, "nullability", NULLABILITY_RULES)
    assert v.verdict == "CONFIRMED"
    assert v.source == "pyright"
    assert "reportOptionalMemberAccess" in v.evidence


@pytest.mark.skipif(shutil.which("pyright") is None, reason="pyright not on PATH")
def test_untyped_optional_access_is_escalated_never_refuted():
    """pyright is silent here because it is blind, not because the access is safe.

    Refuting would trade a false positive for a false negative -- the one trade
    this pipeline exists to refuse.
    """
    v = _settle("untyped_optional.py", 2, "nullability", NULLABILITY_RULES)
    assert v.verdict == "UNCERTAIN"
    assert v.verdict != "FALSE_POSITIVE"
    assert "no type information" in v.evidence


@pytest.mark.skipif(shutil.which("pyright") is None, reason="pyright not on PATH")
def test_bad_argument_type_is_confirmed():
    v = _settle("bad_arg_type.py", 5, "type", TYPE_RULES)
    assert v.verdict == "CONFIRMED"
    assert v.source == "pyright"
