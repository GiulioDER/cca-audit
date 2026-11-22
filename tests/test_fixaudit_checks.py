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
    result = fix_hunks(git_repo, prefix, core.fingerprint(git_repo))
    assert isinstance(result, tuple)  # (hunks, skipped), so skipped files have a consumer
    hunks, skipped = result
    assert skipped == []
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
    hunks, _ = fix_hunks(git_repo, prefix, core.fingerprint(git_repo))
    by_path = {h.path: h for h in hunks}
    assert set(by_path) == {"gone.py", "new.py", "created.py"}
    assert by_path["gone.py"].removed == ("g1", "g2")
    assert by_path["created.py"].added == ("c1", "c2", "c3")
    assert by_path["new.py"].added == ("edited",) and by_path["new.py"].removed == ("l9",)
    fixes = [{"fix_id": "F1", "files": ["gone.py", "new.py"]},
             {"fix_id": "F2", "files": ["created.py"]}]
    lines = {r["fix_id"]: r["lines"] for r in size_report(hunks, fixes)}
    assert lines == {"F1": 4, "F2": 3}


def test_a_fix_listing_the_old_path_of_a_renamed_file_owns_its_lines(git_repo):
    """Invariant: a renamed and edited file's lines go to a fix listing its old or new path,
    shared evenly, and a fix listing both counts them once.

    Node: tests/test_fixaudit_checks.py::test_a_fix_listing_the_old_path_of_a_renamed_file_owns_its_lines.
    Red 2026-11-22: baseline 1860ff7; failed `assert lines == {...}` (F1 got 0: only the new
    path matched).
    """
    _write(git_repo, "old.py", "".join(f"l{i}\n" for i in range(10)))
    _git(git_repo, "add", "-A")
    _git(git_repo, "commit", "-qm", "more")
    prefix = core.fingerprint(git_repo)
    (git_repo / "old.py").rename(git_repo / "new.py")
    _write(git_repo, "new.py", "".join(f"l{i}\n" for i in range(9)) + "edited\n")
    hunks, _ = fix_hunks(git_repo, prefix, core.fingerprint(git_repo))
    fixes = [{"fix_id": "F1", "files": ["old.py"]}, {"fix_id": "F2", "files": ["new.py"]},
             {"fix_id": "F3", "files": ["old.py", "new.py"]}]
    lines = {r["fix_id"]: r["lines"] for r in size_report(hunks, fixes)}
    assert lines == {"F1": 2 / 3, "F2": 2 / 3, "F3": 2 / 3}


def test_a_binary_or_non_utf8_file_is_skipped_and_reported(git_repo):
    """Invariant: a binary or non-UTF-8 file adds no hunk and is named in `skipped`.

    Node: tests/test_fixaudit_checks.py::test_a_binary_or_non_utf8_file_is_skipped_and_reported.
    Red 2026-11-22: baseline 1860ff7; failed `assert skipped == ["blob.bin", "latin.txt"]`
    (latin.txt was counted as a hunk, not skipped).
    """
    prefix = core.fingerprint(git_repo)
    _write(git_repo, "blob.bin", b"\x00\x01\x02binary")
    _write(git_repo, "latin.txt", b"caf\xe9\n")
    _write(git_repo, "ok.py", "y = 2\n")
    hunks, skipped = fixaudit.parse_fix_diff(
        fix_diff(git_repo, prefix, core.fingerprint(git_repo)))
    assert skipped == ["blob.bin", "latin.txt"]
    assert [h.path for h in hunks] == ["ok.py"]


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


def _fix_hunks_of(repo, edit):
    """Hunks of the change `edit()` makes to the working tree, from a fingerprint taken before."""
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "feature")
    prefix = core.fingerprint(repo)
    edit()
    hunks, _ = fix_hunks(repo, prefix, core.fingerprint(repo))
    _git(repo, "add", "-A")  # git grep searches tracked files
    return hunks


def _spaced(line):
    """One line followed by padding, so no sibling falls in the fix's own hunk context."""
    return line + "\n" + "\n".join(f"# pad {i}" for i in range(8)) + "\n"


def _fix(**extra):
    return {"fix_id": "F1", "files": ["a.py"], "sibling_patterns": [], "sibling_reason": "auto only",
            **extra}


def test_bug001_shape_lists_the_other_readers(git_repo):
    """Invariant: a fix that changes one of N identical lines lists the other N-1 as siblings.

    Node: tests/test_fixaudit_checks.py::test_bug001_shape_lists_the_other_readers.
    Red 2026-11-22: skeleton baseline (NotImplementedError at sibling_hits). Mutation: disabling
    the own-hunk exclusion in sibling_hits listed a.py in the declared-pattern assert
    (`['a.py', 'b.py', 'c.py', 'd.py'] == ['b.py', 'c.py', 'd.py']`).
    """
    line = '    return p.read_text(encoding="utf-8")'
    for name in "abcd":
        _write(git_repo, f"{name}.py", _spaced(line))
    fixed = line.replace('"utf-8")', '"utf-8", errors="replace")')
    hunks = _fix_hunks_of(git_repo, lambda: _write(git_repo, "a.py", _spaced(fixed)))
    hits = fixaudit.sibling_hits(git_repo, hunks, [_fix()])
    assert sorted(h["path"] for h in hits) == ["b.py", "c.py", "d.py"]
    assert {h["fix_id"] for h in hits} == {"F1"}
    assert [h["hit_id"] for h in hits] == ["S-1", "S-2", "S-3"]
    assert all(h["text"] == line.strip() and h["pattern"] == line.strip() for h in hits)
    # the fixed line still contains this declared pattern; being inside the fix's own hunk, it is no sibling
    declared = fixaudit.sibling_hits(git_repo, hunks, [_fix(sibling_patterns=['read_text(encoding="utf-8"'])])
    assert sorted(h["path"] for h in declared) == ["b.py", "c.py", "d.py"]


def test_sec001_shape_lists_the_other_bare_git_launches(git_repo):
    """Invariant: the other copies of a changed process launch line are listed.

    Node: tests/test_fixaudit_checks.py::test_sec001_shape_lists_the_other_bare_git_launches.
    Red 2026-11-22: skeleton baseline (NotImplementedError at sibling_hits).
    """
    line = '    out = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True)'
    for name in ("one", "two", "three"):
        _write(git_repo, f"{name}.py", _spaced(line))
    hunks = _fix_hunks_of(git_repo, lambda: _write(git_repo, "one.py",
                                                   _spaced(line.replace('"git"', "GIT"))))
    hits = fixaudit.sibling_hits(git_repo, hunks, [_fix(files=["one.py"])])
    assert sorted(h["path"] for h in hits) == ["three.py", "two.py"]


def test_num001_shape_needs_a_declared_pattern(git_repo):
    """Invariant: a sibling that is not a textual copy is found only through a declared pattern;
    a `re:` prefix makes a declared pattern a regex, and the default is a fixed string.

    Node: tests/test_fixaudit_checks.py::test_num001_shape_needs_a_declared_pattern.
    Red 2026-11-22: skeleton baseline (NotImplementedError at sibling_hits).
    """
    old = '    return load(root / "runs" / run_id / "summary.json")'
    _write(git_repo, "a.py", old + "\n")
    _write(git_repo, "other.py", _spaced('    return json_load(root / "runs" / run_id / "summary.json")'))
    hunks = _fix_hunks_of(git_repo, lambda: _write(
        git_repo, "a.py", old.replace("summary", "run") + "\n"))
    assert fixaudit.sibling_hits(git_repo, hunks, [_fix()]) == []
    declared = _fix(sibling_patterns=['"runs" / run_id / "summary.json"'])
    assert [h["path"] for h in fixaudit.sibling_hits(git_repo, hunks, [declared])] == ["other.py"]
    regex = _fix(sibling_patterns=[r're:"runs" / \w+ / "summary\.json"'])
    assert [h["path"] for h in fixaudit.sibling_hits(git_repo, hunks, [regex])] == ["other.py"]
    literal = _fix(sibling_patterns=[r'"runs" / \w+ / "summary\.json"'])
    assert fixaudit.sibling_hits(git_repo, hunks, [literal]) == []


def test_a_fix_without_sibling_patterns_or_reason_is_refused():
    """Invariant: a fix declares sibling_patterns, empty only with a non-empty sibling_reason.

    Node: tests/test_fixaudit_checks.py::test_a_fix_without_sibling_patterns_or_reason_is_refused.
    Red 2026-11-22: skeleton raised NotImplementedError, not FixAuditRefused, so pytest.raises
    failed.
    """
    from pathlib import Path
    for fix in ({"fix_id": "F1", "files": ["a.py"]},
                {"fix_id": "F1", "files": ["a.py"], "sibling_patterns": []},
                {"fix_id": "F1", "files": ["a.py"], "sibling_patterns": [], "sibling_reason": "  "}):
        with pytest.raises(FixAuditRefused, match="F1"):
            fixaudit.sibling_hits(Path("."), [], [fix])


def test_fixed_is_rejected_while_the_hit_still_matches(git_repo):
    """Invariant: "fixed" holds only if the hit's text is gone from its path; "not_applicable"
    needs a reason; every hit needs a disposition.

    Node: tests/test_fixaudit_checks.py::test_fixed_is_rejected_while_the_hit_still_matches.
    Red 2026-11-22: skeleton baseline (NotImplementedError at check_dispositions).
    """
    _write(git_repo, "b.py", "x = f(1)\n")
    hit = {"hit_id": "S-1", "fix_id": "F1", "pattern": "f(1)", "path": "b.py", "line": 1,
           "text": "x = f(1)"}
    check = fixaudit.check_dispositions
    assert check([hit], {"S-1": {"disposition": "fixed"}}, git_repo) == [
        "S-1: marked fixed, but 'x = f(1)' still occurs in b.py"]
    _write(git_repo, "b.py", "x = f(1, safe=True)\n")
    assert check([hit], {"S-1": {"disposition": "fixed"}}, git_repo) == []
    assert check([hit], {"S-1": {"disposition": "not_applicable", "reason": "a test double"}},
                 git_repo) == []
    assert check([hit], {"S-1": {"disposition": "not_applicable", "reason": " "}}, git_repo) == [
        "S-1: not_applicable needs a reason"]
    assert check([hit], {}, git_repo) == ["S-1: no disposition"]
    assert check([hit], {"S-1": {"disposition": "later"}}, git_repo) == [
        "S-1: disposition must be 'fixed' or 'not_applicable', not 'later'"]


def test_a_pattern_over_50_hits_is_refused(git_repo):
    """Invariant: a pattern matching more than MAX_HITS_PER_PATTERN sites is refused, never cut.

    Node: tests/test_fixaudit_checks.py::test_a_pattern_over_50_hits_is_refused.
    Red 2026-11-22: skeleton raised NotImplementedError, not FixAuditRefused.
    """
    line = "    call(1)"
    many = fixaudit.MAX_HITS_PER_PATTERN + 1
    _write(git_repo, "many.py", "\n".join(line for _ in range(many)) + "\n")
    _write(git_repo, "a.py", _spaced(line))
    hunks = _fix_hunks_of(git_repo, lambda: _write(git_repo, "a.py", _spaced("    call(2)")))
    with pytest.raises(FixAuditRefused, match=r"call\(1\).*narrower"):
        fixaudit.sibling_hits(git_repo, hunks, [_fix()])
    _write(git_repo, "many.py", "\n".join(line for _ in range(many - 1)) + "\n")
    _git(git_repo, "add", "-A")
    assert len(fixaudit.sibling_hits(git_repo, hunks, [_fix()])) == many - 1


def test_trivial_lines_never_become_patterns():
    """Invariant: only removed non-test lines with a call or a string literal become patterns,
    whitespace collapsed.

    Node: tests/test_fixaudit_checks.py::test_trivial_lines_never_become_patterns.
    Red 2026-11-22: skeleton baseline (NotImplementedError at auto_patterns).
    """
    hunks = [Hunk("a.py", 1, 5, 1, 0, ("    return None", "  )", "", "x   =  f( 1 )", "y = 'a'"), ()),
             Hunk("tests/test_a.py", 1, 1, 1, 0, ("assert f(1)",), ())]
    assert fixaudit.auto_patterns(hunks) == ["x = f( 1 )", "y = 'a'"]
    assert fixaudit.auto_patterns(hunks[:1] * 2) == ["x = f( 1 )", "y = 'a'"]  # no duplicates
