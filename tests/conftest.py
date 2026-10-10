"""Shared fixtures for the pipeline gate tests.

`tree_of` deliberately re-implements the fingerprint with plain git commands
instead of importing it: the hole tests in test_gate_holes.py must run unchanged
against baseline d2317de, where the core module does not exist.
"""

from __future__ import annotations

import importlib.util
import os
import pathlib
import shutil
import subprocess
import tempfile

import pytest

_HOOKS = pathlib.Path(__file__).resolve().parents[1] / "cca_checks" / "plugin" / "hooks"


def _git(root, *args, env=None) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, env=env
    ).stdout.decode("utf-8", "replace").strip()


def tree_of(repo) -> str:
    """Tree id of the working tree content, excluding .claude/audits."""
    index = _git(repo, "rev-parse", "--path-format=absolute", "--git-path", "index")
    with tempfile.TemporaryDirectory() as tmp:
        tmp_index = os.path.join(tmp, "index")
        if os.path.exists(index):
            shutil.copy2(index, tmp_index)  # keep mtime: see cca_gate_core.fingerprint
        env = {**os.environ, "GIT_INDEX_FILE": tmp_index}
        _git(repo, "add", "-A", "--", ".", ":(exclude).claude/audits", env=env)
        return _git(repo, "write-tree", env=env)


def load_hook(name: str):
    path = _HOOKS / name
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _init(root: pathlib.Path, gitignore: str) -> pathlib.Path:
    if shutil.which("git") is None:
        pytest.skip("git is required for tree fingerprints")
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@example.invalid")
    _git(root, "config", "user.name", "t")
    _git(root, "config", "core.autocrlf", "false")
    (root / ".gitignore").write_text(gitignore, encoding="utf-8")
    (root / "app.py").write_text("x = 1\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")
    return root


@pytest.fixture
def git_repo(tmp_path) -> pathlib.Path:
    return _init(tmp_path, ".claude/\n")


@pytest.fixture
def git_repo_tracking_claude(tmp_path) -> pathlib.Path:
    """A user repository that does NOT ignore .claude/ (Review Focus 1)."""
    return _init(tmp_path, "")
