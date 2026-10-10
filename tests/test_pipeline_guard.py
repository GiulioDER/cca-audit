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
from conftest import write_output

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
    ps.record("L2", counts={"unique": 0}, output=write_output(git_repo), root=git_repo)
    ps.record("L2.5", counts={"confirmed": 0, "fp": 0, "uncertain": 0, "duplicate": 0},
              output=write_output(git_repo), root=git_repo)
    ps.record("L3", counts={"to_fix": 0}, output=write_output(git_repo), root=git_repo)
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
    from_guard = guard.evaluate(state)
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
