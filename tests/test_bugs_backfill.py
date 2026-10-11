"""Tests for cca_checks.bugs.backfill: rows, evidence, duplicates, and the summary."""
import json
import subprocess

from bugs_testkit import commit, make_repo, publish, run_git

from cca_checks.bugs.backfill import backfill, summarize
from cca_checks.bugs.model import ABSENT, CLAIMED, EVIDENCED
from cca_checks.bugs.store import write

MSG = (
    "fix(store): 2 audit fixes for #12 (DEEP tier)\n\n"
    "P1:\n- FIX-1: delete and bump share one transaction. +test_atomic\n"
    "P2:\n- FIX-2: prefixes match in full.\n"
)


def _ledger(tmp_path, rows, proofs=None, run="r1"):
    folder = tmp_path / f"audits-{run}"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "AUDITOR_SCORECARD.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    if proofs is not None:
        (folder / f"redproof-{run}.json").write_text(json.dumps(proofs), encoding="utf-8")
    return [(path, "acme/widgets")]


def _disp(n, verdict="CONFIRMED", run="r1", ts="2026-09-01T09:00:00Z"):
    return {"ts": ts, "run_id": run, "auditor": "bug-auditor", "category": "atomicity",
            "priority": "P1", "verdict": verdict, "fix_id": f"FIX-{n}"}


def _out(n, arch, run="r1"):
    return {"run_id": run, "fix_id": f"FIX-{n}", "result": "fixed", "architect_verdict": arch}


def _repo(tmp_path, message=MSG, date="2026-09-02T10:00:00+00:00"):
    repo = make_repo(tmp_path / "widgets")
    commit(repo, "src/store.py", "x = 1\n", message, date=date)
    publish(repo)
    return repo


def _head(repo):
    return run_git(repo, "rev-parse", "HEAD").strip()[:12]


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


def test_all_four_on_record_and_bound_is_proved(tmp_path):
    """Red proof: skip the redproof block in _apply_ledger -> FIX-1 stays historical."""
    ledgers = _ledger(tmp_path, [_disp(1), _out(1, "APPROVED")],
                      proofs=[{"finding": "FIX-1", "verdict": "RED"}])
    repo = _repo(tmp_path)
    one = _by_id(backfill([repo], ledgers, {_head(repo): "r1"}))["FIX-1"]
    assert one.tier == "proved"
    assert one.run_id == "r1"
    assert one.category == "atomicity" and one.severity == "P1"
    assert any(s.startswith("redproof:") for s in one.sources)


def test_a_matching_run_is_only_a_candidate_until_bound(tmp_path):
    """Review C1. Red proof: backfill applies the auto-matched candidate -> all three read proved.

    FIX numbers restart in every run, so another run in the window with FIX-1..4 on
    record matches a commit whose own ledger was lost. Its artifacts are about other
    fixes, and must not make these bugs `proved`.
    """
    other = [_disp(n, run="r9") for n in (1, 2, 3, 4)] + [_out(n, "APPROVED", "r9") for n in (1, 2, 3, 4)]
    ledgers = _ledger(tmp_path, other, run="r9",
                      proofs=[{"finding": f"FIX-{n}", "verdict": "RED"} for n in (1, 2, 3, 4)])
    message = ("fix(store): 3 audit fixes for #40 (DEEP tier)\n\nP1:\n"
               "- FIX-1: a. +test_a\n- FIX-2: b. +test_b\n- FIX-3: c. +test_c\n")
    repo = _repo(tmp_path, message, date="2026-09-03T10:00:00+00:00")
    result = backfill([repo], ledgers)
    assert {r.tier for r in result.rows} == {"historical"}
    assert {r.evidence["confirmed_l25"] for r in result.rows} == {CLAIMED}
    assert len(result.candidates) == 1
    assert f"--bind {_head(repo)}=r9" in result.candidates[0]


def test_a_binding_that_matches_no_commit_is_reported(tmp_path):
    """Red proof: backfill does not track used bindings -> the stray binding goes unreported."""
    result = backfill([_repo(tmp_path)], [], {"deadbeef": "r1"})
    assert result.unused_bindings == ["deadbeef=r1"]


def test_blocked_architect_keeps_the_row_and_marks_l6_absent(tmp_path):
    """Red proof: _apply_ledger reads 'result' instead of 'architect_verdict' -> l6 stays claimed."""
    ledgers = _ledger(tmp_path, [_disp(1), _out(1, "BLOCKED")])
    repo = _repo(tmp_path)
    one = _by_id(backfill([repo], ledgers, {_head(repo): "r1"}))["FIX-1"]
    assert one.evidence["l6_approved"] == ABSENT
    assert one.evidence["confirmed_l25"] == EVIDENCED


def test_a_false_positive_on_record_marks_confirmation_absent(tmp_path):
    """Red proof: _apply_ledger treats any verdict as CONFIRMED -> confirmed_l25 reads evidenced."""
    ledgers = _ledger(tmp_path, [_disp(1, "FALSE_POSITIVE")])
    repo = _repo(tmp_path)
    one = _by_id(backfill([repo], ledgers, {_head(repo): "r1"}))["FIX-1"]
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


def test_one_run_split_across_ledger_copies_binds_once(tmp_path):
    """Red proof: _load_runs without merge_runs -> the binding names two runs and nothing is proved."""
    [(path, repo_name)] = _ledger(tmp_path, [_disp(1), _out(1, "APPROVED")],
                                  proofs=[{"finding": "FIX-1", "verdict": "RED"}])
    copy = tmp_path / "worktree" / "audits"
    copy.mkdir(parents=True)
    for f in path.parent.iterdir():
        (copy / f.name).write_bytes(f.read_bytes())
    with (copy / path.name).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(_disp(1, run="r2", ts="2026-09-20T09:00:00Z")) + "\n")
    repo = _repo(tmp_path)
    result = backfill([repo], [(path, repo_name), (copy / path.name, repo_name)],
                      {_head(repo): "r1"})
    assert result.ambiguous == []
    assert _by_id(result)["FIX-1"].tier == "proved"


def test_two_findings_with_one_id_in_one_commit_keep_distinct_ids(tmp_path):
    """Review I1. Red proof: skip _assign_ordinals -> both rows share a bug_id and write keeps one."""
    message = ("fix: audit fixes from CCA, two rounds squashed\n\n"
               "P1:\n- FIX-1: first round fix.\n\nP1:\n- FIX-1: second round fix.\n")
    result = backfill([_repo(tmp_path, message)], [])
    ids = [r.bug_id for r in result.rows]
    assert len(ids) == 2 and len(set(ids)) == 2
    assert summarize(result)["rows"] == write(result.rows, tmp_path / "out", "t")[0] == 2


def test_a_reverted_fix_is_not_counted(tmp_path):
    """Review I4. Red proof: backfill skips the is_undone check -> the reverted BUG-001 is a row."""
    repo = make_repo(tmp_path / "widgets")
    commit(repo, "a.py", "a\n", "fix: 1 audit fix from CCA\n\nP1:\n- BUG-001: a.\n")
    run_git(repo, "revert", "--no-edit", "HEAD")
    publish(repo)
    result = backfill([repo], [])
    assert result.rows == []
    assert len(result.reverted) == 1 and result.unparsed == []


def test_one_commit_in_two_repositories_counts_once(tmp_path):
    """Review I5. Red proof: skip _drop_cross_repo -> the lab copy repeats the row."""
    origin = _repo(tmp_path)
    lab = tmp_path / "lab"
    subprocess.run(["git", "clone", "-q", str(origin), str(lab)], check=True, capture_output=True)
    run_git(lab, "remote", "set-url", "origin", "https://github.com/acme/widgets-lab.git")
    publish(lab)
    result = backfill([origin, lab], [])
    assert [r.repo for r in result.rows] == ["acme/widgets", "acme/widgets"]
    assert len(result.cross_repo) == 2


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


def test_a_stand_in_id_has_no_auditor(tmp_path):
    """Red proof: _row derives the auditor from any ID prefix -> P1-1 gets auditor 'P1'."""
    message = "fix: 2 audit fixes from CCA\n\nP1:\n- B\n  the window closed late.\n- SEC-001: real.\n"
    rows = _by_id(backfill([_repo(tmp_path, message)], []))
    assert rows["P1-1"].auditor is None
    assert rows["P1-1"].summary == "B the window closed late."
    assert rows["SEC-001"].auditor == "SEC"
