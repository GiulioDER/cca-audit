"""Recover locations from archived L2/L4 evidence and local git objects, without a network.

python -m cca_checks.bugs.recover_anchors --repo CLONE [--repo CLONE ...]
    [--archive DIRECTORY ...] [--write] [--json]

The default archive is the ledger's recorded/ directory. A finding's exact file must
appear in its L4 fix and ledger row. Its symbol comes from the archived input tree,
never from today's line numbers; unavailable input trees yield file-level context.
The captured repair must match the landed fix at that symbol (or the entire file).
Only anchors.jsonl is appended. Run bugs rerender --all separately after review.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from collections import Counter
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path

from ..toolpath import resolve_tool
from . import anchor_log, archive_io, records
from .anchors import code_sha, enclosing_symbol, resolve_symbols, symbol_sha, well_formed_file
from .gitread import normalize_remote
from .model import SHA40, BugRow
from .store import bugs_dir

_MAX_BLOB = 2 * 1024 * 1024


class Unavailable(ValueError):
    pass


def _git(repo: Path, *args: str) -> bytes:
    # Read objects only. No inherited index/worktree override, lazy fetch or repo hooks.
    git = resolve_tool("git", str(repo))
    if git is None:
        raise Unavailable("trusted git executable unavailable")
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_NO_LAZY_FETCH="1", GIT_ALLOW_PROTOCOL="none", GIT_OPTIONAL_LOCKS="0",
               GIT_TERMINAL_PROMPT="0", GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    try:
        p = subprocess.run(
            [git, "--no-pager", "--no-replace-objects", "-c", "core.fsmonitor=false",
             "-c", f"core.hooksPath={os.devnull}", "-C", str(repo), *args],
            capture_output=True, env=env, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Unavailable(type(exc).__name__) from None
    if p.returncode:
        raise Unavailable("git object or repository unavailable")
    return p.stdout


@lru_cache(maxsize=64)
def _blob(repo: Path, ref: str, file: str) -> str:
    if not SHA40.fullmatch(ref) or not well_formed_file(file):
        raise Unavailable("invalid object or path")
    spec = f"{ref}:{file}"
    if _git(repo, "cat-file", "-t", spec).strip() != b"blob":
        raise Unavailable("path is not a blob")
    if int(_git(repo, "cat-file", "-s", spec)) > _MAX_BLOB:
        raise Unavailable("blob exceeds recovery limit")
    try:
        return _git(repo, "cat-file", "blob", spec).decode("utf-8")
    except UnicodeDecodeError:
        raise Unavailable("blob is not UTF-8 text") from None


def _json(path: Path) -> tuple[object, str]:
    try:
        data = archive_io.read(path, _MAX_BLOB)
    except archive_io.UnsafeArchive as exc:
        raise Unavailable(str(exc)) from None
    return json.loads(data.decode("utf-8-sig")), hashlib.sha256(data).hexdigest()


def _file_list(value: object) -> list[str]:
    if isinstance(value, str):
        return [s.strip() for s in value.split(",") if s.strip()]
    return [s for s in value if isinstance(s, str)] if isinstance(value, list) else []


@dataclass(frozen=True)
class _Archive:
    meta: object
    summary: object
    found: dict[str, list[dict]]
    fixes: dict[str, list[dict]]
    hashes: tuple[str, str, str, str]


def _index(value: object, keys: tuple[str, ...]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, dict):
            continue
        key = next((item.get(k) for k in keys if item.get(k)), None)
        if isinstance(key, str):
            out.setdefault(key, []).append(item)
    return out


def _snapshot(archive: Path, meta_pair: tuple[object, str] | None = None) -> _Archive:
    meta, mh = meta_pair if meta_pair is not None else _json(archive / "meta.json")
    summary, sh = _json(archive / "summary.json")
    l2, lh = _json(archive / "L2.json")
    l4, fh = _json(archive / "L4.json")
    return _Archive(meta, summary, _index(l2, ("id",)),
                    _index(l4, ("finding", "finding_id", "fix_id")), (mh, sh, lh, fh))


def recover(row: BugRow, archive: Path, repo: Path) -> dict:
    """One independently verifiable annotation, or a reason why recovery must abstain."""
    return _recover(row, archive, repo, _snapshot(archive))


def _duplicate_family(symbol: str, *sources: str) -> bool:
    # Occurrence suffixes are positional, never durable identities across edits.
    parts = symbol.split("#", 1)[0].split(".")
    families = {".".join(parts[:i]) for i in range(1, len(parts) + 1)}
    for source in sources:
        counts = Counter(s.symbol.split("#", 1)[0] for s in resolve_symbols(source))
        if any(counts[family] > 1 for family in families):
            return True
    return False


def _recover(row: BugRow, archive: Path, repo: Path, evidence: _Archive) -> dict:
    meta, summary = evidence.meta, evidence.summary
    mh, sh, lh, fh = evidence.hashes
    if not isinstance(meta, dict) or not isinstance(summary, dict):
        raise Unavailable("invalid archive metadata")
    if (not isinstance(meta.get("repo"), str) or not isinstance(row.repo, str)
            or meta["repo"].lower() != row.repo.lower() or meta.get("run_id") != row.run_id
            or summary.get("run_id") != row.run_id):
        raise Unavailable("archive identity differs")
    head = meta.get("commit")
    if (not isinstance(head, str) or not SHA40.fullmatch(head)
            or summary.get("commit") != head):
        raise Unavailable("captured commit is not established")
    fixes = evidence.fixes.get(row.finding_id, [])
    found = evidence.found.get(row.finding_id, [])
    if len(fixes) != 1 or len(found) != 1:
        raise Unavailable("finding or fix is missing or ambiguous")
    file = found[0].get("file")
    if (not well_formed_file(file) or file not in row.files
            or file not in _file_list(fixes[0].get("files"))):
        raise Unavailable("finding file is not supported by the fix and ledger")
    file = str(file)
    landed = _blob(repo, row.fix_commit, file)
    captured = _blob(repo, head, file)
    symbol = None
    landed_sha = captured_sha = ""
    snapshot = summary.get("base_tree")
    line = found[0].get("line")
    if (isinstance(snapshot, str) and SHA40.fullmatch(snapshot)
            and isinstance(line, int) and not isinstance(line, bool) and line > 0
            and file.endswith(".py")):
        try:
            before = _blob(repo, snapshot, file)
            if line <= len(before.splitlines()):
                symbol = enclosing_symbol(before, line)
                if symbol is not None and _duplicate_family(symbol, before, landed, captured):
                    symbol = None
                if symbol is not None:
                    # Both trees must retain the same repaired symbol; a squash may change
                    # unrelated parts of the file. A missing symbol falls back to the file.
                    landed_sha = symbol_sha(landed, symbol)
                    captured_sha = symbol_sha(captured, symbol)
        except (Unavailable, SyntaxError, KeyError):
            symbol = None
    if symbol is not None:
        sha = landed_sha
        if sha != captured_sha:
            raise Unavailable("landed symbol differs from the captured repair")
    else:
        sha = code_sha(landed)
        if sha != code_sha(captured):
            raise Unavailable("landed file differs from the captured repair")
    note = {"v": 1, "bug_id": row.id, "repo": row.repo, "run_id": row.run_id,
            "fix_commit": row.fix_commit,
            "anchors": [{"file": file, "symbol": symbol, "code_sha": sha}],
            "provenance": {"archive": str(archive), "meta_sha256": mh, "summary_sha256": sh,
                           "l2_sha256": lh, "l4_sha256": fh, "captured_commit": head,
                           "input_tree": snapshot, "line": line}}
    if not anchor_log.valid(note):
        raise Unavailable("derived annotation is invalid")
    return note


@dataclass
class Report:
    annotations: list[dict] = field(default_factory=list)
    unresolved: dict[str, str] = field(default_factory=dict)
    already_anchored: int = 0
    written: int = 0


def plan(root: Path, repos: list[Path], archives: list[Path]) -> Report:
    rows, errors = records.read_ledger(root)
    if errors:
        raise Unavailable("ledger contains unreadable rows")
    clones = {}
    for repo in repos:
        identity = normalize_remote(_git(repo, "remote", "get-url", "origin").decode().strip())
        if identity:
            clones[identity.lower()] = repo
    indexed: dict[tuple[str, str], list[Path]] = {}
    snapshots: dict[Path, _Archive | str] = {}
    metadata: dict[Path, tuple[object, str]] = {}
    for directory in archives:
        try:
            paths = archive_io.directories(directory)
        except archive_io.UnsafeArchive as exc:
            raise Unavailable(str(exc)) from None
        for path in paths:
            try:
                meta_pair = _json(path / "meta.json")
                meta, _ = meta_pair
                if (isinstance(meta, dict) and isinstance(meta.get("repo"), str)
                        and isinstance(meta.get("run_id"), str)):
                    indexed.setdefault((meta["repo"].lower(), meta["run_id"]), []).append(path)
                    metadata[path] = meta_pair
            except (OSError, ValueError, RecursionError):
                continue
    report = Report()
    for row in rows.values():
        if not isinstance(row, BugRow):
            continue
        if row.anchors:
            report.already_anchored += 1
            continue
        if not isinstance(row.repo, str) or not isinstance(row.run_id, str):
            report.unresolved[row.id] = "invalid ledger repository or run identity"
            continue
        candidates = indexed.get((row.repo.lower(), row.run_id), [])
        if not candidates:
            report.unresolved[row.id] = "no matching local audit archive"
            continue
        repo = clones.get(row.repo.lower())
        if repo is None:
            report.unresolved[row.id] = "no matching local repository"
            continue
        notes = []
        why = []
        for archive in candidates:
            try:
                if archive not in snapshots:
                    try:
                        snapshots[archive] = _snapshot(archive, metadata[archive])
                    except (OSError, ValueError, RecursionError) as exc:
                        snapshots[archive] = str(exc)
                evidence = snapshots[archive]
                if isinstance(evidence, str):
                    raise Unavailable(evidence)
                notes.append(_recover(row, archive, repo, evidence))
            except (OSError, ValueError, RecursionError) as exc:
                why.append(str(exc))
        choices = {json.dumps(n["anchors"], sort_keys=True) for n in notes}
        if len(choices) == 1 and not why:
            report.annotations.append(notes[0])
        else:
            report.unresolved[row.id] = "; ".join(sorted(set(why))) \
                or "archives disagree about the anchor"
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, action="append", required=True)
    parser.add_argument("--archive", type=Path, action="append")
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    root = bugs_dir()
    try:
        report = plan(root, args.repo, args.archive or [root / "recorded"])
        if args.write:
            report.written = anchor_log.append(root, report.annotations)
    except (OSError, ValueError, RecursionError) as exc:
        print(json.dumps({"error": str(exc)}))
        return 1
    data = asdict(report)
    data["reasons"] = dict(Counter(report.unresolved.values()))
    print(json.dumps(data, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
