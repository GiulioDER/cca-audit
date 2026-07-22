import subprocess
import sys

import pytest

from cca_checks import property_check as pcheck
from cca_checks.property_check import run_properties

FALSIFYING = """
E   cca_checks.properties.PropertyViolation: PROPERTY monotonic violated | inputs=(0.1, 0.3) | observed=(0.145, 0.26) | required=result non-increasing in arg 1
E
E   Falsifying example: test_growth(
E       mu=0.1,
E       vol=0.3,
E   )
"""

# Real pytest output for a @given test that raises an exception unrelated to
# the declared property (e.g. a ZeroDivisionError deep in the code under
# test). Hypothesis still shrinks and prints the banner -- this must NOT
# confirm, since there is no "PROPERTY ... violated" line anywhere.
FALSIFYING_UNRELATED_EXCEPTION = """
E   ZeroDivisionError: float division by zero
E
E   Falsifying example: test_growth(
E       mu=0.0,
E       vol=0.0,
E   )
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


def test_falsifying_banner_without_property_line_is_uncertain(monkeypatch):
    # Hypothesis prints "Falsifying example:" for ANY exception raised inside
    # a @given test, not only PropertyViolation -- an unrelated crash (e.g. a
    # ZeroDivisionError in the code under test) shrinks and banners exactly
    # like a real violation. Without the "PROPERTY ... violated" line, this
    # must NOT confirm; that would certify a numeric finding on the basis of
    # an incidental exception that never touched the declared property.
    monkeypatch.setattr(subprocess, "run",
                        fake(1, FALSIFYING_UNRELATED_EXCEPTION))
    v = run_properties("NUM-1", "t.py")
    assert v.verdict == "UNCERTAIN"
    assert v.verdict != "CONFIRMED"
    assert "not a declared property violation" in v.evidence
    assert "ZeroDivisionError" in v.evidence


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
    for rc, out in [
        (0, "passed"),
        (1, FALSIFYING),
        (1, "assert 1 == 2"),
        (1, FALSIFYING_UNRELATED_EXCEPTION),
        (5, "no tests"),
    ]:
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


# --- banner rename (Hypothesis 6.159.0 renamed "Falsifying example:" to
# "Failing test case:" with no deprecation window) ---------------------------

@pytest.mark.parametrize("banner", ["Falsifying example", "Failing test case"])
def test_both_banner_wordings_are_recognised(monkeypatch, banner):
    # Same fixture as FALSIFYING above, just with the banner literal swapped --
    # this is what pinning to one wording actually broke: a real property
    # violation on Hypothesis >=6.159 read as UNCERTAIN instead of CONFIRMED,
    # silently, because rc==1 and the PROPERTY line were both present but the
    # banner regex missed.
    out = (
        "E   cca_checks.properties.PropertyViolation: PROPERTY monotonic violated "
        "| inputs=(0.1, 0.3) | observed=(0.145, 0.26) | required=result "
        "non-increasing in arg 1\n"
        "E\n"
        f"E   {banner}: test_growth(\n"
        "E       mu=0.1,\n"
        "E       vol=0.3,\n"
        "E   )\n"
    )
    monkeypatch.setattr(subprocess, "run", fake(1, out))
    v = run_properties("NUM-1", "t_NUM-1_props.py")
    assert v.verdict == "CONFIRMED"
    assert v.source == "hypothesis"
    assert banner in v.evidence


def test_mixed_banner_wordings_still_escalate_as_ambiguous(monkeypatch):
    # The multi-distinct-banner escalation (see test_selfaudit_hardening.py)
    # must fire regardless of which wording each banner uses -- e.g. a repo
    # audited across a Hypothesis upgrade, or two dependencies pinning
    # different Hypothesis versions inside the same collection. Two distinct
    # shrunk inputs, one of each literal wording, must still be UNCERTAIN --
    # not a guessed pairing between an old-style and a new-style banner.
    out = (
        "Falsifying example: test_bounded(x=6.0,)\n"
        "ZeroDivisionError: division by zero\n"
        "\n"
        "Failing test case: test_bounded(x=4.0,)\n"
        "PROPERTY bounded violated | inputs=(4.0,) | observed=9.0 | "
        "required=0 <= result <= 1\n"
    )
    monkeypatch.setattr(subprocess, "run", fake(1, out))
    v = run_properties("NUM-1", "t.py")
    assert v.verdict == "UNCERTAIN"
    assert v.verdict != "CONFIRMED"
    assert "multiple falsifying examples reported" in v.evidence


def test_property_check_banner_matches_installed_hypothesis(tmp_path):
    """Guard against the next rename: run a real, always-failing property
    through the ACTUALLY INSTALLED Hypothesis and assert its banner is one
    _BANNER recognises.

    This is deliberately not mocked. If Hypothesis renames the banner again,
    this test fails loudly -- pointing straight at pcheck._BANNER -- instead
    of every CONFIRMED silently degrading to UNCERTAIN with no test noticing,
    which is exactly what happened between 6.158.0 and 6.159.0.
    """
    pytest.importorskip("hypothesis", reason="numeric extra not installed")
    fixture = tmp_path / "t_banner_guard_props.py"
    fixture.write_text(
        "from hypothesis import given, strategies as st\n"
        "\n"
        "@given(st.integers())\n"
        "def test_always_fails(x):\n"
        "    assert False\n",
        encoding="utf-8",
    )
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         "--", str(fixture)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=pcheck.TIMEOUT_S,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    assert proc.returncode == 1, (
        "fixture did not fail the way this guard expects; output:\n" + out
    )
    match = pcheck._FALSIFYING.search(out)
    assert match, (
        "Hypothesis's banner no longer matches pcheck._BANNER "
        f"({pcheck._BANNER!r}) -- it has been renamed again. Update _BANNER "
        "in cca_checks/property_check.py to add the new wording, or every "
        "CONFIRMED numeric finding degrades to UNCERTAIN silently. Captured "
        "output:\n" + out
    )
