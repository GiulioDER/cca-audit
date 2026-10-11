"""Tests for cca_checks.bugs.model: the bug row, its tier, and its memo."""
from cca_checks.bugs.model import (
    ABSENT,
    CLAIMED,
    CONDITIONS,
    EVIDENCED,
    BugRow,
    memo_filename,
    render_memo,
)


def _row(**evidence):
    base = {c: EVIDENCED for c in CONDITIONS}
    base.update(evidence)
    return BugRow(
        project="widgets", repo="acme/widgets", finding_id="STAKES-001",
        fix_commit="0123456789abcdef0123456789abcdef01234567", date="2026-09-01",
        files=["src/store.py"], summary="delete and bump ran as two statements; +test",
        severity="P1", evidence=base, sources=["git:0123456789ab"],
    )


def test_tier_is_proved_only_when_all_four_are_evidenced():
    """Red proof: model.BugRow.tier `all(` -> `any(` -> the claimed row reads 'proved'."""
    assert _row().tier == "proved"
    assert _row(red_proof=CLAIMED).tier == "historical"
    assert _row(l6_approved=ABSENT).tier == "historical"


def test_bug_id_and_memo_filename():
    """Red proof: memo_filename returns bug_id + '.md' -> the ':' assertion fails."""
    row = _row()
    assert row.bug_id == "widgets:0123456789ab:STAKES-001"
    name = memo_filename(row.bug_id)
    assert ":" not in name
    assert name == "widgets__0123456789ab__STAKES-001.md"


def test_to_json_marks_a_missing_condition_absent():
    """Red proof: to_json uses evidence.get(c) without a default -> None, not 'absent'."""
    row = _row()
    del row.evidence["red_proof"]
    out = row.to_json()
    assert out["evidence"]["red_proof"] == ABSENT
    assert out["tier"] == "historical"
    assert out["bug_id"] == row.bug_id


def test_memo_quotes_the_summary_and_carries_front_matter():
    """Red proof: drop the summary line from render_memo -> the body assertion fails.

    The summary is looked for in the body, under "The bug": it also appears in the
    front-matter description, so a check over the whole text passed with the body empty.
    """
    text = render_memo(_row(), "2026-10-11T00:00:00Z")
    assert text.startswith("---\nname: widgets-0123456789ab-stakes-001\n")
    assert "valid_from: 2026-09-01\n" in text
    assert "  type: project\n" in text
    body = text.split("## The bug\n", 1)[1]
    assert body.startswith("\ndelete and bump ran as two statements; +test\n")
    assert "| red_proof | evidenced |" in text
    assert "`src/store.py`" in text
