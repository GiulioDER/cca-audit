"""The Rust clock-leak verdict table.

Same asymmetry the Python checker enforces, and for the same reason: CONFIRMED feeds
an auto-fix path, so it fires only when BOTH halves are proven from the tree -- a
strong injected-time parameter that is declared and never referenced, beside a real
wall-clock read. Everything short of that adjudicates, and only an absence the file
could not have hidden may refute.
"""

import pathlib

import pytest

from cca_checks.claim import Claim
from cca_checks.languages.rust import verdict_for_clock_leak

pytest.importorskip("tree_sitter")
pytest.importorskip("tree_sitter_rust")

SRC = pathlib.Path(__file__).parent / "fixtures" / "rust" / "src"
CLOCK = str(SRC / "clock.rs")
BLOCKERS = str(SRC / "clock_blockers.rs")


def verdict(path, line):
    return verdict_for_clock_leak(Claim("RS-1", path, line, "clock_leak"))


# --- CONFIRMED: a dead strong parameter beside a real wall-clock read ----------

@pytest.mark.parametrize("line,param,why", [
    (12, "as_of", "the plain case"),
    (31, "now", "through a renamed import: `use chrono::Local as L`"),
    (37, "clock", "through a brace-list import: `use std::time::{SystemTime, ...}`"),
    (71, "as_of", "inside a closure, which is its own scope"),
])
def test_a_dead_clock_parameter_beside_a_wall_clock_read_confirms(line, param, why):
    v = verdict(CLOCK, line)
    assert v.verdict == "CONFIRMED", f"{why}: {v.evidence}"
    assert v.source == "ast"
    assert repr(param) in v.evidence
    assert "NEVER referenced" in v.evidence


def test_the_method_name_of_the_clock_call_does_not_count_as_using_the_parameter():
    """`fn settle(now: i64) { L::now() }` -- the trailing segment of a scoped path is
    not a reference to the local `now`. Counting it made CONFIRMED unreachable for
    the commonest Rust shape, and failed SAFE, which is why it hid."""
    assert verdict(CLOCK, 31).verdict == "CONFIRMED"


# --- UNCERTAIN: co-occurrence is correct code, not a defect --------------------

def test_a_used_clock_parameter_beside_a_wall_clock_read_adjudicates():
    """Stamping a log line with the real time while the logic runs on `as_of` is
    correct and common. Confirming it would auto-'fix' working code."""
    v = verdict(CLOCK, 19)
    assert v.verdict == "UNCERTAIN"
    assert "co-occurs" in v.evidence


@pytest.mark.parametrize("line,expected", [
    (44, "monotonic"),              # Instant::now -- un-injectable but usually right
    (51, "no recognised injected-time parameter"),
    (56, "referenced without being called"),   # `let maker = Utc::now;`
    (64, "weak-signal"),            # `timestamp` is as often data as a clock
])
def test_the_weaker_shapes_adjudicate(line, expected):
    v = verdict(CLOCK, line)
    assert v.verdict == "UNCERTAIN"
    assert expected in v.evidence


def test_a_deferred_clock_handle_does_not_refute():
    """A clock function named but never called runs at a time this checker cannot
    determine, so it must block refutation rather than count as absence."""
    assert verdict(CLOCK, 56).verdict != "FALSE_POSITIVE"


# --- FALSE_POSITIVE: only an absence the file could not have hidden ------------

def test_no_clock_read_of_any_kind_refutes():
    v = verdict(CLOCK, 26)
    assert v.verdict == "FALSE_POSITIVE"
    assert v.source == "ast"
    assert "premise does not hold" in v.evidence


@pytest.mark.parametrize("line,blocker", [
    (14, "glob"),
    (21, "macro"),
])
def test_a_file_that_could_have_hidden_a_clock_read_may_not_refute(line, blocker):
    """A file must not earn a refutation carrying an authoritative `source` precisely
    by being hard to read. The glob is the analogue of Python's `import *`; the macro
    blocker has no Python counterpart, because `ast` has nothing it cannot expand."""
    v = verdict(BLOCKERS, line)
    assert v.verdict == "UNCERTAIN", v.evidence
    assert "not provable" in v.evidence
    assert blocker in v.evidence


def test_an_unparseable_file_may_not_refute(tmp_path):
    broken = tmp_path / "broken.rs"
    broken.write_text("fn f( { let ;;; }\n", encoding="utf-8")
    v = verdict(str(broken), 1)
    assert v.verdict != "FALSE_POSITIVE"


def test_a_missing_grammar_escalates_rather_than_refuting(monkeypatch):
    """An uninstalled optional extra must never read as "no clock here"."""
    from cca_checks import treesitter as ts
    ts._parser.cache_clear()
    monkeypatch.setitem(ts._GRAMMAR_MODULES, "rust", "tree_sitter_no_such_grammar")
    v = verdict(CLOCK, 26)
    ts._parser.cache_clear()
    assert v.verdict == "UNCERTAIN"
    assert "not installed" in v.evidence


def test_an_unreadable_file_escalates():
    v = verdict(str(SRC / "does_not_exist.rs"), 1)
    assert v.verdict == "UNCERTAIN"
    assert "could not read" in v.evidence


# --- the parameter vocabulary is shared with Python, not duplicated ------------

def test_the_strong_parameter_names_come_from_config():
    """`now`, `as_of`, `clock` are the same words in Rust, so CLOCK_STRONG_PARAMS is
    reused rather than forked -- a second copy would drift and the two languages
    would quietly disagree about what counts as an injected clock."""
    from cca_checks.config import CLOCK_STRONG_PARAMS
    from cca_checks.languages import rust
    assert rust.CLOCK_STRONG_PARAMS is CLOCK_STRONG_PARAMS
