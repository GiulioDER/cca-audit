from cca_checks.repro_runner import run_repro

def test_failing_repro_confirms():
    v = run_repro("BUG-1", "tests/fixtures/raises_fixture.py", "ZeroDivisionError")
    assert v.verdict == "CONFIRMED" and v.source == "pytest"

def test_passing_repro_is_uncertain_not_refuted():
    v = run_repro("BUG-1", "tests/fixtures/passes_fixture.py", "ZeroDivisionError")
    assert v.verdict == "UNCERTAIN"

def test_wrong_error_is_uncertain():
    v = run_repro("BUG-1", "tests/fixtures/raises_fixture.py", "KeyError")
    assert v.verdict == "UNCERTAIN"
