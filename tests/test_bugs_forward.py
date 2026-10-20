"""Rows from a captured run plus its merge."""
import json

from cca_checks.bugs.forward import Merge, rows_for
from cca_checks.bugs.model import ABSENT, CLAIMED, EVIDENCED, NOT_REQUIRED

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
    Red 2026-10-20: `_proof` returning ABSENT whenever there is no proof (pre-change);
    failed at `assert doc.evidence["red_proof"] == NOT_REQUIRED and ...`.
    """
    rec = make_record(tmp_path, proofs=[{"finding": "FIX-001", "nodeid": "t::a",
                                         "verdict": "RED"}])
    bug, doc = rows_for(rec, MERGE)
    assert bug.fix_commit == MERGE.sha
    assert bug.bug_id == f"widgets:{'c' * 12}:BUG-001"
    assert bug.tier == "proved"
    assert bug.summary == "dry run creates the file" and bug.severity == "P1"
    # DOC-001 is a P2 with no proof entry, so its red proof is not required; the
    # architect never approved it, so it stays historical.
    assert doc.evidence["red_proof"] == NOT_REQUIRED and doc.evidence["l6_approved"] == CLAIMED
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


def test_a_comma_separated_files_string_is_split_into_paths(tmp_path):
    """BUG-001. Invariant: L4 `files` given as a comma separated string (as real L4.json
    files store it) becomes a list of paths, never a list of single characters.

    Red 2026-10-13: pre-fix `files=list(entry.get("files") or [])`; failed at
    `assert rows[0].files == ["a/x.py", "b/y.py"]` (one entry per character).
    """
    rec = make_record(tmp_path)
    (rec / "L4.json").write_text(json.dumps([
        {"fix_id": "FIX-001", "finding": "BUG-001", "files": "a/x.py, b/y.py ,, "},
        {"fix_id": "FIX-002", "finding": "DOC-001", "files": {"a": 1}}]))
    rows = rows_for(rec, MERGE)
    assert rows[0].files == ["a/x.py", "b/y.py"]
    assert rows[1].files == []


def test_a_parked_or_reverted_fix_yields_no_row_and_is_reported(tmp_path):
    """BUG-002. Invariant: an L4 entry whose scorecard result is parked or reverted, or
    whose fix id is null because it was parked, never becomes a `fixed` row, and the skip
    is reported.

    Red 2026-10-13: pre-fix `"fixed": EVIDENCED` for every L4 entry; failed at
    `assert [r.finding_id ...] == ["BUG-001"]` (DOC-001, PERF-001, PERF-003 written too).
    """
    rec = make_record(tmp_path)
    (rec / "L4.json").write_text(json.dumps([
        {"fix_id": "FIX-001", "finding": "BUG-001", "files": ["a.py"]},
        {"fix_id": "FIX-002", "finding": "DOC-001", "files": ["b.py"]},
        {"fix_id": "FIX-003", "finding": "PERF-001", "files": ["c.py"]},
        {"fix_id": None, "finding_id": "PERF-003",
         "change": "parked, UNCERTAIN to human (FIX_JOURNAL)"}]))
    lines = [{"run_id": "r1", "fix_id": "FIX-001", "result": "fixed",
              "architect_verdict": "APPROVED"},
             {"run_id": "r1", "fix_id": "FIX-002", "result": "parked",
              "architect_verdict": "APPROVED"},
             {"run_id": "r1", "fix_id": "FIX-003", "result": "reverted"}]
    (rec / "scorecard.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines))
    assert [r.finding_id for r in rows_for(rec, MERGE)] == ["BUG-001"]
    from cca_checks.bugs.forward import rows_and_skips  # new with this fix
    _, skipped = rows_and_skips(rec, MERGE)
    assert [s.split(" ", 1)[0] for s in skipped] == ["DOC-001", "PERF-001", "PERF-003"]
    assert "parked" in skipped[0] and "reverted" in skipped[1] and "parked" in skipped[2]


def test_untrusted_field_types_never_reach_the_row(tmp_path):
    """SEC-001. Invariant: every row field that reaches the ledger has the type BugRow
    declares, whatever L2 and L4 hold: summary a non-empty str, severity, category and
    auditor a str or None, files a list of str.

    Red 2026-10-13: pre-fix rows_for with no type checks; failed at
    `assert bug.summary == "(no description recorded)"` (summary was ['x']).
    """
    rec = make_record(tmp_path)
    (rec / "L2.json").write_text(json.dumps([
        {"id": "BUG-001", "priority": {"a": 1}, "category": ["c"], "auditor": 7,
         "claim": ["x"]},
        {"id": "DOC-001", "priority": "P2", "claim": ""}]))
    (rec / "L4.json").write_text(json.dumps([
        {"fix_id": "FIX-001", "finding": "BUG-001", "files": "abc", "summary": {"s": 1}},
        {"fix_id": "FIX-002", "finding": "DOC-001", "files": [1, "b.py"],
         "summary": "from L4"}]))
    bug, doc = rows_for(rec, MERGE)
    assert bug.summary == "(no description recorded)"
    assert bug.severity is None and bug.category is None and bug.auditor is None
    assert bug.files == ["abc"]
    assert doc.summary == "from L4" and doc.severity == "P2" and doc.files == ["b.py"]


def _approve_both(rec, priority=None):
    """Approve FIX-001 and FIX-002; optionally set DOC-001's L2 priority (None drops it)."""
    lines = [{"run_id": "r1", "fix_id": f, "architect_verdict": "APPROVED"}
             for f in ("FIX-001", "FIX-002")]
    (rec / "scorecard.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines))
    if priority != "P2":
        l2 = json.loads((rec / "L2.json").read_text())
        if priority is None:
            del l2[1]["priority"]
        else:
            l2[1]["priority"] = priority
        (rec / "L2.json").write_text(json.dumps(l2))
    return rec


def test_a_p2_fix_with_no_proof_entry_is_proved(tmp_path):
    """Invariant: a confirmed, merged, approved P2 with no proof entry is proved, its red
    proof not_required, because the pipeline asks for red proofs only for P1 fixes.

    Red 2026-10-20: unchanged code (red_proof ABSENT whenever no RED proof); failed at
    `assert doc.evidence["red_proof"] == NOT_REQUIRED`.
    """
    _, doc = rows_for(_approve_both(make_record(tmp_path), "P2"), MERGE)
    assert doc.evidence["red_proof"] == NOT_REQUIRED
    assert doc.tier == "proved"


def test_a_p3_fix_with_no_proof_entry_is_proved(tmp_path):
    """Red 2026-10-20: mutation `_PROOF_OPTIONAL = ("P2",)`; failed at
    `assert doc.evidence["red_proof"] == NOT_REQUIRED`.
    """
    _, doc = rows_for(_approve_both(make_record(tmp_path), "P3"), MERGE)
    assert doc.evidence["red_proof"] == NOT_REQUIRED
    assert doc.tier == "proved"


def test_a_p1_fix_with_no_proof_is_historical(tmp_path):
    """Invariant: a P1 still needs a RED proof to be proved.

    Red 2026-10-20: mutation `_PROOF_OPTIONAL = ("P1", "P2", "P3")`; failed at
    `assert bug.evidence["red_proof"] == ABSENT`.
    """
    bug, _ = rows_for(_approve_both(make_record(tmp_path), "P2"), MERGE)
    assert bug.severity == "P1"
    assert bug.evidence["red_proof"] == ABSENT
    assert bug.tier == "historical"


def test_a_p2_whose_proof_is_not_red_is_historical(tmp_path):
    """Invariant: a failed proof is never excused, whatever the priority.

    Red 2026-10-20: mutation `if not verdicts or priority in _PROOF_OPTIONAL:` in `_proof`
    (priority checked before the verdicts); failed at
    `assert doc.evidence["red_proof"] == ABSENT`.
    """
    rec = make_record(tmp_path, proofs=[{"finding": "FIX-002", "nodeid": "t",
                                         "verdict": "TAUTOLOGICAL"}])
    _, doc = rows_for(_approve_both(rec, "P2"), MERGE)
    assert doc.evidence["red_proof"] == ABSENT
    assert doc.tier == "historical"


def test_a_finding_with_no_priority_and_no_proof_is_historical(tmp_path):
    """Invariant: fail closed when the priority is unknown.

    Red 2026-10-20: mutation `priority not in ("P1",)` in place of
    `priority in _PROOF_OPTIONAL`; failed at `assert doc.evidence["red_proof"] == ABSENT`.
    """
    _, doc = rows_for(_approve_both(make_record(tmp_path), None), MERGE)
    assert doc.severity is None
    assert doc.evidence["red_proof"] == ABSENT
    assert doc.tier == "historical"
