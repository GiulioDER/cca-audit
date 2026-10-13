"""Stage 1: `pipeline close` captures a run that fixed something as a pending record."""
import json
import subprocess

from conftest import findings_output, plan_output, verdicts_output, write_output

from cca_checks import pipeline_state as ps
from cca_checks.bugs import pending


def _finish_run_with_a_fix(repo):
    """A STANDARD run that fixed one finding, recorded through L6, its files on disk."""
    state = ps.start("STANDARD", root=repo)
    ps.record("L1", output=write_output(repo), root=repo)
    ps.record("L2", counts={"unique": 0}, output=findings_output(repo), root=repo)
    ps.record("L2.5", counts={"confirmed": 0, "fp": 0, "uncertain": 0, "duplicate": 0},
              output=verdicts_output(repo), root=repo)
    ps.record("L2.6", source="tool", root=repo)
    ps.record("L3", counts={"to_fix": 0, "deferred": 0}, output=plan_output(repo), root=repo)
    (repo / "app.py").write_text("x = 2\n", encoding="utf-8")
    l4 = write_output(repo, [{"fix_id": "FIX-001", "finding": "BUG-001", "files": ["app.py"]}])
    ps.record("L4", fixes=1, output=l4, root=repo)
    ps.record("L5", root=repo)
    ps.record("L5.5", root=repo)
    ps.record("L5.6", source="tool", root=repo)
    ps.record("L6", detail="APPROVED", root=repo)
    ledger = repo / ".claude" / "audits" / "AUDITOR_SCORECARD.jsonl"
    with ledger.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"run_id": state.run_id, "fix_id": "FIX-001",
                             "architect_verdict": "APPROVED"}) + "\n")
        fh.write(json.dumps({"run_id": "someone-else", "fix_id": "FIX-009"}) + "\n")
    return state


def test_close_captures_the_run_with_its_own_rows_only(git_repo, _private_bugs_dir):
    """Invariant: close leaves pending/<run_id>__<commit12>/ with the run's files and only
    its own scorecard rows.

    Red 2026-10-13: mutation deleting the `_capture_pending(state, root)` call in `close`;
    failed at `folders = list((_private_bugs_dir / "pending").iterdir())` (FileNotFoundError,
    no pending directory), before `assert len(folders) == 1`.
    Red 2026-10-13: mutation dropping the run_id filter in `_own_rows`; failed at
    `assert len(rows) == 1`.
    """
    state = _finish_run_with_a_fix(git_repo)
    assert ps.close(root=git_repo) is not None
    folders = list((_private_bugs_dir / "pending").iterdir())
    assert len(folders) == 1
    record = folders[0]
    head = subprocess.run(["git", "-C", str(git_repo), "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()
    assert record.name == f"{state.run_id}__{head[:12]}"
    meta = json.loads((record / "meta.json").read_text(encoding="utf-8"))
    assert meta["run_id"] == state.run_id and meta["commit"] == head
    assert meta["repo"] is None  # the fixture repository has no GitHub origin
    for name in ("summary.json", "L4.json"):
        assert (record / name).exists()
    rows = (record / "scorecard.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(rows) == 1


def test_a_run_that_fixed_nothing_leaves_no_record(git_repo, _private_bugs_dir):
    """Red 2026-10-13: mutation removing the `if not fixes: return None` guard; failed at
    `assert not (_private_bugs_dir / "pending").exists()`.
    """
    state = ps.start("FAST", root=git_repo)
    ps.record("L1", output=write_output(git_repo), root=git_repo)
    ps.record("L2", counts={"unique": 0}, output=findings_output(git_repo), root=git_repo)
    ps.record("L2.5", counts={"confirmed": 0, "fp": 0, "uncertain": 0, "duplicate": 0},
              output=verdicts_output(git_repo), root=git_repo)
    ps.record("L3", counts={"to_fix": 0, "deferred": 0}, output=plan_output(git_repo),
              root=git_repo)
    ps.record("L6", detail="APPROVED", root=git_repo)
    ps.close(root=git_repo)
    assert state.run_id
    assert not (_private_bugs_dir / "pending").exists()


def test_a_capture_failure_never_refuses_close(git_repo, monkeypatch, capsys):
    """Invariant: capture is best-effort; close still succeeds and warns.

    Red 2026-10-13: mutation removing the try/except in `_capture_pending`; failed at
    `assert ps.close(root=git_repo) is not None` (OSError escaping `ps.close`).
    """
    _finish_run_with_a_fix(git_repo)

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(pending, "capture", boom)
    assert ps.close(root=git_repo) is not None
    assert "could not save this run for the bug ledger" in capsys.readouterr().err
