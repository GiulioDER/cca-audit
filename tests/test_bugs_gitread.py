"""Tests for cca_checks.bugs.gitread: clones, default branch, landed fix commits."""
from bugs_testkit import commit, commit_raw, make_repo, publish, run_git

from cca_checks.bugs.gitread import (
    default_ref,
    fix_commits,
    group_clones,
    is_undone,
    normalize_remote,
    revert_map,
)

FIX = "fix(store): 1 audit fix from CCA (STANDARD tier)\n\nP1:\n- BUG-001: a. +test\n"


def test_normalize_remote():
    """Red proof: _GITHUB without the ':' alternative -> the ssh form returns None."""
    assert normalize_remote("git@github.com:acme/widgets.git") == "acme/widgets"
    assert normalize_remote("https://github.com/acme/widgets.git\n") == "acme/widgets"
    assert normalize_remote("https://github.com/acme/widgets") == "acme/widgets"
    assert normalize_remote(r"C:\bundles\widgets.bundle") is None


def test_clones_of_one_remote_count_once(tmp_path):
    """Red proof: group_clones keys on the path instead of the repo -> two entries."""
    a = make_repo(tmp_path / "a", "git@github.com:acme/widgets.git")
    b = make_repo(tmp_path / "b", "https://github.com/ACME/widgets.git")
    c = make_repo(tmp_path / "c", r"C:\bundles\widgets.bundle")
    repos, skipped = group_clones([a, b, c])
    assert repos == {"acme/widgets": a}
    assert skipped == [(str(c), "no GitHub origin remote")]


def test_default_ref_falls_back_to_main(tmp_path):
    """Review Focus 5. Red proof: drop origin/main from default_ref's candidates -> None."""
    repo = make_repo(tmp_path / "r")
    publish(repo, branch="main")
    assert default_ref(repo) == "refs/remotes/origin/main"


def test_only_landed_fix_commits_are_read(tmp_path):
    """Red proof: fix_commits logs '--all' instead of ref -> the unmerged fix is read."""
    repo = make_repo(tmp_path / "r")
    landed = commit(repo, "src/a.py", "a = 1\n", FIX, date="2026-09-02T10:00:00+00:00")
    commit(repo, "src/b.py", "b = 1\n", "feat: unrelated work\n")
    publish(repo)
    run_git(repo, "checkout", "-q", "-b", "feature")
    commit(repo, "src/c.py", "c = 1\n", FIX.replace("BUG-001", "BUG-002"))
    commits = fix_commits(repo, default_ref(repo))
    assert [c.sha for c in commits] == [landed]
    assert commits[0].date == "2026-09-02"
    assert commits[0].files == ["src/a.py"]
    assert "BUG-001" in commits[0].body


def test_a_message_that_is_not_utf8_does_not_crash(tmp_path):
    """Review Focus 2. Red proof: git() without errors='replace' -> TypeError at the assertion.

    The strict decode fails inside subprocess's reader thread, which swallows it and
    hands back None, so the message is lost silently rather than raising: worse than a
    crash, and exactly what errors='replace' prevents.
    """
    repo = make_repo(tmp_path / "r")
    message = FIX.encode("utf-8").replace(b"a. +test", b"caf\xe9 +test")
    commit_raw(repo, "src/a.py", "a = 1\n", message)
    publish(repo)
    [only] = fix_commits(repo, default_ref(repo))
    assert "\ufffd" in only.body


def test_a_remote_name_cannot_carry_a_path():
    """Review (Minor, re-graded). Red proof: _GITHUB name class [^/\\s] -> the backslash path is accepted."""
    assert normalize_remote(r"https://github.com/acme/x\..\..\evil") is None
    assert normalize_remote("https://github.com/acme/re-call.v2_x") == "acme/re-call.v2_x"


def test_a_line_separator_in_a_subject_does_not_break_the_log(tmp_path):
    """Review (Minor, re-graded). Red proof: fix_commits uses splitlines() -> ValueError in the call."""
    repo = make_repo(tmp_path / "r")
    sha = commit(repo, "a.py", "a\n", FIX.replace("(STANDARD tier)", "(STANDARD tier)\u2028x"))
    publish(repo)
    assert [c.sha for c in fix_commits(repo, default_ref(repo))] == [sha]


def test_reverted_fixes_are_undone_and_a_revert_of_the_revert_restores_them(tmp_path):
    """Review I4. Red proof: is_undone always returns False -> the reverted fix reads as live."""
    repo = make_repo(tmp_path / "r")
    fix = commit(repo, "a.py", "a\n", FIX)
    run_git(repo, "revert", "--no-edit", "HEAD")
    reverts = revert_map(repo, "HEAD")
    assert is_undone(fix, reverts)
    publish(repo)
    assert fix_commits(repo, default_ref(repo))[0].sha == fix  # the 'Revert "..."' commit is not selected
    assert len(fix_commits(repo, default_ref(repo))) == 1
    run_git(repo, "revert", "--no-edit", "HEAD")
    assert not is_undone(fix, revert_map(repo, "HEAD"))
