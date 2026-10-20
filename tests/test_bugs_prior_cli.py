"""Tests for `python -m cca_checks bugs prior`. Red proof (user rule, 2026-09-08).

* Make the missing-ledger path return 1 in `main`:
  `test_missing_ledger_prints_empty_block`, failing assertion `assert rc == 0`.
* Drop the "no origin remote" reason (pass `reason=None` when `remote_repo` is None):
  `test_no_origin_without_repo_flag`, failing assertion
  `assert out.rstrip().endswith("(no origin remote)")`.
* Remove the `except (OSError, GitError)` around `remote_repo` in `_prior`:
  `test_missing_git_binary_prints_empty_block` fails with FileNotFoundError raised inside
  `main` in the test body. `test_unreadable_root_prints_empty_block` stays green under this
  mutation: `git -C <missing>` exits 128, which `remote_repo` already maps to None, so
  it is a regression guard, not the red proof.
Each was applied, the test run, and the code restored. Both failures are assertions.

Final review, run 2026-10-20, same procedure, each failure in the test body:

* `"files": overlap` becomes `"files": row.get("files")` in the `--json` output:
  `test_json_shape`, `assert ['a.py', 'b.py'] == ['a.py']`. Its fixture row names two files
  and the query one, so the overlap differs from the row's files; with one file each (the
  version before this review) the test could not go red under this mutation.
* Drop `type=_date_arg` from `--before`: `test_before_flag_is_validated`, `Failed: DID NOT
  RAISE SystemExit`.
* Ignore `--repo` in `_prior` (`repo = a.repo` becomes `repo = None`):
  `test_prints_block_for_matching_rows`, `assert '- [P2]' in ...` (the empty block prints).
"""
from __future__ import annotations

import json
import subprocess

import pytest

from cca_checks.bugs.__main__ import main


def _ledger(tmp_path, monkeypatch, files=("a.py",)):
    monkeypatch.setenv("CCA_BUGS_DIR", str(tmp_path))
    row = {"bug_id": "B1", "finding_id": "DAT-002", "repo": "o/r", "tier": "proved",
           "sources": ["git:x"], "date": "2026-10-01", "severity": "P2", "files": list(files),
           "summary": "a bug", "fix_commit": "e3ee162" + "0" * 33, "supersedes": None}
    (tmp_path / "bugs.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")


def test_prints_block_for_matching_rows(tmp_path, monkeypatch, capsys):
    _ledger(tmp_path, monkeypatch)
    rc = main(["prior", "--repo", "o/r", "--files", "a.py"])
    assert rc == 0
    assert "- [P2]" in capsys.readouterr().out


def test_missing_ledger_prints_empty_block(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CCA_BUGS_DIR", str(tmp_path / "none"))
    rc = main(["prior", "--repo", "o/r", "--files", "a.py"])
    assert rc == 0
    assert capsys.readouterr().out.rstrip().endswith("(no ledger)")


def test_no_origin_without_repo_flag(tmp_path, monkeypatch, capsys):
    _ledger(tmp_path, monkeypatch)
    work = tmp_path / "work"
    work.mkdir()
    subprocess.run(["git", "init", "-q", str(work)], check=True)
    rc = main(["prior", "--root", str(work), "--files", "a.py"])
    assert rc == 0
    out = capsys.readouterr().out
    assert out.rstrip().endswith("(no origin remote)")


def test_unreadable_root_prints_empty_block(tmp_path, monkeypatch, capsys):
    _ledger(tmp_path, monkeypatch)
    rc = main(["prior", "--root", str(tmp_path / "missing"), "--files", "a.py"])
    assert rc == 0
    assert capsys.readouterr().out.rstrip().endswith("(no origin remote)")


def test_missing_git_binary_prints_empty_block(tmp_path, monkeypatch, capsys):
    _ledger(tmp_path, monkeypatch)

    def no_git(*args, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr("cca_checks.bugs.gitread.subprocess.run", no_git)
    rc = main(["prior", "--root", str(tmp_path), "--files", "a.py"])
    assert rc == 0
    assert capsys.readouterr().out.rstrip().endswith("(no origin remote)")


def test_json_shape(tmp_path, monkeypatch, capsys):
    _ledger(tmp_path, monkeypatch, files=("a.py", "b.py"))
    rc = main(["prior", "--repo", "o/r", "--files", "a.py", "--json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["repo"] == "o/r"
    assert data["reason"] is None
    assert data["rows"][0]["files"] == ["a.py"]
    assert data["rows"][0]["bug_id"] == "B1"


def test_before_flag_is_validated():
    with pytest.raises(SystemExit) as exc:
        main(["prior", "--repo", "o/r", "--files", "a.py", "--before", "2026-13-40"])
    assert exc.value.code == 2
