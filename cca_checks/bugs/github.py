"""Did the pull request that holds a run's commit merge into the default branch?

Asked of GitHub through the `gh` CLI, never a library. GitHub keeps a pull request's
commits after a squash merge, so a commit that never reached the default branch still
names the PR that did, and that PR names the squash commit the ledger keys rows by.
A record without a repository is matched by a pull request search for the sha, limited
to the allowed owners.

What GitHub returns is untrusted: the repository name and the merge sha end up in a
ledger row key and a memo filename, so both are checked before a Merge is built.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .forward import Merge

_SHA = re.compile(r"[0-9a-f]{40}")
_REPO = re.compile(r"(?!\.+/)[A-Za-z0-9_.-]+/(?!\.+$)[A-Za-z0-9_.-]+")
ABANDON_AFTER = timedelta(hours=24)


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


def _search_repos(commit: str, owners: list[str], run: Runner) -> list[tuple[str, set[int]]]:
    """Every allowed-owner repository with a pull request matching the sha, in order,
    each with the numbers of its matching pull requests.

    GitHub's commit search covers only default branches, which a squash merge never puts
    the run's own commit on; the pull request search matches a sha on any PR's commits,
    but also on a PR that only mentions it, so no single hit is trusted to be the one.
    """
    found_in: dict[str, set[int]] = {}
    for owner in owners:
        found = json.loads(run(["api", "-X", "GET", "search/issues",
                                "-f", f"q={commit} is:pr user:{owner}"]))
        for item in found.get("items") or []:
            url = item.get("repository_url") if isinstance(item, dict) else None
            if not isinstance(url, str) or "/repos/" not in url:
                continue
            name = url.split("/repos/", 1)[1]
            if name.split("/", 1)[0].lower() != owner:
                continue
            numbers = found_in.setdefault(name, set())  # checked against _REPO later
            number = item.get("number")
            if isinstance(number, int) and not isinstance(number, bool):
                numbers.add(number)
    return list(found_in.items())


def _when(text) -> datetime | None:
    try:
        return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _commit_missing(exc: GhError) -> bool:
    # gh reports GitHub's answer for a sha it never received as "No commit found for
    # SHA" (HTTP 422); a 404 on this one call means the same.
    text = str(exc)
    return any(mark in text for mark in ("No commit found", "HTTP 422", "HTTP 404"))


Answer = Merge | Waiting | Abandoned | Unknown


def _merged_before(pull: dict, moment: datetime) -> bool:
    merged = _when(pull.get("merged_at"))
    return merged is not None and merged < moment


def _judge(repo: str, commit: str, hits: set[int] | None, run: Runner,
           not_before: datetime | None, now: datetime) -> Answer | None:
    """One repository's answer; None for a searched one that does not hold the commit.

    `hits` is None for the repository a record names itself, and otherwise the numbers
    of the searched pull requests in it: a repository whose commit pulls list none of
    them only mentioned the sha, so it decides nothing.
    """
    if not _REPO.fullmatch(repo):
        return Unknown(f"unusable repository name from GitHub: {repo!r}")
    default = json.loads(run(["api", f"repos/{repo}"]))["default_branch"]
    try:
        pulls = json.loads(run(["api", f"repos/{repo}/commits/{commit}/pulls"]))
    except GhError as exc:
        if not _commit_missing(exc):
            raise
        return Waiting("GitHub has not received the commit") if hits is None else None
    if hits is not None and not any(p.get("number") in hits for p in pulls):
        return None
    if not_before is not None:
        early = [p for p in pulls if _merged_before(p, not_before)]
        pulls = [p for p in pulls if p not in early]
        if early and not pulls:
            return Waiting("only pull requests merged before the run closed list "
                           "the commit")
    for p in pulls:
        if p.get("merged_at") and (p.get("base") or {}).get("ref") == default:
            sha = p.get("merge_commit_sha")
            if not isinstance(sha, str) or not _SHA.fullmatch(sha):
                return Unknown(f"unusable merge commit sha on #{p.get('number')}: {sha!r}")
            return Merge(repo, sha, p["merged_at"][:10], p["number"])
    if not pulls:
        return Waiting("no pull request lists the commit")
    closed = [p for p in pulls if p.get("state") == "closed" and not p.get("merged_at")]
    if len(closed) == len(pulls):
        # A PR closed and reopened within the hour must not be abandoned for good.
        settled = all((_when(p.get("closed_at")) or now) <= now - ABANDON_AFTER
                      for p in closed)
        if settled:
            return Abandoned(f"{repo} closed without merging: "
                             + ", ".join(f"#{p['number']}" for p in pulls))
        return Waiting("closed without merging less than 24 hours ago")
    return Waiting("pull request not merged into " + default)


def lookup(repo: str | None, commit: str, run: Runner = gh, *,
           not_before: datetime | None = None, now: datetime | None = None) -> Answer:
    """Merge, Waiting, Abandoned or Unknown for the pull request holding `commit`.

    `not_before` is when the run closed: a PR merged earlier cannot hold this run's work
    (the run closed at a commit already on master), so it is ignored. Without a repo,
    every searched repository is judged: the first merge wins, and the record is
    abandoned only when every repository that holds the commit abandoned it, so a PR
    that a stranger opened and closed in another repository cannot end the record.
    """
    if not isinstance(commit, str) or not _SHA.fullmatch(commit):
        return Unknown(f"unusable commit sha: {commit!r}")
    now = now or datetime.now(timezone.utc)
    try:
        if repo is not None:
            if not isinstance(repo, str) or not _REPO.fullmatch(repo):
                return Unknown(f"unusable repository name from GitHub: {repo!r}")
            owners = _owners(run)
            if repo.split("/", 1)[0].lower() not in owners:
                return Unknown(f"repository {repo} is not owned by an allowed owner ("
                               + ", ".join(owners) + ")")
            got = _judge(repo, commit, None, run, not_before, now)
            # Without hits _judge never skips; the fallback only satisfies the type.
            return got if got is not None else Waiting("no pull request lists the commit")
        owners = _owners(run)
        candidates = _search_repos(commit, owners, run)
        if not candidates:
            return Waiting("no pull request in a repository owned by "
                           + ", ".join(owners) + " holds the commit yet")
        judged: list[Waiting | Abandoned] = []
        for name, hits in candidates:
            got = _judge(name, commit, hits, run, not_before, now)
            if isinstance(got, (Merge, Unknown)):
                return got
            if got is not None:
                judged.append(got)
        if not judged:
            return Waiting("no searched repository holds the commit")
        if all(isinstance(g, Abandoned) for g in judged):
            return Abandoned("; ".join(g.reason for g in judged))
        return next(g for g in judged if isinstance(g, Waiting))
    except (GhError, ValueError, KeyError, TypeError, AttributeError) as exc:
        return Unknown(str(exc))
