from cca_checks.claim import Claim
from cca_checks.pyright_check import verdict_for_definedness

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
