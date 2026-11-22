"""The fix audit: the code a run's fixes changed, and checks over it."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .bugs.caused_by import _path, fix_diff

# Placeholder: Task 9's measurement of past fixes (their 90th percentile) sets this.
SIZE_THRESHOLD = 40


class FixAuditRefused(ValueError):
    pass


@dataclass(frozen=True)
class Hunk:
    path: str
    old_start: int
    old_len: int
    new_start: int
    new_len: int
    removed: tuple[str, ...]
    added: tuple[str, ...]

    @property
    def id(self) -> str:
        return f"{self.path}:{self.new_start}"


# caused_by._HUNK drops the new-side start, which a hunk's id needs, so this one keeps all four.
_HUNK = re.compile(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_BINARY = re.compile(r"Binary files (.*) differ")


def parse_fix_diff(diff: str) -> tuple[list[Hunk], list[str]]:
    """The hunks of a `fix_diff` and the paths of binary files, which have no lines to count.

    Hunk bodies are consumed by their line counts, as caused_by._changes does, so a removed
    line that reads `-- x` is never mistaken for a file header.
    """
    hunks: list[Hunk] = []
    skipped: list[str] = []
    path: str | None = None
    head: tuple[int, int, int, int] | None = None
    removed: list[str] = []
    added: list[str] = []
    old_left = new_left = 0

    def close() -> None:
        if head is not None and path is not None:
            hunks.append(Hunk(path, *head, tuple(removed), tuple(added)))

    for line in diff.split("\n"):
        if old_left > 0 or new_left > 0:
            tag = line[:1]
            if tag == "-":
                removed.append(line[1:])
                old_left -= 1
            elif tag == "+":
                added.append(line[1:])
                new_left -= 1
            elif tag in (" ", ""):
                old_left -= 1
                new_left -= 1
            continue  # "\ No newline at end of file" belongs to the line before it
        if line.startswith("--- "):
            close()
            head = None
            path = _path(line[4:], "a/")  # a deleted file has no "+++" path; the old one stands
        elif line.startswith("+++ "):
            path = _path(line[4:], "b/") or path
        elif m := _HUNK.match(line):
            close()
            old_start, old_len, new_start, new_len = m.groups()
            head = (int(old_start), 1 if old_len is None else int(old_len),
                    int(new_start), 1 if new_len is None else int(new_len))
            old_left, new_left = head[1], head[3]
            removed, added = [], []
        elif m := _BINARY.match(line):
            close()
            head = None
            old, _, new = m.group(1).rpartition(" and ")
            skipped.append(_path(new, "b/") or _path(old, "a/") or "")
    close()
    return hunks, skipped


def fix_hunks(repo_root: Path, prefix_tree: str | None, tree: str) -> list[Hunk]:
    """The hunks the fixes made: the diff from the run's prefix tree to `tree`."""
    if prefix_tree is None:
        # The base commit would pull the whole feature into the fix diff.
        raise FixAuditRefused("the run has no prefix_tree, so its fix diff cannot be computed")
    return parse_fix_diff(fix_diff(repo_root, prefix_tree, tree))[0]


def is_test_path(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return (path.startswith("tests/") or (name.startswith("test_") and name.endswith(".py"))
            or name.endswith("_test.py"))


def size_report(hunks: list[Hunk], fixes: list[dict]) -> list[dict]:
    """Per fix, its changed non-test lines; a file listed by k fixes counts 1/k to each."""
    lines: dict[str, int] = {}
    for h in hunks:
        if not is_test_path(h.path):
            lines[h.path] = lines.get(h.path, 0) + len(h.added) + len(h.removed)
    sharing: dict[str, int] = {}
    for fix in fixes:
        for f in set(fix["files"]):
            sharing[f] = sharing.get(f, 0) + 1
    out = []
    for fix in fixes:
        total = sum(lines.get(f, 0) / sharing[f] for f in set(fix["files"]))
        out.append({"fix_id": fix["fix_id"], "lines": total, "over": total > SIZE_THRESHOLD,
                    "has_reason": bool(str(fix.get("size_reason") or "").strip())})
    return out
