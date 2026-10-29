"""Recover file context from a finding's own immutable Git commit bullet.

Unlike archived-run recovery, this supports legacy rows with a null run ID.
No run, anchor symbol, line, severity or proof is inferred. A file, or a uniquely
changed Python definition in it, must be named in the exact original finding
bullet, belong to that row, change in the fix, and be a regular UTF-8 Git blob.
Ambiguous basenames, definitions and proof-only test
references grant no anchor. Only anchors.jsonl is appended by --write.

`--single-file` adds one opt-in method for rows those rules refuse: a fix that changed exactly
one production file (tests, docs and configuration excluded) anchors the row at that file, with
no symbol. Its `--write` is refused unless the installed RE-call reader ranks such file-level
records below symbol-anchored ones (`reader_ranks_file_level`).
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import re
import subprocess
import sys
from collections import Counter
from dataclasses import asdict
from functools import lru_cache
from pathlib import Path

from . import anchor_log, records
from .anchors import code_sha, normalize, resolve_symbols, well_formed_file
from .gitread import normalize_remote
from .model import SHA40, BugRow
from .parse import parse_message
from .recover_anchors import Report, Unavailable, _blob, _git
from .store import bugs_dir

_FILE = re.compile(
    r"(?<![\w/\\])(?:[\w.-]+[/\\])*[\w.-]+\.(?:pyi?|sh|ps1|md|ya?ml|json|toml|[jt]sx?|sql|rs|txt)"
    r"(?::[0-9]+)?(?![\w./\\-])"
)
_IDENTIFIER = r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*"
_QUOTED = re.compile(rf"`({_IDENTIFIER})(?:\([^`]*\))?`")
_CALL = re.compile(rf"(?<![\w.])({_IDENTIFIER})\(")
#: A bare word outside backticks, a path or a call. Legacy summaries name the repaired definition
#: this way ("paper_trader _check_stop_loss math.isfinite() guards"): measured 2026-10-28, 502 of
#: 556 unanchored rows named no quoted identifier or file, while 86 named exactly one changed
#: definition bare. Only distinctive words count (`_distinctive`), so "audit" or "predict" never do.
_BARE = re.compile(r"(?<![\w`/\\])([A-Za-z_]\w*)(?![\w`(/\\.])")


def _distinctive(token: str) -> bool:
    """A word that reads as an identifier, not as prose: six or more characters with an inner
    underscore (`should_trade`, `_force_recycle_session`) or a lower-to-upper case step
    (`AlpacaBroker`, `syncStatus`)."""
    camel = re.search(r"[a-z][A-Z]", token) is not None
    return len(token) >= 6 and ("_" in token.strip("_") or camel)


def _proof_path(file: str) -> bool:
    return any(p in {"test", "tests", "fixtures", "examples"} for p in file.split("/"))


def references(summary: str, files: list[str]) -> list[dict[str, str]]:
    """Unambiguous authored filename references, excluding trailing test/proof paths."""
    refs: dict[str, dict[str, str]] = {}
    for match in _FILE.finditer(summary):
        token = match.group().split(":", 1)[0].replace("\\", "/")
        matches = [f for f in files if well_formed_file(f)
                   and (f == token or f.endswith("/" + token))]
        if len(matches) != 1:
            continue
        file = matches[0]
        prefix = summary[:match.start()].strip(" `*()[]:")
        if _proof_path(file) and prefix:
            continue
        refs.setdefault(file, {"file": file, "token": token})
    return list(refs.values())


@lru_cache(maxsize=128)
def _commit(repo: Path, sha: str) -> tuple[bytes, frozenset[str]]:
    raw = _git(repo, "cat-file", "commit", sha)
    if len(raw) > 2 * 1024 * 1024 or b"\n\n" not in raw:
        raise Unavailable("commit message unavailable or oversized")
    headers, message = raw.split(b"\n\n", 1)
    if sum(line.startswith(b"parent ") for line in headers.splitlines()) != 1:
        raise Unavailable("fix does not have one unambiguous parent")
    changed = _git(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", "-z", sha)
    return message, frozenset(p.decode("utf-8") for p in changed.split(b"\0") if p)


def _definitions(source: str) -> dict[str, str | None]:
    spans = resolve_symbols(source)
    counts = Counter(s.symbol.split("#", 1)[0] for s in spans)
    lines = normalize(source).split("\n")
    result: dict[str, str | None] = {}
    for span in spans:
        parts = span.symbol.split("#", 1)[0].split(".")
        if any(counts[".".join(parts[:i])] > 1 for i in range(1, len(parts) + 1)):
            result[span.symbol] = None
            continue
        result[span.symbol] = code_sha("\n".join(lines[span.start_line - 1:span.end_line]))
    return result


def _tree(repo: Path, sha: str, files: list[str]) -> dict[str, str | None]:
    """Regular, bounded blobs for the selected paths, in one Git object listing."""
    entries = _git(repo, "ls-tree", "-l", "-z", sha, "--", *files)
    result = {}
    for entry in entries.split(b"\0"):
        if not entry:
            continue
        metadata, name = entry.split(b"\t", 1)
        mode, kind, blob, size = metadata.split()
        name = name.decode("utf-8")
        result[name] = None
        if (mode in (b"100644", b"100755") and kind == b"blob"
                and int(size) <= 2 * 1024 * 1024):
            result[name] = blob.decode("ascii")
    return result


def _definition_blob(repo: Path, blob: str) -> dict[str, str | None]:
    if not SHA40.fullmatch(blob):
        raise Unavailable("invalid source blob")
    return _definitions(_git(repo, "cat-file", "blob", blob).decode("utf-8"))


@lru_cache(maxsize=8)
def _catalog(repo: Path, sha: str) -> tuple[str, tuple[dict, ...]]:
    """Changed unambiguous definitions in regular Python files at one immutable fix."""
    _, changed = _commit(repo, sha)
    parent = _git(repo, "rev-parse", sha + "^").decode().strip()
    files = sorted(f for f in changed if f.endswith(".py") and well_formed_file(f))
    if not files:
        return parent, ()
    landed = _tree(repo, sha, files)
    prior = _tree(repo, parent, files)
    result = []
    for file in files:
        landed_blob, prior_blob = landed.get(file), prior.get(file)
        if not landed_blob or file in prior and not prior_blob:
            continue
        try:
            before = _definition_blob(repo, prior_blob) if prior_blob else {}
            after = _definition_blob(repo, landed_blob)
        except (Unavailable, SyntaxError, UnicodeError):
            continue
        for symbol in before.keys() | after.keys():
            if (symbol in before and before[symbol] is None
                    or symbol in after and after[symbol] is None):
                continue
            if before.get(symbol) != after.get(symbol):
                result.append({"file": file, "symbol": symbol,
                               "before_sha": before.get(symbol), "after_sha": after.get(symbol)})
    return parent, tuple(result)


def symbol_references(row: BugRow, repo: Path) -> tuple[str, list[dict]]:
    """Quoted identifiers, explicit calls and distinctive bare words locate only one changed
    definition each. A reference records which form named it, so the bare ones can be told apart."""
    forms = {m.group(1): "quoted" for pattern in (_QUOTED, _CALL)
             for m in pattern.finditer(row.summary)}
    for m in _BARE.finditer(row.summary):
        if _distinctive(m.group(1)):
            forms.setdefault(m.group(1), "bare")
    tokens = set(forms)
    if not tokens:
        return "", []
    parent, catalog = _catalog(repo, row.fix_commit)
    refs = {}
    for token in sorted(tokens):
        matches = [r for r in catalog if r["symbol"] == token
                   or "." not in token and r["symbol"].rsplit(".", 1)[-1] == token]
        if len(matches) != 1:
            continue
        found = matches[0]
        if found["file"] not in row.files or _proof_path(found["file"]):
            continue
        refs.setdefault(found["file"], {**found, "token": token, "form": forms[token]})
    return parent, list(refs.values())


def recover(row: BugRow, repo: Path) -> dict:
    """One finding-bound file annotation, or an explicit reason to abstain."""
    identity = normalize_remote(_git(repo, "remote", "get-url", "origin").decode().strip())
    return _recover(row, repo, identity)


def _recover(row: BugRow, repo: Path, identity: str | None) -> dict:
    if not isinstance(row.repo, str) or not identity or identity.lower() != row.repo.lower():
        raise Unavailable("local repository identity differs")
    if not isinstance(row.fix_commit, str) or not SHA40.fullmatch(row.fix_commit):
        raise Unavailable("invalid fix commit")
    if (not isinstance(row.summary, str) or not isinstance(row.finding_id, str)
            or not isinstance(row.files, list) or not all(isinstance(f, str) for f in row.files)):
        raise Unavailable("invalid finding summary or file identity")
    message, changed = _commit(repo, row.fix_commit)
    try:
        body = message.decode("utf-8")
    except UnicodeDecodeError:
        raise Unavailable("commit message is not UTF-8") from None
    bullets = [b for b in parse_message(body)
               if b.finding_id == row.finding_id and b.summary == row.summary]
    if len(bullets) != 1:
        raise Unavailable("exact finding bullet is absent or ambiguous")
    refs = references(row.summary, row.files)
    parent = ""
    if not refs:
        parent, refs = symbol_references(row, repo)
    if not refs:
        raise Unavailable("finding has no unambiguous source file reference")
    anchors = []
    for ref in refs:
        file = ref["file"]
        if file not in changed:
            raise Unavailable("finding file was not changed by the fix")
        entry = _git(repo, "ls-tree", "-z", row.fix_commit, "--", file).split(b"\0")[0]
        if not entry.startswith((b"100644 blob ", b"100755 blob ")):
            raise Unavailable("finding file is not a regular Git blob")
        anchors.append({"file": file, "symbol": None,
                        "code_sha": code_sha(_blob(repo, row.fix_commit, file))})
    note = {"v": 2, "bug_id": row.id, "repo": row.repo, "run_id": row.run_id,
            "fix_commit": row.fix_commit, "anchors": anchors,
            "provenance": {"method": "git-finding-symbol-file" if parent else "git-finding-file",
                           "commit": row.fix_commit,
                           "message_sha256": hashlib.sha256(message).hexdigest(),
                           "summary_sha256": hashlib.sha256(row.summary.encode()).hexdigest(),
                           "finding_id": row.finding_id, "ordinal": row.ordinal,
                           "references": refs}}
    if parent:
        note["provenance"]["parent"] = parent
    if not anchor_log.valid(note):
        raise Unavailable("derived Git annotation is invalid")
    return note


#: Paths the single-file rule never counts as the production file. Raise the version whenever
#: the lists change, so annotations made under an older list can be told apart.
EXCLUSIONS_VERSION = 1
#: Reading of "paths under tests/": these directories count only as the FIRST path component
#: (root level). A nested `src/pkg/fixtures/x.py` is a production path, so a fix touching it and
#: another production file stays a two-file fix. Basename and extension rules apply at any depth.
_EXCLUDED_DIRECTORIES = frozenset({"tests", "test", "fixtures", "docs", ".github"})
_EXCLUDED_SUFFIXES = frozenset({".md", ".txt", ".json", ".jsonl", ".yaml", ".yml", ".toml",
                                ".cfg", ".ini", ".lock"})
#: Prefixes whose defect often lives in configuration or a manifest, which the exclusions remove.
_EXCLUDED_PREFIXES = frozenset({"ENV", "DEP", "DEPLOY"})


def excluded(path: str) -> bool:
    """A test, documentation or configuration path: never the single production file."""
    parts = path.replace("\\", "/").split("/")
    name = parts[-1]
    suffix = name[name.rfind("."):].lower() if "." in name else ""
    return ((len(parts) > 1 and parts[0] in _EXCLUDED_DIRECTORIES)
            or name.startswith("test_") or fnmatch.fnmatchcase(name, "*_test.*")
            or suffix in _EXCLUDED_SUFFIXES or name.startswith("CHANGELOG"))


def _recover_single_file(row: BugRow, repo: Path, identity: str | None) -> dict:
    """A file-level annotation from a fix that changed exactly one production file."""
    if not isinstance(row.repo, str) or not identity or identity.lower() != row.repo.lower():
        raise Unavailable("local repository identity differs")
    if not isinstance(row.fix_commit, str) or not SHA40.fullmatch(row.fix_commit):
        raise Unavailable("invalid fix commit")
    if (not isinstance(row.summary, str) or not isinstance(row.finding_id, str)
            or not isinstance(row.files, list) or not all(isinstance(f, str) for f in row.files)):
        raise Unavailable("invalid finding summary or file identity")
    parents = _git(repo, "rev-list", "--parents", "-n", "1", row.fix_commit).decode().split()[1:]
    if len(parents) != 1 or not SHA40.fullmatch(parents[0]):
        raise Unavailable("fix does not have one unambiguous parent")
    parent = parents[0]
    if row.finding_id.split("-", 1)[0].upper() in _EXCLUDED_PREFIXES:
        raise Unavailable("finding prefix is excluded")
    raw = _git(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", "-z",
               parent, row.fix_commit)
    changed = sorted({p.decode("utf-8") for p in raw.split(b"\0") if p})
    production = [p for p in changed if not excluded(p)]
    if not production:
        raise Unavailable("fix changed no production file")
    if len(production) > 1:
        raise Unavailable("fix changed more than one production file")
    file = production[0]
    if file not in row.files:
        raise Unavailable("production file is not in the row's files")
    # The row's files and the fix's changed files together, so a summary naming either kind of
    # other file refuses; references() already skips trailing test and proof paths.
    if any(ref["file"] != file
           for ref in references(row.summary, sorted(set(row.files) | set(changed)))):
        raise Unavailable("summary names another file")
    entry = _git(repo, "ls-tree", "-z", row.fix_commit, "--", file).split(b"\0")[0]
    if not well_formed_file(file) or not entry.startswith((b"100644 blob ", b"100755 blob ")):
        raise Unavailable("production file is not a regular Git blob at the fix")
    commit_raw = _git(repo, "cat-file", "commit", row.fix_commit)
    if len(commit_raw) > 2 * 1024 * 1024 or b"\n\n" not in commit_raw:
        raise Unavailable("commit message unavailable or oversized")
    message = commit_raw.split(b"\n\n", 1)[1]
    note = {"v": 2, "bug_id": row.id, "repo": row.repo, "run_id": row.run_id,
            "fix_commit": row.fix_commit,
            "anchors": [{"file": file, "symbol": None,
                         "code_sha": code_sha(_blob(repo, row.fix_commit, file))}],
            "provenance": {"method": "git-single-production-file", "commit": row.fix_commit,
                           "parent": parent,
                           "message_sha256": hashlib.sha256(message).hexdigest(),
                           "summary_sha256": hashlib.sha256(row.summary.encode()).hexdigest(),
                           "finding_id": row.finding_id, "ordinal": row.ordinal,
                           "references": [{"file": file}],
                           # The sorted changed paths, joined by "\n" and UTF-8 encoded.
                           "changed_sha256": hashlib.sha256(
                               "\n".join(changed).encode("utf-8")).hexdigest(),
                           "exclusions_version": EXCLUSIONS_VERSION}}
    if not anchor_log.valid(note):
        raise Unavailable("derived Git annotation is invalid")
    return note


_PROBE = """
import sys
sys.path[:] = [p for p in sys.path if p]  # Python 3.10 equivalent of -P for -c
import json
sys.path.insert(0, sys.argv[1])
try:
    from recall_hooks import auditor_brief as brief
    result = {'ranks': getattr(brief, 'FILE_LEVEL_RANKS_AS_FILE_ONLY', None) is True}
except BaseException as exc:
    result = {'ranks': False, 'error': type(exc).__name__}
print(json.dumps(result))
"""


def _reader_root() -> Path:
    return Path(os.environ.get("CCA_RECALL_ROOT") or Path.home() / ".claude" / "hooks")


def reader_ranks_file_level(root: Path) -> tuple[bool, str]:
    """Whether the installed reader ranks file-level records below symbol-anchored ones.

    The reader is imported in a child process as `local_brief.build` imports it, so neither
    the audited checkout nor PYTHONPATH can supply it. Any failure refuses."""
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    try:
        if not (root / "recall_hooks" / "auditor_brief.py").is_file():
            return False, "operator reader is not installed"
        safe_path = ["-P"] if sys.version_info >= (3, 11) else []
        completed = subprocess.run(
            [sys.executable, *safe_path, "-X", "utf8", "-c", _PROBE, str(root.resolve())],
            text=True, encoding="utf-8", capture_output=True, timeout=25,
            cwd=Path.home(), env=environment, check=False, stdin=subprocess.DEVNULL,
        )
        if completed.returncode != 0 or len(completed.stdout) > 4096:
            return False, "reader probe process refused"
        value = json.loads(completed.stdout)
    except subprocess.TimeoutExpired:
        return False, "reader probe timed out"
    except (OSError, ValueError) as exc:
        return False, f"reader probe failed ({type(exc).__name__})"
    if not isinstance(value, dict):
        return False, "reader probe returned an invalid result"
    if isinstance(value.get("error"), str):
        return False, f"reader import failed ({value['error']})"
    if value.get("ranks") is not True:
        return False, "installed reader lacks FILE_LEVEL_RANKS_AS_FILE_ONLY = True"
    return True, ""


def plan(root: Path, repos: list[Path], single_file: bool = False) -> Report:
    """Dry-run effective unanchored bug rows; preserve individual refusal reasons."""
    rows, errors = records.read_ledger(root)
    if errors:
        raise Unavailable("ledger contains unreadable rows")
    clones = {}
    for repo in repos:
        identity = normalize_remote(_git(repo, "remote", "get-url", "origin").decode().strip())
        if identity:
            clones[identity.lower()] = repo
    report = Report()
    for row in rows.values():
        if not isinstance(row, BugRow):
            continue
        if row.anchors:
            report.already_anchored += 1
            continue
        repo = clones.get(row.repo.lower()) if isinstance(row.repo, str) else None
        if repo is None:
            report.unresolved[row.id] = "no matching local repository"
            continue
        try:
            report.annotations.append(_recover(row, repo, row.repo))
            continue
        except (OSError, ValueError, RecursionError) as exc:
            reason = str(exc)
        if single_file:
            # Only rows the bullet rules refuse, so those keep precedence.
            try:
                report.annotations.append(_recover_single_file(row, repo, row.repo))
                continue
            except (OSError, ValueError, RecursionError) as exc:
                reason = str(exc)
        report.unresolved[row.id] = reason
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, action="append", required=True)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--single-file", action="store_true",
                        help="also anchor refused rows whose fix changed one production file")
    args = parser.parse_args(argv)
    allowed, refusal = True, ""
    try:
        report = plan(bugs_dir(), args.repo, single_file=args.single_file)
        if args.single_file:
            allowed, refusal = reader_ranks_file_level(_reader_root())
        if args.write and allowed:
            report.written = anchor_log.append(bugs_dir(), report.annotations)
    except (OSError, ValueError, RecursionError) as exc:
        print(json.dumps({"error": str(exc)}))
        return 1
    data = asdict(report)
    data["reasons"] = dict(Counter(report.unresolved.values()))
    if args.single_file:
        data["write_allowed"] = allowed
        if not allowed:
            data["write_refusal"] = refusal
    print(json.dumps(data, indent=2))
    return 1 if args.write and not allowed else 0


if __name__ == "__main__":
    raise SystemExit(main())
