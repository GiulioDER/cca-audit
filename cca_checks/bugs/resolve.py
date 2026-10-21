"""Stage 2 of the forward ledger: write a pending run's bugs once its pull request merged.

Runs at every session close and hourly (the dotclaude worker `refresh-cca-vps2.ps1`).
A record waits in pending/ until GitHub says its PR merged into the default branch, moves
to recorded/ after its rows are appended, or to abandoned/ when every PR has been closed
unmerged for more than a day. Append-only end to end: a rerun after any crash appends
nothing new, and a bug a backfill already recorded under the same merge is reported as
`already_backfilled` instead of counted twice. A record kept in pending/ for longer than
`stale_days`, whether waiting, unknown or incomplete, is listed as stale.

A record folder may be unfinished (a capture that crashed before writing meta.json) or
hostile (a harvested copy), so its meta.json is checked before anything reaches gh, and
a run file that cannot be read stops only its own record. Both are reported under
`incomplete` and left where they are, as is a record closed at its base commit. A
`.tmp-` folder is a capture still being built and is skipped.
"""
from __future__ import annotations

import json
import shutil
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import github
from .forward import Merge, rows_and_skips
from .ledgers import fix_number
from .model import SHA40, SHA_LEN, BugRow, render_memo
from .pending import ABANDONED, PENDING, RECORDED, TMP_PREFIX
from .store import LEDGER, bug_rows
from .store import write as write_rows

DEFAULT_RUNNER = github.gh
_COMMIT = SHA40


@dataclass
class ResolveReport:
    recorded: list[str] = field(default_factory=list)
    abandoned: list[str] = field(default_factory=list)
    waiting: list[str] = field(default_factory=list)
    unknown: list[str] = field(default_factory=list)
    stale: list[str] = field(default_factory=list)
    incomplete: list[str] = field(default_factory=list)
    already_backfilled: list[str] = field(default_factory=list)
    not_fixed: list[str] = field(default_factory=list)
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


def _utc(text) -> datetime | None:
    try:
        return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _captured(meta: dict) -> datetime | None:
    return _utc(meta.get("captured_utc"))


def _closed(folder: Path, meta: dict) -> datetime | None:
    """When the run closed: summary.json's completed_utc, else the capture time.

    A run captured after the fact (Task 9's retro capture of runs closed before capture
    existed) has a captured_utc later than its own merge; completed_utc does not.
    """
    try:
        summary = json.loads((folder / "summary.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        summary = None
    done = _utc(summary.get("completed_utc")) if isinstance(summary, dict) else None
    return done or _captured(meta)


def _stale(report: ResolveReport, folder: Path, meta: dict, kind: str, now: datetime,
           stale_days: int) -> None:
    """List a record that has sat in pending/ too long, whatever kept it there."""
    since = _captured(meta)
    if since is None:
        try:
            since = datetime.fromtimestamp(folder.stat().st_mtime, timezone.utc)
        except OSError:
            return
    if now - since > timedelta(days=stale_days):
        report.stale.append(f"{folder.name}: {kind} since "
                            f"{since.strftime('%Y-%m-%dT%H:%M:%SZ')}")


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


class _Backfilled:
    """Bugs a backfill already recorded, by project and commit.

    A backfill row is one without a run_id, or one whose sources name no run: backfill
    sets run_id on a row it links to a scorecard run, while a forward row always carries
    a `run:` source.

    A squash keeps the CCA commit message, so backfill may have parsed the same merge
    under the bullet's id: L4's finding id, or its fix id (FIX-n). Either names one bug.
    """

    def __init__(self, ledger: Path):
        self.ids: dict[tuple[str, str], set[str]] = defaultdict(set)
        if ledger.exists():
            for line in ledger.read_text(encoding="utf-8").splitlines():
                try:
                    row = json.loads(line)
                    sources = row.get("sources")
                    forward = isinstance(sources, list) and any(
                        isinstance(s, str) and s.startswith("run:") for s in sources)
                    if row.get("run_id") is not None and forward:
                        continue
                    project, commit, finding = _key(row["project"], row["fix_commit"],
                                                    row["finding_id"])
                except (ValueError, KeyError, TypeError, AttributeError):
                    continue
                self.ids[(project, commit)].add(finding)

    def match(self, row: BugRow, fix_id: str) -> str | None:
        project, commit, finding = _key(row.project, row.fix_commit, row.finding_id)
        held = self.ids.get((project, commit), set())
        if finding in held:
            return finding
        n = fix_number(fix_id)
        if n is None:
            return None
        return next((f for f in sorted(held) if fix_number(f) == n), None)


def _sort_key(folder: Path) -> tuple[str, str]:
    captured = _meta(folder).get("captured_utc")
    return (captured if isinstance(captured, str) else "", folder.name)


def resolve(root: Path, *, write: bool, run: github.Runner | None = None,
            stale_days: int = 14, now: datetime | None = None) -> ResolveReport:
    run = github.cached(run or DEFAULT_RUNNER)
    now = now or datetime.now(timezone.utc)
    report = ResolveReport()
    base = root / PENDING
    folders = sorted((p for p in base.iterdir() if p.is_dir()
                      and not p.name.startswith(TMP_PREFIX)) if base.exists() else [],
                     key=_sort_key)
    merged: list[tuple[Path, Merge]] = []
    for folder in folders:
        problem = _check_meta(folder)
        meta = _meta(folder)
        if problem is None and meta.get("commit_is_base") is True:
            problem = ("closed at its base commit with fixes listed: the fixes were not "
                       "committed before close, so no pull request can hold this run")
        if problem is not None:
            report.incomplete.append(f"{folder.name}: {problem}")
            _stale(report, folder, meta, "incomplete", now, stale_days)
            continue
        got = github.lookup(meta.get("repo"), meta["commit"], run,
                            not_before=_closed(folder, meta), now=now)
        if isinstance(got, Merge):
            merged.append((folder, got))
        elif isinstance(got, github.Abandoned):
            report.abandoned.append(folder.name)
            if write:
                _move(folder, root, ABANDONED)
        elif isinstance(got, github.Unknown):
            report.unknown.append(f"{folder.name}: {got.reason}")
            _stale(report, folder, meta, "unknown", now, stale_days)
        else:
            report.waiting.append(f"{folder.name}: {got.reason}")
            _stale(report, folder, meta, "waiting", now, stale_days)
    ordinals = _Ordinals(root / LEDGER)
    backfilled = _Backfilled(root / LEDGER)
    known_rows = bug_rows(root / LEDGER)  # caused_by: the earlier fixes a row's lines came from
    batch = []
    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    for folder, merge in merged:
        try:
            pairs, skipped = rows_and_skips(folder, merge, known_rows)
            for row, _ in pairs:  # a memo that cannot render must fail here, not mid-write
                render_memo(row, stamp)
        except Exception as exc:  # one bad record must not stop the others
            report.incomplete.append(f"{folder.name}: {type(exc).__name__}: {exc}")
            _stale(report, folder, _meta(folder), "incomplete", now, stale_days)
            continue
        report.not_fixed += [f"{folder.name}: {s}" for s in skipped]
        rows = []
        known = []
        for row, fix_id in pairs:
            same = backfilled.match(row, fix_id)
            if same is not None:
                known.append(f"{folder.name}: {row.finding_id} is backfill row {same}")
                continue
            rows.append(row)
        report.already_backfilled += known
        if not rows and not known and not skipped:
            report.incomplete.append(f"{folder.name}: no ledger rows could be built")
            _stale(report, folder, _meta(folder), "incomplete", now, stale_days)
            continue
        for row in rows:
            row.ordinal = ordinals.assign(row)
        batch.append((folder, rows))
    if write and batch:
        added, memos = write_rows([r for _, rows in batch for r in rows], root, stamp)
        report.rows_written, report.memos_written = added, memos
        for folder, _ in batch:  # only after the rows are on disk
            _move(folder, root, RECORDED)
    report.recorded = [f.name for f, _ in batch]
    return report
