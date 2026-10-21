"""Regression edges: which earlier CCA fix introduced the lines a fix had to change.

Derived deterministically, never by a model: `git blame` at the base commit on every line the
fix modified or removed gives the commits that introduced them, and a ledger row of the same
repository whose `fix_commit` is one of those commits is a cause. A pure insertion changes no
existing line, so it blames nothing.

Blame needs the checkout, so the run computes the commits (`introducing_commits`) and carries
them in its record; the mapping to bug ids (`bugs_for_commits`) needs the ledger and runs
where the ledger is. `caused_by_for_fix` is both, for a caller that has both.

A fix merged with a merge commit rather than a squash keeps its own commits on the branch, so
blame names those and not the ledger's merge sha: such a cause is missed, never invented.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from .gitread import GitError, git, remote_repo
from .model import BugRow
from .records import one_line

_HUNK = re.compile(r"@@ -(\d+)(?:,(\d+))? \+\d+(?:,(\d+))? @@")
_PORCELAIN = re.compile(r"([0-9a-f]{40}) \d+ \d+(?: \d+)?")


def _path(header: str) -> str | None:
    """The path in a `--- ` header, without git's `a/` prefix; None for /dev/null."""
    name = header.split("\t", 1)[0]
    if name.startswith('"') and name.endswith('"'):
        # git quotes a path with special characters, C style, with octal UTF-8 bytes.
        raw = name[1:-1].encode("latin-1", "backslashreplace").decode("unicode_escape")
        name = raw.encode("latin-1").decode("utf-8", errors="replace")
    if name == "/dev/null":
        return None
    return name[2:] if name.startswith("a/") else name


def changed_old_lines(fix_diff: str) -> dict[str, list[int]]:
    """For each file, the base-side line numbers the diff modifies or removes, in order.

    Hunk bodies are consumed by their line counts, so a removed line whose text starts with
    `-- ` (an SQL comment, say) is never taken for a file header.
    """
    out: dict[str, list[int]] = {}
    path: str | None = None
    old = old_left = new_left = 0
    for line in fix_diff.split("\n"):
        if old_left > 0 or new_left > 0:
            tag = line[:1]
            if tag == "-":
                if path is not None:
                    out.setdefault(path, []).append(old)
                old += 1
                old_left -= 1
            elif tag == "+":
                new_left -= 1
            elif tag == " ":
                old += 1
                old_left -= 1
                new_left -= 1
            # "\ No newline at end of file" belongs to the line before it.
            continue
        if line.startswith("--- "):
            path = _path(line[4:])
            continue
        m = _HUNK.match(line)
        if m:
            old = int(m.group(1))
            old_left = int(m.group(2)) if m.group(2) is not None else 1
            new_left = int(m.group(3)) if m.group(3) is not None else 1
            if old_left == 0:
                old += 1  # "-n,0": the insertion goes after line n, and nothing is removed
    return out


def _ranges(lines: list[int]) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    for n in sorted(set(lines)):
        if ranges and n == ranges[-1][1] + 1:
            ranges[-1] = (ranges[-1][0], n)
        else:
            ranges.append((n, n))
    return ranges


def introducing_commits(repo_root: Path, base_commit: str, fix_diff: str) -> list[str]:
    """The commits that introduced, as of `base_commit`, the lines `fix_diff` changes.

    Sorted and unique. Raises GitError when blame fails (a path not in the base, say).
    """
    shas: set[str] = set()
    for path, lines in changed_old_lines(fix_diff).items():
        spans = [arg for a, b in _ranges(lines) for arg in ("-L", f"{a},{b}")]
        out = git(Path(repo_root), "blame", "--porcelain", *spans, base_commit, "--", path)
        for line in out.split("\n"):
            m = _PORCELAIN.fullmatch(line)
            if m:
                shas.add(m.group(1))
    return sorted(shas)


def _same_repo(a: str | None, b: str | None) -> bool:
    # GitHub owner and repository names are case-insensitive.
    return bool(a) and bool(b) and str(a).lower() == str(b).lower()


def bugs_for_commits(commits: list[str], repo: str | None, ledger: list[BugRow]) -> list[str]:
    """bug_ids of `repo`'s ledger rows fixed by one of `commits`, sorted and unique."""
    wanted = set(commits)
    return sorted({row.bug_id for row in ledger
                   if _same_repo(row.repo, repo) and row.fix_commit in wanted})


def caused_by_for_fix(repo_root: Path, base_commit: str, fix_diff: str, ledger: list[BugRow],
                      repo: str | None = None) -> list[str]:
    """The bug_ids of earlier CCA fixes in this repository whose lines this fix changed.

    `repo` defaults to `repo_root`'s GitHub origin; with neither, no row can be matched.
    """
    repo = repo or remote_repo(Path(repo_root))
    if repo is None:
        return []
    return bugs_for_commits(introducing_commits(repo_root, base_commit, fix_diff), repo, ledger)


def fix_diff(repo_root: Path, base: str, head: str, files: list[str] | None = None) -> str:
    """`git diff base head`, with the prefixes this module parses whatever the user's config."""
    args = ["diff", "--no-color", "--no-ext-diff", "--src-prefix=a/", "--dst-prefix=b/",
            base, head]
    return git(Path(repo_root), *args, *(["--", *files] if files else []))


@dataclass
class BackfillReport:
    # bug_id -> caused_by, for every row that gained one.
    changed: dict[str, list[str]] = field(default_factory=dict)
    # "bug_id: reason" for rows of this repository whose fix could not be blamed.
    failed: list[str] = field(default_factory=list)


def backfill_caused_by(root: Path, repo_root: Path, *, write: bool) -> BackfillReport:
    """Set caused_by on this repository's ledger rows that have none, from their fix commits.

    Each fix is blamed against its first parent. Only a row whose caused_by is empty and
    whose computed one is not is changed, and only that line is rewritten: every other line
    keeps its bytes. Dry run unless `write`; with it, the old ledger is kept beside the new
    one as `bugs.jsonl.before-caused-by`.
    """
    from .store import LEDGER

    report = BackfillReport()
    repo = remote_repo(Path(repo_root))
    ledger = root / LEDGER
    if repo is None or not ledger.exists():
        return report
    lines = ledger.read_text(encoding="utf-8").split("\n")
    parsed: list[tuple[int, dict, BugRow]] = []
    for i, line in enumerate(lines):
        try:
            value = json.loads(line)
            if value.get("kind", "bug") != "bug":
                continue
            parsed.append((i, value, BugRow.from_json(value)))
        except (ValueError, KeyError, TypeError, AttributeError):
            continue
    rows = [row for _, _, row in parsed]
    for i, value, row in parsed:
        if row.caused_by or not _same_repo(row.repo, repo):
            continue
        try:
            parent = git(Path(repo_root), "rev-parse", f"{row.fix_commit}^").strip()
            diff = fix_diff(repo_root, parent, row.fix_commit)
            causes = bugs_for_commits(introducing_commits(repo_root, parent, diff), repo, rows)
        except GitError as exc:
            report.failed.append(f"{row.bug_id}: {exc}")
            continue
        causes = [c for c in causes if c != row.bug_id]
        if causes:
            report.changed[row.bug_id] = causes
            lines[i] = one_line(json.dumps({**value, "caused_by": causes},
                                           ensure_ascii=False, sort_keys=True))
    if write and report.changed:
        backup = ledger.with_name(f"{ledger.name}.before-caused-by")
        backup.write_bytes(ledger.read_bytes())
        tmp = ledger.with_name(f".tmp-{ledger.name}-{os.getpid()}")
        tmp.write_text("\n".join(lines), encoding="utf-8", newline="\n")
        os.replace(tmp, ledger)
    return report
