"""Tests for `python -m cca_checks bugs prior`. Red proof (user rule, 2026-09-08).

* Make the missing-ledger path return 1 in `main`:
  `test_missing_ledger_prints_empty_block`, failing assertion `assert rc == 0`.
* Drop the "no origin remote" reason (pass `reason=None` when `remote_repo` is None):
  `test_no_origin_without_repo_flag`, failing assertion
  `assert out.rstrip().endswith("(no origin remote)")`.
Each was applied, the test run, and the code restored. Both failures are assertions.
"""
from __future__ import annotations

import json
import subprocess

import pytest

from cca_checks.bugs.__main__ import main


def _ledger(tmp_path, monkeypatch):
    monkeypatch.setenv("CCA_BUGS_DIR", str(tmp_path))
    row = {"bug_id": "B1", "finding_id": "DAT-002", "repo": "o/r", "tier": "proved",
           "sources": ["git:x"], "date": "2026-10-01", "severity": "P2", "files": ["a.py"],
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


def test_json_shape(tmp_path, monkeypatch, capsys):
    _ledger(tmp_path, monkeypatch)
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
