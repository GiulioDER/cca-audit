import subprocess
import sys

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

def test_timeout_escalates_to_uncertain(monkeypatch):
    # A hanging repro must not wedge the verifier forever; it must escalate.
    def boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="pytest", timeout=120)
    monkeypatch.setattr(subprocess, "run", boom)
    v = run_repro("BUG-1", "tests/fixtures/raises_fixture.py", "ZeroDivisionError")
    assert v.verdict == "UNCERTAIN" and v.source == "pytest"

def test_launch_failure_escalates_to_uncertain(monkeypatch):
    # pytest/interpreter not launchable (e.g. no `python`/pytest) must escalate,
    # not crash the CLI with an uncaught OSError.
    def boom(*a, **k):
        raise FileNotFoundError("interpreter not found")
    monkeypatch.setattr(subprocess, "run", boom)
    v = run_repro("BUG-1", "tests/fixtures/raises_fixture.py", "ZeroDivisionError")
    assert v.verdict == "UNCERTAIN"

def test_invocation_is_hardened(monkeypatch):
    # Locks in the fixes: current interpreter (not bare "python"), an option-injection
    # guard, an explicit UTF-8 decode, and a timeout.
    captured = {}
    class _Proc:
        returncode = 0
        stdout = ""
        stderr = ""
    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return _Proc()
    monkeypatch.setattr(subprocess, "run", fake_run)
    run_repro("BUG-1", "tests/fixtures/raises_fixture.py", "X")
    argv, kw = captured["argv"], captured["kwargs"]
    assert argv[0] == sys.executable          # not bare "python" -> right venv interpreter
    assert "--" in argv                        # path can't be parsed as a tool option
    assert kw.get("encoding") == "utf-8"       # decode is locale-independent
    assert kw.get("timeout")                   # never hangs unbounded
