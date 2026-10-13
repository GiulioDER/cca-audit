"""Did the pull request that holds a run's commit merge into the default branch?

Asked of GitHub through the `gh` CLI, never a library. GitHub keeps a pull request's
commits after a squash merge, so a commit that never reached the default branch still
names the PR that did, and that PR names the squash commit the ledger keys rows by.

What GitHub returns is untrusted: the repository name and the merge sha end up in a
ledger row key and a memo filename, so both are checked before a Merge is built.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from typing import Callable

from .forward import Merge

_SHA = re.compile(r"[0-9a-f]{40}")
_REPO = re.compile(r"(?!\.+/)[A-Za-z0-9_.-]+/(?!\.+$)[A-Za-z0-9_.-]+")


class GhError(Exception):
    pass


@dataclass(frozen=True)
class Waiting:
    reason: str


@dataclass(frozen=True)
class Abandoned:
    reason: str


@dataclass(frozen=True)
class Unknown:
    reason: str


Runner = Callable[[list[str]], str]


def gh(args: list[str]) -> str:
    try:
        out = subprocess.run(["gh", *args], capture_output=True, text=True,
                             encoding="utf-8", timeout=60)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise GhError(f"gh could not run: {exc}") from exc
    if out.returncode != 0:
        first = (out.stderr.strip().splitlines() or ["no message"])[0]
        raise GhError(f"gh {' '.join(args[:2])} exited {out.returncode}: {first}")
    return out.stdout


def _owners(run: Runner) -> list[str]:
    """Owners whose repositories a searched commit may be attributed to."""
    raw = os.environ.get("CCA_BUGS_OWNERS", "")
    owners = [o.strip().lower() for o in raw.split(",") if o.strip()]
    if owners:
        return owners
    login = json.loads(run(["api", "user"]))["login"]
    if not isinstance(login, str) or not login:
        raise ValueError("gh api user returned no login")
    return [login.lower()]


def lookup(repo: str | None, commit: str, run: Runner = gh):
    if not isinstance(commit, str) or not _SHA.fullmatch(commit):
        return Unknown(f"unusable commit sha: {commit!r}")
    try:
        if repo is None:
            items = json.loads(run(["api", "-X", "GET", "search/commits",
                                    "-f", f"q=hash:{commit}"])).get("items") or []
            owners = _owners(run)
            names = [(i.get("repository") or {}).get("full_name") for i in items]
            repo = next((n for n in names if isinstance(n, str) and "/" in n
                         and n.split("/", 1)[0].lower() in owners), None)
            if repo is None:
                return Waiting("no repository owned by " + ", ".join(owners)
                               + " holds the commit yet")
        if not isinstance(repo, str) or not _REPO.fullmatch(repo):
            return Unknown(f"unusable repository name from GitHub: {repo!r}")
        default = json.loads(run(["api", f"repos/{repo}"]))["default_branch"]
        pulls = json.loads(run(["api", f"repos/{repo}/commits/{commit}/pulls"]))
        for p in pulls:
            if p.get("merged_at") and (p.get("base") or {}).get("ref") == default:
                sha = p.get("merge_commit_sha")
                if not isinstance(sha, str) or not _SHA.fullmatch(sha):
                    return Unknown(f"unusable merge commit sha on #{p.get('number')}: {sha!r}")
                return Merge(repo, sha, p["merged_at"][:10], p["number"])
        if not pulls:
            return Waiting("no pull request lists the commit")
        if all(p.get("state") == "closed" and not p.get("merged_at") for p in pulls):
            return Abandoned("closed without merging: "
                             + ", ".join(f"#{p['number']}" for p in pulls))
    except (GhError, ValueError, KeyError, TypeError, AttributeError) as exc:
        return Unknown(str(exc))
    return Waiting("pull request not merged into " + default)
