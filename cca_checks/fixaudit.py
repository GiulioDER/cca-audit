"""The fix audit: the code a run's fixes changed, and checks over it."""
from __future__ import annotations

import hashlib
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
    added_at: tuple[int, ...] = ()  # new-side line numbers of the added lines
    # (new-side line number, text) of the context line above a block that only adds lines: where
    # a new guard attaches, so the sibling sweep can search for what that line calls
    anchors: tuple[tuple[int, str], ...] = ()

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
    added_at: list[int] = []
    anchors: list[tuple[int, str]] = []
    old_left = new_left = 0
    new_no = 0
    ctx: tuple[int, str] | None = None  # the last context line
    run_anchor: tuple[int, str] | None = None  # the context line above the current +/- run
    run_added = run_removed = 0

    def end_run() -> None:
        if run_anchor is not None and run_added and not run_removed:
            anchors.append(run_anchor)

    def close() -> None:
        end_run()
        if head is not None and path is not None:
            hunks.append(Hunk(path, *head, tuple(removed), tuple(added), old_path,
                              tuple(added_at), tuple(anchors)))

    for line in diff.split("\n"):
        if old_left > 0 or new_left > 0:
            tag = line[:1]
            if tag in ("-", "+") and not (run_added or run_removed):
                run_anchor = ctx
            if tag == "-":
                removed.append(line[1:])
                old_left -= 1
                run_removed += 1
            elif tag == "+":
                added.append(line[1:])
                added_at.append(new_no)
                new_no += 1
                new_left -= 1
                run_added += 1
            elif tag in (" ", ""):
                end_run()
                run_added = run_removed = 0
                ctx = (new_no, line[1:])
                new_no += 1
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
            removed, added, added_at, anchors = [], [], [], []
            new_no, ctx, run_anchor, run_added, run_removed = head[2], None, None, 0, 0
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
_NOT_CALLS = {"if", "elif", "while", "for", "in", "and", "or", "not", "return"}
_TOKEN = re.compile(r'"[^"\\\n]*"|\'[^\'\\\n]*\'|\w+|[^\w\s]')


def _call_heads(line: str) -> list[str]:
    """Each call on `line` whose first argument holds a string literal, as `callee(first-arg`.

    `exe = resolve_tool("git", os.getcwd())` gives `resolve_tool("git"`. A dotted prefix is
    dropped (`ledger.read_text(...)` gives `read_text(...`), so a sibling on another object
    still matches. The head, not the whole line, is the pattern: a sibling rarely repeats the
    rest of the line.
    """
    heads = []
    for m in re.finditer(r"([\w.]*)\(", line):
        callee = m.group(1).rsplit(".", 1)[-1]
        if not callee or callee in _NOT_CALLS:
            continue
        depth, quote, end = 0, "", len(line)
        for k in range(m.end(), len(line)):
            c = line[k]
            if quote:
                quote = "" if c == quote else quote
            elif c in "\"'":
                quote = c
            elif depth == 0 and (c in ")]}" or c == ","):
                end = k
                break
            elif c in "([{":
                depth += 1
            elif c in ")]}":
                depth -= 1
        # whitespace outside string literals is collapsed; inside one it is part of the value
        arg = re.sub(r"(\"[^\"]*\"|'[^']*')|\s+", lambda x: x.group(1) or " ",
                     line[m.end():end]).strip()
        if '"' in arg or "'" in arg:
            heads.append(f"{callee}({arg}")
    return heads


def auto_patterns(hunks: list[Hunk]) -> list[str]:
    """The call heads of the removed lines of non-test hunks, and of the context line above a
    block that only adds lines (where a new guard attaches). Deduplicated, in order."""
    out: list[str] = []
    for h in hunks:
        if is_test_path(h.path):
            continue
        for line in (*h.removed, *(text for _, text in h.anchors)):
            for head in _call_heads(line):
                if head not in out:
                    out.append(head)
    return out


def _tolerant(text: str) -> str:
    """An extended regex matching `text` literally, with any run of spaces where it has tokens
    apart (so a reformatted or double spaced copy still matches). A leading word is not matched
    inside a longer name (`load(` is not `json_load(`), though a dotted prefix is allowed."""
    parts: list[str] = []
    tokens = _TOKEN.findall(text)
    for prev, tok in zip([None, *tokens], tokens):
        if prev is not None:
            word = prev[-1].isalnum() or prev[-1] == "_"
            parts.append("[[:space:]]+" if word and (tok[0].isalnum() or tok[0] == "_")
                         else "[[:space:]]*")
        parts.append(re.sub(r"([\\.^$*+?()\[\]{}|])", r"\\\1", tok))
    body = "".join(parts)
    if tokens and (tokens[0][0].isalnum() or tokens[0][0] == "_"):
        return "(^|[^[:alnum:]_])" + body
    return body


def _grep(repo_root: Path, pattern: str) -> list[tuple[str, int, str, int]]:
    """(path, line, text, n) of every tracked text file line matching `pattern`: a fixed string
    (whitespace tolerant), or an extended regex when it starts with `re:`. `n` counts the
    identical stripped texts earlier in the same path, which tells two copies of a line apart."""
    body = pattern[len(_REGEX_PREFIX):] if pattern.startswith(_REGEX_PREFIX) else _tolerant(pattern)
    try:
        out = git(repo_root, "grep", "-n", "-I", "-z", "-E", "-e", body)
    except GitError as e:
        # git grep exits 1 with no stderr when nothing matches; a bad regex has a message.
        if str(e).rstrip().endswith(f"failed in {repo_root}:"):
            return []
        raise FixAuditRefused(f"sibling pattern {pattern!r} could not be searched: {e}") from e
    hits = []
    seen: dict[tuple[str, str], int] = {}
    for row in out.split("\n"):  # not splitlines(): a form feed inside a matched line is no row end
        if not row:
            continue
        path, line, text = row.split("\0", 2)
        text = text.strip()
        hits.append((path, int(line), text, seen.get((path, text), 0)))
        seen[(path, text)] = seen.get((path, text), 0) + 1
    return hits


@dataclass(frozen=True)
class SiblingSweep:
    hits: list[dict]
    # auto patterns that matched more than MAX_HITS_PER_PATTERN sites and were dropped:
    # [{"fix_id", "pattern", "count"}]. Too common to say anything about one fix.
    too_broad: list[dict]


def _hit_id(fix_id: str, pattern: str, path: str, text: str, n: int) -> str:
    """Stable across runs of the sweep, so a disposition written once keeps its hit; `n` keeps
    two identical lines of one file apart."""
    key = "\0".join((fix_id, pattern, path, text, str(n)))
    return "S-" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:10]


def sibling_hits(repo_root: Path, hunks: list[Hunk], fixes: list[dict]) -> SiblingSweep:
    """Every other place a fix's patterns occur, so each can be fixed or justified.

    A fix's patterns are the call heads of its changes (auto_patterns) plus its declared
    `sibling_patterns`: fixed strings, or a regex when prefixed `re:`. Empty `sibling_patterns`
    needs a `sibling_reason`. A site on a line the fixes added is not a hit (it was edited), nor
    is the anchor line a guard was attached to. A declared pattern over MAX_HITS_PER_PATTERN
    hits is refused; an auto pattern over it is dropped into `too_broad`.
    """
    for fix in fixes:
        declared = fix.get("sibling_patterns")
        if declared is None or (not declared and not str(fix.get("sibling_reason") or "").strip()):
            raise FixAuditRefused(
                f"{fix['fix_id']}: give sibling_patterns, or a sibling_reason when there are none")
    edited = {(h.path, n) for h in hunks for n in h.added_at}
    renamed = {h.old_path: h.path for h in hunks if h.old_path != h.path}
    out: list[dict] = []
    too_broad: list[dict] = []
    for fix in fixes:
        files = {renamed.get(f, f) for f in fix["files"]}
        own = [h for h in hunks if h.path in files]
        skip = edited | {(h.path, n) for h in own for n, _ in h.anchors}
        auto = auto_patterns(own)
        seen: set[tuple[str, int]] = set()
        for pattern in auto + [d for d in fix["sibling_patterns"] if d not in auto]:
            found = [x for x in _grep(repo_root, pattern) if x[:2] not in skip]
            if len(found) > MAX_HITS_PER_PATTERN:
                if pattern in auto and pattern not in fix["sibling_patterns"]:
                    too_broad.append({"fix_id": fix["fix_id"], "pattern": pattern,
                                      "count": len(found)})
                    continue
                raise FixAuditRefused(
                    f"{fix['fix_id']}: pattern {pattern!r} matches {len(found)} sites "
                    f"(limit {MAX_HITS_PER_PATTERN}); declare a narrower one")
            for path, line, text, n in found:
                if (path, line) not in seen:
                    seen.add((path, line))
                    out.append({"hit_id": _hit_id(fix["fix_id"], pattern, path, text, n),
                                "fix_id": fix["fix_id"], "pattern": pattern, "path": path,
                                "line": line, "text": text})
    return SiblingSweep(out, too_broad)


def check_dispositions(hits: list[dict], dispositions: dict, repo_root: Path) -> list[str]:
    """Reasons a hit has no valid disposition. A hit still listed is a site no fix edited, so
    only `not_applicable` with a reason can clear it; `fixed` is rejected. `repo_root` is kept
    for the callers' signature and is not read."""
    reasons = []
    for hit in hits:
        hid = hit["hit_id"]
        entry = dispositions.get(hid)
        if not entry:
            reasons.append(f"{hid}: no disposition")
            continue
        kind = entry.get("disposition")
        if kind == "fixed":
            reasons.append(f"{hid}: marked fixed, but {hit['path']}:{hit['line']} is unedited; "
                           "edit it, or mark it not_applicable with a reason")
        elif kind != "not_applicable":
            reasons.append(f"{hid}: disposition must be 'not_applicable', not {kind!r}")
        elif not str(entry.get("reason") or "").strip():
            reasons.append(f"{hid}: not_applicable needs a reason")
    return reasons
