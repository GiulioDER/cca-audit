"""Tests for cca_checks.bugs.backfill: rows, evidence, duplicates, and the summary."""
import json

from bugs_testkit import commit, make_repo, publish

from cca_checks.bugs.backfill import backfill, summarize
from cca_checks.bugs.model import ABSENT, CLAIMED, EVIDENCED

MSG = (
    "fix(store): 2 audit fixes for #12 (DEEP tier)\n\n"
    "P1:\n- FIX-1: delete and bump share one transaction. +test_atomic\n"
    "P2:\n- FIX-2: prefixes match in full.\n"
)


def _ledger(tmp_path, rows, proofs=None):
    folder = tmp_path / "audits"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "AUDITOR_SCORECARD.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    if proofs is not None:
        (folder / "redproof-r1.json").write_text(json.dumps(proofs), encoding="utf-8")
    return [(path, "acme/widgets")]


def _disp(n, verdict="CONFIRMED"):
    return {"ts": "2026-09-01T09:00:00Z", "run_id": "r1", "auditor": "bug-auditor",
            "category": "atomicity", "priority": "P1", "verdict": verdict, "fix_id": f"FIX-{n}"}


def _out(n, arch):
    return {"run_id": "r1", "fix_id": f"FIX-{n}", "result": "fixed", "architect_verdict": arch}


def _repo(tmp_path, message=MSG):
    repo = make_repo(tmp_path / "widgets")
    commit(repo, "src/store.py", "x = 1\n", message, date="2026-09-02T10:00:00+00:00")
    publish(repo)
    return repo


def _by_id(result):
    return {r.finding_id: r for r in result.rows}


def test_without_a_ledger_every_condition_but_fixed_is_claimed(tmp_path):
    """Red proof: TEST_HINT match sets EVIDENCED instead of CLAIMED -> red_proof reads evidenced."""
    rows = _by_id(backfill([_repo(tmp_path)], []))
    one, two = rows["FIX-1"], rows["FIX-2"]
    assert one.evidence == {"confirmed_l25": CLAIMED, "fixed": EVIDENCED,
                            "l6_approved": CLAIMED, "red_proof": CLAIMED}
    assert two.evidence["red_proof"] == ABSENT
    assert one.tier == two.tier == "historical"
    assert one.files == ["src/store.py"] and one.project == "widgets"


def test_all_four_on_record_is_proved(tmp_path):
    """Red proof: skip the redproof block in _apply_ledger -> FIX-1 stays historical."""
    ledgers = _ledger(tmp_path, [_disp(1), _out(1, "APPROVED")],
                      proofs=[{"finding": "FIX-1", "verdict": "RED"}])
    one = _by_id(backfill([_repo(tmp_path)], ledgers))["FIX-1"]
    assert one.tier == "proved"
    assert one.run_id == "r1"
    assert one.category == "atomicity" and one.severity == "P1"
    assert any(s.startswith("redproof:") for s in one.sources)


def test_blocked_architect_keeps_the_row_and_marks_l6_absent(tmp_path):
    """Red proof: _apply_ledger reads 'result' instead of 'architect_verdict' -> l6 stays claimed."""
    ledgers = _ledger(tmp_path, [_disp(1), _out(1, "BLOCKED")])
    one = _by_id(backfill([_repo(tmp_path)], ledgers))["FIX-1"]
    assert one.evidence["l6_approved"] == ABSENT
    assert one.evidence["confirmed_l25"] == EVIDENCED


def test_a_false_positive_on_record_marks_confirmation_absent(tmp_path):
    """Red proof: _apply_ledger treats any verdict as CONFIRMED -> confirmed_l25 reads evidenced."""
    ledgers = _ledger(tmp_path, [_disp(1, "FALSE_POSITIVE")])
    one = _by_id(backfill([_repo(tmp_path)], ledgers))["FIX-1"]
    assert one.evidence["confirmed_l25"] == ABSENT


def test_a_squash_duplicate_collapses_to_the_earlier_commit(tmp_path):
    """Red proof: _dedupe keys on (repo, finding_id, fix_commit) -> two FIX-1 rows."""
    repo = make_repo(tmp_path / "widgets")
    first = commit(repo, "src/a.py", "a\n", MSG, date="2026-09-02T10:00:00+00:00")
    commit(repo, "src/b.py", "b\n", MSG.replace("for #12", "for #12 (#13)"),
           date="2026-09-03T10:00:00+00:00")
    publish(repo)
    rows = backfill([repo], []).rows
    assert [r.finding_id for r in rows] == ["FIX-1", "FIX-2"]
    assert {r.fix_commit for r in rows} == {first}


def test_a_ledger_copied_into_a_worktree_counts_once(tmp_path):
    """Red proof: _load_runs without the digest check -> two identical runs, ambiguous, not proved."""
    [(path, repo)] = _ledger(tmp_path, [_disp(1), _out(1, "APPROVED")],
                             proofs=[{"finding": "FIX-1", "verdict": "RED"}])
    copy = tmp_path / "worktree" / "audits"
    copy.mkdir(parents=True)
    for f in path.parent.iterdir():
        (copy / f.name).write_bytes(f.read_bytes())
    ledgers = [(path, repo), (copy / path.name, repo)]
    result = backfill([_repo(tmp_path)], ledgers)
    assert result.ambiguous == []
    assert _by_id(result)["FIX-1"].tier == "proved"


def test_unparsed_commits_are_reported_and_summarised(tmp_path):
    """Red proof: backfill drops commits with no bullet without recording them -> unparsed is empty."""
    repo = _repo(tmp_path)
    commit(repo, "src/c.py", "c\n", "fix: apply CCA audit fixes\n\nSee the PR.\n",
           date="2026-09-04T10:00:00+00:00")
    publish(repo)
    result = backfill([repo], [])
    assert len(result.unparsed) == 1 and "apply CCA audit fixes" in result.unparsed[0]
    summary = summarize(result)
    assert summary["rows"] == 2
    assert summary["tiers"] == {"historical": 2}
    assert summary["unparsed"] == 1
    assert summary["by_repo"] == {"acme/widgets": 2}
    assert summary["evidence"]["red_proof"] == {"claimed": 1, "absent": 1}
