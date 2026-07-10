import subprocess

import pytest

from cca_checks.claim import Claim
from cca_checks.pyright_check import (
    DEFINEDNESS_RULES,
    NULLABILITY_RULES,
    RULES_BY_CLAIM,
    TYPE_RULES,
    run_pyright,
    verdict_for_claim,
    verdict_for_definedness,
)


def diag(line_1based, rule, msg="boom"):
    """Build a synthetic pyright diagnostic. pyright's range.start.line is 0-indexed."""
    return {"range": {"start": {"line": line_1based - 1}}, "rule": rule, "message": msg}


def claim(claim_type="definedness", line=7):
    return Claim("F-1", "svc.py", line, claim_type)


def test_rule_sets_are_pairwise_disjoint():
    assert not DEFINEDNESS_RULES & NULLABILITY_RULES
    assert not DEFINEDNESS_RULES & TYPE_RULES
    assert not NULLABILITY_RULES & TYPE_RULES


def test_rules_by_claim_covers_the_three_claim_types():
    assert set(RULES_BY_CLAIM) == {"definedness", "nullability", "type"}


def test_matching_rule_at_line_confirms():
    diags = [diag(7, "reportUndefinedVariable", "\"RISK_CAP\" is not defined")]
    v = verdict_for_claim(claim(), diags, DEFINEDNESS_RULES)
    assert v.verdict == "CONFIRMED"
    assert v.source == "pyright"
    assert "reportUndefinedVariable" in v.evidence
    assert "is not defined" in v.evidence


def test_matching_rule_on_a_different_line_does_not_confirm():
    diags = [diag(99, "reportUndefinedVariable")]
    v = verdict_for_claim(claim(), diags, DEFINEDNESS_RULES)
    assert v.verdict == "FALSE_POSITIVE"


def test_unexpected_rule_at_the_line_escalates_rather_than_refuting():
    # pyright sees *something* here, just not what we asked about. Never refute blind:
    # a renamed rule must cost us a confirmation, not produce a false negative.
    diags = [diag(7, "reportSelfClsParameterName")]
    v = verdict_for_claim(claim(), diags, DEFINEDNESS_RULES)
    assert v.verdict == "UNCERTAIN"
    assert v.source == "pyright"
    assert "reportSelfClsParameterName" in v.evidence


def test_clean_line_refutes_a_definedness_claim():
    v = verdict_for_claim(claim(), [], DEFINEDNESS_RULES)
    assert v.verdict == "FALSE_POSITIVE"
    assert v.source == "pyright"


def test_definedness_refutation_evidence_is_unchanged_from_v3_0():
    v = verdict_for_claim(claim(), [], DEFINEDNESS_RULES)
    assert v.evidence == "pyright: no undefined-symbol diagnostic @ svc.py:7"


def test_missing_pyright_escalates_to_llm():
    v = verdict_for_claim(claim(), None, DEFINEDNESS_RULES)
    assert v.verdict == "UNCERTAIN"
    assert v.source == "llm"
    assert "unavailable" in v.evidence


def test_null_range_does_not_crash():
    diags = [{"range": None, "rule": "reportUndefinedVariable", "message": "m"}]
    v = verdict_for_claim(claim(), diags, DEFINEDNESS_RULES)
    assert v.verdict == "FALSE_POSITIVE"


def test_missing_range_key_does_not_crash():
    diags = [{"rule": "reportUndefinedVariable", "message": "m"}]
    v = verdict_for_claim(claim(), diags, DEFINEDNESS_RULES)
    assert v.verdict == "FALSE_POSITIVE"


def test_matched_diagnostic_missing_message_still_confirms_with_evidence():
    # _diag_at matches on `rule` alone; a diagnostic missing `message` must not
    # crash `hit['message']` and must still produce a non-empty artifact (the
    # rule name + location alone are sufficient evidence).
    diags = [{"range": {"start": {"line": 6}}, "rule": "reportOptionalMemberAccess"}]
    v = verdict_for_claim(claim("nullability"), diags, NULLABILITY_RULES)
    assert v.verdict == "CONFIRMED"
    assert v.source == "pyright"
    assert v.evidence.strip()
    assert "reportOptionalMemberAccess" in v.evidence


def test_verdict_for_definedness_delegates_to_verdict_for_claim():
    diags = [diag(7, "reportUnboundVariable", "possibly unbound")]
    assert verdict_for_definedness(claim(), diags).verdict == "CONFIRMED"
    assert verdict_for_definedness(claim(), []).verdict == "FALSE_POSITIVE"
    assert verdict_for_definedness(claim(), None).source == "llm"


@pytest.mark.parametrize("rule", sorted(NULLABILITY_RULES))
def test_every_nullability_rule_confirms_a_nullability_claim(rule):
    diags = [diag(7, rule)]
    v = verdict_for_claim(claim("nullability"), diags, NULLABILITY_RULES)
    assert v.verdict == "CONFIRMED"


@pytest.mark.parametrize("rule", sorted(TYPE_RULES))
def test_every_type_rule_confirms_a_type_claim(rule):
    diags = [diag(7, rule)]
    v = verdict_for_claim(claim("type"), diags, TYPE_RULES)
    assert v.verdict == "CONFIRMED"


# --- run_pyright crash-path cascade (CRITICAL 1: a crashed/garbled pyright must
# never be read as "ran clean" -- that silence is what licenses a FALSE_POSITIVE) ---

class FakeCompletedProcess:
    def __init__(self, stdout):
        self.stdout = stdout


def _patch_run(monkeypatch, stdout):
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: FakeCompletedProcess(stdout)
    )


def test_run_pyright_returns_none_on_empty_stdout(monkeypatch):
    _patch_run(monkeypatch, "")
    assert run_pyright("whatever.py") is None


def test_run_pyright_returns_none_on_unparseable_stdout(monkeypatch):
    _patch_run(monkeypatch, "not json")
    assert run_pyright("whatever.py") is None


def test_run_pyright_returns_none_when_zero_files_analyzed(monkeypatch):
    import json as _json
    _patch_run(monkeypatch, _json.dumps(
        {"summary": {"filesAnalyzed": 0}, "generalDiagnostics": []}
    ))
    assert run_pyright("whatever.py") is None


def test_run_pyright_returns_none_when_summary_missing(monkeypatch):
    import json as _json
    _patch_run(monkeypatch, _json.dumps({"generalDiagnostics": []}))
    assert run_pyright("whatever.py") is None


def test_run_pyright_returns_empty_list_when_genuinely_clean(monkeypatch):
    import json as _json
    _patch_run(monkeypatch, _json.dumps(
        {"summary": {"filesAnalyzed": 1}, "generalDiagnostics": []}
    ))
    result = run_pyright("whatever.py")
    assert result == []
    assert result is not None


def test_run_pyright_returns_none_on_timeout(monkeypatch):
    def raise_timeout(*a, **k):
        raise subprocess.TimeoutExpired(cmd="pyright", timeout=120)
    monkeypatch.setattr(subprocess, "run", raise_timeout)
    assert run_pyright("whatever.py") is None


def test_run_pyright_returns_none_when_binary_missing(monkeypatch):
    def raise_fnf(*a, **k):
        raise FileNotFoundError("pyright not found")
    monkeypatch.setattr(subprocess, "run", raise_fnf)
    assert run_pyright("whatever.py") is None


def test_crashed_pyright_escalates_definedness_claim_not_refutes(monkeypatch):
    """Composition test: a definedness claim whose run_pyright crashed (None)
    must yield UNCERTAIN, never FALSE_POSITIVE -- a wrong refutation drops a
    real bug."""
    _patch_run(monkeypatch, "")
    diags = run_pyright("whatever.py")
    assert diags is None
    v = verdict_for_claim(claim("definedness"), diags, DEFINEDNESS_RULES)
    assert v.verdict == "UNCERTAIN"
    assert v.verdict != "FALSE_POSITIVE"
    assert v.source == "llm"
