import shutil
from pathlib import Path

import pytest

from cca_checks.claim import Claim
from cca_checks.semgrep_check import verdict_for_taint

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "taint"

needs_semgrep = pytest.mark.skipif(shutil.which("semgrep") is None,
                                   reason="semgrep not on PATH")


def settle(fixture_name, line, sink_class="sql"):
    path = str(FIXTURES / fixture_name)
    return verdict_for_taint(Claim(fixture_name, path, line, "taint", sink_class=sink_class))


@needs_semgrep
def test_orm_with_no_raw_sql_sink_is_refuted_with_an_artifact():
    """The marquee: 'SQL injection' flagged on code whose ORM never builds raw SQL."""
    v = settle("orm_no_sink.py", 5)
    assert v.verdict == "FALSE_POSITIVE"
    assert v.source == "semgrep"
    assert "no sql sink in the enclosing scope" in v.evidence


@needs_semgrep
def test_concatenated_sql_is_escalated_with_the_taint_match_never_refuted():
    v = settle("sql_concat.py", 5)
    assert v.verdict == "UNCERTAIN"
    assert v.verdict != "FALSE_POSITIVE"
    assert v.source == "semgrep"
    assert "sink-strict-sql" in v.evidence
    assert "taint-sql" in v.evidence  # the rule matched; it is evidence, not proof


@needs_semgrep
def test_parameterized_sql_is_neither_blessed_nor_refuted():
    """Semgrep's taint rule fires here even though the query is safely parameterized.

    We must not launder that hit into CONFIRMED, and we must not refute a sink that
    genuinely exists. UNCERTAIN is the only honest verdict. This row exists precisely
    because the taint rule fires on safe code—that false positive is the feature we're
    testing.
    """
    v = settle("sql_parameterized.py", 4)
    assert v.verdict == "UNCERTAIN"
    assert v.verdict != "CONFIRMED"
    assert v.verdict != "FALSE_POSITIVE"
    assert v.source == "semgrep"
    assert "sink-strict-sql" in v.evidence
    assert "taint-sql" in v.evidence  # the rule genuinely matches, proving the property
    assert "not proof" in v.evidence.lower()


@needs_semgrep
def test_unlisted_driver_escalates_never_refutes():
    """The sink is real but not in our vetted catalog. Refuting would drop a real bug."""
    v = settle("sql_unlisted_driver.py", 4)
    assert v.verdict == "UNCERTAIN"
    assert v.verdict != "FALSE_POSITIVE"
    assert "unrecognized sink" in v.evidence


@needs_semgrep
@pytest.mark.parametrize("fixture,line", [
    ("orm_no_sink.py", 5), ("sql_concat.py", 5),
    ("sql_parameterized.py", 4), ("sql_unlisted_driver.py", 4),
])
def test_taint_is_never_confirmed_on_any_fixture(fixture, line):
    assert settle(fixture, line).verdict != "CONFIRMED"
