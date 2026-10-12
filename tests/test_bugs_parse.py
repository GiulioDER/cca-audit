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
    """Red proof: _heading_kind without the skip-word check -> DEPLOY-001 counted."""
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
    """Review Focus 1. Red proof: _heading_kind without .strip('*') -> the bold heading opens nothing."""
    body = "fix: 2 audit fixes from CCA\n\n**P1:**\n- BUG-001: a.\n\n## P2\n- BUG-002: b.\n"
    assert _ids(parse_message(body)) == [("BUG-001", "P1"), ("BUG-002", "P2")]


SQUASH = """R2-1 forget: audit fixes (#830)

* R2-1 forget: bound the detector

P1:
- FIX-1, regex backtracking: the pattern was exponential.

* R2-2 forget: audit fixes for the review round

P1:
- FIX-1: the ledger keeps a bounded copy.
"""


def test_a_squash_subject_line_is_not_a_finding():
    """Review C2. Red proof: star_finding without the ':' or ',' check -> R2-1 read as an ID.

    The subject must close the section: it is not a finding of any kind, and a bullet
    after it with no heading of its own is not counted under the previous commit's P1
    (since 2026-10-11 it counts as a topic-section finding, with no severity).
    """
    rejected = []
    assert _ids(parse_message(SQUASH, rejected)) == [("FIX-1", "P1"), ("FIX-1", "P1")]
    assert rejected == []
    tail = "P1:\n- FIX-1: a.\n\n* R2-2 forget: second commit\n\n- FIX-2: no heading of its own.\n"
    assert _ids(parse_message(tail)) == [("FIX-1", "P1"), ("FIX-2", None)]


def test_a_severity_heading_that_says_deferred_opens_nothing():
    """Review I2. Red proof: _heading_kind without the skip-word check -> BUG-001 counted."""
    body = "P2 (deferred to follow-up):\n- BUG-001: a.\nP2, not fixed here:\n- BUG-002: b.\n"
    assert parse_message(body) == []


def test_a_number_after_a_comma_is_text_not_an_id():
    """Review I3. Red proof: _BULLET_RE accepts a bare number after ',' -> a phantom FIX-3."""
    [only] = parse_message("P1:\n- FIX-1, 3 call sites now hold the lock.\n")
    assert only.finding_id == "FIX-1"
    assert only.summary == "3 call sites now hold the lock."


def test_an_unknown_label_under_a_severity_heading_counts_under_a_stand_in_id():
    """Review I3, revised 2026-10-11. Red proof: _known_ids skips the KNOWN_PREFIXES check -> UTF-8 is an ID.

    Under P1/P2 every top-level bullet is a fix. A token like UTF-8 must not become its
    finding ID, but the bullet still counts, under a stand-in ID, with its whole text.
    """
    rejected = []
    body = "P1:\n- UTF-8 decoding is strict now.\n- SHA-256 digests match.\n- BUG-001: real.\n"
    bullets = parse_message(body, rejected)
    assert _ids(bullets) == [("P1-1", "P1"), ("P1-2", "P1"), ("BUG-001", "P1")]
    assert bullets[0].summary == "UTF-8 decoding is strict now."
    assert rejected == []


def test_an_unknown_id_under_a_topic_heading_is_reported_not_counted():
    """Red proof: topic sections count any ID-shaped token -> UTF-8 counted as a finding."""
    rejected = []
    assert parse_message("Notes:\n- UTF-8 decoding is strict now.\n", rejected) == []
    assert rejected == ["UTF-8"]


LETTERED = """fix(lrp_growth): 7 audit fixes from CCA v2 (9-auditor) review

P1 Critical:
- B
  DELETE ran before the reward window closed.

P2:
- A
  regardless of the server setting.
- F-01: the index covers condition_id.
- gate.py: a fault is logged at warning.
- Renamed the helper so callers stop guessing.

P3 fixes:
- C: hygiene.
"""


def test_every_bullet_under_p1_or_p2_counts_even_without_an_id():
    """Red proof: the stand-in append -> pass -> nothing from LETTERED."""
    bullets = parse_message(LETTERED)
    assert _ids(bullets) == [("P1-1", "P1"), ("P2-1", "P2"), ("P2-2", "P2"),
                             ("P2-3", "P2"), ("P2-4", "P2")]
    assert bullets[0].summary == "B DELETE ran before the reward window closed."
    assert bullets[2].summary == "F-01: the index covers condition_id."


THEMATIC = """fix(beam): 12 deferred fixes from CCA second pass

Second pass over the P2/P3 findings deferred in round one.

Shipped library and install:
- DEPLOY-003: the execution provider is part of the fingerprint.
- CODE-001/002: helpers pinned.
- a plain bullet with no finding ID.

Stale (already fixed in round 1):
- BUG-009: nothing to do.

SKIPPED (with reason):
- SEC-004: out of scope.
"""


def test_known_ids_under_a_topic_heading_count_with_no_severity():
    """Red proof: a topic heading opens a skipped section -> nothing from THEMATIC."""
    assert _ids(parse_message(THEMATIC)) == [
        ("DEPLOY-003", None), ("CODE-001", None), ("CODE-002", None),
    ]


def test_stale_and_skipped_headings_open_nothing():
    """Red proof: remove 'stale' from _SKIP_HEADING -> BUG-009 counted."""
    found = {b.finding_id for b in parse_message(THEMATIC)}
    assert "BUG-009" not in found and "SEC-004" not in found


def test_a_mixed_p2_p3_heading_counts_known_ids_only():
    """Red proof: _heading_kind ignores a P3 beside P2 -> the ID-less bullet counts as P2-1."""
    body = "P2/P3:\n- tidy the helper.\n- BUG-004: real.\n"
    assert _ids(parse_message(body)) == [("BUG-004", None)]


def test_a_bullet_that_says_false_positive_is_skipped():
    """Red proof: remove 'false positive' from _SKIP_TEXT -> BUG-003 counted."""
    body = "P2:\n- BUG-003: false positive, no change.\n- BUG-005: real.\n"
    assert _ids(parse_message(body)) == [("BUG-005", "P2")]


def test_an_id_less_star_line_is_a_subject_not_a_bullet():
    """Red proof: count ID-less '*' lines under a severity heading -> the subject is P1-2."""
    body = "P1:\n- a fix.\n\n* Second commit subject\n- another line\n"
    assert _ids(parse_message(body)) == [("P1-1", "P1")]


def test_a_bullet_about_stale_comments_is_a_fix():
    """Measured regression, 2026-10-11. Red proof: put 'stale' back in _SKIP_TEXT -> CODE-003 dropped.

    "Stale" is a heading word ("Stale (already fixed in round 1):"). At the start of a
    bullet it describes what was fixed, as in a real RE-call commit the first dry run read.
    """
    body = "P2:\n- CODE-003: Stale comments: the transport note named the old default.\n"
    assert _ids(parse_message(body)) == [("CODE-003", "P2")]


def test_a_declined_heading_opens_nothing():
    """Measured, 2026-10-11. Red proof: remove 'declined' from _SKIP_HEADING -> CODE-007 counted."""
    body = "DECLINED:\n- CODE-007: closure refactor (verified correct; risk without benefit).\n"
    assert parse_message(body) == []
