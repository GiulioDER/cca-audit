import textwrap

import pytest

from cca_checks.claim import Claim
from cca_checks import pyright_check as pc
from cca_checks.pyright_check import (
    NULLABILITY_RULES,
    DEFINEDNESS_RULES,
    enclosing_span,
    pyright_is_blind_at,
    verdict_for_claim,
)


def write(tmp_path, name, src):
    p = tmp_path / name
    p.write_text(textwrap.dedent(src).lstrip(), encoding="utf-8")
    return str(p)


# --- enclosing_span --------------------------------------------------------

def test_enclosing_span_finds_the_function_containing_the_line(tmp_path):
    path = write(tmp_path, "m.py", """
        def a():
            x = 1
            return x

        def b(card):
            return card.token
    """)
    # after dedent().lstrip(): line 5 is `def b`, line 6 is the access
    assert enclosing_span(path, 6) == (5, 6)
    assert enclosing_span(path, 2) == (1, 3)


def test_enclosing_span_picks_the_innermost_function(tmp_path):
    path = write(tmp_path, "m.py", """
        def outer():
            def inner():
                return 1
            return inner
    """)
    assert enclosing_span(path, 3) == (2, 3)


def test_enclosing_span_falls_back_to_the_module(tmp_path):
    path = write(tmp_path, "m.py", """
        X = 1
        Y = 2
    """)
    lo, hi = enclosing_span(path, 1)
    assert lo == 1 and hi >= 2


def test_enclosing_span_handles_async_functions(tmp_path):
    path = write(tmp_path, "m.py", """
        async def fetch(card):
            return card.token
    """)
    assert enclosing_span(path, 2) == (1, 2)


# --- pyright_is_blind_at ---------------------------------------------------

def test_blind_when_strict_pyright_is_unavailable(tmp_path, monkeypatch):
    path = write(tmp_path, "m.py", "def f(x):\n    return x\n")
    monkeypatch.setattr(pc, "run_pyright_strict", lambda p: None)
    assert pyright_is_blind_at(path, 2) is True


def test_blind_when_the_probe_raises(tmp_path, monkeypatch):
    path = write(tmp_path, "m.py", "def f(x):\n    return x\n")

    def boom(p):
        raise OSError("pyright exploded")

    monkeypatch.setattr(pc, "run_pyright_strict", boom)
    assert pyright_is_blind_at(path, 2) is True


def test_blind_when_the_file_does_not_parse(tmp_path, monkeypatch):
    path = write(tmp_path, "m.py", "def f(:\n")
    monkeypatch.setattr(pc, "run_pyright_strict", lambda p: [])
    assert pyright_is_blind_at(path, 1) is True


def test_blind_when_a_blindness_rule_fires_inside_the_enclosing_function(tmp_path, monkeypatch):
    path = write(tmp_path, "m.py", """
        def charge(card):
            return card.token
    """)
    # reportMissingParameterType fires on the `def` line (1), not the access line (2)
    monkeypatch.setattr(pc, "run_pyright_strict", lambda p: [
        {"range": {"start": {"line": 0}}, "rule": "reportMissingParameterType", "message": "x"}
    ])
    assert pyright_is_blind_at(path, 2) is True


def test_not_blind_when_the_blindness_rule_is_in_a_different_function(tmp_path, monkeypatch):
    path = write(tmp_path, "m.py", """
        def typed(card: str) -> str:
            return card

        def untyped(card):
            return card
    """)
    # blindness diag on line 4 (`def untyped`) must not taint line 2 in `typed`
    monkeypatch.setattr(pc, "run_pyright_strict", lambda p: [
        {"range": {"start": {"line": 3}}, "rule": "reportMissingParameterType", "message": "x"}
    ])
    assert pyright_is_blind_at(path, 2) is False


def test_not_blind_when_strict_pyright_is_clean(tmp_path, monkeypatch):
    path = write(tmp_path, "m.py", "def f(x: int) -> int:\n    return x\n")
    monkeypatch.setattr(pc, "run_pyright_strict", lambda p: [])
    assert pyright_is_blind_at(path, 2) is False


# --- probe wiring into verdict_for_claim -----------------------------------

def test_nullability_refutation_is_escalated_when_pyright_is_blind():
    claim = Claim("F-1", "svc.py", 7, "nullability")
    v = verdict_for_claim(claim, [], NULLABILITY_RULES, blind_probe=lambda p, l: True)
    assert v.verdict == "UNCERTAIN"
    assert v.source == "pyright"
    assert "no type information" in v.evidence


def test_nullability_refutation_stands_when_pyright_can_see():
    claim = Claim("F-1", "svc.py", 7, "nullability")
    v = verdict_for_claim(claim, [], NULLABILITY_RULES, blind_probe=lambda p, l: False)
    assert v.verdict == "FALSE_POSITIVE"
    assert v.evidence == "pyright: no optional-access diagnostic @ svc.py:7"


def test_type_refutation_is_escalated_when_pyright_is_blind():
    claim = Claim("F-1", "svc.py", 7, "type")
    v = verdict_for_claim(claim, [], pc.TYPE_RULES, blind_probe=lambda p, l: True)
    assert v.verdict == "UNCERTAIN"


def test_definedness_never_runs_the_probe():
    calls = []

    def probe(p, l):
        calls.append((p, l))
        return True

    claim = Claim("F-1", "svc.py", 7, "definedness")
    v = verdict_for_claim(claim, [], DEFINEDNESS_RULES, blind_probe=probe)
    assert v.verdict == "FALSE_POSITIVE"
    assert calls == []


def test_probe_is_not_run_on_the_confirm_path():
    calls = []

    def probe(p, l):
        calls.append(1)
        return True

    diags = [{"range": {"start": {"line": 6}}, "rule": "reportOptionalMemberAccess", "message": "m"}]
    claim = Claim("F-1", "svc.py", 7, "nullability")
    v = verdict_for_claim(claim, diags, NULLABILITY_RULES, blind_probe=probe)
    assert v.verdict == "CONFIRMED"
    assert calls == []


# --- default probe wiring + end-to-end safety property ---------------------
# Every test above injects blind_probe=..., so `probe = blind_probe or
# pyright_is_blind_at` (the default binding in verdict_for_claim) is never
# exercised there. A typo in that default name would pass the whole suite above
# while silently disabling the safety property in production. These two tests
# drive the *default* probe -- the one real callers actually get.

def test_default_probe_is_wired_when_no_blind_probe_is_passed(monkeypatch):
    # No blind_probe kwarg at all: verdict_for_claim must fall back to the module's
    # real pyright_is_blind_at, not silently skip the blindness check.
    monkeypatch.setattr(pc, "pyright_is_blind_at", lambda path, line: True)
    claim = Claim("F-1", "svc.py", 7, "nullability")
    v = verdict_for_claim(claim, [], NULLABILITY_RULES)
    assert v.verdict == "UNCERTAIN"


def test_probe_failure_escalates_end_to_end_not_false_positive(tmp_path, monkeypatch):
    # This is the branch's central safety property: if the strict-pyright subprocess
    # itself blows up, the failure must propagate all the way through
    # pyright_is_blind_at into verdict_for_claim as UNCERTAIN -- never as a
    # FALSE_POSITIVE that silently drops a real null-dereference bug.
    path = write(tmp_path, "svc.py", """
        def charge(user):
            return user.card.token
    """)

    def boom(p):
        raise RuntimeError("pyright exploded")

    monkeypatch.setattr(pc, "run_pyright_strict", boom)
    claim = Claim("F-1", path, 2, "nullability")
    v = verdict_for_claim(claim, [], NULLABILITY_RULES)
    assert v.verdict == "UNCERTAIN"
    assert v.verdict != "FALSE_POSITIVE"
