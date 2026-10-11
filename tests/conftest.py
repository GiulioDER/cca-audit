"""Shared fixtures for the pipeline gate tests.

`tree_of` deliberately re-implements the fingerprint with plain git commands
instead of importing it: the hole tests in test_gate_holes.py must run unchanged
against baseline d2317de, where the core module does not exist.
"""

from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import shutil
import subprocess
import tempfile
import uuid

import pytest

_HOOKS = pathlib.Path(__file__).resolve().parents[1] / "cca_checks" / "plugin" / "hooks"


def _git(root, *args, env=None) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, env=env
    ).stdout.decode("utf-8", "replace").strip()


def tree_of(repo) -> str:
    """Tree id of the whole repository's working tree, excluding the run's .claude/audits.

    `repo` may be a subdirectory: the tree is still the whole repository's, and the
    excluded path is `repo`'s own `.claude/audits`. Mirrors cca_gate_core.fingerprint.
    """
    top = _git(repo, "rev-parse", "--show-toplevel")
    rel = os.path.relpath(os.path.realpath(repo), os.path.realpath(top)).replace(os.sep, "/")
    excluded = ":(literal)" + (".claude/audits" if rel == "." else f"{rel}/.claude/audits")
    index = _git(top, "rev-parse", "--path-format=absolute", "--git-path", "index")
    with tempfile.TemporaryDirectory() as tmp:
        tmp_index = os.path.join(tmp, "index")
        if os.path.exists(index):
            shutil.copy2(index, tmp_index)  # keep mtime: see cca_gate_core.fingerprint
        env = {**os.environ, "GIT_INDEX_FILE": tmp_index}
        _git(top, "add", "-A", "--", ".", env=env)
        _git(top, "rm", "--cached", "-r", "-q", "--ignore-unmatch", "--", excluded, env=env)
        return _git(top, "write-tree", env=env)


def write_output(root, value=None) -> str:
    """A JSON layer output file for `record(..., output=...)`; returns its path.

    Written under `root`'s `.claude/audits/`, which the fingerprint excludes, so creating
    it never reads as an edit to the tree under audit (a file anywhere else would make
    the fix layers owed). Each call writes a new file.
    """
    folder = pathlib.Path(root) / ".claude" / "audits" / "outputs-in"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{uuid.uuid4().hex}.json"
    path.write_text(json.dumps([] if value is None else value), encoding="utf-8")
    return str(path)


def load_hook(name: str):
    path = _HOOKS / name
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_repo(root: pathlib.Path, gitignore: str) -> pathlib.Path:
    """A one commit repository whose .gitignore holds exactly `gitignore`."""
    return _init(root, gitignore)


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
