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


def _fix(**extra):
    return {"fix_id": "F1", "files": ["a.py"], "sibling_patterns": [], "sibling_reason": "auto only",
            **extra}


# Real lines of cca_checks/bugs at 191d9c5 (store.py, resolve.py), with their own surroundings.
_STORE = '''    if not ledger.exists():
        return set()
    ids: set[str] = set()
    for line in ledger.read_text(encoding="utf-8").splitlines():
        try:
            value = json.loads(line)
            ids.add(value["bug_id"] if "bug_id" in value else value["id"])
        except (ValueError, KeyError):
            continue
    return ids


def rows(ledger):
    annotations = caused_by_annotations(ledger.parent)
    recovered = anchor_log.read(ledger.parent)
    rows = []
    for line in ledger.read_text(encoding="utf-8").splitlines():
        try:
            value = json.loads(line)
'''
_RESOLVE = '''    def __init__(self, ledger: Path):
        self.taken: dict[tuple, set[int]] = defaultdict(set)
        self.own: dict[tuple, list[int]] = defaultdict(list)
        if ledger.exists():
            for line in ledger.read_text(encoding="utf-8").splitlines():
                try:  # as tolerant as store.existing_ids
                    row = json.loads(line)


    def __init__(self, ledger: Path):
        self.ids: dict[tuple[str, str], set[str]] = defaultdict(set)
        if ledger.exists():
            for line in ledger.read_text(encoding="utf-8").splitlines():
                try:
                    row = json.loads(line)
'''


def test_bug001_shape_lists_the_other_readers(git_repo):
    """Invariant: a fix that adds errors="replace" to one of four ledger readers lists the other
    three, found through the call head `read_text(encoding="utf-8"` of its removed line.

    Node: tests/test_fixaudit_checks.py::test_bug001_shape_lists_the_other_readers.
    Red 2026-11-22: mutating `_call_heads` so that `heads.append(...)` is never reached failed
    `assert sorted(...) == [('resolve.py', 5), ...]` with `[] == [...]`. Dropping the occurrence
    index `str(n)` from the key in `_hit_id` failed `assert 2 == 3` (the two identical resolve.py
    lines shared one id).
    """
    _write(git_repo, "store.py", _STORE)
    _write(git_repo, "resolve.py", _RESOLVE)
    fixed = _STORE.replace('"utf-8").splitlines():\n        try:\n            value = json.loads(line)\n            ids',
                           '"utf-8", errors="replace").splitlines():\n        try:\n            value = json.loads(line)\n            ids')
    assert fixed != _STORE
    hunks = _fix_hunks_of(git_repo, lambda: _write(git_repo, "store.py", fixed))
    sweep = fixaudit.sibling_hits(git_repo, hunks, [_fix(files=["store.py"])])
    assert sorted((h["path"], h["line"]) for h in sweep.hits) == [
        ("resolve.py", 5), ("resolve.py", 13), ("store.py", 17)]
    ids = [h["hit_id"] for h in sweep.hits]
    assert len(set(ids)) == len(ids)  # resolve.py:5 and :13 are identical lines
    assert {h["pattern"] for h in sweep.hits} == {'read_text(encoding="utf-8"'}
    assert sweep.too_broad == []


_GITREAD = '''def git(clone: Path, *args: str) -> str:
    # Resolved, never bare: Windows looks for a bare `git` in the working directory first.
    exe = resolve_tool("git", os.getcwd())
    proc = subprocess.run(
        [exe, "-C", str(clone), *args], capture_output=True,
        text=True, encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed in {clone}: {proc.stderr.strip()}")
    return proc.stdout
'''
_GUARD = '''    if exe is not None and _is_inside(os.path.normcase(os.path.realpath(exe)),
                                      os.path.normcase(os.path.realpath(clone))):
        exe = None
    if exe is None:
        raise GitError("no trusted git executable on PATH")
'''
_HOLDOUT = '''def project_root() -> Path:
    """The Git top level of the working directory, else the working directory itself."""
    exe = resolve_tool("git", os.getcwd())  # never a bare `git` found in the audited cwd
    if exe is None:
        return Path(".")
'''
_GITHUB = '''def gh(args: list[str]) -> str:
    exe = resolve_tool("gh", os.getcwd())  # never a bare `gh` found in the working directory
    if exe is None:
'''


def test_sec001_shape_a_guard_added_after_a_call_lists_the_other_callers(git_repo):
    """Invariant: a fix that only ADDS lines (a guard after `exe = resolve_tool("git", ...)`) is
    swept through the call head of the context line it attaches to, and neither that anchor line
    nor the added lines are hits. `resolve_tool("gh"` is a different head: a declared pattern.

    Node: tests/test_fixaudit_checks.py::test_sec001_shape_a_guard_added_after_a_call_lists_the_other_callers.
    Red 2026-11-22: mutating `auto_patterns` to skip `h.anchors` failed
    `assert [h["path"] ...] == ["holdout.py"]` with `[] == ['holdout.py']`.
    """
    _write(git_repo, "gitread.py", _GITREAD)
    _write(git_repo, "holdout.py", _HOLDOUT)
    _write(git_repo, "github.py", _GITHUB)
    guarded = _GITREAD.replace('os.getcwd())\n', "os.getcwd())\n" + _GUARD, 1)
    hunks = _fix_hunks_of(git_repo, lambda: _write(git_repo, "gitread.py", guarded))
    assert [h.removed for h in hunks] == [()]
    assert [t for _, t in hunks[0].anchors] == ['    exe = resolve_tool("git", os.getcwd())']
    fix = _fix(files=["gitread.py"])
    assert [h["path"] for h in fixaudit.sibling_hits(git_repo, hunks, [fix]).hits] == ["holdout.py"]
    both = _fix(files=["gitread.py"], sibling_patterns=['resolve_tool("gh"'])
    assert sorted(h["path"] for h in fixaudit.sibling_hits(git_repo, hunks, [both]).hits) == [
        "github.py", "holdout.py"]


def test_a_sibling_on_the_line_right_after_the_fix_is_listed(git_repo):
    """Invariant: only ADDED lines are the fix's own; an unedited line in the fix's hunk context
    (here directly below the added guard) is still a sibling.

    Node: tests/test_fixaudit_checks.py::test_a_sibling_on_the_line_right_after_the_fix_is_listed.
    Red 2026-11-22: mutating the `edited` set in `sibling_hits` to the whole new-side hunk range
    (`range(h.new_start, h.new_start + h.new_len)`) failed `assert [...] == [("a.py", 4)]` with
    `[] == [('a.py', 4)]`.
    """
    _write(git_repo, "a.py", 'exe = resolve_tool("git", cwd)\nother = resolve_tool("git", cwd)\n')
    hunks = _fix_hunks_of(git_repo, lambda: _write(
        git_repo, "a.py",
        'exe = resolve_tool("git", cwd)\nif exe is None:\n    raise GitError("x")\n'
        'other = resolve_tool("git", cwd)\n'))
    sweep = fixaudit.sibling_hits(git_repo, hunks, [_fix()])
    assert [(h["path"], h["line"]) for h in sweep.hits] == [("a.py", 4)]


def test_a_pattern_matches_across_internal_spacing(git_repo):
    """Invariant: patterns are whitespace tolerant: a declared fixed string with single spaces
    finds a copy with doubled spaces, and a removed line's head finds a reformatted call.

    Node: tests/test_fixaudit_checks.py::test_a_pattern_matches_across_internal_spacing.
    Red 2026-11-22: mutating `_grep` to search `re.escape(pattern)` (no `_tolerant`) failed `assert [...] == [("b.py", 1), ("b.py", 2)]` with `[] == [...]`.
    """
    _write(git_repo, "a.py", 'x = f("a")  # why\n')
    _write(git_repo, "b.py", 'y = f(  "a" , 2)\nz =  f( "a")\n')
    hunks = _fix_hunks_of(git_repo, lambda: _write(git_repo, "a.py", 'x = f("a", safe=1)  # why\n'))
    sweep = fixaudit.sibling_hits(git_repo, hunks, [_fix()])
    assert [(h["path"], h["line"]) for h in sweep.hits] == [("b.py", 1), ("b.py", 2)]
    declared = fixaudit.sibling_hits(git_repo, hunks, [_fix(sibling_patterns=['f( "a" , 2'])])
    assert sorted(h["line"] for h in declared.hits) == [1, 2]


def test_a_tracked_binary_file_holding_the_pattern_is_skipped(git_repo):
    """Invariant: a tracked binary file that contains the pattern is never a hit nor a crash.

    Node: tests/test_fixaudit_checks.py::test_a_tracked_binary_file_holding_the_pattern_is_skipped.
    Red 2026-11-22: removing `-I` from the `git grep` arguments in `_grep` failed with
    ValueError (not enough values to unpack) from the "Binary file blob.bin matches" row.
    """
    _write(git_repo, "a.py", 'x = f("a")\n')
    _write(git_repo, "blob.bin", b'\x00\x01 f("a") \x02\n')
    hunks = _fix_hunks_of(git_repo, lambda: _write(git_repo, "a.py", 'x = f("a", 1)\n'))
    assert fixaudit.sibling_hits(git_repo, hunks, [_fix()]).hits == []


def test_the_sweep_never_lists_the_runs_own_files(git_repo_tracking_claude):
    """Invariant: in a repository that tracks .claude/, a pattern held by a run file (a run's
    notes quote the fix lines) is not a sibling; one elsewhere still is.

    Node: tests/test_fixaudit_checks.py::test_the_sweep_never_lists_the_runs_own_files.
    Red 2026-11-22: `_grep` without `"--", ":(exclude,literal).claude/audits"` (as at 2b269bd)
    failed `assert ['.claude/audits/runs/r/notes.txt', 'b.py'] == ['b.py']`.
    """
    repo = git_repo_tracking_claude
    _write(repo, "a.py", 'x = f("a")\n')
    _write(repo, "b.py", 'y = f("a")\n')
    _write(repo, ".claude/audits/runs/r/notes.txt", 'x = f("a")\n')
    hunks = _fix_hunks_of(repo, lambda: _write(repo, "a.py", 'x = f("a", 1)\n'))
    assert sorted(h["path"] for h in fixaudit.sibling_hits(repo, hunks, [_fix()]).hits) == ["b.py"]


def test_num001_shape_needs_a_declared_pattern(git_repo):
    """Invariant: a sibling that is not a textual copy is found only through a declared pattern;
    a `re:` prefix makes a declared pattern a regex, and the default is a fixed string.

    Node: tests/test_fixaudit_checks.py::test_num001_shape_needs_a_declared_pattern.
    Red 2026-11-22: mutating `_grep` to ignore the `re:` prefix (`if False else`) failed
    `assert [] == ['other.py']` at the `regex` assert. Dropping the word boundary in `_tolerant`
    failed the first assert (the `json_load(...)` reader came back as an auto hit).
    """
    old = '    return load(root / "runs" / run_id / "summary.json")'
    _write(git_repo, "a.py", old + "\n")
    _write(git_repo, "other.py", '    return json_load(root / "runs" / run_id / "summary.json")\n')
    hunks = _fix_hunks_of(git_repo, lambda: _write(
        git_repo, "a.py", old.replace("summary", "run") + "\n"))
    assert fixaudit.sibling_hits(git_repo, hunks, [_fix()]).hits == []
    declared = _fix(sibling_patterns=['"runs" / run_id / "summary.json"'])
    assert [h["path"] for h in fixaudit.sibling_hits(git_repo, hunks, [declared]).hits] == ["other.py"]
    regex = _fix(sibling_patterns=[r're:"runs" / \w+ / "summary\.json"'])
    assert [h["path"] for h in fixaudit.sibling_hits(git_repo, hunks, [regex]).hits] == ["other.py"]
    literal = _fix(sibling_patterns=[r'"runs" / \w+ / "summary\.json"'])
    assert fixaudit.sibling_hits(git_repo, hunks, [literal]).hits == []


def test_a_fix_without_sibling_patterns_or_reason_is_refused(git_repo):
    """Invariant: a fix declares sibling_patterns, empty only with a non-empty sibling_reason; a
    declared pattern needs no reason.

    Node: tests/test_fixaudit_checks.py::test_a_fix_without_sibling_patterns_or_reason_is_refused.
    Red 2026-11-22, each a mutation of the validation in `sibling_hits`:
    (1) `not str(fix.get("sibling_reason") or "").strip()` replaced by `False`: `[]` with no
    reason is accepted, `Failed: DID NOT RAISE FixAuditRefused` at case 2;
    (2) `.strip()` dropped: the whitespace reason is accepted, DID NOT RAISE at case 3;
    (3) `not declared and` dropped: a declared pattern without a reason is refused, so the
    positive case raises `FixAuditRefused: F1: give sibling_patterns, ...`;
    (4) `declared is None or` dropped: the missing key falls through to `fix["sibling_patterns"]`
    and fails with KeyError at case 1 (a crash, as the later code cannot run without the key).
    """
    base = {"fix_id": "F1", "files": ["a.py"]}
    for fix in ({**base, "sibling_reason": "no other copy"},
                {**base, "sibling_patterns": []},
                {**base, "sibling_patterns": [], "sibling_reason": "  "}):
        with pytest.raises(FixAuditRefused, match="F1"):
            fixaudit.sibling_hits(git_repo, [], [fix])
    assert fixaudit.sibling_hits(git_repo, [], [{**base, "sibling_patterns": ['zzz("q"']}]).hits == []
    assert fixaudit.sibling_hits(git_repo, [], [{**base, "sibling_patterns": [],
                                                 "sibling_reason": "no other copy"}]).hits == []


def test_an_unedited_hit_cannot_be_marked_fixed_and_ids_are_stable(git_repo):
    """Invariant: a listed hit is an unedited site, so only `not_applicable` with a reason clears
    it; its id is the same on a re-run and changes when its text does.

    Node: tests/test_fixaudit_checks.py::test_an_unedited_hit_cannot_be_marked_fixed_and_ids_are_stable.
    Red 2026-11-22: mutating `check_dispositions` to skip the `kind == "fixed"` branch failed
    `assert [...'must be not_applicable, not fixed'] == [...'marked fixed, but b.py:1 is
    unedited; ...']` (the generic message came back).
    """
    _write(git_repo, "a.py", 'x = f("a")\n')
    _write(git_repo, "b.py", 'y = f("a")\n')
    hunks = _fix_hunks_of(git_repo, lambda: _write(git_repo, "a.py", 'x = f("a", 1)\n'))
    (hit,) = fixaudit.sibling_hits(git_repo, hunks, [_fix()]).hits
    again = fixaudit.sibling_hits(git_repo, hunks, [_fix()]).hits[0]
    assert again["hit_id"] == hit["hit_id"] and hit["hit_id"].startswith("S-")
    assert len(hit["hit_id"]) == 2 + 10
    hid = hit["hit_id"]
    check = fixaudit.check_dispositions
    assert check([hit], {hid: {"disposition": "fixed"}}) == [
        f"{hid}: marked fixed, but b.py:1 is unedited; edit it, or mark it not_applicable with a reason"]
    assert check([hit], {hid: {"disposition": "not_applicable", "reason": "a test double"}}) == []
    assert check([hit], {hid: {"disposition": "not_applicable", "reason": " "}}) == [
        f"{hid}: not_applicable needs a reason"]
    assert check([hit], {}) == [f"{hid}: no disposition"]
    assert check([hit], {hid: {"disposition": "later"}}) == [
        f"{hid}: disposition must be 'not_applicable', not 'later'"]
    _write(git_repo, "b.py", 'y = f("a")  \nz = 1\n')  # trailing spaces only: same stripped text
    assert fixaudit.sibling_hits(git_repo, hunks, [_fix()]).hits[0]["hit_id"] == hid
    _write(git_repo, "b.py", 'y2 = f("a")\n')
    assert fixaudit.sibling_hits(git_repo, hunks, [_fix()]).hits[0]["hit_id"] != hid


def test_a_declared_pattern_over_50_hits_is_refused_an_auto_one_is_dropped(git_repo):
    """Invariant: a declared pattern over MAX_HITS_PER_PATTERN is refused naming it; an auto
    pattern over it is dropped into `too_broad` with its count; exactly the limit is accepted.

    Node: tests/test_fixaudit_checks.py::test_a_declared_pattern_over_50_hits_is_refused_an_auto_one_is_dropped.
    Red 2026-11-22: (a) mutating the declared branch to `continue` instead of raising failed
    `pytest.raises(FixAuditRefused)` (DID NOT RAISE); (b) mutating the auto branch
    (`if pattern in auto and ...` to `if False:`) failed the first `sibling_hits` call with
    `FixAuditRefused: F1: pattern 'call("x"' matches 51 sites`; (c) dropping
    `and pattern not in fix["sibling_patterns"]` (a declared pattern equal to an auto one is
    demoted) failed `Failed: DID NOT RAISE FixAuditRefused` at the `same` case.
    """
    line = '    call("x")'
    many = fixaudit.MAX_HITS_PER_PATTERN + 1
    _write(git_repo, "many.py", "\n".join(line for _ in range(many)) + "\n")
    _write(git_repo, "a.py", line + "\n")
    hunks = _fix_hunks_of(git_repo, lambda: _write(git_repo, "a.py", '    call("x", 2)\n'))
    sweep = fixaudit.sibling_hits(git_repo, hunks, [_fix()])
    assert sweep.hits == [] and sweep.too_broad == [
        {"fix_id": "F1", "pattern": 'call("x"', "count": many}]
    broad = _fix(sibling_patterns=['call("x")'])
    with pytest.raises(FixAuditRefused, match=r"call\(\"x\"\).*narrower"):
        fixaudit.sibling_hits(git_repo, hunks, [broad])
    same = _fix(sibling_patterns=['call("x"'])  # declared, though identical to the auto pattern
    with pytest.raises(FixAuditRefused, match="narrower"):
        fixaudit.sibling_hits(git_repo, hunks, [same])
    _write(git_repo, "many.py", "\n".join(line for _ in range(many - 1)) + "\n")
    _git(git_repo, "add", "-A")
    ok = fixaudit.sibling_hits(git_repo, hunks, [broad])
    assert len(ok.hits) == many - 1 and ok.too_broad == []


def test_call_heads_become_patterns_and_trivial_lines_do_not():
    """Invariant: a pattern is a call head `callee(first-arg` whose first argument holds a string
    literal, taken from removed non-test lines and from the context line above an added-only
    block; `return None`, `)`, calls without a literal and test files give none.

    Node: tests/test_fixaudit_checks.py::test_call_heads_become_patterns_and_trivial_lines_do_not.
    Red 2026-11-22: (a) mutating `auto_patterns` to skip `h.anchors` failed the final assert,
    `assert [] == ['h("k"']`; (b) forcing the literal check in `_call_heads` true
    (`if True:`) failed `assert ['f(1', 'read...', 'getcwd('] == ['read_text(e...']`;
    (c) not skipping test paths (`if False:` for `is_test_path`) failed
    `assert ['read_text(e...', 'g("z"'] == [...]`.
    """
    removed = ("    return None", "  )", "", "x = f(1)", 'for l in p.read_text(encoding="utf-8").splitlines():',
               'exe = resolve_tool("git", os.getcwd())', "y = 'a'")
    hunks = [Hunk("a.py", 1, 7, 1, 0, removed, ()),
             Hunk("tests/test_a.py", 1, 1, 1, 0, ('assert g("z")',), ())]
    assert fixaudit.auto_patterns(hunks) == ['read_text(encoding="utf-8"', 'resolve_tool("git"']
    assert fixaudit.auto_patterns(hunks[:1] * 2) == fixaudit.auto_patterns(hunks[:1])
    anchored = Hunk("b.py", 1, 2, 1, 3, (), ("x",), added_at=(2,), anchors=((1, '    h("k", 1)'),))
    assert fixaudit.auto_patterns([anchored]) == ['h("k"']


def test_a_head_keeps_the_whitespace_inside_a_string_literal(git_repo):
    """Invariant: whitespace inside a string literal of a call head is not collapsed, so
    `f("a  b")` matches its own copies and not `f("a b")`.

    Node: tests/test_fixaudit_checks.py::test_a_head_keeps_the_whitespace_inside_a_string_literal.
    Red 2026-11-22: mutating `_call_heads` to `arg = " ".join(line[m.end():end].split())` failed
    `assert [...] == [("b.py", 1)]`: the head became `f("a b"`, which matched c.py instead.
    """
    _write(git_repo, "a.py", 'x = f("a  b")\n')
    _write(git_repo, "b.py", 'y = f("a  b")\n')
    _write(git_repo, "c.py", 'z = f("a b")\n')
    hunks = _fix_hunks_of(git_repo, lambda: _write(git_repo, "a.py", 'x = f("a  b", 1)\n'))
    sweep = fixaudit.sibling_hits(git_repo, hunks, [_fix()])
    assert [(h["path"], h["line"]) for h in sweep.hits] == [("b.py", 1)]
    assert sweep.hits[0]["pattern"] == 'f("a  b"'


def test_a_call_head_does_not_match_inside_a_longer_name(git_repo):
    """Invariant: `load(` is not found in `json_load(` or `reload(`, but is found as `obj.load(`.

    Node: tests/test_fixaudit_checks.py::test_a_call_head_does_not_match_inside_a_longer_name.
    Red 2026-11-22: (a) dropping the `(^|[^[:alnum:]_])` prefix in `_tolerant` failed
    `assert [('b.py', 1), ..., ('c.py', 3)] == [('b.py', 1), ('c.py', 3)]` (the json_load and
    reload lines came back as well); (b) a prefix that also refuses a dot,
    `(^|[^.[:alnum:]_])`, failed `assert [('c.py', 3)] == [('b.py', 1), ('c.py', 3)]`.
    The NUM-001 test also fails under (a): its `json_load(...)` reader becomes an auto hit.
    """
    _write(git_repo, "a.py", 'x = load("a")\n')
    _write(git_repo, "b.py", 'y = obj.load("a")\n')
    _write(git_repo, "c.py", 'z = json_load("a")\nw = reload("a")\nv = load("a")\n')
    hunks = _fix_hunks_of(git_repo, lambda: _write(git_repo, "a.py", 'x = load("a", 1)\n'))
    sweep = fixaudit.sibling_hits(git_repo, hunks, [_fix()])
    assert sorted((h["path"], h["line"]) for h in sweep.hits) == [("b.py", 1), ("c.py", 3)]


def test_a_matched_line_holding_a_form_feed_is_a_hit_not_a_crash(git_repo):
    """Invariant: a matched line with a form feed (which `str.splitlines` treats as a line end)
    is one hit.

    Node: tests/test_fixaudit_checks.py::test_a_matched_line_holding_a_form_feed_is_a_hit_not_a_crash.
    Red 2026-11-22: mutating `_grep` to `out.splitlines()` failed with
    `ValueError: not enough values to unpack (expected 3, got 1)`.
    """
    _write(git_repo, "a.py", 'x = f("a")\n')
    _write(git_repo, "b.py", 'y = f("a")\x0c# tail\n')
    hunks = _fix_hunks_of(git_repo, lambda: _write(git_repo, "a.py", 'x = f("a", 1)\n'))
    sweep = fixaudit.sibling_hits(git_repo, hunks, [_fix()])
    assert [(h["path"], h["line"], h["text"]) for h in sweep.hits] == [("b.py", 1, 'y = f("a")\x0c# tail')]


def test_parse_records_added_lines_and_anchors():
    """Invariant: `added_at` holds the new-side numbers of added lines, and an `anchors` entry is
    the context line above a block that adds lines and removes none (not above a replacement).

    Node: tests/test_fixaudit_checks.py::test_parse_records_added_lines_and_anchors.
    Red 2026-11-22: mutating `end_run` to ignore `run_removed` failed
    `assert ((1, 'one'), (4, 'four')) == ((1, 'one'),)` (a replacement block's context line was
    also recorded).
    """
    diff = ("--- a/m.py\n+++ b/m.py\n@@ -1,5 +1,7 @@\n one\n+two\n+three\n four\n-five\n+FIVE\n six\n"
            " seven\n")
    (h,) = fixaudit.parse_fix_diff(diff)[0]
    assert h.added_at == (2, 3, 5)
    assert h.anchors == ((1, "one"),)


def test_a_matched_line_holding_a_bare_carriage_return_is_one_hit(git_repo):
    """Invariant: git() reads in text mode, so a bare CR in a matched line arrives as a newline;
    that line is still one hit, and the row after it is not lost.

    Node: tests/test_fixaudit_checks.py::test_a_matched_line_holding_a_bare_carriage_return_is_one_hit.
    Red 2026-11-22: mutating the `_grep` parse to the round 2 one (skip empty rows, split every
    row on NUL into three) failed with
    `ValueError: not enough values to unpack (expected 3, got 1)`.
    """
    _write(git_repo, "a.py", 'x = f("a")\n')
    _write(git_repo, "b.py", 'y = f("a")\r# tail\n')
    _write(git_repo, "c.py", 'z = f("a")\n')
    hunks = _fix_hunks_of(git_repo, lambda: _write(git_repo, "a.py", 'x = f("a", 1)\n'))
    sweep = fixaudit.sibling_hits(git_repo, hunks, [_fix()])
    assert [(h["path"], h["line"]) for h in sweep.hits] == [("b.py", 1), ("c.py", 1)]
    assert sweep.hits[0]["text"] == 'y = f("a")\n# tail'


def _added(path, *lines):
    """Hunks of a diff that only adds `lines` to `path`."""
    body = "".join(f"+{x}\n" for x in lines)
    diff = f"--- a/{path}\n+++ b/{path}\n@@ -1,0 +1,{len(lines)} @@\n{body}"
    return fixaudit.parse_fix_diff(diff)[0]


def _flagged(path, *lines):
    return {(f["mechanism"], f["line"])
            for f in fixaudit.mechanism_flags(_added(path, *lines), [{"fix_id": "F1", "files": [path]}])}


def test_bug002_shape_flags_a_timeout():
    r"""Invariant: a fix that adds a process launch with a timeout is flagged for both.

    Node: tests/test_fixaudit_checks.py::test_bug002_shape_flags_a_timeout.
    Red: 2026-11-22 mutating the timeout regex `\btimeout\s*=` to `\btimeoutX\s*=` failed
    `assert {('process', 1)} == {('process', 1), ('timeout', 1)}`.
    """
    assert _flagged("a.py", "    r = subprocess.run(cmd, timeout=t)") == {("process", 1), ("timeout", 1)}


def test_bug003_shape_flags_a_parser():
    r"""Invariant: json.loads over ledger rows is a parser of untrusted input.

    Node: tests/test_fixaudit_checks.py::test_bug003_shape_flags_a_parser.
    Red: 2026-11-22 mutating `\bjson\.loads?\b` to `\bjson\.dumps?\b` failed
    `assert set() == {('parser', 2)}`.
    """
    assert _flagged("a.py", "x = 1", "    row = json.loads(line)") == {("parser", 2)}


def test_a_shell_installer_has_its_own_rows():
    """Invariant: .sh and .ps1 files match the shell rows, not the Python ones.

    Node: tests/test_fixaudit_checks.py::test_a_shell_installer_has_its_own_rows.
    Red: 2026-11-22 mutating mechanism_flags to `table = MECHANISMS` failed
    `assert set() == {('redirect', 2), ('trap', 1)}`.
    """
    assert _flagged("i.sh", "trap cleanup EXIT", "run 2>/dev/null") == {("trap", 1), ("redirect", 2)}
    assert _flagged("i.ps1", "$ErrorActionPreference = 'Stop'") == {("ErrorActionPreference", 1)}
    assert _flagged("i.sh", "x = subprocess.run(c)") == set()


def test_test_files_never_raise_flags():
    """Invariant: a test may launch processes and write files freely.

    Node: tests/test_fixaudit_checks.py::test_test_files_never_raise_flags.
    Red: 2026-11-22 dropping `or is_test_path(h.path)` in mechanism_flags failed
    `assert {('process', 1), ('timeout', 1)} == set()`.
    """
    assert _flagged("tests/test_a.py", "subprocess.run(c, timeout=1)") == set()


def _flag(fix_id="F1", mechanism="timeout", auditors=("bug-auditor", "perf-auditor")):
    return {"fix_id": fix_id, "mechanism": mechanism, "path": "a.py", "line": 1,
            "auditors": list(auditors)}


def test_a_flagged_fix_without_failure_test_is_refused(tmp_path):
    """Invariant: a flagged fix must name failure_test; an unflagged one need not.

    Node: tests/test_fixaudit_checks.py::test_a_flagged_fix_without_failure_test_is_refused.
    Red: 2026-11-22 mutating `if not node:` in failure_test_reasons to `if False:` failed
    `assert (1 == 1 and False)` (the reason read "failure_test '' is not an existing ...").
    """
    fixes = [{"fix_id": "F1", "files": ["a.py"]}, {"fix_id": "F2", "files": ["b.py"]}]
    reasons = fixaudit.failure_test_reasons([_flag()], fixes, tmp_path)
    assert len(reasons) == 1 and reasons[0].startswith("F1: adds timeout")


def test_a_failure_test_naming_a_missing_node_is_refused(tmp_path):
    """Invariant: failure_test must name an existing file holding that def; a real one passes.

    Node: tests/test_fixaudit_checks.py::test_a_failure_test_naming_a_missing_node_is_refused.
    Red: 2026-11-22 mutating the def check to `if False and not re.search(...)` failed with
    `IndexError: list index out of range` (no reason for a missing def).
    """
    _write(tmp_path, "tests/test_x.py", "def test_timeout_fires():\n    pass\n")

    def reasons(node):
        return fixaudit.failure_test_reasons(
            [_flag()], [{"fix_id": "F1", "files": ["a.py"], "failure_test": node}], tmp_path)

    assert reasons("tests/test_x.py::test_timeout_fires") == []
    assert reasons("tests/test_x.py::TestC::test_timeout_fires[p1]") == []
    assert "no def test_nope" in reasons("tests/test_x.py::test_nope")[0]
    assert "not an existing" in reasons("tests/test_y.py::test_timeout_fires")[0]
    assert "not an existing" in reasons("tests/test_x.py")[0]


def test_no_more_than_four_auditors():
    """Invariant: at most 4 auditors, the most needed first; among ties an auditor a mechanism
    routes to comes first, security-auditor before the rest, then alphabetical; the data auditor
    is counted apart from bug-auditor.

    Node: tests/test_fixaudit_checks.py::test_no_more_than_four_auditors.
    Red: 2026-11-22 mutating `[:MAX_AUDITORS]` to `[:MAX_AUDITORS + 1]` failed
    `assert [..., 'perf-auditor'] == [..., 'numeric-auditor']` (a fifth auditor). Red (m5)
    2026-11-22 against 2b269bd's fixaudit.py (ties alphabetical) failed `assert ['bug-auditor',
    'bug-auditor:DAT', 'deploy-auditor', 'numeric-auditor'] == ['bug-auditor',
    'security-auditor', 'bug-auditor:DAT', 'deploy-auditor']`: the process launch lost
    security-auditor.
    """
    fixes = [{"fix_id": f"F{i}", "files": [], "finding": f"BUG-{i}"} for i in (1, 2, 3)]
    categories = {"BUG-1": "bug-auditor", "BUG-2": "bug-auditor", "BUG-3": "numeric-auditor"}
    flags = [_flag("F1", "process", ("security-auditor", "bug-auditor")),
             _flag("F2", "parser", ("bug-auditor", fixaudit.DATA_AUDITOR)),
             _flag("F3", "timeout", ("bug-auditor", "perf-auditor")),
             _flag("F3", "trap", ("deploy-auditor",))]
    got = fixaudit.auditors_for(fixes, flags, categories)
    assert got == ["bug-auditor", "security-auditor", "bug-auditor:DAT", "deploy-auditor"]
    assert fixaudit.auditors_for(fixes, [], categories) == ["bug-auditor", "numeric-auditor"]


def test_a_lock_is_flagged_and_a_lookalike_is_not():
    r"""Invariant: threading and msvcrt.locking are locks; a word that only contains one is not.

    Node: tests/test_fixaudit_checks.py::test_a_lock_is_flagged_and_a_lookalike_is_not.
    Red: 2026-11-22 mutating `\bmsvcrt\.locking\b` to `\bmsvcrtX\.locking\b` failed
    `assert {('lock', 1)} == {('lock', 1), ('lock', 2)}`; mutating `\bLock\b` to `Lock` failed
    `assert {('lock', 1)} == set()` (MyLocker flagged).
    """
    assert _flagged("a.py", "m = threading.Lock()", "msvcrt.locking(fd, 1, 1)") == {("lock", 1), ("lock", 2)}
    assert _flagged("a.py", "MyLocker = 1") == set()


def test_a_file_write_is_flagged_and_a_read_or_dump_is_not():
    r"""Invariant: open with a write, append or exclusive mode and write_text are file writes;
    open for reading and json.dumps are not.

    Node: tests/test_fixaudit_checks.py::test_a_file_write_is_flagged_and_a_read_or_dump_is_not.
    Red: 2026-11-22 mutating the mode class `[wax]` to `[wrax]` failed
    `assert {('file_write', 2)} == set()` (open(p, "r") flagged); mutating `\.write_(?:text|bytes)\(` to
    `\.writeX_(...` failed `assert {...} == {...}` (('file_write', 2) missing).
    """
    assert _flagged("a.py", 'f = open(p, "w")', "Path(p).write_text(s)", "g = open(p, 'ab')",
                    "os.replace(a, b)") == {("file_write", 1), ("file_write", 2), ("file_write", 3),
                                            ("file_write", 4)}
    assert _flagged("a.py", "f = open(p)", 'g = open(p, "r")', "s = json.dumps(x)") == set()


def test_a_docs_file_raises_no_flags():
    """Invariant: only .py files use the Python rows, so prose naming json.loads is not flagged.

    Node: tests/test_fixaudit_checks.py::test_a_docs_file_raises_no_flags.
    Red: 2026-11-22 mutating mechanism_flags `elif h.path.endswith(".py"):` to `elif True:` failed
    `assert {('parser', 1), ('timeout', 1)} == set()`.
    """
    assert _flagged("NOTES.md", "call json.loads(x) with timeout=3") == set()
    assert _flagged("cfg.toml", "timeout=3") == set()


def test_only_the_hunks_holding_u_fffd_are_dropped():
    """Invariant (SEC-001): a hunk holding U+FFFD (bytes that were not UTF-8) is dropped and its
    path named in `skipped`, but the file's other hunks stay, so their mechanism flags and their
    audit survive.
    Failure caught: one U+FFFD line hid a `subprocess.run(..., shell=True)` hunk of the same file
    from the delta and from the mechanism flags.
    Red 2026-11-22, tests/test_fixaudit_checks.py::test_only_the_hunks_holding_u_fffd_are_dropped:
    baseline 023a1b5 (`parse_fix_diff` dropped every hunk of the file); failed
    `assert [] == ['m.py:1']`."""
    diff = ("--- a/m.py\n+++ b/m.py\n@@ -1 +1 @@\n-a = 1\n+subprocess.run(x, shell=True)\n"
            f"@@ -10 +10 @@\n-b = 1\n+b = '{chr(0xFFFD)}'\n")
    hunks, skipped = fixaudit.parse_fix_diff(diff)
    assert [h.id for h in hunks] == ["m.py:1"]
    assert skipped == ["m.py"]
    flags = fixaudit.mechanism_flags(hunks, [{"fix_id": "F1", "files": ["m.py"]}])
    assert [f["mechanism"] for f in flags] == ["process"]


def test_the_size_threshold_stays_140_until_a_per_fix_re_measure():
    """Invariant: SIZE_THRESHOLD is 140. Correction 1 of
    docs/preregistrations/2026-11-22-fix-audit-loop.md holds it at 140 until a per-fix
    re-measure with the statistic `size_report` applies, from run archives; Q3's numbers are
    commit-level averages and must not set it. Correction 3 withdraws Correction 2's change to 35.
    Failure mode (FA-1-10, FIX-006, NUM-001): the threshold was set to 35 from that average.

    Node: tests/test_fixaudit_checks.py::test_the_size_threshold_stays_140_until_a_per_fix_re_measure.
    Red 2026-11-22 on the pass-1 tree (run 20261122T184346Z, before the repair), target
    `fixaudit.SIZE_THRESHOLD`: failed at the assert (35 != 140).
    """
    assert fixaudit.SIZE_THRESHOLD == 140
