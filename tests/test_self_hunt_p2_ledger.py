"""P2 ledger fixes from the CCA self hunt, run 20261120T201246Z.

Each test names its finding. Red proofs: each behaviour test below, run against the snapshot
taken before these fixes (`.claude/audits/.p2_snapshot`), failed in its assertion; the run's
`tautology verify` output is quoted in the pull request.
"""
from __future__ import annotations

import json
import os
import sys
import threading
from dataclasses import replace

import pytest

from cca_checks.bugs import fp_records, github, gitread, holdout, pending, store
from cca_checks.bugs.anchors import Anchor
from cca_checks.bugs.model import EVIDENCED, BugRow
from cca_checks.bugs.records import read_ledger


def _bug(finding_id: str = "BUG-1", **changes) -> BugRow:
    row = BugRow(project="widgets", repo="acme/widgets", finding_id=finding_id,
                 fix_commit="a" * 40, date="2026-09-01", files=["pool.py"],
                 summary="leaks a connection", anchors=[Anchor("pool.py", "lease", "1" * 64)],
                 evidence={"confirmed_l25": EVIDENCED, "fixed": EVIDENCED,
                           "l6_approved": EVIDENCED, "red_proof": EVIDENCED})
    return replace(row, **changes)


def _ids(root) -> list[str]:
    lines = (root / store.LEDGER).read_text(encoding="utf-8").splitlines()
    out = []
    for line in lines:
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        out.append(value.get("bug_id") or value.get("id"))
    return out


def test_a_list_valued_supersedes_does_not_crash_the_ledger_readers(tmp_path):
    """BUG-004. Invariant: one malformed ledger line is skipped, not a TypeError out of every
    reader. Failure mode: `inherit_rows` used a list-valued `supersedes` as a dict key.
    Red: TypeError "cannot use 'list' as a dict key" from read_ledger on the pre-fix code."""
    good = _bug("BUG-1")
    bad = dict(_bug("BUG-2", anchors=[]).to_json(), supersedes=["widgets:x:BUG-0"])
    (tmp_path / store.LEDGER).write_text(
        json.dumps(good.to_json()) + "\n" + json.dumps(bad) + "\n", encoding="utf-8")
    rows, _ = read_ledger(tmp_path)
    assert good.id in rows
    assert good.id in {r.id for r in store.bug_rows(tmp_path / store.LEDGER)}


def test_a_row_after_a_torn_last_line_is_still_written(tmp_path):
    """BUG-005. Invariant: a row appended after an interrupted append is readable.
    Failure mode: `write` appended onto the torn line, so both became one unparseable line
    while `write` still counted the row as added. Red: "BUG-2" missing from the ids."""
    (tmp_path / store.LEDGER).write_text('{"kind": "bug", "bug_id": "torn', encoding="utf-8")
    added, _ = store.write([_bug("BUG-2")], tmp_path, "2026-11-20T00:00:00Z")
    assert added == 1
    assert _bug("BUG-2").id in _ids(tmp_path)


def test_overlapping_writers_append_each_row_once(tmp_path, monkeypatch):
    """DAT-003. Invariant: two writers that overlap still write each id once.
    Failure mode: `write` read the existing ids and appended with no lock, so a second writer
    in that window appended the same rows again. The first writer pauses after reading the ids
    until the second has finished (or 1 s passes, which the lock forces).
    Red: each id twice in the ledger on the pre-fix code."""
    rows = [_bug(f"BUG-{i}") for i in range(1, 6)]
    first_read = threading.Event()
    second_done = threading.Event()
    real = store.existing_ids

    def existing_ids(ledger):
        ids = real(ledger)
        if threading.current_thread().name == "first":
            first_read.set()
            second_done.wait(timeout=1)
        return ids

    monkeypatch.setattr(store, "existing_ids", existing_ids)

    def second():
        first_read.wait(timeout=5)
        store.write(rows, tmp_path, "2026-11-20T00:00:00Z")
        second_done.set()

    a = threading.Thread(target=store.write, name="first",
                         args=(rows, tmp_path, "2026-11-20T00:00:00Z"))
    b = threading.Thread(target=second, name="second")
    a.start()
    b.start()
    a.join(timeout=30)
    b.join(timeout=30)
    ids = _ids(tmp_path)
    assert sorted(ids) == sorted(r.id for r in rows)


def _fp_row(finding_id: str) -> dict:
    return {"finding_id": finding_id, "run_id": "20261120T000000Z", "category": "c",
            "claim": "x", "evidence_refs": ["a.py:1"]}


def test_a_false_positive_id_that_escapes_the_memos_folder_is_refused():
    """SEC-004. Invariant: no false-positive record is built from an id that is not a safe
    memo name. Failure mode: `/../../../escaped` became a memo path outside the memos folder
    on Windows. Red: one record built from the unsafe id on the pre-fix code."""
    picked = [(_fp_row("/../../../escaped"), [Anchor("a.py", None, None)])]
    rows, skipped = fp_records.to_rows(picked, repo="acme/widgets", date="2026-11-20")
    assert rows == [] and skipped


def test_a_safe_false_positive_id_still_builds_its_record():
    """Control for the test above, before and after the fix."""
    picked = [(_fp_row("BUG-1"), [Anchor("a.py", None, None)])]
    rows, skipped = fp_records.to_rows(picked, repo="acme/widgets", date="2026-11-20")
    assert len(rows) == 1 and skipped == []


class _Argv:
    def __init__(self):
        self.seen: list[str] = []

    def __call__(self, argv, *args, **kwargs):
        self.seen.append(argv[0])

        class Done:
            returncode = 0
            stdout = "main\n"
            stderr = ""
        return Done()


def test_ledger_git_and_gh_launches_use_a_resolved_executable(tmp_path, monkeypatch):
    """SEC-005. Invariant: the bug-ledger modules never launch a bare `git` or `gh`, which
    Windows looks up in the working directory (the audited root at `pipeline close`) first.
    Red: argv[0] "git" from gitread.git on the pre-fix code."""
    for module in (gitread, github, holdout):
        argv = _Argv()
        monkeypatch.setattr(module.subprocess, "run", argv)
        if module is gitread:
            gitread.git(tmp_path, "status")
            assert pending._branch(tmp_path) == "main"
        elif module is github:
            github.gh(["api", "user"])
        else:
            holdout.project_root()
        assert argv.seen and all(os.path.isabs(exe) for exe in argv.seen), (module, argv.seen)


def test_a_line_torn_inside_a_character_does_not_stop_the_ledger_readers(tmp_path):
    """BUG-001 of audit run 20261120T225224Z. Invariant: every reader resolve, relink and
    record-unlinked run before writing skips a line torn inside a multibyte character.
    Failure mode: only existing_ids read with errors="replace"; bug_rows, _Ordinals and
    _Backfilled raised UnicodeDecodeError on every later pass, and the append-only ledger
    keeps the bytes. Red: UnicodeDecodeError from store.bug_rows on the pre-fix code."""
    from cca_checks.bugs import resolve

    good = json.dumps(_bug("BUG-1").to_json()).encode("utf-8")
    ledger = tmp_path / store.LEDGER
    ledger.write_bytes(good + b'\n{"kind": "bug", "bug_id": "torn", "summary": "x \xe2\x80')
    assert [r.id for r in store.bug_rows(ledger)] == [_bug("BUG-1").id]
    resolve._Ordinals(ledger)
    resolve._Backfilled(ledger)


@pytest.mark.skipif(sys.platform != "win32", reason="the PATHEXT and cwd-first lookup is Windows")
def test_a_git_bat_in_a_subdirectory_of_the_clone_is_never_run(tmp_path, monkeypatch):
    """SEC-001 of audit run 20261120T225224Z. Invariant: gitread.git never launches a git
    found inside the repository it reads. Failure mode: it refused only a git directly in the
    clone root, while Windows looks in the working directory first and applies PATHEXT, so a
    git.bat in a subdirectory the command ran from was executed. Red: the marker is written."""
    clone = tmp_path / "repo"
    sub = clone / "sub"
    sub.mkdir(parents=True)
    marker = tmp_path / "ran.txt"
    (sub / "git.bat").write_text(f'@echo hijacked> "{marker}"\r\n', encoding="ascii")
    monkeypatch.delenv("NoDefaultCurrentDirectoryInExePath", raising=False)
    monkeypatch.chdir(sub)
    try:
        gitread.git(clone, "status")
    except gitread.GitError:
        pass
    assert not marker.exists()
