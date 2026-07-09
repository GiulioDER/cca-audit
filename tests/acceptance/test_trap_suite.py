import shutil
import pytest
from cca_checks.claim import Claim
from cca_checks.pyright_check import run_pyright, verdict_for_definedness
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
