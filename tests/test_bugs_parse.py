"""Tests for cca_checks.bugs.parse: fix commit messages to one bullet per finding."""
from cca_checks.bugs.parse import expand_ids, is_cca_fix_subject, parse_message

DEEP = """fix(store): 3 audit fixes from CCA (DEEP tier)

P1:
- STAKES-001: delete ran the row delete and the version bump as two
  autocommit statements, so a failed bump left rows gone. Both now share
  one transaction; +test_delete_is_atomic, red at PR head.
P2:
- SEC-002: prefixes match with fullmatch (+test_prefix_fullmatch).
- CODE-001/002: tests pin the copied helpers.
- PERF-004/005, BUG-012: reads follow the database order.
Owner decision, not fixed here:
- DEPLOY-001: numpy is not a core dependency.
Deferred (P3): zero-norm cosines, file mode.

Audit: DEEP, 6 auditors, 3 fixed.
"""

FIX_IDS = """R2-1 forget: audit fixes for #805 (bounds) (#828)

* R2-1 forget: bound the detector (audit of #805)

Two fixes from a DEEP audit.

P1:
- FIX-1, regex backtracking: _POLITE was exponential. Whitespace is
  collapsed once. +test_detector_is_not_superlinear (9 cases).

P2:

- FIX-2: the ledger keeps 1,000 characters per request.

- DOC-009 deferred: scope wording.

P3:

- DOC-002: scope wording.
"""

SELF = """fix(cca_checks): 2 audit fixes from CCA self-audit (DEEP tier)

P1 Critical:
- SEC-001 pyright was launched by bare name. +test
- SEC-003/STAKES-002 run_pyright inherited the audited config. +test
"""


def _ids(bullets):
    return [(b.finding_id, b.severity) for b in bullets]


def test_subject_selection():
    """Red proof: SUBJECT_RE without the 'for' alternative -> the #805 subject is rejected."""
    assert is_cca_fix_subject("fix(store): 3 audit fixes from CCA (DEEP tier)")
    assert is_cca_fix_subject("R2-1 forget: audit fixes for #805 (bounds) (#828)")
    assert is_cca_fix_subject("fix(capabilities): apply CCA audit fixes")
    assert is_cca_fix_subject("fix(cca_checks): 21 audit fixes from CCA self-audit (DEEP tier)")
    assert not is_cca_fix_subject("feat(dashboard): a local memory dashboard")


def test_one_bullet_per_finding_with_its_continuation_joined():
    """Red proof: parse_message drops continuation lines (append -> pass) -> summary lacks 'one transaction'."""
    bullets = parse_message(DEEP)
    first = bullets[0]
    assert (first.finding_id, first.severity) == ("STAKES-001", "P1")
    assert first.summary.startswith("delete ran the row delete")
    assert "one transaction; +test_delete_is_atomic, red at PR head." in first.summary


def test_sections_that_were_not_fixed_are_skipped():
    """Red proof: the non-heading branch keeps the section (section = None -> pass) -> DEPLOY-001 parsed."""
    assert _ids(parse_message(DEEP)) == [
        ("STAKES-001", "P1"),
        ("SEC-002", "P2"),
        ("CODE-001", "P2"),
        ("CODE-002", "P2"),
        ("PERF-004", "P2"),
        ("PERF-005", "P2"),
        ("BUG-012", "P2"),
    ]


def test_a_bullet_marked_deferred_is_skipped():
    """Red proof: remove 'deferred' from _SKIP_TEXT -> DOC-009 is parsed."""
    assert _ids(parse_message(FIX_IDS)) == [("FIX-1", "P1"), ("FIX-2", "P2")]


def test_comma_after_the_id_is_a_separator():
    """Red proof: _BULLET_RE rest group without ',' -> FIX-1 is not parsed."""
    first = parse_message(FIX_IDS)[0]
    assert first.finding_id == "FIX-1"
    assert first.summary.startswith("regex backtracking: _POLITE was exponential.")


def test_multi_id_bullets():
    """Red proof: expand_ids splits on '/' only -> 'CODE-003, DOC-003' yields nothing."""
    assert expand_ids("CODE-001/002") == ["CODE-001", "CODE-002"]
    assert expand_ids("CODE-003, DOC-003") == ["CODE-003", "DOC-003"]
    assert expand_ids("PERF-004/005, BUG-012") == ["PERF-004", "PERF-005", "BUG-012"]
    assert expand_ids("SEC-003/STAKES-002") == ["SEC-003", "STAKES-002"]
    assert _ids(parse_message(SELF)) == [
        ("SEC-001", "P1"), ("SEC-003", "P1"), ("STAKES-002", "P1"),
    ]


def test_bold_and_markdown_headings_open_a_section():
    """Review Focus 1. Red proof: _heading_severity without .strip('*') -> the bold heading opens nothing."""
    body = "fix: 2 audit fixes from CCA\n\n**P1:**\n- BUG-001: a.\n\n## P2\n- BUG-002: b.\n"
    assert _ids(parse_message(body)) == [("BUG-001", "P1"), ("BUG-002", "P2")]
