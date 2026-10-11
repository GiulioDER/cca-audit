"""Tests for cca_checks.bugs.ledgers: scorecard ledgers, proof files, run matching."""
import json
from datetime import date

from bugs_testkit import make_repo

from cca_checks.bugs.ledgers import (
    LoadReport,
    fix_number,
    ledger_repo,
    load_ledger,
    match_key,
    match_run,
    merge_runs,
)


def _write(path, rows, extra_lines=()):
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(r) for r in rows] + list(extra_lines)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return path


DISP = {"ts": "2026-09-30T07:28:42+00:00", "run_id": "r1", "auditor": "bug-auditor",
        "category": "regex", "severity": "High", "priority": "P1",
        "verdict": "CONFIRMED", "fix_id": "FIX-1"}
OUT = {"run_id": "r1", "fix_id": "FIX-1", "result": "fixed", "architect_verdict": "APPROVED"}


def test_fix_ids_normalise():
    """Red proof: fix_number without int() (keep the digits string) -> 'FIX-01' != 'FIX-1'."""
    assert fix_number("FIX-01") == fix_number("FIX-1") == fix_number("fix-001") == 1
    assert fix_number("STAKES-001") is None
    assert fix_number(None) is None
    assert match_key("FIX-007") == "FIX-7"
    assert match_key("SEC-001") == "SEC-001"


def test_a_ledger_belongs_to_the_clone_that_holds_it(tmp_path):
    """Red proof: ledger_repo reads parents[3] instead of parents[2] -> None.

    parents[1] (.claude) would not go red: git walks up from it to the same clone. One
    level too far leaves the clone, which is the off-by-one that loses the repository.
    """
    project = make_repo(tmp_path / "proj")
    path = _write(project / ".claude" / "audits" / "AUDITOR_SCORECARD.jsonl", [DISP])
    assert ledger_repo(path) == "acme/widgets"


def test_a_ledger_groups_rows_into_runs(tmp_path):
    """Red proof: load_ledger files outcome rows into dispositions -> outcomes is empty."""
    path = _write(tmp_path / "AUDITOR_SCORECARD.jsonl", [DISP, OUT])
    [run] = load_ledger(path, "acme/widgets", LoadReport())
    assert run.run_id == "r1"
    assert run.day == date(2026, 9, 30)
    assert run.dispositions[1]["verdict"] == "CONFIRMED"
    assert run.dispositions[1]["_line"] == 1
    assert run.outcomes[1]["architect_verdict"] == "APPROVED"
    assert run.keys() == {"FIX-1"}


def test_a_malformed_line_is_skipped_and_counted(tmp_path):
    """Review Focus 3. Red proof: drop `report.malformed += 1` -> the count stays 0."""
    path = _write(tmp_path / "AUDITOR_SCORECARD.jsonl", [DISP], extra_lines=["{not json"])
    report = LoadReport()
    [run] = load_ledger(path, "acme/widgets", report)
    assert report.malformed == 1
    assert 1 in run.dispositions


def test_a_finding_key_is_kept_beside_the_fix_id(tmp_path):
    """Red proof: load_ledger ignores the 'finding' key -> findings is empty."""
    row = {"ts": "2026-09-05T12:00:00Z", "verdict": "CONFIRMED",
           "finding": "SEC-001", "fix_id": "FIX-002"}
    [run] = load_ledger(_write(tmp_path / "L.jsonl", [row]), "acme/widgets", LoadReport())
    assert run.findings["SEC-001"]["fix_id"] == "FIX-002"
    assert run.keys() == {"FIX-2", "SEC-001"}


def test_a_proof_file_attaches_to_the_only_run(tmp_path):
    """Red proof: _attach_proofs lets a later RED overwrite a TAUTOLOGICAL -> FIX-2 reads RED."""
    path = _write(tmp_path / "AUDITOR_SCORECARD.jsonl", [DISP, OUT])
    proofs = [
        {"finding": "FIX-1", "nodeid": "t::a", "verdict": "RED"},
        {"finding": "FIX-1", "nodeid": "t::b", "verdict": "RED"},
        {"finding": "FIX-2", "nodeid": "t::c", "verdict": "TAUTOLOGICAL"},
        {"finding": "FIX-2", "nodeid": "t::d", "verdict": "RED"},
    ]
    (tmp_path / "redproof-fix1.json").write_text(json.dumps(proofs), encoding="utf-8")
    [run] = load_ledger(path, "acme/widgets", LoadReport())
    assert run.redproof == {1: "RED", 2: "TAUTOLOGICAL"}
    assert run.proof_source.endswith("redproof-fix1.json")


def test_a_proof_file_with_two_runs_and_no_run_id_is_ignored(tmp_path):
    """Red proof: attach to the first run when more than one exists -> nothing reported."""
    other = {**DISP, "run_id": "r2"}
    path = _write(tmp_path / "AUDITOR_SCORECARD.jsonl", [DISP, other])
    (tmp_path / "redproof-x.json").write_text(
        json.dumps([{"finding": "FIX-1", "verdict": "RED"}]), encoding="utf-8")
    report = LoadReport()
    runs = load_ledger(path, "acme/widgets", report)
    assert all(not r.redproof for r in runs)
    assert len(report.ignored_proofs) == 1


def _runs(tmp_path, *rows):
    return load_ledger(_write(tmp_path / "L.jsonl", list(rows)), "acme/widgets", LoadReport())


def test_match_needs_shared_keys_in_the_window(tmp_path):
    """Red proof: match_run without the key-overlap condition -> the STAKES commit matches r1."""
    runs = _runs(tmp_path, DISP)
    hit, problem = match_run(runs, "acme/widgets", date(2026, 10, 1), {"FIX-1"})
    assert hit is runs[0] and problem is None
    assert match_run(runs, "acme/widgets", date(2026, 10, 1), {"STAKES-001"}) == (None, None)
    assert match_run(runs, "acme/widgets", date(2026, 10, 9), {"FIX-1"}) == (None, None)
    assert match_run(runs, "acme/other", date(2026, 10, 1), {"FIX-1"}) == (None, None)


def test_an_ambiguous_match_upgrades_nothing(tmp_path):
    """Red proof: match_run returns hits[0] when several qualify -> a run comes back."""
    runs = _runs(tmp_path, DISP, {**DISP, "run_id": "r2"})
    hit, problem = match_run(runs, "acme/widgets", date(2026, 10, 1), {"FIX-1"})
    assert hit is None
    assert problem.startswith("ambiguous:")


def test_runs_split_across_ledger_copies_merge_into_one(tmp_path):
    """Review I6. Red proof: merge_runs returns its input unchanged -> two r1 runs."""
    a = load_ledger(_write(tmp_path / "a" / "L.jsonl", [DISP]), "acme/widgets", LoadReport())
    b = load_ledger(_write(tmp_path / "b" / "L.jsonl", [DISP, OUT, {**DISP, "run_id": "r2"}]),
                    "acme/widgets", LoadReport())
    runs = merge_runs(a + b)
    assert sorted(r.key for r in runs) == ["acme/widgets:r1", "acme/widgets:r2"]
    [r1] = [r for r in runs if r.run_id == "r1"]
    assert r1.outcomes[1]["architect_verdict"] == "APPROVED"
