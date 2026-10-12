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

# GitHub's own character set for owners and names. Anything wider lets an origin URL put
# a path separator into the project name, and the project name becomes a memo filename.
_GITHUB = re.compile(
    r"github\.com[:/](?P<owner>[A-Za-z0-9._-]+)/(?P<name>[A-Za-z0-9._-]+?)(?:\.git)?/?$"
)
_REVERTS = re.compile(r"This reverts commit ([0-9a-f]{7,40})")
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
    log = git(clone, "log", ref, "--no-show-signature", "--format=%H%x1f%cI%x1f%s")
    # split("\n"), never splitlines(): a subject may hold U+2028, which splitlines breaks on.
    for line in log.split("\n"):
        if not line:
            continue
        sha, when, subject = line.split("\x1f", 2)
        if not is_cca_fix_subject(subject) or subject.startswith('Revert "'):
            continue
        body = git(clone, "show", "-s", "--format=%B", sha)
        commits.append(Commit(sha, when[:10], subject, body, _changed_files(clone, sha)))
    return commits


def revert_map(clone: Path, ref: str) -> dict[str, list[str]]:
    """Revert commit sha -> the (possibly abbreviated) shas its message says it reverts."""
    log = git(clone, "log", ref, "--no-show-signature", "--format=%H%x1f%B%x1e")
    reverts: dict[str, list[str]] = {}
    for record in log.split("\x1e"):
        sha, _, body = record.strip("\n").partition("\x1f")
        targets = _REVERTS.findall(body)
        if sha and targets:
            reverts[sha.strip()] = targets
    return reverts


def is_undone(sha: str, reverts: dict[str, list[str]], _seen: frozenset = frozenset()) -> bool:
    """True when a revert that is itself still in effect undoes `sha`.

    A revert of a revert puts the fix back, so a revert counts only if nothing undoes it.
    """
    if sha in _seen:
        return False
    return any(
        not is_undone(revert, reverts, _seen | {sha})
        for revert, targets in reverts.items()
        if any(sha.startswith(t) for t in targets)
    )
