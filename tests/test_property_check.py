import subprocess
import sys

import pytest

from cca_checks import property_check as pcheck
from cca_checks.property_check import run_properties

FALSIFYING = """
E   cca_checks.properties.PropertyViolation: PROPERTY monotonic violated | inputs=(0.1, 0.3) | observed=(0.145, 0.26) | required=result non-increasing in arg 1

Falsifying example: test_growth(
    mu=0.1,
    vol=0.3,
)
"""


class _Proc:
    def __init__(self, rc, out="", err=""):
        self.returncode = rc
        self.stdout = out
        self.stderr = err


def fake(rc, out="", err=""):
    return lambda *a, **k: _Proc(rc, out, err)


def test_property_violation_confirms(monkeypatch):
    monkeypatch.setattr(subprocess, "run", fake(1, FALSIFYING))
    v = run_properties("NUM-1", "t_NUM-1_props.py")
    assert v.verdict == "CONFIRMED"
    assert v.source == "hypothesis"
    assert "Falsifying example" in v.evidence


def test_clean_run_is_uncertain_never_refuted(monkeypatch):
    monkeypatch.setattr(subprocess, "run", fake(0, "2 passed"))
    v = run_properties("NUM-1", "t.py")
    assert v.verdict == "UNCERTAIN"
    assert v.verdict != "FALSE_POSITIVE"
    assert "200 examples" in v.evidence
    assert v.evidence.endswith("; escalated")


def test_missing_hypothesis_is_uncertain_not_a_pass(monkeypatch):
    monkeypatch.setattr(subprocess, "run",
                        fake(2, "ModuleNotFoundError: No module named 'hypothesis'"))
    v = run_properties("NUM-1", "t.py")
    assert v.verdict == "UNCERTAIN"
    assert "hypothesis not installed" in v.evidence


def test_missing_hypothesis_is_detected_before_the_returncode(monkeypatch):
    # A missing module can surface as rc=1 or rc=2 depending on where it is
    # imported. The dependency check must not depend on which.
    monkeypatch.setattr(subprocess, "run",
                        fake(1, "ModuleNotFoundError: No module named 'hypothesis'"))
    v = run_properties("NUM-1", "t.py")
    assert "hypothesis not installed" in v.evidence


def test_collection_error_is_uncertain_never_confirmed(monkeypatch):
    monkeypatch.setattr(subprocess, "run", fake(4, "ERROR: file or directory not found"))
    v = run_properties("NUM-1", "nope.py")
    assert v.verdict == "UNCERTAIN"
    assert "rc=4" in v.evidence


def test_failure_without_a_falsifying_example_is_uncertain(monkeypatch):
    # A plain assertion failure is not a property violation. Reading it as one
    # would let any red test CONFIRM a numeric finding.
    monkeypatch.setattr(subprocess, "run", fake(1, "assert 1 == 2"))
    v = run_properties("NUM-1", "t.py")
    assert v.verdict == "UNCERTAIN"
    assert "without a falsifying example" in v.evidence


def test_timeout_escalates_to_uncertain(monkeypatch):
    def boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="pytest", timeout=120)
    monkeypatch.setattr(subprocess, "run", boom)
    v = run_properties("NUM-1", "t.py")
    assert v.verdict == "UNCERTAIN"
    assert v.source == "hypothesis"
    assert "timed out" in v.evidence


def test_launch_failure_escalates_to_uncertain(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError("interpreter not found")
    monkeypatch.setattr(subprocess, "run", boom)
    v = run_properties("NUM-1", "t.py")
    assert v.verdict == "UNCERTAIN"


def test_false_positive_is_unreachable(monkeypatch):
    # The asymmetry is the contract: this checker may never refute a finding.
    for rc, out in [(0, "passed"), (1, FALSIFYING), (1, "assert 1 == 2"), (5, "no tests")]:
        monkeypatch.setattr(subprocess, "run", fake(rc, out))
        assert run_properties("NUM-1", "t.py").verdict != "FALSE_POSITIVE"


def test_invocation_is_hardened(monkeypatch):
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return _Proc(0, "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    run_properties("NUM-1", "t.py")
    argv, kw = captured["argv"], captured["kwargs"]
    assert argv[0] == sys.executable
    assert "--" in argv
    assert argv[-1] == "t.py"
    assert kw.get("encoding") == "utf-8"
    assert kw.get("timeout") == pcheck.TIMEOUT_S
