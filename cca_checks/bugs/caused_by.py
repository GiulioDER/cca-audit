"""Regression edges: which earlier CCA fix introduced the lines a fix had to change.

Derived deterministically, never by a model: `git blame` at the base commit on every line the
fix modified or removed gives the commits that introduced them, and a ledger row of the same
repository whose `fix_commit` is one of those commits is a cause. A pure insertion changes no
existing line, so it blames nothing.

Deterministic means the host's git configuration cannot change the answer: every diff and blame
here pins the settings that would (blank-line suppression, prefixes, path quoting, rename
detection, the diff algorithm, ignored revisions).

Blame needs the checkout, so the run computes the commits (`introducing_commits`) and carries
them in its record; the mapping to bug ids (`bugs_for_commits`) needs the ledger and runs
where the ledger is. `caused_by_for_fix` is both, for a caller that has both.

A fix merged with a merge commit rather than a squash keeps its own commits on the branch, so
blame names those and not the ledger's merge sha: such a cause is missed, never invented. The
forward path has one diff per run, not per fix, so two findings fixed in the same file share
that file's causes.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .gitread import GitError, git, remote_repo
from .model import BugRow
from .records import one_line

_HUNK = re.compile(r"@@ -(\d+)(?:,(\d+))? \+\d+(?:,(\d+))? @@")
_PORCELAIN = re.compile(r"([0-9a-f]{40}) \d+ \d+(?: \d+)?")
# Settings that change what `git diff` prints, pinned for every call.
_PINNED = ("-c", "diff.suppressBlankEmpty=false", "-c", "core.quotePath=true",
           "-c", "diff.noprefix=false", "-c", "diff.mnemonicPrefix=false",
           "-c", "diff.relative=false")


def _path(header: str, prefix: str) -> str | None:
    """The path in a `--- ` or `+++ ` header, without git's prefix; None for /dev/null."""
    name = header.split("\t", 1)[0]
    if name.startswith('"') and name.endswith('"'):
        # core.quotePath=true: C-style quoting, non-ASCII as octal UTF-8 bytes, so ASCII only.
        raw = name[1:-1].encode("ascii", "backslashreplace").decode("unicode_escape")
        name = raw.encode("latin-1").decode("utf-8", errors="replace")
    if name == "/dev/null":
        return None
    return name[len(prefix):] if name.startswith(prefix) else name


@dataclass
class _Change:
    old: str | None
    new: str | None
    lines: list[int] = field(default_factory=list)


def _changes(fix_diff: str) -> list[_Change]:
    """Each file of the diff with its old and new path and the old lines it modifies or removes.

    Hunk bodies are consumed by their line counts, so a removed line whose text starts with
    `-- ` (an SQL comment, say) is never taken for a file header. An empty line inside a hunk
    is a blank context line, as `diff.suppressBlankEmpty` prints it.
    """
    out: list[_Change] = []
    current: _Change | None = None
    old = old_left = new_left = 0
    for line in fix_diff.split("\n"):
        if old_left > 0 or new_left > 0:
            tag = line[:1]
            if tag == "-":
                if current is not None:
                    current.lines.append(old)
                old += 1
                old_left -= 1
            elif tag == "+":
                new_left -= 1
            elif tag in (" ", ""):
                old += 1
                old_left -= 1
                new_left -= 1
            # "\ No newline at end of file" belongs to the line before it.
            continue
        if line.startswith("--- "):
            current = _Change(_path(line[4:], "a/"), None)
            out.append(current)
            continue
        if line.startswith("+++ ") and current is not None:
            current.new = _path(line[4:], "b/")
            continue
        m = _HUNK.match(line)
        if m:
            old = int(m.group(1))
            old_left = int(m.group(2)) if m.group(2) is not None else 1
            new_left = int(m.group(3)) if m.group(3) is not None else 1
            if old_left == 0:
                old += 1  # "-n,0": the insertion goes after line n, and nothing is removed
    return out


def changed_old_lines(fix_diff: str) -> dict[str, list[int]]:
    """For each base-side file, the line numbers the diff modifies or removes, in order."""
    out: dict[str, list[int]] = {}
    for change in _changes(fix_diff):
        if change.old is not None and change.lines:
            out.setdefault(change.old, []).extend(change.lines)
    return out


def _ranges(lines: list[int]) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    for n in sorted(set(lines)):
        if ranges and n == ranges[-1][1] + 1:
            ranges[-1] = (ranges[-1][0], n)
        else:
            ranges.append((n, n))
    return ranges


def _norm(path: str) -> str:
    path = path.strip().replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    return path


def introducing_commits(repo_root: Path, base_commit: str, fix_diff: str,
                        files: list[str] | None = None) -> list[str]:
    """The commits that introduced, as of `base_commit`, the lines `fix_diff` changes.

    With `files`, only the diff's files whose old or new path is one of them count, so a fix
    that renamed a file is found by either name. Sorted and unique. Raises GitError when blame
    fails (a path not in the base, say).
    """
    keep = {_norm(f) for f in files} if files else None
    lines: dict[str, list[int]] = {}
    for change in _changes(fix_diff):
        if change.old is None or not change.lines:
            continue
        if keep is not None and change.old not in keep and change.new not in keep:
            continue
        lines.setdefault(change.old, []).extend(change.lines)
    shas: set[str] = set()
    for path, numbers in lines.items():
        spans = [arg for a, b in _ranges(numbers) for arg in ("-L", f"{a},{b}")]
        # An empty --ignore-revs-file clears blame.ignoreRevsFile from the user's config.
        out = git(Path(repo_root), "blame", "--porcelain", "--ignore-revs-file", "", *spans,
                  base_commit, "--", path)
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


def fix_diff(repo_root: Path, base: str, head: str) -> str:
    """`git diff base head` in the one form `_changes` parses, whatever the host's config.

    The whole range, never a pathspec: a pathspec hides the other side of a rename.
    """
    return git(Path(repo_root), *_PINNED, "diff", "--no-color", "--no-ext-diff",
               "--no-textconv", "--find-renames", "--diff-algorithm=myers",
               "--src-prefix=a/", "--dst-prefix=b/", base, head)


@dataclass
class BackfillReport:
    # bug_id -> caused_by, for every row that gained one.
    changed: dict[str, list[str]] = field(default_factory=dict)
    # "bug_id: reason" for rows of this repository whose fix could not be blamed, and any
    # reason the ledger was not written.
    failed: list[str] = field(default_factory=list)


def backfill_caused_by(root: Path, repo_root: Path, *, write: bool) -> BackfillReport:
    """Set caused_by on this repository's ledger rows that have none, from their fix commits.

    Each fix is blamed against its first parent, on the row's own files. Only a row whose
    caused_by is empty and whose computed one is not is changed, and only that line is
    rewritten: every other line keeps its bytes. Dry run unless `write`; with it, the ledger as
    it was is kept beside it as `bugs.jsonl.before-caused-by-<UTC stamp>`, and rows appended
    while the blame ran (by `resolve --write`) are kept. If the ledger changed in any other
    way meanwhile, nothing is written.
    """
    from .store import LEDGER

    report = BackfillReport()
    repo = remote_repo(Path(repo_root))
    ledger = root / LEDGER
    if repo is None or not ledger.exists():
        return report
    original = ledger.read_text(encoding="utf-8")
    lines = original.split("\n")
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
    diffs: dict[str, tuple[str, str]] = {}
    for i, value, row in parsed:
        if row.caused_by or not _same_repo(row.repo, repo):
            continue
        try:
            if row.fix_commit not in diffs:
                parent = git(Path(repo_root), "rev-parse", f"{row.fix_commit}^").strip()
                diffs[row.fix_commit] = (parent, fix_diff(repo_root, parent, row.fix_commit))
            parent, diff = diffs[row.fix_commit]
            commits = introducing_commits(repo_root, parent, diff, row.files or None)
        except (GitError, UnicodeError) as exc:
            report.failed.append(f"{row.bug_id}: {exc}")
            continue
        causes = [c for c in bugs_for_commits(commits, repo, rows) if c != row.bug_id]
        if causes:
            report.changed[row.bug_id] = causes
            lines[i] = one_line(json.dumps({**value, "caused_by": causes},
                                           ensure_ascii=False, sort_keys=True))
    if write and report.changed:
        now = ledger.read_text(encoding="utf-8")
        if not now.startswith(original):
            report.failed.append("the ledger changed other than by appending while the "
                                 "back-fill ran; nothing was written")
            return report
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        ledger.with_name(f"{ledger.name}.before-caused-by-{stamp}").write_text(
            now, encoding="utf-8", newline="\n")
        tmp = ledger.with_name(f".tmp-{ledger.name}-{os.getpid()}")
        tmp.write_text("\n".join(lines) + now[len(original):], encoding="utf-8", newline="\n")
        os.replace(tmp, ledger)
    return report
