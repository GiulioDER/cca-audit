"""Find the landed CCA fix commits in a set of clones.

"Fixed" means landed: only commits reachable from the remote's default branch are
read. Clones are grouped by their GitHub origin, so thirty checkouts of one repository
count its bugs once; a clone without a GitHub origin (a local bundle, say) is skipped
and reported, because it would count the same bugs under a second name.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .parse import is_cca_fix_subject

_GITHUB = re.compile(r"github\.com[:/](?P<owner>[^/\s]+)/(?P<name>[^/\s]+?)(?:\.git)?/?$")
_DEFAULT_REFS = (
    "refs/remotes/origin/HEAD",
    "refs/remotes/origin/master",
    "refs/remotes/origin/main",
)


class GitError(RuntimeError):
    pass


def git(clone: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(clone), *args], capture_output=True,
        text=True, encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed in {clone}: {proc.stderr.strip()}")
    return proc.stdout


def normalize_remote(url: str) -> str | None:
    m = _GITHUB.search(url.strip())
    return f"{m.group('owner')}/{m.group('name')}" if m else None


def remote_repo(path: Path) -> str | None:
    try:
        return normalize_remote(git(path, "remote", "get-url", "origin"))
    except GitError:
        return None


def group_clones(paths: list[Path]) -> tuple[dict[str, Path], list[tuple[str, str]]]:
    repos: dict[str, Path] = {}
    seen: set[str] = set()
    skipped: list[tuple[str, str]] = []
    for path in paths:
        repo = remote_repo(path)
        if repo is None:
            skipped.append((str(path), "no GitHub origin remote"))
            continue
        if repo.lower() in seen:
            continue
        seen.add(repo.lower())
        repos[repo] = path
    return repos, skipped


def default_ref(clone: Path) -> str | None:
    for ref in _DEFAULT_REFS:
        try:
            git(clone, "rev-parse", "--verify", "--quiet", ref)
            return ref
        except GitError:
            continue
    return None


@dataclass
class Commit:
    sha: str
    date: str
    subject: str
    body: str
    files: list[str]


def _changed_files(clone: Path, sha: str) -> list[str]:
    try:
        out = git(clone, "diff", "--name-only", f"{sha}~1", sha)
    except GitError:  # a root commit has no parent
        out = git(clone, "show", "--format=", "--name-only", sha)
    return sorted({f.strip() for f in out.splitlines() if f.strip()})


def fix_commits(clone: Path, ref: str) -> list[Commit]:
    commits: list[Commit] = []
    for line in git(clone, "log", ref, "--format=%H%x1f%cI%x1f%s").splitlines():
        sha, when, subject = line.split("\x1f", 2)
        if not is_cca_fix_subject(subject):
            continue
        body = git(clone, "show", "-s", "--format=%B", sha)
        commits.append(Commit(sha, when[:10], subject, body, _changed_files(clone, sha)))
    return commits
