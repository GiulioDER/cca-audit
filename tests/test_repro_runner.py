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

def test_no_expected_error_is_uncertain_not_confirmed():
    # rc==1 but no predicted error to confirm against -> must NOT auto-CONFIRM
    v = run_repro("BUG-1", "tests/fixtures/raises_fixture.py", None)
    assert v.verdict == "UNCERTAIN" and v.verdict != "CONFIRMED"

def test_collection_error_is_uncertain_never_confirmed():
    # nonexistent test path -> pytest rc=4 (usage/collection error), not a test failure.
    # Must degrade to UNCERTAIN, never be misread as CONFIRMED.
    v = run_repro("BUG-1", "tests/fixtures/does_not_exist.py", "ZeroDivisionError")
    assert v.verdict == "UNCERTAIN"
    assert v.verdict != "FALSE_POSITIVE"
