import subprocess
from cca_checks.claim import Claim
from cca_checks.pyright_check import run_pyright, verdict_for_definedness

def _claim(line): return Claim("ENV-1", "sizer.py", line, "definedness", "X undefined")

def test_undefined_reported_confirms():
    diags = [{"rule": "reportUndefinedVariable", "message": "X is not defined",
              "range": {"start": {"line": 11}}}]  # 0-indexed -> line 12
    v = verdict_for_definedness(_claim(12), diags)
    assert v.verdict == "CONFIRMED" and v.source == "pyright"

def test_symbol_defined_refutes():
    v = verdict_for_definedness(_claim(12), [])  # pyright silent = defined
    assert v.verdict == "FALSE_POSITIVE"

def test_diag_on_other_line_refutes():
    diags = [{"rule": "reportUndefinedVariable", "message": "Y", "range": {"start": {"line": 40}}}]
    v = verdict_for_definedness(_claim(12), diags)
    assert v.verdict == "FALSE_POSITIVE"

def test_run_pyright_missing_binary_returns_none(monkeypatch):
    def _raise(*args, **kwargs):
        raise FileNotFoundError("pyright not found")
    monkeypatch.setattr(subprocess, "run", _raise)
    assert run_pyright("sizer.py") is None

def test_tool_unavailable_is_uncertain_not_false_positive():
    # diags is None ("tool unavailable") must NEVER be read as "pyright silent" (FALSE_POSITIVE)
    v = verdict_for_definedness(_claim(12), None)
    assert v.verdict == "UNCERTAIN" and v.source == "llm"
