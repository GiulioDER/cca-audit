"""The fix diff starts at the prefix tree, and each fix's size is checked against a threshold."""

from __future__ import annotations

import pytest
from conftest import _git, load_hook

from cca_checks import fixaudit
from cca_checks.bugs.caused_by import fix_diff
from cca_checks.fixaudit import FixAuditRefused, Hunk, fix_hunks, size_report

core = load_hook("cca_gate_core.py")


def _write(root, name, text):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text if isinstance(text, bytes) else text.encode("utf-8"))


def test_the_fix_diff_starts_at_the_prefix_tree_not_the_base(git_repo):
    """Invariant: only what the fixes changed, never the feature (committed or not).

    Node: tests/test_fixaudit_checks.py::test_the_fix_diff_starts_at_the_prefix_tree_not_the_base.
    Red 2026-11-22: skeleton baseline (NotImplementedError); then mutation of fix_hunks to diff
    from HEAD instead of prefix_tree failed `assert [h.added ...] == [("C = 3",)]` (B = 2 leaked).
    """
    _write(git_repo, "app.py", "x = 1\nA = 1\n")
    _git(git_repo, "commit", "-qam", "feature A")
    _write(git_repo, "app.py", "x = 1\nA = 1\nB = 2\n")
    prefix = core.fingerprint(git_repo)
    _write(git_repo, "app.py", "x = 1\nA = 1\nB = 2\nC = 3\n")
    hunks = fix_hunks(git_repo, prefix, core.fingerprint(git_repo))
    assert [h.added for h in hunks] == [("C = 3",)]
    assert hunks[0].id == "app.py:1"  # the hunk's start, with its leading context


def test_a_missing_prefix_tree_is_refused(git_repo):
    """Invariant: no prefix_tree means no fix diff; the base commit is never the fallback.

    Node: tests/test_fixaudit_checks.py::test_a_missing_prefix_tree_is_refused.
    Red 2026-11-22: skeleton baseline; failed with NotImplementedError, not FixAuditRefused.
    """
    with pytest.raises(FixAuditRefused, match="prefix_tree"):
        fix_hunks(git_repo, None, core.fingerprint(git_repo))


def test_added_deleted_and_renamed_files_are_counted_to_their_fix(git_repo):
    """Invariant: a created, deleted or renamed-and-edited file's lines reach its fix.

    Node: tests/test_fixaudit_checks.py::test_added_deleted_and_renamed_files_are_counted_to_their_fix.
    Red 2026-11-22: skeleton baseline; failed with NotImplementedError from fix_hunks.
    """
    _write(git_repo, "old.py", "".join(f"l{i}\n" for i in range(10)))
    _write(git_repo, "gone.py", "g1\ng2\n")
    _git(git_repo, "add", "-A")
    _git(git_repo, "commit", "-qm", "more")
    prefix = core.fingerprint(git_repo)
    (git_repo / "gone.py").unlink()
    (git_repo / "old.py").rename(git_repo / "new.py")
    _write(git_repo, "new.py", "".join(f"l{i}\n" for i in range(9)) + "edited\n")
    _write(git_repo, "created.py", "c1\nc2\nc3\n")
    hunks = fix_hunks(git_repo, prefix, core.fingerprint(git_repo))
    by_path = {h.path: h for h in hunks}
    assert set(by_path) == {"gone.py", "new.py", "created.py"}
    assert by_path["gone.py"].removed == ("g1", "g2")
    assert by_path["created.py"].added == ("c1", "c2", "c3")
    assert by_path["new.py"].added == ("edited",) and by_path["new.py"].removed == ("l9",)
    fixes = [{"fix_id": "F1", "files": ["gone.py", "new.py"]},
             {"fix_id": "F2", "files": ["created.py"]}]
    lines = {r["fix_id"]: r["lines"] for r in size_report(hunks, fixes)}
    assert lines == {"F1": 4, "F2": 3}


def test_a_binary_file_is_skipped_and_reported_and_odd_bytes_do_not_crash(git_repo):
    """Invariant: a binary file adds no hunk but is named; a non-UTF-8 text file still counts.

    Node: tests/test_fixaudit_checks.py::test_a_binary_file_is_skipped_and_reported_and_odd_bytes_do_not_crash.
    Red 2026-11-22: skeleton baseline; failed with NotImplementedError from parse_fix_diff.
    """
    prefix = core.fingerprint(git_repo)
    _write(git_repo, "blob.bin", b"\x00\x01\x02binary")
    _write(git_repo, "latin.txt", b"caf\xe9\n")
    hunks, skipped = fixaudit.parse_fix_diff(
        fix_diff(git_repo, prefix, core.fingerprint(git_repo)))
    assert skipped == ["blob.bin"]
    assert [h.path for h in hunks] == ["latin.txt"]
    assert len(hunks[0].added) == 1


def test_a_fix_over_the_threshold_needs_a_size_reason():
    """Invariant: lines split evenly between fixes sharing a file; test files do not count;
    an over-threshold fix without a size_reason is flagged.

    Node: tests/test_fixaudit_checks.py::test_a_fix_over_the_threshold_needs_a_size_reason.
    Red 2026-11-22: skeleton baseline (NotImplementedError); then mutating size_report to count
    test paths failed the F1 `lines` assert (521.0 != 21.0).
    """
    big = tuple(f"n{i}" for i in range(fixaudit.SIZE_THRESHOLD + 2))
    hunks = [Hunk("a.py", 1, 0, 1, len(big), (), big),
             Hunk("tests/test_a.py", 1, 0, 1, 500, (), ("t",) * 500),
             Hunk("b.py", 1, 0, 1, 1, (), ("x",))]
    fixes = [{"fix_id": "F1", "files": ["a.py", "tests/test_a.py"]},
             {"fix_id": "F2", "files": ["a.py", "b.py"], "size_reason": "generated table"},
             {"fix_id": "F3", "files": ["b.py"]}]
    report = {r["fix_id"]: r for r in size_report(hunks, fixes)}
    assert report["F1"] == {"fix_id": "F1", "lines": len(big) / 2, "over": False,
                            "has_reason": False}
    half = len(big) / 2
    assert report["F2"]["lines"] == half + 0.5 and report["F3"]["lines"] == 0.5
    solo = size_report([Hunk("a.py", 1, 0, 1, len(big), (), big)],
                       [{"fix_id": "F1", "files": ["a.py"]}])[0]
    assert solo["over"] and not solo["has_reason"]
    reasoned = size_report([Hunk("a.py", 1, 0, 1, len(big), (), big)],
                           [{"fix_id": "F1", "files": ["a.py"], "size_reason": "why"}])[0]
    assert reasoned["over"] and reasoned["has_reason"]
