"""The PreToolUse guard, and the drift pin that keeps it honest.

The guard holds no rules of its own: it loads `cca_gate_core.py` from beside itself
by path, because it runs from the user's global hooks directory against
repositories where `cca_checks` was never installed. The drift pin
`test_guard_agrees_with_pipeline_state_across_a_matrix` still proves that the
guard and `pipeline_state` reach the same verdict on legacy states, and the
"lonely guard" tests prove a guard copied without its core fails closed.
"""

from __future__ import annotations

import importlib.util
import io
import json
import pathlib

import pytest
from conftest import findings_output, plan_output, verdicts_output, write_output

from cca_checks import pipeline_state as ps

_GUARD_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "cca_checks" / "plugin" / "hooks" / "cca_pipeline_guard.py"
)


def _load_guard():
    spec = importlib.util.spec_from_file_location("cca_pipeline_guard", _GUARD_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


guard = _load_guard()


def _run(monkeypatch, cwd: pathlib.Path, command: str) -> int:
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
    monkeypatch.chdir(cwd)
    monkeypatch.setattr("sys.stdin", io.StringIO(payload))
    return guard.main()


def _repo(tmp_path: pathlib.Path) -> pathlib.Path:
    (tmp_path / ".git").mkdir(parents=True)
    return tmp_path


def test_guard_is_silent_when_no_run_is_open(monkeypatch, tmp_path):
    assert _run(monkeypatch, _repo(tmp_path), "git commit -m x") == 0


def test_guard_ignores_commands_that_are_not_commits(monkeypatch, tmp_path):
    repo = _repo(tmp_path)
    ps.start("DEEP", root=repo)
    for command in ("pytest -q", "git status", "git log --oneline", "ls"):
        assert _run(monkeypatch, repo, command) == 0, command


@pytest.mark.parametrize(
    "command",
    [
        "git commit -m x",
        "git commit -am 'fix'",
        "git -C . commit -m x",
        "pytest -q && git commit -m x",
    ],
)
def test_guard_blocks_every_shape_of_commit(monkeypatch, tmp_path, command):
    # Under-matching is the dangerous direction: a commit form the regex misses
    # is an unverified fix that ships. Over-matching costs one file read.
    repo = _repo(tmp_path)
    ps.start("DEEP", root=repo)
    assert _run(monkeypatch, repo, command) == 2, command


def test_guard_allows_the_commit_once_the_gates_are_satisfied(monkeypatch, git_repo):
    state = ps.start("FAST", root=git_repo)
    ps.record("L1", output=write_output(git_repo), root=git_repo)
    ps.record("L2", counts={"unique": 0}, output=findings_output(git_repo), root=git_repo)
    ps.record("L2.5", counts={"confirmed": 0, "fp": 0, "uncertain": 0, "duplicate": 0},
              output=verdicts_output(git_repo), root=git_repo)
    ps.record("L3", counts={"to_fix": 0}, output=plan_output(git_repo), root=git_repo)
    ps.record("L6", detail="APPROVED", root=git_repo)
    assert state.run_id
    assert _run(monkeypatch, git_repo, "git commit -m x") == 0


def test_guard_refuses_an_unparseable_state(monkeypatch, tmp_path):
    repo = _repo(tmp_path)
    path = ps.state_path(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{oops", encoding="utf-8")
    assert _run(monkeypatch, repo, "git commit -m x") == 2


def test_a_run_in_a_sibling_checkout_does_not_block_this_one(monkeypatch, tmp_path):
    # find_root stops at the repository boundary. Without that, one worktree
    # mid-audit would block commits in every sibling worktree under the same
    # parent directory, which is a guard nobody keeps installed.
    parent = tmp_path
    ps.start("DEEP", root=parent)
    repo = _repo(parent / "other")
    assert _run(monkeypatch, repo, "git commit -m x") == 0


def test_guard_reads_a_bare_command_from_stdin(monkeypatch, tmp_path):
    # This repository's other hooks are invoked through a `case` wrapper that
    # pipes the raw command in. A guard that only understood the JSON envelope
    # would see an empty command there and guard nothing.
    repo = _repo(tmp_path)
    ps.start("DEEP", root=repo)
    monkeypatch.chdir(repo)
    monkeypatch.setattr("sys.stdin", io.StringIO("git commit -m x"))
    assert guard.main() == 2


def test_guard_names_the_missing_layers_on_stderr(monkeypatch, capsys, tmp_path):
    repo = _repo(tmp_path)
    ps.start("DEEP", root=repo)
    ps.record("L1", detail="8 auditors", output=write_output(repo), root=repo)
    assert _run(monkeypatch, repo, "git commit -m x") == 2
    err = capsys.readouterr().err
    assert "L2.5" in err and "L6" in err
    # The refusal has to carry its own escape hatch. A block with no stated way
    # out gets resolved by deleting the state file, which loses the trail.
    assert "pipeline abort" in err


_MATRIX = [
    ("DEEP", False, 0, {}),
    ("DEEP", False, 0, {"L1": "done", "L2": "done", "L2.5": "done", "L6": "APPROVED"}),
    ("DEEP", False, 4, {"L1": "done", "L2": "done", "L2.5": "done", "L6": "APPROVED"}),
    ("FAST", False, 2, {"L1": "done", "L2": "done", "L2.5": "done", "L5": "green",
                        "L6": "APPROVED"}),
    ("STANDARD", False, 1, {"L1": "done", "L2": "done", "L2.5": "done", "L5": "green",
                            "L5.5": "SAFE", "L5.6": "RED", "L6": "REVISE"}),
    ("STANDARD", True, 0, {"L1": "done", "L2": "done", "L2.5": "done", "L6": "APPROVED"}),
    ("FAST", False, 0, {"L1": "done", "L2": "done", "L2.5": "done", "L6": ""}),
]


@pytest.mark.parametrize("tier,no_fix,fixes,layers", _MATRIX)
def test_guard_agrees_with_pipeline_state_across_a_matrix(tmp_path, tier, no_fix, fixes, layers):
    """The drift pin. If the two required-layer tables diverge, this fails."""
    state = {
        "run_id": "t",
        "tier": tier,
        "mode": "DIFF",
        "no_fix": no_fix,
        "started_utc": "2026-01-01T00:00:00Z",
        "fixes_applied": fixes,
        "layers": {k: {"status": "done", "detail": v} for k, v in layers.items()},
    }
    from_guard = guard._load_core().commit_reasons(state, None)
    from_module = ps.verify_commit_allowed(ps.PipelineState.from_json(state)).reasons
    assert from_guard == from_module


def _lonely_guard(tmp_path_factory, name):
    import shutil
    hooks = tmp_path_factory.mktemp("hooks")
    shutil.copy(_GUARD_PATH, hooks / "cca_pipeline_guard.py")
    spec = importlib.util.spec_from_file_location(name, hooks / "cca_pipeline_guard.py")
    lonely = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lonely)
    return lonely


def test_guard_refuses_when_the_core_is_missing_and_a_run_is_open(
    monkeypatch, git_repo, tmp_path_factory
):
    """A guard copied without its core must not fail open.

    Invariant: with a run open and cca_gate_core.py absent, main() exits 2.
    Mutation: in main(), the `except (ImportError, OSError, SyntaxError)` branch returns 0
    instead of refusing.

    Red 2026-10-10: that mutation of main() in cca_pipeline_guard.py; failed at
        assert lonely.main() == 2   (assert 0 == 2)
    """
    lonely = _lonely_guard(tmp_path_factory, "lonely_guard")
    ps.start("FAST", root=git_repo)
    monkeypatch.chdir(git_repo)
    monkeypatch.setattr("sys.stdin", io.StringIO("git commit -m x"))
    assert lonely.main() == 2


def test_guard_refuses_when_the_core_raises_at_import_and_a_run_is_open(
    git_repo, tmp_path_factory
):
    """A core that raises at import (not only ImportError) must not fail open.

    Invariant: with a run open and a core whose import raises RuntimeError, the guard
    process exits 2, never 1 (an uncaught exception exits 1, which Claude Code treats as
    non blocking). Run as a subprocess so an exception escaping main() shows up as the
    exit code it really produces.
    Mutation: `_load_core()` guarded by `except (ImportError, OSError, SyntaxError)`
    instead of `except Exception` (the a8d7445 text).

    Red 2026-10-10: baseline a8d7445; failed at `assert proc.returncode == 2` (1 == 2).
    """
    import shutil
    import subprocess
    import sys

    hooks = tmp_path_factory.mktemp("hooks_boom")
    shutil.copy(_GUARD_PATH, hooks / "cca_pipeline_guard.py")
    core_path = hooks / "cca_gate_core.py"
    core_path.write_text('raise RuntimeError("boom")' + chr(10), encoding="utf-8")
    ps.start("FAST", root=git_repo)
    proc = subprocess.run(
        [sys.executable, str(hooks / "cca_pipeline_guard.py")],
        input="git commit -m x", cwd=git_repo, capture_output=True, text=True,
    )
    assert proc.returncode == 2, proc.stderr
    assert "could not be loaded" in proc.stderr


def test_guard_without_its_core_is_silent_when_no_run_is_open(
    monkeypatch, git_repo, tmp_path_factory
):
    """No run, no core: nothing to guard, so the commit proceeds (exit 0).

    Mutation: in main(), `if root is None: return 0` becomes `root = Path(os.getcwd())`, so
    the core is loaded (and refused) even with no run open.

    Red 2026-10-10: that mutation of main() in cca_pipeline_guard.py; failed at
        assert lonely.main() == 0   (assert 2 == 0)
    """
    lonely = _lonely_guard(tmp_path_factory, "lonely_guard2")
    monkeypatch.chdir(git_repo)
    monkeypatch.setattr("sys.stdin", io.StringIO("git commit -m x"))
    assert lonely.main() == 0


def test_guard_refuses_a_malformed_legacy_state_instead_of_crashing(monkeypatch, git_repo):
    """An exception after a run is found exits 1, which Claude Code treats as non blocking.

    Invariant: any failure to evaluate an open run is a refusal (exit 2).
    Mutation: remove main()'s `except Exception` around the evaluation.

    Red 2026-10-10: baseline b106c3a; failed at
        assert _run(monkeypatch, git_repo, "git commit -m x") == 2   (AttributeError raised)
    """
    path = ps.state_path(git_repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"tier": "FAST", "layers": {"L1": "done"}, "fixes_applied": 0}),
        encoding="utf-8",
    )
    assert _run(monkeypatch, git_repo, "git commit -m x") == 2


def test_guard_refuses_when_the_rules_raise(monkeypatch, git_repo):
    """Same invariant, with the exception coming out of commit_reasons.

    Red 2026-10-10: baseline b106c3a; failed at
        assert _run(monkeypatch, git_repo, "git commit -m x") == 2   (RuntimeError raised)
    """
    ps.start("FAST", root=git_repo)
    core = guard._load_core()

    def boom(state, tree):
        raise RuntimeError("boom")

    monkeypatch.setattr(core, "commit_reasons", boom)
    monkeypatch.setattr(guard, "_load_core", lambda: core)
    assert _run(monkeypatch, git_repo, "git commit -m x") == 2


def test_guard_refuses_a_commit_while_the_run_is_paused(monkeypatch, capsys, git_repo):
    """A complete run that is paused may not commit, and the refusal names the pause.

    Red 2026-10-11: mutation `[PAUSED_REASON] if state.get("paused") else []` became `[]` in
    cca_gate_core.commit_reasons; failed at
    `assert _run(monkeypatch, git_repo, "git commit -m x") == 2` (0 == 2).
    """
    ps.start("FAST", root=git_repo)
    ps.record("L1", output=write_output(git_repo), root=git_repo)
    ps.record("L2", counts={"unique": 0}, output=findings_output(git_repo), root=git_repo)
    ps.record("L2.5", counts={"confirmed": 0, "fp": 0, "uncertain": 0, "duplicate": 0},
              output=verdicts_output(git_repo), root=git_repo)
    ps.record("L3", counts={"to_fix": 0}, output=plan_output(git_repo), root=git_repo)
    ps.record("L6", detail="APPROVED", root=git_repo)
    assert _run(monkeypatch, git_repo, "git commit -m x") == 0
    ps.pause("end of day", root=git_repo)
    capsys.readouterr()
    assert _run(monkeypatch, git_repo, "git commit -m x") == 2
    assert "the run is paused" in capsys.readouterr().err


def _session_and_child(tmp_path: pathlib.Path, name: str = "child") -> tuple:
    """A session checkout with a second checkout below it, and a run open in the second."""
    parent = _repo(tmp_path)
    child = _repo(parent / name)
    ps.start("DEEP", root=child)
    return parent, child


def test_cd_into_a_checkout_with_an_open_run_then_commit_is_blocked(monkeypatch, tmp_path):
    """The live test's defect: `cd <worktree> && git commit` from the session directory.

    Red 2026-10-11: baseline 0ec8c47 (the guard looked only from its own cwd); failed at
    `assert _run(monkeypatch, parent, "cd child && git commit -m x") == 2` (0 == 2).
    """
    parent, _ = _session_and_child(tmp_path)
    assert _run(monkeypatch, parent, "cd child && git commit -m x") == 2
    assert _run(monkeypatch, parent, "cd child; git commit -m x") == 2
    assert _run(monkeypatch, parent, "cd child || exit 1; git commit -m x") == 2


def test_git_dash_c_into_a_checkout_with_an_open_run_is_blocked(monkeypatch, tmp_path):
    """Red 2026-10-11: baseline 0ec8c47; failed at
    `assert _run(monkeypatch, parent, "git -C child commit -m x") == 2` (0 == 2).
    """
    parent, _ = _session_and_child(tmp_path)
    assert _run(monkeypatch, parent, "git -C child commit -m x") == 2


def test_quoted_directories_with_spaces_are_followed(monkeypatch, tmp_path):
    """Red 2026-10-11: baseline 0ec8c47; failed at
    `assert _run(monkeypatch, parent, 'cd "child dir" && git commit -m x') == 2` (0 == 2).
    """
    parent, _ = _session_and_child(tmp_path, "child dir")
    assert _run(monkeypatch, parent, 'cd "child dir" && git commit -m x') == 2
    assert _run(monkeypatch, parent, "git -C 'child dir' commit -m x") == 2


def test_the_payload_cwd_is_the_base_directory(monkeypatch, tmp_path):
    """Claude Code sends the session's directory as `cwd`; the hook process may run elsewhere.

    Red 2026-10-11: baseline 0ec8c47 (payload `cwd` ignored); failed at
    `assert guard.main() == 2` (0 == 2).
    """
    _, child = _session_and_child(tmp_path / "work")
    elsewhere = _repo(tmp_path / "elsewhere")
    payload = json.dumps({"tool_name": "Bash", "cwd": str(child),
                          "tool_input": {"command": "git commit -m x"}})
    monkeypatch.chdir(elsewhere)
    monkeypatch.setattr("sys.stdin", io.StringIO(payload))
    assert guard.main() == 2


def test_a_heredoc_commit_message_does_not_hide_the_cd(monkeypatch, tmp_path):
    """A heredoc body is not shell syntax: an apostrophe in it must not lose the `cd`.

    Red 2026-10-11: mutation the longest parsable prefix loop in `commit_directories`
    replaced by one attempt on the whole command (a parse failure keeps the base
    directory); failed at `assert _run(monkeypatch, parent, command) == 2` (0 == 2). Also red
    against baseline 0ec8c47 at the same assert.
    """
    parent, _ = _session_and_child(tmp_path)
    command = "cd child && git commit -F - <<'EOF'\nit's the fix\nEOF"
    assert _run(monkeypatch, parent, command) == 2


def test_a_commit_in_the_session_checkout_ignores_a_run_below_it(monkeypatch, tmp_path):
    """The other direction: a run in a checkout below does not block the session's commit.

    Red 2026-10-11: mutation `commit_directories` also returns `base / "child"`; failed at
    `assert _run(monkeypatch, parent, "git commit -m x") == 0` (2 == 0).
    """
    parent, _ = _session_and_child(tmp_path)
    assert _run(monkeypatch, parent, "git commit -m x") == 0
    assert _run(monkeypatch, parent, "cd child | cat; git commit -m x") == 0


# --- the command read the way its shell runs it (SEC-001, SEC-002, STAKES-003, BUG-002) ---


def _run_ps(monkeypatch, cwd: pathlib.Path, command: str) -> int:
    payload = json.dumps({"tool_name": "PowerShell", "tool_input": {"command": command}})
    monkeypatch.chdir(cwd)
    monkeypatch.setattr("sys.stdin", io.StringIO(payload))
    return guard.main()


@pytest.mark.parametrize(
    "command",
    [
        "git \\\ncommit -m x",
        "Git commit -m x",
        "git co''mmit -m x",
        'g"i"t commit -m x',
        "git -c 'core.x=a;b' commit -m x",
        "git -c 'core.x=a|b' commit -m x",
        "X=commit; git $X -m x",
    ],
)
def test_every_spelling_bash_runs_as_a_commit_is_refused(monkeypatch, tmp_path, command):
    """SEC-001. Invariant: with a run open, a command bash runs as `git commit` exits 2.

    Failure mode: the `_COMMIT_RE` prefilter missed the spelling, so `main` exited 0
    before it read any state: a continuation, a case-folding file system, quote removal,
    a quoted separator inside a `-c` value, a commit named by a variable.

    Red 2026-11-20: baseline 1db5bbe (the regex prefilter); every case failed at
    `assert _run(monkeypatch, repo, command) == 2` (0 == 2).
    """
    repo = _repo(tmp_path)
    ps.start("DEEP", root=repo)
    assert _run(monkeypatch, repo, command) == 2, command


@pytest.mark.parametrize(
    "command",
    [
        "cd -P child && git commit -m x",
        "cd -- child && git commit -m x",
        "pushd child && git commit -m x",
        "env -C child git commit -m x",
        "bash -c 'cd child && git commit -m x'",
        'WT=child; git -C "$WT" commit -m x',
        'git -C "$CCA_GUARD_TEST_WT" commit -m x',
    ],
)
def test_every_directory_move_bash_makes_is_followed(monkeypatch, tmp_path, command):
    """SEC-002. Invariant: a commit is judged in the directory bash runs it in.

    Failure mode: only `cd <one word>` and a literal `git -C` were followed, so these
    commits were judged from the session directory and a run open in `child` was missed.

    Red 2026-11-20: baseline 1db5bbe; every case failed at
    `assert _run(monkeypatch, parent, command) == 2` (0 == 2).
    """
    parent, child = _session_and_child(tmp_path)
    monkeypatch.setenv("CCA_GUARD_TEST_WT", str(child))
    assert _run(monkeypatch, parent, command) == 2, command


@pytest.mark.parametrize(
    "command",
    [
        "cd child && git commit -m x",
        "cd child; git commit -m x",
        "pytest -q && cd child && git commit -m x",
        "git -C child commit -m x",
        "cd -P child && git commit -m x",
        "pushd child && git commit -m x",
        "env -C child git commit -m x",
        "bash -c 'cd child && git commit -m x'",
    ],
)
def test_a_run_in_the_enclosing_checkout_does_not_judge_a_nested_commit(
    monkeypatch, tmp_path, command
):
    """The other direction: a commit in a nested checkout is out of scope for a run above it.

    Red 2026-11-20: baseline 1db5bbe failed for the `cd -P`, `pushd`, `env -C` and
    `bash -c` cases at `assert _run(monkeypatch, parent, command) == 0` (2 == 0): the
    move was not followed, so the commit was judged against the enclosing run. The first
    four cases passed there. Their red is a mutation, failing at the same assert
    (2 == 0): `_Walk._cd` returning the state unchanged (the three `cd` cases), and
    `_git_commit` ignoring `-C` (the `git -C` case).
    """
    parent = _repo(tmp_path)
    _repo(parent / "child")
    ps.start("DEEP", root=parent)
    assert _run(monkeypatch, parent, command) == 0, command


def test_a_mid_word_hash_is_not_a_comment(monkeypatch, tmp_path):
    """Pre-existing at 1db5bbe: shlex read the `#` of `a#b` as a comment, hiding the commit.

    Red 2026-11-20: baseline 1db5bbe failed at the assert below (0 == 2).
    """
    parent, _ = _session_and_child(tmp_path)
    assert _run(monkeypatch, parent, "cd child && echo a#b; git commit -m x") == 2


@pytest.mark.parametrize(
    "command",
    [
        # An escaped backslash before a newline is a backslash, then a real newline.
        "echo x\\\\\ncd child && git commit -m x",
        # A comment ends at the newline even when it ends in a backslash.
        "# note \\\ncd child && git commit -m x",
    ],
)
def test_a_backslash_newline_is_a_continuation_only_where_bash_reads_one(
    monkeypatch, tmp_path, command
):
    """The two bypasses run 20261120T201246Z's repair cycles opened (FIX_JOURNAL, SEC-001).

    Both pass at 1db5bbe. Red 2026-11-20: mutation `_Walk.script` deleting every
    backslash-newline pair from the text before parsing it (what cycles 1 and 3 did);
    both failed at the assert below (0 == 2).
    """
    parent, _ = _session_and_child(tmp_path)
    assert _run(monkeypatch, parent, command) == 2, command


def test_every_commit_in_a_command_is_judged(monkeypatch, tmp_path):
    """Cycle 3's parked case: a commit in the run's checkout, then one elsewhere.

    Passes at 1db5bbe. Red 2026-11-20: mutation `commit_directories` returning only the
    last directory (`[-1:]`); failed at the assert below (0 == 2).
    """
    parent = _repo(tmp_path)
    _repo(parent / "child")
    ps.start("DEEP", root=parent)
    command = "echo a#b;\ngit commit -m x\ngit \\\n-C child commit -m y"
    assert _run(monkeypatch, parent, command) == 2


@pytest.mark.parametrize(
    "command",
    [
        "cat <<'EOF'\nit's fine, we ship it later\nEOF",
        "echo it's && git status && echo committed",
        "Git status",
        "grep -rn commit docs",
        "git status && echo committed",
        '[ "$CI" = true ] && echo y',
    ],
)
def test_commands_that_do_not_commit_are_not_refused(monkeypatch, tmp_path, command):
    """Invariant: with a run open, a command bash would not run as a commit exits 0.

    A command that names both `git` and `commit` as words is judged even when the walk
    finds no commit in it (`_names_a_commit`, FIX-SEC-002), so these controls name at
    most one of them; `git log --grep commit` is refused, as it was at 1db5bbe.

    These pass at 1db5bbe. Red 2026-11-20, each a mutation failing at the assert below
    (2 == 0): `_names_a_commit` returning True (every case); `_git_commit`
    accepting any subcommand (`Git status`, `git status`); `_git_commit` taking every
    word as a candidate program (`grep`). The `[` case, red 2026-11-21: the run's first
    repair (`_GLOB` matching a lone `[`, so the test builtin became an unknown program
    with an unknown argument), at the same assert. FIX-SEC-005 is parked since, and the
    case stays as a control.
    """
    repo = _repo(tmp_path)
    ps.start("DEEP", root=repo)
    assert _run(monkeypatch, repo, command) == 0, command


def test_a_run_in_a_subdirectory_of_the_repository_is_judged(monkeypatch, tmp_path):
    """STAKES-003. A commit ships the whole index, so a run opened below the top counts.

    Red 2026-11-20: baseline 1db5bbe (`find_root` only walks up); failed at the first
    assert (0 == 2).
    """
    repo = _repo(tmp_path)
    sub = repo / "sub"
    sub.mkdir()
    ps.start("DEEP", root=sub)
    assert _run(monkeypatch, repo, "git commit -m x") == 2
    assert _run(monkeypatch, sub, "git -C .. commit -m x") == 2


def test_a_run_in_a_nested_checkout_stays_out_of_scope(monkeypatch, tmp_path):
    """The registry lookup counts a run only when its nearest checkout is the commit's.

    Passes at 1db5bbe. Red 2026-11-20: mutation `_commit_roots` dropping the
    `_same_path(owner, top)` condition; failed at the assert below (2 == 0).
    """
    repo = _repo(tmp_path)
    nested = _repo(repo / "nested")
    ps.start("DEEP", root=nested)
    assert _run(monkeypatch, repo, "git commit -m x") == 0


def test_a_directory_the_command_hides_is_judged_against_every_open_run(
    monkeypatch, capsys, tmp_path
):
    """A `cd` to a value the guard cannot see must not escape to a directory without a run.

    Red 2026-11-20: baseline 1db5bbe (`$X` read literally, so the walk stayed in the
    session checkout); failed at `assert ... == 2` (0 == 2). Also red 2026-11-21 against
    the run's first repair (None judged only inside the session checkout, journal attempt
    1 of FIX-SEC-002), at the same assert.
    """
    session = _repo(tmp_path / "session")
    elsewhere = _repo(tmp_path / "elsewhere")
    ps.start("DEEP", root=elsewhere)
    monkeypatch.delenv("CCA_GUARD_TEST_UNSET", raising=False)
    assert _run(monkeypatch, session, 'cd "$CCA_GUARD_TEST_UNSET" && git commit -m x') == 2
    assert "could not tell from the command" in capsys.readouterr().err


def test_text_that_only_names_a_commit_is_judged_inside_this_checkout(monkeypatch, tmp_path):
    """FIX-SEC-002's net judges MAYBE against the runs in the session's checkout only.

    A command that names git and commit but that the walk finds no commit in (`trap`) is
    judged against the run of the session's checkout and runs registered inside it (a
    worktree below), not against every run on the machine, which would refuse most such
    commands on a machine with many open runs.

    Red 2026-11-21: mutation `_commit_roots` judging MAYBE like None (every registered
    run); failed at the last assert (2 == 0).
    """
    session = _repo(tmp_path / "session")
    worktree = _repo(session / ".claude" / "worktrees" / "wt")
    ps.start("DEEP", root=worktree)
    command = "trap 'git commit -m x' EXIT"
    assert _run(monkeypatch, session, command) == 2
    assert _run(monkeypatch, _repo(tmp_path / "elsewhere"), command) == 0


@pytest.mark.parametrize(
    "command",
    [
        "Set-Location child; git commit -m x",
        "Push-Location -Path child; git commit -m x",
        "$wt = 'child'; git -C $wt commit -m x",
        "if ($true) { cd child }; git commit -m x",
    ],
)
def test_a_powershell_commit_is_judged_where_powershell_runs_it(monkeypatch, tmp_path, command):
    """BUG-002's other half: a PowerShell payload is read as PowerShell, not as bash.

    Red 2026-11-20: baseline 1db5bbe failed for every case at the assert below (0 == 2);
    it read `Set-Location`, `Push-Location`, `$wt` and the script block as bash.
    """
    parent, _ = _session_and_child(tmp_path)
    assert _run_ps(monkeypatch, parent, command) == 2, command


def test_powershell_spellings_of_a_commit_are_refused(monkeypatch, tmp_path):
    """Red 2026-11-20: baseline 1db5bbe failed at the first assert (0 == 2)."""
    repo = _repo(tmp_path)
    ps.start("DEEP", root=repo)
    assert _run_ps(monkeypatch, repo, "g`it commit -m x") == 2
    assert _run_ps(monkeypatch, repo, "& 'git' commit -m x") == 2
    assert _run_ps(monkeypatch, repo, "GIT COMMIT -m x") == 2


@pytest.mark.parametrize("command", ["git status", "Get-ChildItem | Select-String commit"])
def test_powershell_commands_that_do_not_commit_are_not_refused(monkeypatch, tmp_path, command):
    """Pass at 1db5bbe. Red 2026-11-20, each failing at the assert below (2 == 0):
    mutation `_git_commit` accepting any subcommand (`git status`); mutation
    `_git_commit` taking every word as a candidate program (`Select-String commit`).
    """
    repo = _repo(tmp_path)
    ps.start("DEEP", root=repo)
    assert _run_ps(monkeypatch, repo, command) == 0, command


def test_a_powershell_commit_in_a_nested_checkout_is_out_of_scope(monkeypatch, tmp_path):
    """Red 2026-11-20: baseline 1db5bbe (Set-Location not followed, so the commit was
    judged against the enclosing run); failed at the assert below (2 == 0).
    """
    parent = _repo(tmp_path)
    _repo(parent / "child")
    ps.start("DEEP", root=parent)
    assert _run_ps(monkeypatch, parent, "Set-Location child; git commit -m 'it''s'") == 0


# --- audit fixes of run 20261120T223924Z: what the walk did not model ---------------------
#
# Red for every test below is the pre-fix guard of that run (the Step 4 snapshot, with the
# walk of this branch before its audit fixes), each failing at its own assert; the node ids
# are recorded with the run's Step 5.6 proof.


def _in_run(tmp_path: pathlib.Path) -> pathlib.Path:
    repo = _repo(tmp_path)
    ps.start("DEEP", root=repo)
    return repo


def _enclosing(tmp_path: pathlib.Path) -> pathlib.Path:
    """A run open in the session checkout, with a second checkout `child` nested in it."""
    parent = _repo(tmp_path)
    _repo(parent / "child")
    ps.start("DEEP", root=parent)
    return parent


@pytest.mark.parametrize("tool,command", [
    ("Bash", "eval \"$(printf 'git commit -m x')\""),
    ("Bash", "$(printf git) $(printf commit) -m x"),
    ("PowerShell", "iex ('git co' + 'mmit -m x')"),
    ("PowerShell", "'git commit -m x' | iex"),
])
def test_text_the_guard_cannot_read_is_judged_when_it_runs(monkeypatch, tmp_path, tool, command):
    """SEC-001. Invariant: eval, iex or a command whose program and subcommand are both
    unknown is judged as a commit in an unknown directory. Failure mode: they counted as no
    commit, so main judged no run and exited 0.
    """
    repo = _in_run(tmp_path)
    run = _run_ps if tool == "PowerShell" else _run
    assert run(monkeypatch, repo, command) == 2, command


@pytest.mark.parametrize("tool,command", [
    ("Bash", "echo 'git commit -m x' | bash"),
    ("Bash", "bash <<'X'\ngit commit -m x\nX"),
    ("Bash", "trap 'git commit -m x' EXIT"),
    ("Bash", 'cmd //c "git commit -m x"'),
    ("Bash", "python -c \"import os; os.system('git commit -m x')\""),
    ("PowerShell", "Start-Process git -ArgumentList 'commit -m x' -Wait"),
    ("PowerShell", "Set-Alias g git; g commit -m x"),
    ("Bash", r"cmd //c 'C:\Git\cmd\git.exe commit -m x'"),
    ("PowerShell", r'cmd /c "C:\Git\cmd\git.exe commit -m x"'),
    ("PowerShell", r"Start-Process -FilePath C:\Git\cmd\git.exe -ArgumentList 'commit -m x'"),
])
def test_a_commit_run_by_something_the_walk_does_not_model_is_judged(
    monkeypatch, tmp_path, tool, command
):
    """SEC-002. Invariant: a command naming `git` and `commit` as words is judged when the
    walk finds no commit in it. Failure mode: removing the regex prefilter and its `[base]`
    fallback let every executor the walk does not model (stdin shells, trap, cmd /c,
    python -c, Start-Process, aliases) run a commit unjudged. The Windows path cases: the
    net deleted every backslash, gluing `C:\\Git\\cmd\\git.exe` into one word with no `git`
    in it.

    Red 2026-11-21 (the Windows path cases): `guard-postRC11.py`; each failed at the assert
    below (0 == 2).
    """
    repo = _in_run(tmp_path)
    run = _run_ps if tool == "PowerShell" else _run
    assert run(monkeypatch, repo, command) == 2, command


@pytest.mark.parametrize("command", [
    "git -c alias.ci=commit ci -m x",
    "git -c help.autocorrect=immediate comit -m x",
    "git config alias.ci commit; git ci -m x",
])
def test_git_aliases_and_autocorrect_are_judged(monkeypatch, tmp_path, command):
    """SEC-003. Invariant: a git told to resolve its subcommand through an alias or
    autocorrect is judged as a possible commit. Failure mode: only a literal `commit`
    subcommand counted.
    """
    assert _run(monkeypatch, _in_run(tmp_path), command) == 2, command


@pytest.mark.parametrize("command", [
    "true() { git commit -m x; }; true",
    "exit() { :; }; exit; git commit -m x",
    "git() { :; }; command git commit -m x",
])
def test_functions_are_looked_up_as_bash_does(monkeypatch, tmp_path, command):
    """SEC-004. Invariant: a function shadows a builtin of its name, and `command`/`builtin`
    skip functions. Failure mode: builtins were matched first, and `command git` still ran
    a `git` function, so the commit was never walked.
    """
    assert _run(monkeypatch, _in_run(tmp_path), command) == 2, command


def test_a_model_fault_keeps_the_commits_already_found(monkeypatch, tmp_path):
    """SEC-006. Invariant: when the walk hits a bound, the commits it found are still judged.
    Failure mode: main dropped them and fell back to a regex that `git $X` defeats.
    """
    helpers = "f0(){ :; }; " + " ".join(f"f{n}(){{ f{n - 1}; f{n - 1}; }};" for n in range(1, 18))
    command = "X=commit; git $X -m x; " + helpers + " f17"
    assert _run(monkeypatch, _in_run(tmp_path), command) == 2


def test_a_lone_carriage_return_ends_a_powershell_statement(monkeypatch, tmp_path):
    """SEC-012. Invariant: PowerShell ends a statement at a lone CR. Failure mode: the
    reader took CR as a blank, so the commit became arguments of Set-Location.
    """
    parent, _ = _session_and_child(tmp_path)
    assert _run_ps(monkeypatch, parent, "Set-Location child\rgit commit -m x") == 2


@pytest.mark.parametrize("command", ["$out = git commit -m x", "$null = git commit -m x 2>&1"])
def test_a_powershell_assignment_runs_its_right_side(monkeypatch, tmp_path, command):
    """STAKES-002. Invariant: `$x = <command>` runs the command. Failure mode: a right side
    of more than one word was read as a plain value and never walked.
    """
    assert _run_ps(monkeypatch, _in_run(tmp_path), command) == 2, command


@pytest.mark.parametrize("tool,command", [
    ("Bash", "X=commit; (X=status); git $X -m x"),
    ("Bash", "X=commit; echo $(X=status); git $X -m x"),
    ("Bash", "X=commit; X=status | cat; git $X -m x"),
    ("Bash", "X=commit; f() { local X=status; }; f; git $X -m x"),
    ("Bash", "(git() { :; }); git commit -m x"),
    ("Bash", "X=commit; test -d nowhere && X=status; git $X -m x"),
    ("PowerShell", "$x = 'commit'; if (Test-Path nowhere) { $x = 'status' }; git $x -m y"),
])
def test_assignments_and_functions_keep_their_scope(monkeypatch, tmp_path, tool, command):
    """BUG-001. Invariant: what a subshell, a substitution, a pipeline element or a `local`
    assigns does not reach the parent, and an assignment that may not run makes a differing
    value unknown. Failure mode: one global dict let a discarded `X=status` (or function)
    replace the value bash uses, hiding the commit.
    """
    run = _run_ps if tool == "PowerShell" else _run
    assert run(monkeypatch, _in_run(tmp_path), command) == 2, command


def test_a_conditional_assignment_does_not_pick_one_directory(monkeypatch, tmp_path):
    """BUG-001, the directory half: `W` is `child` or `.`, so the commit's directory is unknown
    and the run registered in the nested checkout is judged.
    """
    parent, _ = _session_and_child(tmp_path)
    command = "W=child; [ -f /nope ] && W=.; cd $W && git commit -m x"
    assert _run(monkeypatch, parent, command) == 2


def test_nested_arithmetic_is_parsed_in_linear_time(monkeypatch, tmp_path):
    """PERF-001. Invariant: the guard decides within its hook timeout, which fails open.
    Failure mode: each `$((` that is really `$( (` was re-parsed at every level, so 18
    levels took about 13 s and 21 levels passed 60 s.
    """
    import time
    command = "git commit -m x\necho " + "$((echo " * 18 + "x" + ") )" * 18
    started = time.perf_counter()
    assert _run(monkeypatch, _in_run(tmp_path), command) == 2
    assert time.perf_counter() - started < 5


def test_a_long_argument_list_is_walked_in_linear_time(monkeypatch, tmp_path):
    """PERF-002. Invariant: as above. Failure mode: each field re-scanned every field
    before it for `env`, so 5,000 paths took about 20 s and 10,000 passed 60 s.
    """
    import time
    command = "git commit -m x -- " + "a " * 5000
    started = time.perf_counter()
    assert _run(monkeypatch, _in_run(tmp_path), command) == 2
    assert time.perf_counter() - started < 5


def test_a_long_quoted_message_is_read_in_linear_time(tmp_path):
    """PERF-003. Failure mode: each character re-joined the literal so far, so a 400 KB
    message took about 7 s and 1 MB passed 60 s.
    """
    import time
    started = time.perf_counter()
    assert guard.commit_directories('git commit -m "' + "x" * 400_000 + '"', tmp_path) \
        == [tmp_path]
    assert time.perf_counter() - started < 3


def test_the_loose_net_is_linear_in_the_command(tmp_path):
    """PERF-004. Failure mode: `git[\\s\\S]*commit` backtracked once per `git`, so an
    unparseable command with 10,000 `git status` lines and no commit took about 18 s.
    """
    import time
    started = time.perf_counter()
    assert guard.commit_directories("echo 'x\n" + "git status\n" * 10_000, tmp_path) == []
    assert time.perf_counter() - started < 3


def test_a_failed_line_does_not_pin_a_later_commit_to_the_start(monkeypatch, tmp_path):
    """BUG-005. Failure mode: after a syntax error the loose match placed the commit in
    the starting directory, though the completed lines had moved into `child`.
    """
    parent, _ = _session_and_child(tmp_path)
    command = "cd child\ncat <<EOF\n$(\nEOF\ngit commit -m y"
    assert _run(monkeypatch, parent, command) == 2


def test_variables_resolve_only_from_the_command(monkeypatch, tmp_path):
    """BUG-011 and ENV-002. Invariant: a bash variable the command assigns is followed exactly;
    one it does not assign is unknown, never read from the hook's own environment, which is
    not the shell's. A PowerShell variable is never known (FIX-SEC-007, root cause): the
    walk does not model PowerShell's operators, casts or variable cmdlets. Failure modes:
    the earlier tests passed when nothing resolved (an unknown directory is judged against
    the child's run too), and a hook-only variable named a known, wrong directory.
    """
    parent, child = _session_and_child(tmp_path)
    assert guard.commit_directories('WT=child; git -C "$WT" commit -m x', parent) == \
        [parent / "child"]
    assert guard.commit_directories("$wt = 'child'; git -C $wt commit -m x", parent, "ps") == \
        [None]
    monkeypatch.setenv("CCA_GUARD_TEST_HOOK_ONLY", str(parent))
    assert guard.commit_directories('git -C "$CCA_GUARD_TEST_HOOK_ONLY" commit -m x', child) \
        == [None]
    assert _run(monkeypatch, child, 'cd "$CCA_GUARD_TEST_HOOK_ONLY"; git commit -m x') == 2


@pytest.mark.parametrize("command", [
    "IFS=,; X=git,commit; $X -m x",
    "X=c; X+=ommit; git $X -m x",
    "X=co; export X+=mmit; git $X -m x",
    "X=status; printf -v X commit; git $X -m x",
])
def test_expansions_that_rewrite_a_value_are_followed(monkeypatch, tmp_path, command):
    """SEC-007. Failure mode: field splitting ignored IFS, `+=` replaced instead of
    appending, and `printf -v` left the old value, each a known wrong word for `commit`.
    """
    assert _run(monkeypatch, _in_run(tmp_path), command) == 2, command


def test_a_for_loop_without_in_runs_its_body(monkeypatch, tmp_path):
    """SEC-008. Failure mode: `for a; do` (over "$@") was walked zero times."""
    assert _run(monkeypatch, _in_run(tmp_path), "set -- 1; for a; do git commit -m x; done") == 2


def test_lastpipe_keeps_the_last_element_in_this_shell(monkeypatch, tmp_path):
    """SEC-009. Failure mode: with `shopt -s lastpipe` a final `cd` persists, but the walk
    treated it as a subshell and judged the old directory.
    """
    parent, _ = _session_and_child(tmp_path)
    assert _run(monkeypatch, parent, "shopt -s lastpipe; echo | cd child; git commit -m x") == 2


@pytest.mark.parametrize("command", [
    "env --ch child git commit -m x",
    "env -vC child git commit -m x",
])
def test_env_options_are_parsed_as_getopt_does(monkeypatch, tmp_path, command):
    """SEC-010. Failure mode: an abbreviated `--chdir` and a clustered `-vC` were missed."""
    parent, _ = _session_and_child(tmp_path)
    assert _run(monkeypatch, parent, command) == 2, command


def test_cd_follows_cdpath(monkeypatch, tmp_path):
    """SEC-011. Failure mode: `cd wt` was joined to the cwd literally, ignoring CDPATH."""
    session = _repo(tmp_path / "base")
    worktree = _repo(tmp_path / "cdp" / "wt")
    ps.start("DEEP", root=worktree)
    assert _run(monkeypatch, session, "CDPATH=../cdp; cd wt && git commit -m x") == 2


def test_a_powershell_commit_is_judged_lone_or_chained_by_powershell_rules(
    monkeypatch, git_repo
):
    """SEC-013. Invariant: once the gates pass, a lone PowerShell commit is allowed and one
    chained with another statement is refused. Failure modes: the bash scanner refused a
    lone commit holding a backtick escape, and passed `y\\; git commit`, which PowerShell
    runs as two statements.
    """
    ps.start("FAST", root=git_repo)
    ps.record("L1", output=write_output(git_repo), root=git_repo)
    ps.record("L2", counts={"unique": 0}, output=findings_output(git_repo), root=git_repo)
    ps.record("L2.5", counts={"confirmed": 0, "fp": 0, "uncertain": 0, "duplicate": 0},
              output=verdicts_output(git_repo), root=git_repo)
    ps.record("L3", counts={"to_fix": 0}, output=plan_output(git_repo), root=git_repo)
    ps.record("L6", detail="APPROVED", root=git_repo)
    assert _run_ps(monkeypatch, git_repo, 'git commit -m "it`"s"') == 0
    assert _run_ps(monkeypatch, git_repo, "Set-Content f y\\; git commit -m x") == 2
    assert _run_ps(monkeypatch, git_repo, "Set-Content f y\rgit commit -m x") == 2


@pytest.mark.parametrize("command", [
    "$a = 'commit','-m','x'; git @a",
    "$ExecutionContext.SessionState.Path.SetLocation('child'); git commit -m x",
])
def test_powershell_splatting_and_location_methods_are_judged(monkeypatch, tmp_path, command):
    """SEC-014. Failure mode: `@a` was read as a literal word, and a method that moves the
    location was assumed to keep it.
    """
    parent, _ = _session_and_child(tmp_path)
    if "@a" in command:
        parent = _in_run(tmp_path / "solo")
    assert _run_ps(monkeypatch, parent, command) == 2, command


@pytest.mark.parametrize("command", [
    "while true; do for x in a; do break 2; done; done; git commit -m x",
    "for y in a; do for x in b; do continue 2; done; cd child; done; git commit -m x",
])
def test_break_and_continue_honour_their_count(monkeypatch, tmp_path, command):
    """STAKES-006. Failure mode: `break 2` and `continue 2` left one loop only, losing the
    commit after an infinite outer loop, or keeping a `cd` that never runs.
    """
    assert _run(monkeypatch, _enclosing(tmp_path), command) == 2, command


@pytest.mark.parametrize("command", [
    "Set-Location -ErrorAction Stop child; git commit -m x",
    "'child' | Set-Location; git commit -m x",
])
def test_set_location_parameters_and_pipeline_input(monkeypatch, tmp_path, command):
    """STAKES-007 and STAKES-008. Failure modes: the value of `-ErrorAction` was taken as the
    path, and a path piped into Set-Location was read as no path.
    """
    parent, _ = _session_and_child(tmp_path)
    assert _run_ps(monkeypatch, parent, command) == 2, command


def test_a_child_shell_inherits_oldpwd(monkeypatch, tmp_path):
    """STAKES-009. Failure mode: the child shell's `cd -` went to the parent's cwd, not to
    the exported OLDPWD, so the commit was judged in the nested checkout.
    """
    parent = _enclosing(tmp_path)
    assert _run(monkeypatch, parent, "cd child; bash -c 'cd - && git commit -m x'") == 2


def test_an_unreadable_directory_does_not_crash_the_guard(monkeypatch, tmp_path):
    """STAKES-010. Invariant: an OSError while looking for a run is judged, never raised:
    an exception exits 1, which Claude Code does not treat as a refusal. Failure mode: only
    `resolve()` was guarded, and POSIX Python 3.10 to 3.13 re-raise EACCES from `exists()`.
    """
    repo = _in_run(tmp_path)
    locked = repo / "locked"
    real_exists = pathlib.Path.exists

    def exists(self, *args, **kwargs):
        if str(self).startswith(str(locked)):
            raise PermissionError(13, "Permission denied", str(self))
        return real_exists(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "exists", exists)
    try:
        code = _run(monkeypatch, repo, "git -C locked commit -m a; git commit -m b")
    except PermissionError:
        code = "raised"
    assert code == 2


# --- regressions the L5.5 review of run 20261120T223924Z found in its first repair --------


def test_cdpath_from_the_hook_environment_is_not_used(monkeypatch, tmp_path):
    """FIX-SEC-011, attempt 2. Failure mode: the first repair read CDPATH from the hook's
    environment, which is not the shell's, and judged `cd wt` in the wrong checkout.

    Red 2026-11-21: the first repair (`self.vars.get("CDPATH", os.environ.get("CDPATH"))`);
    failed at the assert below (0 == 2).
    """
    session = _repo(tmp_path / "base")
    worktree = _repo(session / "wt")
    _repo(tmp_path / "other" / "wt")
    ps.start("DEEP", root=worktree)
    monkeypatch.setenv("CDPATH", str(tmp_path / "other"))
    assert _run(monkeypatch, session, "cd wt && git commit -m x") == 2


def test_a_malformed_child_script_keeps_its_directory(monkeypatch, tmp_path):
    """FIX-BUG-005, attempt 2. Failure mode: the first repair recorded only an unknown
    directory for a malformed script that names a commit, dropping where it runs, so a run
    in an unrelated checkout was not judged.

    Red 2026-11-21: the first repair (`self.commits.append(None)` in `_Walk.script`, with
    None judged inside the session checkout); failed at the assert below (0 == 2).
    """
    session = _repo(tmp_path / "session")
    other = _repo(tmp_path / "other")
    ps.start("DEEP", root=other)
    command = f"cd '{other.as_posix()}' && bash -c \"echo 'oops; git commit -m x\""
    assert _run(monkeypatch, session, command) == 2


# --- regressions the second L5.5 review of run 20261120T223924Z found -------------------


@pytest.mark.parametrize("template", [
    "$TIME $NICE git -C '{other}' commit -m x",
    "\"$A\" \"$B\" bash -c \"cd '{other}' && git commit -m x\"",
])
def test_an_unknown_program_does_not_hide_the_commit_after_it(monkeypatch, tmp_path, template):
    """FIX-SEC-001, attempt 2. Failure mode: after recording an unknown program as a
    possible commit, `_programs` returned early, so the known `git -C <other>` commit (or
    the shell) after it was never read, and the run in that checkout was not judged.

    Red 2026-11-21: the second repair (the early `return` in `_programs`); failed at the
    assert below (0 == 2).
    """
    session = _repo(tmp_path / "session")
    other = _repo(tmp_path / "other")
    ps.start("DEEP", root=other)
    command = template.format(other=other.as_posix())
    assert _run(monkeypatch, session, command) == 2, command


@pytest.mark.parametrize("command", [
    "$LASTEXITCODE",
    "$files = Get-ChildItem; $files.Count",
    "@(1,2)",
    "[Console]::WriteLine('hi')",
    "[datetime]::Now",
])
def test_powershell_expressions_are_not_refused(monkeypatch, tmp_path, command):
    """FIX-SEC-001, the PowerShell side. Invariant: a statement that runs no program is not
    judged. Failure mode: an expression statement starting with a variable was taken for
    an unknown program.

    Red 2026-11-21: the second repair; the three variable and array cases failed at the
    assert below (2 == 0). The two `[Type]` cases failed there too, but only because of
    the glob reading of the parked FIX-SEC-005; they stay as controls with no live red.
    """
    assert _run_ps(monkeypatch, _in_run(tmp_path), command) == 0, command


def test_an_empty_cdpath_entry_is_the_current_directory(monkeypatch, tmp_path):
    """FIX-SEC-011, attempt 3. Failure mode: an empty CDPATH entry was skipped, but bash
    reads it as the current directory at its place in the list, so `cd child` was judged
    in `cdp/child` instead of `child`.

    Red 2026-11-21: the second repair (`if not entry: continue` in `_cdpath`); failed at
    the assert below (0 == 2).
    """
    parent = _in_run(tmp_path)
    (parent / "child").mkdir()
    _repo(parent / "cdp" / "child")
    assert _run(monkeypatch, parent, "CDPATH=:cdp; cd child && git commit -m x") == 2


# --- what the L6 architect gate of run 20261120T223924Z sent back -------------------------


def test_a_walk_fault_judges_every_open_run(monkeypatch, tmp_path):
    """FIX-SEC-006, L6 cycle 1. Invariant: when the walk raises, the directory it reached is
    lost, so every open run is judged. Failure mode: the fault was judged as MAYBE, scoped
    to the session's checkout, so a commit after `cd <other>` into a checkout with a run
    was allowed (HEAD refused it).

    Red 2026-11-21: the guard before the L6 revision (`walk.commits.append(MAYBE)` in
    `commit_directories`); failed at the assert below (0 == 2).
    """
    session = _repo(tmp_path / "session")
    other = _repo(tmp_path / "other")
    ps.start("DEEP", root=other)
    command = f"cd '{other.as_posix()}'; f() {{ f; }}; f; git commit -m x"
    assert _run(monkeypatch, session, command) == 2


@pytest.mark.parametrize("tool,command", [
    ("Bash", "[ -e q ] && cd d1; X=c; X+=ommit; git $X -m x"),
    ("PowerShell", "$x = 'co'; $x += 'mmit'; git $x -m y"),
    ("PowerShell", "$x='co'; $x+='mmit'; git $x -m y"),
    ("PowerShell", "$x = 'co'; $x+= 'mmit'; git $x -m y"),
    ("PowerShell", "$x = 'co'; $x +='mmit'; git $x -m y"),
])
def test_an_append_is_applied_once_whatever_the_states(monkeypatch, tmp_path, tool, command):
    """FIX-SEC-007, L6 cycles 1 and 2. Failure modes: with two possible states, `X+=ommit`
    was applied once per state to one shared value (`commitommit`); PowerShell `+=` was
    not an assignment at all, and after the first L6 revision only its spaced spelling was.

    Red 2026-11-21: the first two cases against the guard before the first L6 revision,
    the last three against the guard before the second; each failed at the assert below
    (0 == 2).
    """
    run = _run_ps if tool == "PowerShell" else _run
    assert run(monkeypatch, _in_run(tmp_path), command) == 2, command


def test_a_value_that_keeps_doubling_does_not_outrun_the_hook(monkeypatch, tmp_path):
    """FIX-PERF-002, L6 cycle 1. Invariant: the guard decides within its hook timeout.
    Failure mode: steps were bounded, not their cost, so `X=$X$X` 26 times made each later
    step huge; 1 KB of such text took 66.6 s, and the hook fails open past 60 s.

    Red 2026-11-21: the guard before the L6 revision; failed at the time assert below.
    """
    import time
    command = "git commit -m x; X=a; " + "X=$X$X; " * 26 + "echo $X; " * 12
    started = time.perf_counter()
    assert _run(monkeypatch, _in_run(tmp_path), command) == 2
    assert time.perf_counter() - started < 5


def test_the_walk_stops_at_its_deadline(monkeypatch, tmp_path):
    """FIX-PERF-002, L6 cycle 1. Invariant: past `_DEADLINE_S` the walk stops and the command
    is judged against every open run (None).

    Red 2026-11-21: the guard before the L6 revision (no deadline); failed at the assert
    below ([tmp_path] == [None]).
    """
    monkeypatch.setattr(guard, "_DEADLINE_S", -1, raising=False)
    assert guard.commit_directories("git commit -m x", tmp_path) == [None]


def test_one_command_cannot_expand_past_the_budget(monkeypatch, tmp_path):
    """FIX-PERF-002, L6 cycle 2. Invariant: past `_MAX_EXPANDED` characters of expansion the
    walk stops and the command is judged against every open run. Failure mode: the deadline
    is checked only between commands, so after 19 doublings one `cd $X$X...` (5.6 KB of
    text) took 64.9 s, past the 60 s hook timeout, which fails open.

    Red 2026-11-21: the guard before the second L6 revision (no expansion budget); failed
    at the assert below (the known directory instead of [None]).
    """
    monkeypatch.setattr(guard, "_MAX_EXPANDED", 5, raising=False)
    assert guard.commit_directories("X=aaaa; cd $X$X$X; git commit -m x", tmp_path) == [None]


def test_an_unquoted_expansion_is_charged_before_it_is_split(monkeypatch, tmp_path):
    """FIX-PERF-002, L6 cycle 2, the split path. Failure mode: the expansion budget was
    charged only at flush, after `value.split()` had read the whole value, so `cd $X$X...`
    with a 524 K character X took 65 s at 300 K characters of text (8.7 s at 40 K), past
    the hook timeout.

    Red 2026-11-21: the guard before this charge; failed at the time assert below.
    """
    import time
    command = "X=a; " + "X=$X$X; " * 19 + "cd " + "$X" * 20_000 + "; git commit -m x"
    started = time.perf_counter()
    assert guard.commit_directories(command, tmp_path) == [None]
    assert time.perf_counter() - started < 5


def test_a_path_no_file_system_takes_is_unknown(tmp_path):
    """FIX-PERF-002, L6 cycle 2, the path walk. Failure mode: a known 524 K character path
    reached `find_root`, whose `[start, *start.parents]` is quadratic in it, and `main`
    raised MemoryError after about 98 s, which exits 1 and fails open.

    Red 2026-11-21: the guard before `_MAX_PATH` in `_join`; failed at the assert below
    (the huge path instead of [None]). Since an unknown `cd` may fail, the starting
    directory is judged too.
    """
    command = "X=a/; " + "X=$X$X; " * 18 + 'cd "$X"; git commit -m x'
    assert set(guard.commit_directories(command, tmp_path)) == {None, tmp_path}


# --- root causes found after the BLOCKED L6 pass (owner override, 2026-11-21) -------------


def _guard_process(cwd: pathlib.Path, command: str, watchdog_s: float):
    """Run the guard as its own process with `_WATCHDOG_S` set, as the hook would run it,
    with a walk that stalls for 30 s (then finds an unknown directory) on every platform.

    A command that is slow to judge on one system is fast on another: `find_root` on a deep
    path took 20 s on Windows and no time on Linux, where the test passed by not reaching
    the watchdog at all. The stall is the walk's, so the watchdog is what is tested.
    """
    import subprocess
    import sys
    import time
    launcher = (
        "import importlib.util, sys, time\n"
        f"spec = importlib.util.spec_from_file_location('g', {str(_GUARD_PATH)!r})\n"
        "g = importlib.util.module_from_spec(spec); spec.loader.exec_module(g)\n"
        f"g._WATCHDOG_S = {watchdog_s!r}\n"
        "g.commit_directories = lambda *a, **k: (time.sleep(30), [None])[1]\n"
        "sys.exit(g.main())\n"
    )
    payload = json.dumps({"tool_name": "Bash", "cwd": str(cwd),
                          "tool_input": {"command": command}})
    started = time.perf_counter()
    proc = subprocess.run([sys.executable, "-c", launcher], input=payload, cwd=cwd,
                          capture_output=True, text=True, timeout=120)
    return proc.returncode, time.perf_counter() - started, proc.stderr


def test_a_decision_that_takes_too_long_is_a_refusal(tmp_path):
    """FIX-PERF-002, root cause. Invariant: the guard decides within its hook timeout, which
    fails open, wherever the time goes. Failure mode: three cycles bounded proxies (steps,
    characters, field length) at chosen sites, and each probe spent its time elsewhere:
    `find_root` on an 8,192-component directory took 20 s for a 120-character command.

    Red 2026-11-22, with the stalling walk: the guard before the watchdog (`guard-preRC.py`)
    exited 2 only after the 30 s stall, failing the time assert below. The earlier proof
    used a slow command instead, which is slow only on Windows.
    """
    repo = _in_run(tmp_path)
    code, elapsed, err = _guard_process(repo, "git commit -m x", 1.0)
    assert code == 2, err
    assert elapsed < 8
    assert "could not judge this command in time" in err


def test_a_slow_decision_with_no_run_open_is_allowed(tmp_path):
    """The other half: with no run that could be judged, running out of time allows.

    Red 2026-11-22, with the stalling walk: the guard before the watchdog (`guard-preRC.py`)
    exited 0 only after the 30 s stall, failing the time assert below.
    """
    repo = _repo(tmp_path)
    code, elapsed, _ = _guard_process(repo, "git commit -m x", 1.0)
    assert code == 0
    assert elapsed < 8


@pytest.mark.parametrize("tool,command", [
    ("PowerShell", "$x='co'; $x+='m'+'mit'; git $x -m y"),
    ("PowerShell", "$x='co'; $x=$x+'mmit'; git $x -m y"),
    ("PowerShell", "$x = 'com'+'mit'; git $x -m y"),
    ("PowerShell", "$x='co'; [string]$x+='mmit'; git $x -m y"),
    ("PowerShell", "$x='status'; Set-Variable x ('com'+'mit'); git $x -m y"),
    ("PowerShell", "$x='status'; foreach ($x in 'com'+'mit') { }; git $x -m y"),
    ("Bash", "X=co; declare -n R=X; R+=mmit; git $X -m y"),
    ("Bash", "X=status; source ./f; git $X -m y"),
    ("Bash", "X=status; getopts ab X; git $X -m y"),
])
def test_a_value_the_walk_cannot_model_is_unknown(monkeypatch, tmp_path, tool, command):
    """FIX-SEC-007, root cause. Invariant: a variable is known only while every write to it is
    one the walk models; any other write makes it unknown. Failure mode: the walk kept the old
    value after a writer it does not model (`declare -n`, `source`, `getopts`, a cast,
    `Set-Variable`, `foreach`), and rendered a PowerShell expression as literal text
    (`'com'+'mit'` became `com+mit`), so `git $x` read as a known non-commit.

    Red 2026-11-21: the guard before the inverted default; each failed at the assert below
    (0 == 2).
    """
    run = _run_ps if tool == "PowerShell" else _run
    assert run(monkeypatch, _in_run(tmp_path), command) == 2, command


# --- what the L5.5 review of the root-cause fixes found -------------------------------


@pytest.mark.parametrize("template", [
    'HOME={other}; echo "HOME is $HOME"; cd && git commit -m x',
    "HOME=x; read HOME; cd && git commit -m x",
    'PWD={other}; echo PWD; cd "$PWD" && git commit -m x',
])
def test_a_distrusted_name_is_unknown_not_its_default(monkeypatch, tmp_path, template):
    """FIX-SEC-007, root-cause fix. Invariant: a variable the walk distrusts is unknown.
    Failure mode: an assigned HOME or PWD that was distrusted fell through to the defaults
    for HOME and PWD (the hook's HOME, the walked cwd), a wrong known directory, so a
    commit into another checkout's run was allowed.

    Red 2026-11-21: the guard after the first root-cause fix; each failed at the assert
    below (0 == 2).
    """
    session = _repo(tmp_path / "session")
    other = _repo(tmp_path / "other")
    ps.start("DEEP", root=other)
    monkeypatch.setenv("HOME", str(_repo(tmp_path / "home")))
    command = template.format(other=other.as_posix())
    assert _run(monkeypatch, session, command) == 2, command


def test_brace_detection_is_linear(tmp_path):
    """FIX-PERF-002, root cause. Invariant: no call the guard makes into C may be super-linear
    in the command, because the watchdog is a Python thread and cannot run while one holds
    the interpreter. Failure mode: the brace-expansion regex backtracked quadratically on
    `{` followed by commas: 40,000 took 8.7 s, 115,000 kept the hook past its timeout.

    Red 2026-11-21: the guard before the linear scan; failed at the time assert below.
    """
    import time
    started = time.perf_counter()
    assert guard.commit_directories("git commit -m x; echo {" + "," * 40_000, tmp_path) == \
        [tmp_path]
    assert time.perf_counter() - started < 3


@pytest.mark.parametrize("command", [
    "X=; N=$'\\x58'; eval \": \\${$N:=com}\"; X+=mit; git $X -m y",
    "X=status; printf -vX %s%s com mit; git $X -m y",
    "X=co; O=$(printf %s -n); N=$'\\x58'; declare $O R=$N; R+=mmit; git $X -m y",
])
def test_every_mention_of_a_name_counts(monkeypatch, tmp_path, command):
    """FIX-SEC-007, root-cause fix. Invariant: a name is trusted only while every mention of
    it is modelled, wherever the mention is. Failure modes: a mention in eval text was
    judged against that text alone, a name glued to an option (`-vX`) was not seen, and an
    unknown word in `declare` could be `-n`.

    Red 2026-11-21: the guard after the first root-cause fix; each failed at the assert
    below (0 == 2).
    """
    assert _run(monkeypatch, _in_run(tmp_path), command) == 2, command


@pytest.mark.parametrize("arithmetic", [
    "(( D=0 ))",
    "(( ((1)) + (D=0) ))",
    "(( $(case a in a) echo 1;; esac) + $(case b in b) echo 2;; esac) + (D=0) ))",
    "(( $(echo \"))\" >/dev/null; echo 1) + (D=0) ))",
])
def test_an_arithmetic_assignment_is_not_a_modelled_one(monkeypatch, tmp_path, arithmetic):
    """FIX-SEC-007, root-cause fix. Failure mode: `(( D=0 ))` with spaces read as a plain
    assignment to D, so `cd $D` was taken into `child` while bash fails it and commits in the
    session checkout. The other cases end a character-level guess at the arithmetic early,
    hiding `(D=0)`: a nested `((1))`, a `case` pattern's `)`, a quoted `))`. Only the
    parser's own reading of where the arithmetic ends is safe from that.

    Red 2026-11-21: the first case against the guard after the first root-cause fix; the
    nested case against `guard-postRC2.py` (first `))`); the `case` and quoted cases against
    `guard-postRC3.py` (a paren depth count); each failed at the assert below (0 == 2).
    """
    parent = _enclosing(tmp_path)
    command = f"D=child; {arithmetic}; cd $D; git commit -m x"
    assert _run(monkeypatch, parent, command) == 2, command


@pytest.mark.parametrize("write", [
    "[[ 1 -eq D=0 ]]",
    "[[ D=0 -eq 0 ]]",
    "x=abc; echo ${x: D=0}",
    "a[ D=0 ]=1",
    "echo $[ D=0 ]",
])
def test_only_a_parsed_assignment_is_a_modelled_one(monkeypatch, tmp_path, write):
    """FIX-SEC-007, root-cause fix. Invariant: `NAME=` is trusted as a write the walk models
    only where the parser made an assignment word of it. Failure mode: any `NAME=` after a
    space was taken as one, yet bash reads these as arithmetic and sets D to 0, so `cd $D`
    fails and the commit runs in the session checkout while the guard judged `child`.
    Real bash (Git Bash 5.3.15) assigns D in each case.

    Red 2026-11-21: `guard-postRC4.py`, the guard with the parser's arithmetic spans; each
    failed at the assert below (0 == 2).
    """
    parent = _enclosing(tmp_path)
    command = f"D=child; {write}; cd $D; git commit -m x"
    assert _run(monkeypatch, parent, command) == 2, command


@pytest.mark.parametrize("command", [
    "D=child; X=$[ D=0 ]; cd $D; git commit -m x",
    'D=child; echo "$[ D=0 ]"; cd $D; git commit -m x',
    "D=child; f() { cd $D; git commit -m x; }; D=nope f",
    "D=child; D=nope eval 'cd $D; git commit -m x'",
    "D=nope; local D=child; cd $D; git commit -m x",
])
def test_a_value_follows_bash_assignment_rules(monkeypatch, tmp_path, command):
    """FIX-SEC-007, root-cause fix. Invariant: the walk keeps a known value only where it
    assigns as bash does. Failure modes: `$[ ]` was not read as arithmetic, so `D=0` in it
    looked like an assignment word; a prefix assignment before a function or `eval` is
    visible inside the call, but the walk ignored it; `local` outside a function fails in
    bash and assigns nothing, but the walk applied it. Real bash (Git Bash 5.3.15) sets D to
    0, sees D=nope inside the call, and leaves D=nope after `local`.

    Red 2026-11-21: `guard-postRC5.py`, the guard with the parsed-assignment allowlist; each
    failed at the assert below (0 == 2), except the double-quoted `$[ ]`, which the allowlist
    already refused and which stays as a control for reading `$[ ]` inside quotes.
    """
    parent = _enclosing(tmp_path)
    assert _run(monkeypatch, parent, command) == 2, command


@pytest.mark.parametrize("write", ["read CDPATH <<< other", ": ${CDPATH:=other}"])
def test_a_distrusted_cdpath_is_unknown(monkeypatch, tmp_path, write):
    """FIX-SEC-007. Invariant: CDPATH, like any name, is unknown once written somewhere the
    walk does not model. Failure mode: `cd` read CDPATH from the walk's values directly,
    past the distrust rule, so `cd child` was judged as the separate checkout `child` while
    bash follows CDPATH to `other/child`, inside the run's checkout (checked with Git Bash
    5.3.15).

    Red 2026-11-21: the `${CDPATH:=other}` case against `guard-postRC8.py`, failing at the
    assert below (0 == 2). The `read` case was already refused there (`read` stores an
    unknown value) and stays as a control.
    """
    parent = _enclosing(tmp_path)
    (parent / "other" / "child").mkdir(parents=True)
    command = f"{write}; cd child; git commit -m x"
    assert _run(monkeypatch, parent, command) == 2, command


@pytest.mark.parametrize("write", ["printf -v IFS %s /", "read -r IFS <<< /"])
def test_a_distrusted_ifs_splits_unknowably(monkeypatch, tmp_path, write):
    """FIX-SEC-007. Invariant: an IFS written somewhere the walk does not model makes
    unquoted expansions split unknowably. Failure mode: only a modelled assignment set
    `ifs_changed`, so `cd $D` with D=child/x was taken into `child/x`, while bash splits it
    on `/`, fails `cd child x` (too many arguments) and commits in the session checkout
    (checked with Git Bash 5.3.15).

    Red 2026-11-21: `guard-postRC8.py`; each failed at the assert below (0 == 2).
    """
    parent = _enclosing(tmp_path)
    (parent / "child" / "x").mkdir()
    command = f"{write}; D=child/x; cd $D; git commit -m x"
    assert _run(monkeypatch, parent, command) == 2, command


def test_cd_dash_follows_an_assigned_oldpwd(monkeypatch, tmp_path):
    """FIX-SEC-007. Invariant: `cd -` goes to $OLDPWD, as bash does. Failure mode: the walk
    took its own record of the previous directory, `child/x`, past `OLDPWD=..`, while bash
    goes to the enclosing checkout, where the run is open (checked with Git Bash 5.3.15).

    Red 2026-11-21: `guard-postRC9.py`; failed at the assert below (0 == 2).
    """
    parent = _enclosing(tmp_path)
    (parent / "child" / "x").mkdir()
    command = "cd x; cd ..; OLDPWD=..; cd -; git commit -m x"
    assert _run(monkeypatch, parent / "child", command) == 2


def test_an_assigned_pwd_does_not_survive_cd(monkeypatch, tmp_path):
    """FIX-SEC-007. Invariant: bash rewrites PWD on every `cd`, so an earlier assignment to
    it is not its value after one. Failure mode: the walk kept `PWD=<child>` across `cd x`
    and judged `cd $PWD` as the separate checkout `child`, while bash goes back to `x`,
    inside the run's checkout (checked with Git Bash 5.3.15).

    Red 2026-11-21: `guard-postRC9.py`; failed at the assert below (0 == 2).
    """
    parent = _enclosing(tmp_path)
    (parent / "x").mkdir()
    command = f"PWD={(parent / 'child').as_posix()}; cd x; cd $PWD; git commit -m x"
    assert _run(monkeypatch, parent, command) == 2, command


@pytest.mark.parametrize("write", ["PWD=nosuch; cd \"$PWD\"", "X=nosuch; echo 'IFS=:'; cd $X",
                                   "echo 'CDPATH=x'; cd nosuch"])
def test_a_cd_to_an_unknown_place_may_fail(monkeypatch, tmp_path, write):
    """FIX-SEC-007. Invariant: a `cd` whose target is unknown may fail, so what runs after
    `||` is judged in the directory it started in. Failure mode: an unknown target counted
    as a certain move, dropping the failure branch, so the commit after `||` was lost (the
    commit before it kept the loose net from catching it).

    Red 2026-11-21: `guard-postRC10.py`, where these targets first became unknown; each
    failed at the assert below (0 == 2).
    """
    session = _in_run(tmp_path / "r")
    other = _repo(tmp_path / "o")
    command = f"git -C {other.as_posix()} commit -m y; {write} || git commit -m x"
    assert _run(monkeypatch, session, command) == 2, command


@pytest.mark.parametrize("template", [
    "for OLDPWD in {o}; do cd {o}; cd \"$OLDPWD\"; git commit -m x; done",
    "D={a}; [ -e nosuch ] && for D in {o}; do :; done; cd $D && git commit -m x",
    "D={o}/; sh -c 'cd ${{D}}.; git commit -m x'",
    "f() {{ cd {o}; }}; bash -c 'f; git commit -m x'",
    "sh -c 'cd {o} &>/dev/null; git commit -m x'",
    "bash -c 'echo $\\\n[ ; cd {o} ; ]\ngit commit -m x'",
], ids=["for-oldpwd", "conditional-for", "unexported-variable", "unexported-function",
        "dash-ampersand-redirect", "dollar-continued-bracket"])
def test_what_master_refused_stays_refused(monkeypatch, tmp_path, template):
    """FIX-SEC-007, SEC-015. Invariant: where bash or dash commits in the session checkout,
    which has the open run, the guard refuses, as master 1db5bbe did. Failure modes, each a
    wrong known directory outside the run: a for loop wrote its variable past `_set` (so
    bash's rewrite of OLDPWD on `cd`, and a loop that never ran, were missed); a child
    shell saw a variable or a function the parent never exported; dash reads `&>` as `&`
    then `>`, so its `cd` runs in the background; bash joins `$\\<newline>[` into `$[`
    before reading it. Checked in bash (Git Bash 5.3.15) and dash by the L5.5 reviewer and
    against master by the L6 architect, except the unexported function (bash: `f: command
    not found`), which shares the unexported variable's cause.

    Red 2026-11-21: `guard-postRC12.py`; each failed at the assert below (0 == 2).
    """
    session = _in_run(tmp_path / "a")
    other = _repo(tmp_path / "o")
    command = template.format(a=session.as_posix(), o=other.as_posix())
    assert _run(monkeypatch, session, command) == 2, command


@pytest.mark.parametrize("template", [
    "HOME={a}; bash -c 'cd && git commit -m x'",
    "HOME={a}; sh -c 'cd ~ && git commit -m x'",
    "cd {o}; OLDPWD={a}; bash -c 'cd - && git commit -m x'",
    "set -a; f() {{ cd {a}; }}; bash -c 'f && git commit -m x'",
    "cd {o}; sh -c 'cd {a}; echo $\\\n[ ; git commit -m x ; ]'",
], ids=["home", "home-tilde-sh", "oldpwd", "allexport-function", "continued-bracket-in-sh"])
def test_a_child_shell_judges_what_it_may_inherit(monkeypatch, tmp_path, template):
    """FIX-SEC-007, SEC-015. Invariant: a child shell may see any name the parent assigned
    (it may have come from the environment, which marks it exported) and any function
    (`set -a` exports it), so neither is known to be absent. Failure modes: a name dropped
    from the child's view fell back to `_value`'s default (the hook's HOME, the walk's own
    OLDPWD), a wrong known value; a function `set -a` exported was dropped; and the two
    readings of a nested `sh` were chosen on the raw text, missing `$\\<newline>[`. Real bash
    5.3 passes HOME and OLDPWD to the child and exports functions under `set -a` (checked by
    the L5.5 reviewer).

    Red 2026-11-21: `guard-postRC13.py`, the first child view; each failed at the assert
    below (0 == 2).
    """
    session = _in_run(tmp_path / "a")
    other = _repo(tmp_path / "o")
    monkeypatch.setenv("HOME", str(other))
    command = template.format(a=session.as_posix(), o=other.as_posix())
    assert _run(monkeypatch, other, command) == 2, command


def test_dollar_bracket_is_one_expansion_in_bash(monkeypatch, tmp_path):
    """FIX-SEC-007. Invariant: bash reads `$[ ... ]` as one arithmetic expansion. Failure
    mode: read as text, its `; cd child;` looked like commands, so the commit on the next
    line was judged in `child`. Bash fails the arithmetic, never runs that `cd`, and commits
    in the session checkout, where the run is open (checked with Git Bash 5.3.15).

    Red 2026-11-21: `guard-postRC5.py`, before `$[` was lexed; failed at the assert below
    (0 == 2).
    """
    parent = _enclosing(tmp_path)
    assert _run(monkeypatch, parent, "echo $[ 1; cd child; ]\ngit commit -m x") == 2


@pytest.mark.parametrize("shell", ["sh", "dash"])
def test_a_shell_that_may_not_be_bash_is_read_both_ways(monkeypatch, tmp_path, shell):
    """FIX-SEC-007. Invariant: a script for a shell that may not be bash is judged under every
    reading it may get. Failure mode: `$[` is arithmetic in bash and zsh but text in dash
    (which `sh` is on Debian and Ubuntu), so dash runs the `cd ..` that bash would read as
    part of `$[ ]`, and the commit lands in the enclosing checkout's run.

    Red 2026-11-21: `guard-postRC6.py`, which read every POSIX shell's script as bash; each
    failed at the assert below (0 == 2).
    """
    parent = _enclosing(tmp_path)
    command = f"{shell} -c 'echo $[; cd ..; ]; git commit -m x'"
    assert _run(monkeypatch, parent / "child", command) == 2, command


@pytest.mark.parametrize("command", [
    "echo " + "$x " * 20_000 + "; git status",
    "env " + "-u A " * 5_000 + "$x " * 5_000 + "; git status",
], ids=["unknown-words", "env-options-then-unknown-words"])
def test_unknown_words_are_judged_in_linear_time(tmp_path, command):
    """FIX-PERF-002. Invariant: judging a command is linear in its words. Failure modes: for
    every unknown or git field the guard searched back over all earlier fields for an `env`
    (12,000 unknown words took 6.6 s), then parsed env's options again (5,000 options and
    5,000 unknown words took 27 s). Long enough, either passes the watchdog, which refuses a
    command it could not judge while a run is open, commit or not.

    Red 2026-11-21: the first case against `guard-postRC5.py` (the backward search), the
    second against `guard-postRC7.py` (env parsed per field); each failed at the first
    assert below (`[None] == []`): the walk ran into its 15 s deadline, which makes the
    directory unknown, so the slowness shows there before the time assert is reached.
    """
    import time
    started = time.perf_counter()
    assert guard.commit_directories(command, tmp_path) == []
    assert time.perf_counter() - started < 3


@pytest.mark.parametrize("command", [
    "D=sub; export E=1 F=2; cd $D && git commit -m x",
    "D=sub; f() { local G=3; }; f; cd $D && git commit -m x",
    "export D=sub; cd $D && git commit -m x",
    "D=sub; A=1 B=2 make; cd $D && git commit -m x",
])
def test_parsed_assignments_stay_trusted(tmp_path, command):
    """FIX-SEC-007, the other side: an assignment the parser produced keeps its known value.
    A control for the test above, against a fix that distrusts every `NAME=`: green before
    and after this fix by design (checked 2026-11-21 against `guard-postRC4.py`), so it is
    not red proof of anything.
    """
    (tmp_path / "sub").mkdir()
    assert guard.commit_directories(command, tmp_path) == [tmp_path / "sub"], command


def test_a_name_argument_does_not_refuse_a_command_without_a_commit(monkeypatch, tmp_path):
    """FIX-SEC-007, over-refusal control: `make CC=gcc` makes CC unknown, which must not
    refuse a command with no commit in it. Green before and after the fix by design."""
    assert _run(monkeypatch, _in_run(tmp_path), "make CC=gcc && git status") == 0


@pytest.mark.parametrize("tool,command", [
    ("Bash", "X=status; echo X; git $X -m y"),
    ("PowerShell", "$s = 'status'; git $s"),
])
def test_git_with_an_unknown_subcommand_is_refused_by_design(monkeypatch, tmp_path, tool, command):
    """The stated cost of FIX-SEC-007's inverted default: with a run open, `git` given a
    subcommand the walk cannot know is judged as a possible commit, even when it is not one.
    Over-refusal here is the fail-closed side; the alternative is a wrong known value.

    Red 2026-11-21 (as a contract pin): the guard before the root-cause fixes allowed both
    (0), failing at the assert below (0 == 2).
    """
    run = _run_ps if tool == "PowerShell" else _run
    assert run(monkeypatch, _in_run(tmp_path), command) == 2, command
