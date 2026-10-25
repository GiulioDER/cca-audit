"""Tiny git repositories for the cca_checks.bugs tests.

Not collected (no test_ prefix). Nothing touches the network: `origin` is only a URL,
and the remote-tracking ref that marks a commit as landed is written with update-ref.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_git_environment(monkeypatch):
    """Keep recovery fixtures independent of the caller's repository and Git configuration."""
    for name in list(os.environ):
        if name.startswith("GIT_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")


def run_git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True,
        text=True, encoding="utf-8",
    ).stdout


def make_repo(path: Path, remote: str = "https://github.com/acme/widgets.git") -> Path:
    path.mkdir(parents=True, exist_ok=True)
    run_git(path, "init", "-q", "-b", "master")
    run_git(path, "config", "user.email", "test@example.com")
    run_git(path, "config", "user.name", "Test")
    run_git(path, "config", "commit.gpgsign", "false")
    (path / ".nohooks").mkdir()
    run_git(path, "config", "core.hooksPath", str(path / ".nohooks"))
    run_git(path, "remote", "add", "origin", remote)
    commit(path, "README.md", "seed\n", "initial commit")
    return path


def commit(repo: Path, filename: str, content: str, message: str | bytes,
           date: str = "2026-09-01T12:00:00+00:00") -> str:
    target = repo / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8", newline="\n")
    run_git(repo, "add", filename)
    env = {**os.environ, "GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date}
    data = message if isinstance(message, bytes) else message.encode("utf-8")
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-F", "-"], input=data,
                   check=True, capture_output=True, env=env)
    return run_git(repo, "rev-parse", "HEAD").strip()


def commit_raw(repo: Path, filename: str, content: str, message: bytes) -> str:
    """Commit with the message bytes stored exactly as given.

    `git commit` rewrites a message that is not UTF-8 as if it were Latin-1 (git 2.55
    does), so it cannot produce the raw bytes that commits made by other tools carry.
    Writing the commit object directly can.
    """
    target = repo / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8", newline="\n")
    run_git(repo, "add", filename)
    tree = run_git(repo, "write-tree").strip()
    parent = run_git(repo, "rev-parse", "HEAD").strip()
    who = b"Test <test@example.com> 1756728000 +0000"
    obj = (b"tree " + tree.encode() + b"\nparent " + parent.encode() + b"\nauthor " + who
           + b"\ncommitter " + who + b"\n\n" + message)
    sha = subprocess.run(["git", "-C", str(repo), "hash-object", "-t", "commit", "-w", "--stdin"],
                         input=obj, check=True, capture_output=True).stdout.decode().strip()
    run_git(repo, "update-ref", "HEAD", sha)
    return sha


def publish(repo: Path, ref: str = "master", branch: str = "master") -> None:
    run_git(repo, "update-ref", f"refs/remotes/origin/{branch}", ref)
