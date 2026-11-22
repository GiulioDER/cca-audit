"""The fix audit: the code a run's fixes changed, and checks over it."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .bugs.caused_by import _path, fix_diff
from .bugs.gitread import GitError, git

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
    old_path: str | None = None  # before a rename, so a fix may list either name

    @property
    def id(self) -> str:
        return f"{self.path}:{self.new_start}"


# caused_by._HUNK drops the new-side start, which a hunk's id needs, so this one keeps all four.
_HUNK = re.compile(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_REPLACEMENT = chr(0xFFFD)
_BINARY = re.compile(r"Binary files (.*) differ")


def parse_fix_diff(diff: str) -> tuple[list[Hunk], list[str]]:
    """The hunks of a `fix_diff` and the paths of binary or non-UTF-8 files, which are not counted.

    Hunk bodies are consumed by their line counts, as caused_by._changes does, so a removed
    line that reads `-- x` is never mistaken for a file header.
    """
    hunks: list[Hunk] = []
    skipped: list[str] = []
    path: str | None = None
    old_path: str | None = None
    head: tuple[int, int, int, int] | None = None
    removed: list[str] = []
    added: list[str] = []
    old_left = new_left = 0

    def close() -> None:
        if head is not None and path is not None:
            hunks.append(Hunk(path, *head, tuple(removed), tuple(added), old_path))

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
            old_path = path = _path(line[4:], "a/")  # a deleted file has no "+++" path; the old one stands
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
    # gitread.git decodes with errors="replace", so U+FFFD marks bytes that are not UTF-8. (A file
    # that really contains U+FFFD is skipped too; the cost of reusing caused_by.fix_diff.)
    bad = {h.path for h in hunks if any(_REPLACEMENT in x for x in h.removed + h.added)}
    return [h for h in hunks if h.path not in bad], skipped + sorted(bad)


def fix_hunks(repo_root: Path, prefix_tree: str | None, tree: str) -> tuple[list[Hunk], list[str]]:
    """The hunks the fixes made (the diff from the run's prefix tree to `tree`), and the paths
    of the files skipped because they have no countable lines."""
    if prefix_tree is None:
        # The base commit would pull the whole feature into the fix diff.
        raise FixAuditRefused("the run has no prefix_tree, so its fix diff cannot be computed")
    return parse_fix_diff(fix_diff(repo_root, prefix_tree, tree))


def is_test_path(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return (path.startswith("tests/") or (name.startswith("test_") and name.endswith(".py"))
            or name.endswith("_test.py"))


def size_report(hunks: list[Hunk], fixes: list[dict]) -> list[dict]:
    """Per fix, its changed non-test lines; a file listed by k fixes counts 1/k to each."""
    renamed = {h.old_path: h.path for h in hunks if h.old_path != h.path}

    def files_of(fix: dict) -> set[str]:
        return {renamed.get(f, f) for f in fix["files"]}  # a renamed file is one file

    lines: dict[str, int] = {}
    for h in hunks:
        if not is_test_path(h.path):
            lines[h.path] = lines.get(h.path, 0) + len(h.added) + len(h.removed)
    sharing: dict[str, int] = {}
    for fix in fixes:
        for f in files_of(fix):
            sharing[f] = sharing.get(f, 0) + 1
    out = []
    for fix in fixes:
        total = sum(lines.get(f, 0) / sharing[f] for f in files_of(fix))
        out.append({"fix_id": fix["fix_id"], "lines": total, "over": total > SIZE_THRESHOLD,
                    "has_reason": bool(str(fix.get("size_reason") or "").strip())})
    return out


MAX_HITS_PER_PATTERN = 50
_REGEX_PREFIX = "re:"


def auto_patterns(hunks: list[Hunk]) -> list[str]:
    """Removed non-test lines, whitespace collapsed, that hold a call or a string literal.

    A bare `return None` or `)` would match everywhere, so a line must carry a `(` or a quote.
    """
    out: list[str] = []
    for h in hunks:
        if is_test_path(h.path):
            continue
        for line in h.removed:
            text = " ".join(line.split())
            if ("(" in text or '"' in text or "'" in text) and text not in out:
                out.append(text)
    return out


def _grep(repo_root: Path, pattern: str) -> list[tuple[str, int, str]]:
    """(path, line, text) of every tracked-file line matching `pattern`: a fixed string, or an
    extended regex when it starts with `re:`."""
    mode, body = ("-E", pattern[len(_REGEX_PREFIX):]) if pattern.startswith(_REGEX_PREFIX) else ("-F", pattern)
    try:
        out = git(repo_root, "grep", "-n", mode, "-e", body)
    except GitError as e:
        # git grep exits 1 with no stderr when nothing matches; a bad regex has a message.
        if str(e).rstrip().endswith(f"failed in {repo_root}:"):
            return []
        raise FixAuditRefused(f"sibling pattern {pattern!r} could not be searched: {e}") from e
    hits = []
    for row in out.splitlines():
        path, line, text = row.split(":", 2)
        hits.append((path, int(line), text.strip()))
    return hits


def sibling_hits(repo_root: Path, hunks: list[Hunk], fixes: list[dict]) -> list[dict]:
    """Every other place a fix's patterns occur, so each can be fixed or justified.

    A fix's patterns are its removed lines (auto_patterns) plus its declared `sibling_patterns`,
    fixed strings unless prefixed `re:`. Empty `sibling_patterns` needs a `sibling_reason`.
    A hit inside one of the fix's own hunks (new side) is the fix itself, not a sibling.
    """
    for fix in fixes:
        declared = fix.get("sibling_patterns")
        if declared is None or (not declared and not str(fix.get("sibling_reason") or "").strip()):
            raise FixAuditRefused(
                f"{fix['fix_id']}: give sibling_patterns, or a sibling_reason when there are none")
    renamed = {h.old_path: h.path for h in hunks if h.old_path != h.path}
    out: list[dict] = []
    for fix in fixes:
        files = {renamed.get(f, f) for f in fix["files"]}
        own = [h for h in hunks if h.path in files]
        seen: set[tuple[str, int]] = set()
        for pattern in auto_patterns(own) + list(fix["sibling_patterns"]):
            found = [(path, line, text) for path, line, text in _grep(repo_root, pattern)
                     if not any(h.path == path and h.new_start <= line < h.new_start + h.new_len
                                for h in own)]
            if len(found) > MAX_HITS_PER_PATTERN:
                raise FixAuditRefused(
                    f"{fix['fix_id']}: pattern {pattern!r} matches {len(found)} sites "
                    f"(limit {MAX_HITS_PER_PATTERN}); declare a narrower one")
            for path, line, text in found:
                if (path, line) not in seen:
                    seen.add((path, line))
                    out.append({"hit_id": f"S-{len(out) + 1}", "fix_id": fix["fix_id"],
                                "pattern": pattern, "path": path, "line": line, "text": text})
    return out


def check_dispositions(hits: list[dict], dispositions: dict, repo_root: Path) -> list[str]:
    """Reasons a hit has no valid disposition. `fixed` holds only while the hit's text is gone
    from its path; `not_applicable` needs a reason."""
    reasons = []
    for hit in hits:
        hid = hit["hit_id"]
        entry = dispositions.get(hid)
        if not entry:
            reasons.append(f"{hid}: no disposition")
            continue
        kind = entry.get("disposition")
        if kind == "not_applicable":
            if not str(entry.get("reason") or "").strip():
                reasons.append(f"{hid}: not_applicable needs a reason")
        elif kind == "fixed":
            body = (repo_root / hit["path"]).read_text(encoding="utf-8", errors="replace")
            if hit["text"] in body:
                reasons.append(f"{hid}: marked fixed, but {hit['text']!r} still occurs in {hit['path']}")
        else:
            reasons.append(f"{hid}: disposition must be 'fixed' or 'not_applicable', not {kind!r}")
    return reasons
