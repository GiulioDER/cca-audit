"""Stage 1: `pipeline close` captures a run that fixed something as a pending record."""
import json
import os
import subprocess

import pytest
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


def _run_dir(root, run_id, *, commit, base):
    run_dir = root / ".claude" / "audits" / "runs" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "L4.json").write_text(json.dumps([{"fix_id": "FIX-001", "finding": "BUG-001"}]))
    (run_dir / "summary.json").write_text(json.dumps({"commit": commit, "base_commit": base}))
    return run_dir


def test_a_run_closed_at_its_base_commit_is_marked(git_repo, _private_bugs_dir):
    """I2. Invariant: capture writes commit_is_base true when HEAD at close is the run's
    base commit while L4 lists fixes, and false otherwise.

    Red 2026-10-13: mutation writing `"commit_is_base": False`; failed at
    `assert based["commit_is_base"] is True`."""
    _run_dir(git_repo, "based", commit="a" * 40, base="a" * 40)
    _run_dir(git_repo, "moved", commit="b" * 40, base="a" * 40)
    based = json.loads((pending.capture(git_repo, "based") / "meta.json").read_text())
    moved = json.loads((pending.capture(git_repo, "moved") / "meta.json").read_text())
    assert based["commit_is_base"] is True
    assert moved["commit_is_base"] is False


def test_a_capture_is_never_visible_half_written(git_repo, _private_bugs_dir, monkeypatch):
    """I5. Invariant: while a capture is being built, pending/ holds no folder under the
    record's final name; it appears only by rename, complete with meta.json.

    Red 2026-10-13: mutation building in place (`tmp = dest`, rename removed); failed at
    `assert seen == [[...]]` (the final name was visible before meta.json existed)."""
    _run_dir(git_repo, "r1", commit="a" * 40, base="b" * 40)
    seen = []
    real = pending.remote_repo

    def peek(root):
        seen.append(sorted(p.name for p in (_private_bugs_dir / "pending").iterdir()))
        return real(root)

    monkeypatch.setattr(pending, "remote_repo", peek)
    dest = pending.capture(git_repo, "r1")
    assert seen == [[f".tmp-r1__{'a' * 12}-{os.getpid()}"]]
    assert [p.name for p in (_private_bugs_dir / "pending").iterdir()] == [dest.name]
    assert (dest / "meta.json").exists()


def test_a_failed_capture_leaves_nothing_in_pending(git_repo, _private_bugs_dir, monkeypatch):
    """I5. Invariant: a capture that fails midway removes its temporary folder, so neither
    resolve nor the harvest sees a partial record.

    Red 2026-10-13: mutation deleting the `shutil.rmtree(tmp, ...)` cleanup; failed at
    `assert list(...) == []`."""
    _run_dir(git_repo, "r1", commit="a" * 40, base="b" * 40)

    def boom(root):
        raise OSError("disk full")

    monkeypatch.setattr(pending, "remote_repo", boom)
    with pytest.raises(OSError):
        pending.capture(git_repo, "r1")
    assert list((_private_bugs_dir / "pending").iterdir()) == []


def test_capturing_a_run_again_replaces_its_record(git_repo, _private_bugs_dir):
    """Invariant: a second capture of one run (a retro capture of a run already captured)
    replaces the record rather than failing on the rename. Not red-proved: without the
    removal, Windows raises FileExistsError from rename rather than failing an assert."""
    _run_dir(git_repo, "r1", commit="a" * 40, base="b" * 40)
    first = pending.capture(git_repo, "r1")
    assert pending.capture(git_repo, "r1") == first
    assert [p.name for p in (_private_bugs_dir / "pending").iterdir()] == [first.name]
