"""The fix audit: the code a run's fixes changed, and checks over it."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath

from . import layer_outputs, pipeline_state
from .bugs.caused_by import _path, fix_diff
from .bugs.gitread import GitError, git

# 90th percentile of past fix sizes (139.4 non-test lines per fix, n=198), rounded up; see
# docs/preregistrations/2026-11-22-fix-audit-loop.md, Result (2026-11-22).
SIZE_THRESHOLD = 140


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
            # a deleted file has no "+++" path; the old one stands
            old_path = path = _path(line[4:], "a/")
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


def _files_of(hunks: list[Hunk], fix: dict) -> set[str]:
    """The files a fix lists, a renamed file under its new path (it is one file)."""
    renamed = {h.old_path: h.path for h in hunks if h.old_path != h.path}
    return {renamed.get(f) or f for f in fix["files"]}


def size_report(hunks: list[Hunk], fixes: list[dict]) -> list[dict]:
    """Per fix, its changed non-test lines; a file listed by k fixes counts 1/k to each."""
    def files_of(fix: dict) -> set[str]:
        return _files_of(hunks, fix)

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
    # one longer than `tokens` on purpose: each token is paired with the one before it
    for prev, tok in zip([None, *tokens], tokens, strict=False):
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
        # The run's own files (a repository may track .claude/) would list the fixes' patterns
        # back from the report that holds them.
        out = git(repo_root, "grep", "-n", "-I", "-z", "-E", "-e", body,
                  "--", ":(exclude,literal).claude/audits")
    except GitError as e:
        # git grep exits 1 with no stderr when nothing matches; a bad regex has a message.
        if str(e).rstrip().endswith(f"failed in {repo_root}:"):
            return []
        raise FixAuditRefused(f"sibling pattern {pattern!r} could not be searched: {e}") from e
    # git() reads in text mode, which turns a bare carriage return inside a matched line into a
    # newline. A row without a NUL is therefore the rest of the previous row's text.
    rows: list[list] = []
    for row in out.split("\n"):  # not splitlines(): a form feed inside a matched line is no row end
        if "\0" in row:
            path, line, text = row.split("\0", 2)
            rows.append([path, int(line), text])
        elif rows:
            rows[-1][2] += "\n" + row
    hits = []
    seen: dict[tuple[str, str], int] = {}
    for path, line, text in rows:
        text = text.strip()
        hits.append((path, line, text, seen.get((path, text), 0)))
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
    `sibling_patterns`: fixed strings (tolerant of spacing, and like the auto patterns not matched
    inside a longer name), or a regex when prefixed `re:`. Empty `sibling_patterns`
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
    out: list[dict] = []
    too_broad: list[dict] = []
    for fix in fixes:
        files = _files_of(hunks, fix)
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


def check_dispositions(hits: list[dict], dispositions: dict) -> list[str]:
    """Reasons a hit has no valid disposition. A hit still listed is a site no fix edited, so
    only `not_applicable` with a reason can clear it; `fixed` is rejected."""
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


# The data auditor is bug-auditor run with the DAT- prompt; the suffix keeps it a separate need.
DATA_AUDITOR = "bug-auditor:DAT"
MAX_AUDITORS = 4
_SHELL_SUFFIXES = (".sh", ".ps1")

# name -> (regexes matched against an added line, the auditors the mechanism routes to)
MECHANISMS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "process": ((r"\bsubprocess\b", r"\bPopen\b", r"\bos\.system\b"),
                ("security-auditor", "bug-auditor")),
    "lock": ((r"\bthreading\b", r"\bLock\b", r"\bfcntl\b", r"\bmsvcrt\.locking\b", r"\bflock\b"),
             ("bug-auditor", DATA_AUDITOR)),
    "timeout": ((r"\btimeout\s*=", r"\.wait\(", r"\bsignal\.alarm\b"),
                ("bug-auditor", "perf-auditor")),
    "parser": ((r"\bjson\.loads?\b", r"\bre\.compile\b", r"\bast\.parse\b", r"\bcsv\."),
               ("bug-auditor", DATA_AUDITOR)),
    "file_write": ((r"\.write_(?:text|bytes)\(", r"\bopen\(.*[\"'][wax][bt+]*[\"']",
                    r"\bos\.replace\b"),
                   (DATA_AUDITOR,)),
}
# Shell installers: a trap or a changed error preference alters what a failure does, and a
# redirection of stderr decides whether it is seen. Bug for the logic, deploy for the install.
SHELL_MECHANISMS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "trap": ((r"^\s*trap\b",), ("bug-auditor", "deploy-auditor")),
    "ErrorActionPreference": ((r"\$ErrorActionPreference\b",), ("bug-auditor", "deploy-auditor")),
    "redirect": ((r"\b2>",), ("bug-auditor", "deploy-auditor")),
}


def mechanism_flags(hunks: list[Hunk], fixes: list[dict]) -> list[dict]:
    """One flag per added non-test line and mechanism it matches: a process launch, a lock, a
    timeout, a parser of untrusted input, a file write in a .py file, or the shell rows in a
    .sh or .ps1 file. Other files (docs, config) raise none."""
    out = []
    for fix in fixes:
        files = _files_of(hunks, fix)
        for h in hunks:
            if h.path not in files or is_test_path(h.path):
                continue
            if h.path.endswith(_SHELL_SUFFIXES):
                table = SHELL_MECHANISMS
            elif h.path.endswith(".py"):
                table = MECHANISMS
            else:
                continue
            for line_no, text in zip(h.added_at, h.added, strict=True):
                for name, (patterns, auditors) in table.items():
                    if any(re.search(p, text) for p in patterns):
                        out.append({"fix_id": fix["fix_id"], "mechanism": name, "path": h.path,
                                    "line": line_no, "auditors": list(auditors)})
    return out


def failure_test_reasons(flags: list[dict], fixes: list[dict], repo_root: Path) -> list[str]:
    """Reasons a flagged fix does not name a test of its mechanism's own failure: no
    `failure_test`, or one whose file or function does not exist."""
    reasons = []
    for fix in fixes:
        mechanisms = sorted({f["mechanism"] for f in flags if f["fix_id"] == fix["fix_id"]})
        if not mechanisms:
            continue
        fid, node = fix["fix_id"], str(fix.get("failure_test") or "").strip()
        if not node:
            reasons.append(f"{fid}: adds {', '.join(mechanisms)}; name failure_test, a test of "
                           "the mechanism's own failure")
            continue
        file, _, rest = node.partition("::")
        name = rest.rsplit("::", 1)[-1].split("[", 1)[0]
        path = repo_root / file
        if not name or not path.is_file():
            reasons.append(f"{fid}: failure_test {node!r} is not an existing file::test node")
            continue
        source = path.read_text(encoding="utf-8", errors="replace")
        if not re.search(rf"\bdef\s+{re.escape(name)}\b", source):
            reasons.append(f"{fid}: failure_test {node!r}: no def {name} in {file}")
    return reasons


def auditors_for(fixes: list[dict], flags: list[dict], categories: dict[str, str]) -> list[str]:
    """At most MAX_AUDITORS, by how many fixes need each: the auditor of a fix's finding
    category, and those its flagged mechanisms route to. A tie goes first to an auditor a
    mechanism routes to, security-auditor first, so a process launch cannot lose its security
    review to alphabetical order; then alphabetically."""
    needs: dict[str, int] = {}
    for fix in fixes:
        wanted = {a for f in flags if f["fix_id"] == fix["fix_id"] for a in f["auditors"]}
        if fix.get("finding") in categories:
            wanted.add(categories[fix["finding"]])
        for a in wanted:
            needs[a] = needs.get(a, 0) + 1
    routed = {a for f in flags for a in f["auditors"]}
    rank = sorted(needs, key=lambda a: (-needs[a], a not in routed, a != "security-auditor", a))
    return rank[:MAX_AUDITORS]


REPORT = "fixaudit.json"
#: Each pass's verdict file, as `fixaudit record` kept it: the journal holds no tree, and the
#: next pass's delta is measured from this file's `audited_tree`.
PASS_FILE = "fixaudit-pass-{}.json"


def kept_passes(root: Path, run_id: str) -> dict[int, dict]:
    """The verdict files `fixaudit record` kept for this run, by pass number, in order."""
    found = {}
    for path in (Path(root) / ".claude" / "audits" / "runs" / run_id).glob(PASS_FILE.format("*")):
        number = path.stem.rsplit("-", 1)[-1]
        if number.isdigit():
            found[int(number)] = json.loads(path.read_bytes())
    return dict(sorted(found.items()))


def stored_fixes(root: Path, state) -> list[dict]:
    """The run's stored L4 entries, each with the `fix_id` and `files` every check reads."""
    value = pipeline_state._stored_json(state, "L4", root)
    if value is None:
        raise FixAuditRefused("this run has no stored L4 file; record L4 with --output first")
    # `finding` set the way l4_fixes resolves it, so a `finding_id` entry routes and journals too
    fixes = [{**f, "finding": f.get("finding") or f.get("finding_id") or f.get("fix_id")}
             for f in layer_outputs.l4_fixes(value)]
    for n, fix in enumerate(fixes):
        if not isinstance(fix.get("fix_id"), str) or not fix["fix_id"]:
            raise FixAuditRefused(f'L4 entry {n} needs a non-empty string "fix_id"')
        files = fix.get("files")
        # Spelt as the diff spells paths: the revert check compares these with the diff's paths.
        if not isinstance(files, list) or not files or not all(
                isinstance(f, str) and canonical(f) for f in files):
            raise FixAuditRefused(f'{fix["fix_id"]}: "files" must list the paths the fix changed, '
                                  "each a repo-relative POSIX path as git diff spells it")
    return fixes


def canonical(path: str) -> bool:
    """A repo-relative POSIX path spelt the one way the fix diff spells it."""
    pure = PurePosixPath(path)
    return (path == pure.as_posix() and path != "." and not pure.is_absolute()
            and ".." not in pure.parts and "\\" not in path and ":" not in path)


def _unlisted(hunks: list[Hunk], skipped: list[str], fixes: list[dict]) -> list[str]:
    """The non-test paths the fix diff edits that no fix's `files` lists (a renamed file may
    be listed under either name)."""
    listed = {p for f in fixes for p in f["files"]}
    names = [(h.path, h.old_path) for h in hunks] + [(p, None) for p in skipped]
    return sorted({path for path, old in names if not is_test_path(path)
                   and path not in listed and old not in listed})


def finding_auditors(root: Path, state) -> dict[str, str]:
    """Each L2 finding's auditor, under the names `auditors_for` routes to."""
    out = {}
    for row in pipeline_state._stored_l2(state, root):
        if row["id"].startswith("DAT-"):  # the data auditor is bug-auditor with the DAT- prompt
            out[row["id"]] = DATA_AUDITOR
        elif isinstance(row.get("auditor"), str) and row["auditor"]:
            out[row["id"]] = row["auditor"].removeprefix("cca-")
    return out


def build_report(root: Path, state, tree: str) -> dict:
    """The deterministic report at `tree`, unwritten. `delta_hunks` are the hunks changed since
    the last kept pass's audited tree (every fix hunk before the first pass)."""
    fixes = stored_fixes(root, state)
    hunks, skipped = fix_hunks(root, state.prefix_tree, tree)
    unlisted = _unlisted(hunks, skipped, fixes)
    if unlisted:
        # An edit no fix owns escapes the size check, the sweep and the revert check.
        raise FixAuditRefused(f"the fixes edit {', '.join(unlisted)}, which no L4 entry lists in "
                              '"files"; add each to the fix that made it and record L4 again')
    sweep = sibling_hits(root, hunks, fixes)
    flags = mechanism_flags(hunks, fixes)
    passes = kept_passes(root, state.run_id)
    delta = fix_hunks(root, passes[max(passes)]["audited_tree"], tree)[0] if passes else hunks
    return {
        "tree": tree, "prefix_tree": state.prefix_tree,
        "hunks": [{"id": h.id, **asdict(h)} for h in hunks],
        "siblings": {"hits": sweep.hits, "too_broad": sweep.too_broad},
        "mechanisms": flags, "sizes": size_report(hunks, fixes), "skipped": skipped,
        "auditors": auditors_for(fixes, flags, finding_auditors(root, state)),
        # with their text: a later pass audits the diff since the last audited tree, not the fix
        "delta_hunks": [{"id": h.id, **asdict(h)} for h in delta],
    }


def check_report(root: Path, state) -> dict:
    """Write `runs/<run_id>/fixaudit.json` at the current tree and return it."""
    tree = pipeline_state.current_tree(state, root)
    if tree is None:
        raise FixAuditRefused("the working tree could not be fingerprinted, so the report could "
                              "never be shown current (git missing from PATH, or timed out)")
    report = build_report(root, state, tree)
    pipeline_state._store_run_file(state, REPORT, report, root)
    return report
