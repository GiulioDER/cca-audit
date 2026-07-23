"""Rust taint, and the parse control that makes its refutations trustworthy.

Semgrep's Rust support is younger than its Python support, and a `FALSE_POSITIVE`
here rests entirely on semgrep's SILENCE. `run_semgrep` already proves semgrep OPENED
the file (`paths.scanned`) -- but not that its parser understood it, and a file it
failed to parse is scanned, reported without errors, and matches nothing. That is
byte-for-byte what a file with no sinks looks like. The `parse-control` rule is the
cross-examination, and most of this file is about it.
"""

import pathlib
import shutil

import pytest

from cca_checks import semgrep_check as sc
from cca_checks.claim import Claim

SRC = pathlib.Path(__file__).parent / "fixtures" / "rust" / "src"
TAINT = str(SRC / "taint.rs")

# Coordinates pinned in tests/test_fixture_contract.py.
COMMAND_SINK_LINE = 12
NO_SINK_LINE = 19
PATH_SINK_LINE = 24
LOOSE_SINK_LINE = 30

needs_semgrep = pytest.mark.skipif(
    shutil.which("semgrep") is None,
    reason="semgrep is not installed; the CI job installs it and "
           "test_ci_installs_the_deterministic_layer asserts that it does",
)


def claim(line, sink_class, file=TAINT):
    return Claim("T-1", file, line, "taint", sink_class=sink_class)


# --- the catalog is wired to the backend, not hardcoded -----------------------

def test_the_rust_backend_supplies_its_own_catalog():
    assert sc.catalog_for(TAINT) == ("rust_sinks.yaml", "rust_taint.yaml")


def test_an_uncovered_language_has_no_catalog():
    assert sc.catalog_for("handler.ts") is None


# --- the parse control --------------------------------------------------------

def test_the_rust_catalog_ships_a_parse_control():
    assert sc.catalog_has_control("rust_sinks.yaml") is True


def test_the_control_is_opt_in_per_catalog():
    """Python's catalog has no control, so its silence is trusted exactly as before.
    Making the mechanism opt-in is what keeps this from changing Python behaviour."""
    assert sc.catalog_has_control("python_sinks.yaml") is False
    assert sc._control_fired([], "python_sinks.yaml") is True


def test_a_catalog_with_a_control_requires_it_to_fire():
    assert sc._control_fired([], "rust_sinks.yaml") is False
    assert sc._control_fired([{"check_id": "rules.rust_sinks.parse-control"}],
                             "rust_sinks.yaml") is True


def test_a_silent_control_blocks_the_refutation(monkeypatch):
    """The whole point. Semgrep scanned the file and matched nothing -- but it also
    failed to recognise a single function, so it did not understand the file, and its
    silence is not evidence of absence."""
    monkeypatch.setattr(sc, "enclosing_span", lambda p, line: (1, 40))
    v = sc.verdict_for_taint(claim(NO_SINK_LINE, "sql"), sinks=[], taint=[])
    assert v.verdict == "UNCERTAIN"
    assert "parse control did not fire" in v.evidence


def test_a_fired_control_permits_the_refutation(monkeypatch):
    """The other half: a control that fires must actually unblock the refutation, or
    the check is a permanent off-switch rather than a gate."""
    monkeypatch.setattr(sc, "enclosing_span", lambda p, line: (1, 40))
    v = sc.verdict_for_taint(
        claim(NO_SINK_LINE, "sql"),
        sinks=[{"check_id": "rules.rust_sinks.parse-control",
                "start": {"line": 11}}],
        taint=[])
    assert v.verdict == "FALSE_POSITIVE"


def test_the_control_is_not_scoped_to_the_claims_span(monkeypatch):
    """It answers "did the parser understand this FILE", and a function anywhere
    proves that. Scoping it to the span would make a claim inside a scope with no
    function item -- a const block, a trait body -- permanently unrefutable for a
    reason unrelated to parsing."""
    monkeypatch.setattr(sc, "enclosing_span", lambda p, line: (18, 20))
    v = sc.verdict_for_taint(
        claim(NO_SINK_LINE, "sql"),
        sinks=[{"check_id": "rules.rust_sinks.parse-control",
                "start": {"line": 999}}],   # far outside the span
        taint=[])
    assert v.verdict == "FALSE_POSITIVE"


# --- end to end, against real semgrep -----------------------------------------

@needs_semgrep
@pytest.mark.parametrize("line,sink_class,expected,note", [
    (COMMAND_SINK_LINE, "command", "UNCERTAIN", "a vetted Command::new is present"),
    (PATH_SINK_LINE, "path", "UNCERTAIN", "a vetted fs::read_to_string is present"),
    (LOOSE_SINK_LINE, "command", "UNCERTAIN", "an unvetted name the loose tier catches"),
    (NO_SINK_LINE, "command", "FALSE_POSITIVE", "no sink of any class in this scope"),
    (NO_SINK_LINE, "sql", "FALSE_POSITIVE", "no sql sink either"),
])
def test_end_to_end_verdicts(line, sink_class, expected, note):
    v = sc.verdict_for_taint(claim(line, sink_class))
    assert v.verdict == expected, f"{note}: {v.evidence}"


@needs_semgrep
def test_semgrep_really_parses_rust():
    """A standing check that the assumption the catalog rests on still holds.

    If a future semgrep drops or breaks Rust support, every refutation above would
    start passing for the wrong reason -- the control would stop firing and the
    verdicts would become escalations. This asserts the control fires on a real scan,
    so that regression is visible as a failure here rather than as a quiet loss of
    coverage.
    """
    results = sc.run_semgrep(sc.rules_path("rust_sinks.yaml"), TAINT)
    assert results is not None, "semgrep could not scan the fixture at all"
    assert sc._control_fired(results, "rust_sinks.yaml"), (
        "semgrep scanned the Rust fixture but recognised no function item; its Rust "
        "parser is not working, and every refutation resting on its silence is void")
