"""The PreToolUse guard, and the drift pin that keeps it honest.

The guard carries its own copy of the required-layer table because it runs from
the user's global hooks directory against repositories where `cca_checks` was
never installed, and an ImportError in a PreToolUse hook fails in the worst
available way: it either blocks every commit on the machine or is swallowed and
silently stops guarding. Duplication is the right trade there, but only while
something proves the two copies still agree. That is
`test_guard_agrees_with_pipeline_state_across_a_matrix`, and it is the most
important test in this file.
"""

from __future__ import annotations

import importlib.util
import io
import json
import pathlib

import pytest

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


def test_guard_allows_the_commit_once_the_gates_are_satisfied(monkeypatch, tmp_path):
    repo = _repo(tmp_path)
    ps.start("FAST", root=repo)
    for layer in ("L1", "L2", "L2.5"):
        ps.record(layer, detail="done", root=repo)
    ps.record("L6", detail="APPROVED", root=repo)
    assert _run(monkeypatch, repo, "git commit -m x") == 0


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
    ps.record("L1", detail="8 auditors", root=repo)
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
