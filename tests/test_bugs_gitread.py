"""Tests for cca_checks.bugs.gitread: clones, default branch, landed fix commits."""
from bugs_testkit import commit, commit_raw, make_repo, publish, run_git

from cca_checks.bugs.gitread import (
    default_ref,
    fix_commits,
    group_clones,
    normalize_remote,
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
