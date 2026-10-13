"""Rows from a captured run plus its merge."""
import json

from cca_checks.bugs.forward import Merge, rows_for
from cca_checks.bugs.model import ABSENT, CLAIMED, EVIDENCED

MERGE = Merge(repo="acme/widgets", sha="c" * 40, date="2026-10-14", pr=12)


def make_record(tmp_path, *, l4_key="finding", proofs=None, approved=True, verdict="CONFIRMED"):
    r = tmp_path / "rec"
    r.mkdir(parents=True)
    (r / "meta.json").write_text(json.dumps({"run_id": "r1", "commit": "a" * 40}))
    (r / "L2.json").write_text(json.dumps([
        {"id": "BUG-001", "priority": "P1", "category": "state", "auditor": "bug-auditor",
         "claim": "dry run creates the file"},
        {"id": "DOC-001", "priority": "P2", "category": "docs", "auditor": "doc-auditor",
         "claim": "help text is stale"}]))
    (r / "L2.5.json").write_text(json.dumps([{"id": "BUG-001", "verdict": verdict},
                                              {"id": "DOC-001", "verdict": "CONFIRMED"}]))
    (r / "L4.json").write_text(json.dumps([
        {"fix_id": "FIX-001", l4_key: "BUG-001", "files": ["a.py"]},
        {"fix_id": "FIX-002", l4_key: "DOC-001", "files": ["b.py"]}]))
    rows = [{"run_id": "r1", "fix_id": "FIX-001",
             "architect_verdict": "APPROVED" if approved else "BLOCKED"}]
    (r / "scorecard.jsonl").write_text("".join(json.dumps(x) + "\n" for x in rows))
    if proofs is not None:
        (r / "L5.6.json").write_text(json.dumps(proofs))
    return r


def test_a_merged_red_proved_fix_is_proved(tmp_path):
    """Invariant: confirmed + merged + approved + RED proof gives a proved row keyed by the
    merge commit.

    Red 2026-10-13: mutation `fix_commit=meta["commit"]` instead of `merge.sha`; failed at
    `assert bug.fix_commit == MERGE.sha`.
    Red 2026-10-13: mutation making red_proof always ABSENT; failed at
    `assert bug.tier == "proved"`.
    """
    rec = make_record(tmp_path, proofs=[{"finding": "FIX-001", "nodeid": "t::a",
                                         "verdict": "RED"}])
    bug, doc = rows_for(rec, MERGE)
    assert bug.fix_commit == MERGE.sha
    assert bug.bug_id == f"widgets:{'c' * 12}:BUG-001"
    assert bug.tier == "proved"
    assert bug.summary == "dry run creates the file" and bug.severity == "P1"
    assert doc.evidence["red_proof"] == ABSENT and doc.evidence["l6_approved"] == CLAIMED
    assert doc.tier == "historical"


def test_codex_l4_files_name_the_finding_as_finding_id(tmp_path):
    """Review Focus 4. Red 2026-10-13: mutation reading only `entry.get("finding")`; failed
    at `assert [r.finding_id for r in rows] == ["BUG-001", "DOC-001"]` (got FIX ids).
    """
    rows = rows_for(make_record(tmp_path, l4_key="finding_id"), MERGE)
    assert [r.finding_id for r in rows] == ["BUG-001", "DOC-001"]


def test_a_proof_named_by_the_finding_id_counts(tmp_path):
    """Red 2026-10-13: mutation matching proofs on the fix id only; failed at
    `assert rows[0].evidence["red_proof"] == EVIDENCED`.
    """
    rec = make_record(tmp_path, proofs=[{"finding": "BUG-001", "nodeid": "t", "verdict": "RED"}])
    rows = rows_for(rec, MERGE)
    assert rows[0].evidence["red_proof"] == EVIDENCED


def test_a_non_red_proof_or_a_blocked_architect_is_absent(tmp_path):
    """Red 2026-10-13: mutation `EVIDENCED if proof else ABSENT` (any verdict counts);
    failed at `assert rows[0].evidence["red_proof"] == ABSENT`.
    """
    rec = make_record(tmp_path, approved=False,
                      proofs=[{"finding": "FIX-001", "nodeid": "t", "verdict": "TAUTOLOGICAL"}])
    rows = rows_for(rec, MERGE)
    assert rows[0].evidence["red_proof"] == ABSENT
    assert rows[0].evidence["l6_approved"] == ABSENT


def test_an_unconfirmed_finding_is_not_confirmed(tmp_path):
    """Red 2026-10-13: mutation setting confirmed_l25 to EVIDENCED unconditionally; failed at
    `assert rows[0].evidence["confirmed_l25"] == ABSENT`.
    """
    rows = rows_for(make_record(tmp_path, verdict="UNCERTAIN"), MERGE)
    assert rows[0].evidence["confirmed_l25"] == ABSENT


def test_the_last_architect_verdict_decides(tmp_path):
    """Backfill keeps the last verdict per fix, so a REVISE then APPROVED retry is approved.

    Red 2026-10-13: mutation iterating `rows` instead of `reversed(rows)` in `_architect`;
    failed at `assert rows[0].evidence["l6_approved"] == EVIDENCED`.
    """
    rec = make_record(tmp_path)
    lines = [{"run_id": "r1", "fix_id": "FIX-001", "architect_verdict": v}
             for v in ("REVISE", "APPROVED")]
    (rec / "scorecard.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines))
    rows = rows_for(rec, MERGE)
    assert rows[0].evidence["l6_approved"] == EVIDENCED


def test_an_unsafe_finding_id_yields_no_row(tmp_path):
    """L4 is untrusted and the id becomes a memo filename.

    Red 2026-10-13: mutation removing the `_SAFE_ID` check in `rows_for`; failed at
    `assert [r.finding_id for r in rows] == ["DOC-001"]`.
    """
    rec = make_record(tmp_path)
    (rec / "L4.json").write_text(json.dumps([
        {"fix_id": "FIX-001", "finding": "../../evil", "files": ["a.py"]},
        {"fix_id": "FIX-003", "finding": "a/b", "files": ["a.py"]},
        {"fix_id": "FIX-004", "finding": "a\\b", "files": ["a.py"]},
        {"fix_id": "FIX-005", "finding": "..", "files": ["a.py"]},
        {"fix_id": "FIX-002", "finding": "DOC-001", "files": ["b.py"]}]))
    rows = rows_for(rec, MERGE)
    assert [r.finding_id for r in rows] == ["DOC-001"]
