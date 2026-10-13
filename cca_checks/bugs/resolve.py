"""Stage 2 of the forward ledger: write a pending run's bugs once its pull request merged.

Runs at every session close and hourly (the dotclaude worker `refresh-cca-vps2.ps1`).
A record waits in pending/ until GitHub says its PR merged into the default branch, moves
to recorded/ after its rows are appended, or to abandoned/ when every PR was closed
unmerged. Append-only end to end: a rerun after any crash appends nothing new.

A record folder may be unfinished (a capture that crashed before writing meta.json) or
hostile (a harvested copy), so its meta.json is checked before anything reaches gh, and
a run file that cannot be read stops only its own record. Both are reported under
`incomplete` and left where they are.
"""
from __future__ import annotations

import json
import re
import shutil
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import github
from .forward import Merge, rows_for
from .model import SHA_LEN, BugRow
from .pending import ABANDONED, PENDING, RECORDED
from .store import LEDGER
from .store import write as write_rows

DEFAULT_RUNNER = github.gh
_COMMIT = re.compile(r"[0-9a-fA-F]{40}")


@dataclass
class ResolveReport:
    recorded: list[str] = field(default_factory=list)
    abandoned: list[str] = field(default_factory=list)
    waiting: list[str] = field(default_factory=list)
    unknown: list[str] = field(default_factory=list)
    stale: list[str] = field(default_factory=list)
    incomplete: list[str] = field(default_factory=list)
    rows_written: int = 0
    memos_written: int = 0


def _meta(folder: Path) -> dict:
    try:
        meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return meta if isinstance(meta, dict) else {}


def _check_meta(folder: Path) -> str | None:
    """Why this folder's meta.json cannot be trusted, or None when it can."""
    path = folder / "meta.json"
    if not path.exists():
        return "no meta.json (capture did not finish)"
    try:
        meta = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return f"meta.json unreadable: {exc}"
    if not isinstance(meta, dict):
        return "meta.json is not an object"
    run_id = meta.get("run_id")
    if not isinstance(run_id, str) or not run_id.strip():
        return "meta.json has no run_id"
    commit = meta.get("commit")
    if not isinstance(commit, str) or not _COMMIT.fullmatch(commit):
        return f"meta.json commit is not 40 hex characters: {commit!r}"
    return None


def _move(folder: Path, root: Path, where: str) -> None:
    dest = root / where / folder.name
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        shutil.rmtree(dest)
    shutil.move(str(folder), str(dest))


def _captured(meta: dict) -> datetime | None:
    try:
        return datetime.strptime(meta["captured_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc)
    except (KeyError, TypeError, ValueError):
        return None


def _key(project, fix_commit, finding_id) -> tuple[str, str, str]:
    # The parts a bug_id is built from: rows that agree on these collide.
    return (str(project), str(fix_commit)[:SHA_LEN], str(finding_id))


class _Ordinals:
    """Ordinals that stay the same across invocations, so no run's rows collide.

    A row reuses the ordinal its own run already holds in the ledger (a rerun after a
    crash appends nothing); otherwise it takes the smallest ordinal that no other run
    holds, in the ledger or earlier in this batch. A ledger row without a run_id (a
    backfill row) blocks nothing: the forward row that lands on it is the same bug.
    """

    def __init__(self, ledger: Path):
        self.taken: dict[tuple, set[int]] = defaultdict(set)
        self.own: dict[tuple, list[int]] = defaultdict(list)
        if ledger.exists():
            for line in ledger.read_text(encoding="utf-8").splitlines():
                try:  # as tolerant as store.existing_ids
                    row = json.loads(line)
                    key = _key(row["project"], row["fix_commit"], row["finding_id"])
                    ordinal = int(row.get("ordinal", 1))
                    run_id = row.get("run_id")
                except (ValueError, KeyError, TypeError, AttributeError, OverflowError):
                    continue
                if run_id is None:
                    continue
                self.taken[key].add(ordinal)
                if isinstance(run_id, str):  # anything else blocks, and no run reuses it
                    self.own[(key, run_id)].append(ordinal)
        for held in self.own.values():
            held.sort()

    def assign(self, row: BugRow) -> int:
        key = _key(row.project, row.fix_commit, row.finding_id)
        held = self.own[(key, row.run_id)]
        if held:
            return held.pop(0)
        k = 1
        while k in self.taken[key]:
            k += 1
        self.taken[key].add(k)
        return k


def _sort_key(folder: Path) -> tuple[str, str]:
    captured = _meta(folder).get("captured_utc")
    return (captured if isinstance(captured, str) else "", folder.name)


def resolve(root: Path, *, write: bool, run: github.Runner | None = None,
            stale_days: int = 14, now: datetime | None = None) -> ResolveReport:
    run = run or DEFAULT_RUNNER
    now = now or datetime.now(timezone.utc)
    report = ResolveReport()
    base = root / PENDING
    folders = sorted((p for p in base.iterdir() if p.is_dir()) if base.exists() else [],
                     key=_sort_key)
    merged: list[tuple[Path, Merge]] = []
    for folder in folders:
        problem = _check_meta(folder)
        if problem is not None:
            report.incomplete.append(f"{folder.name}: {problem}")
            continue
        meta = _meta(folder)
        got = github.lookup(meta.get("repo"), meta["commit"], run)
        if isinstance(got, Merge):
            merged.append((folder, got))
        elif isinstance(got, github.Abandoned):
            report.abandoned.append(folder.name)
            if write:
                _move(folder, root, ABANDONED)
        elif isinstance(got, github.Unknown):
            report.unknown.append(f"{folder.name}: {got.reason}")
        else:
            report.waiting.append(f"{folder.name}: {got.reason}")
            when = _captured(meta)
            if when is not None and now - when > timedelta(days=stale_days):
                report.stale.append(f"{folder.name}: waiting since {meta['captured_utc']}")
    ordinals = _Ordinals(root / LEDGER)
    batch = []
    for folder, merge in merged:
        try:
            rows = rows_for(folder, merge)
        except Exception as exc:  # one bad record must not stop the others
            report.incomplete.append(f"{folder.name}: {type(exc).__name__}: {exc}")
            continue
        for row in rows:
            row.ordinal = ordinals.assign(row)
        batch.append((folder, rows))
    if write and batch:
        stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        added, memos = write_rows([r for _, rows in batch for r in rows], root, stamp)
        report.rows_written, report.memos_written = added, memos
        for folder, _ in batch:  # only after the rows are on disk
            _move(folder, root, RECORDED)
    report.recorded = [f.name for f, _ in batch]
    return report
