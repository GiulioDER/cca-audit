"""Record a pending run's fixes without a merge link, when no pull request can be found for it.

`python -m cca_checks bugs record-unlinked [--older-than-hours N] [--assume-repo OWNER/NAME]
[--write] [--json]`

`resolve` writes a run's rows once GitHub names the merged pull request holding the run's commit.
That link is lost when a session amends or rebases the commit before pushing, or carries the fix
to master through another branch: measured 2026-10-28, 84 runs holding 461 fixes (P1 29, P2 228,
P3 200) sat in pending/ for up to two weeks, with every finding, verdict and fix still on disk,
for want of that one link. For example, the fixes of run 20261028T110733Z on
`claude/cca-pr847-descending` reached master in #1045, from another branch; no PR lists the
run's commit.

This writes those rows anyway, keyed by the run's own commit instead of a squash merge, with
`fixed` CLAIMED rather than EVIDENCED, so every one is `historical`, never `proved`. Each carries
an `unlinked:` source saying so, and the record moves to unlinked/, never back to pending/, so a
later `resolve` cannot write the same fixes a second time. A later row that finds the merge can
supersede an unlinked row through `supersedes`.

Only a record whose meta.json is trustworthy and whose capture is older than `--older-than-hours`
is taken, so a run whose PR is merely still open gets `resolve`'s chance first. A record that
names no repository is skipped unless `--assume-repo` supplies one.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .forward import Merge, rows_and_skips
from .model import CLAIMED, render_memo
from .pending import PENDING, TMP_PREFIX
from .resolve import _Backfilled, _captured, _check_meta, _meta, _move, _Ordinals
from .store import LEDGER, bug_rows
from .store import write as write_rows

UNLINKED = "unlinked"
UNLINKED_SOURCE = "unlinked:no merged pull request lists this run's commit"


@dataclass
class UnlinkedReport:
    recorded: list[str] = field(default_factory=list)
    too_recent: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    not_fixed: list[str] = field(default_factory=list)
    already_backfilled: list[str] = field(default_factory=list)
    rows_written: int = 0
    memos_written: int = 0
    rows: int = 0


def record_unlinked(root: Path, *, write: bool, older_than_hours: float = 24,
                    assume_repo: str | None = None, now: datetime | None = None) -> UnlinkedReport:
    now = now or datetime.now(timezone.utc)
    report = UnlinkedReport()
    pending = root / PENDING
    if not pending.is_dir():
        return report
    ordinals = _Ordinals(root / LEDGER)
    backfilled = _Backfilled(root / LEDGER)
    known_rows = bug_rows(root / LEDGER)
    # A finding this run already has a bug row for, under any commit, is never written again:
    # the run-commit key of an unlinked row would not collide with a merge-keyed row.
    held = {(r.run_id, r.finding_id) for r in known_rows if r.run_id}
    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    batch = []
    for folder in sorted(p for p in pending.iterdir() if p.is_dir()):
        if folder.name.startswith(TMP_PREFIX):
            continue
        why = _check_meta(folder)
        if why is not None:
            report.skipped.append(f"{folder.name}: {why}")
            continue
        meta = _meta(folder)
        captured = _captured(meta)
        if captured is None or now - captured < timedelta(hours=older_than_hours):
            report.too_recent.append(folder.name)
            continue
        repo = meta.get("repo") or assume_repo
        if not isinstance(repo, str) or "/" not in repo:
            report.skipped.append(f"{folder.name}: names no repository (pass --assume-repo)")
            continue
        merge = Merge(repo, meta["commit"], captured.strftime("%Y-%m-%d"), 0)
        try:
            pairs, skipped = rows_and_skips(folder, merge, known_rows)
            for row, _ in pairs:
                render_memo(row, stamp)
        except Exception as exc:  # noqa: BLE001 - one bad record must not stop the others
            report.skipped.append(f"{folder.name}: {type(exc).__name__}: {exc}")
            continue
        report.not_fixed += [f"{folder.name}: {s}" for s in skipped]
        rows = []
        for row, fix_id in pairs:
            if (row.run_id, row.finding_id) in held:
                report.already_backfilled.append(f"{folder.name}: {row.finding_id} is already "
                                                 "in the ledger for this run")
                continue
            same = backfilled.match(row, fix_id)
            if same is not None:
                report.already_backfilled.append(f"{folder.name}: {row.finding_id} is {same}")
                continue
            # No merge proves the fix reached the default branch: the run says it fixed it.
            row.evidence["fixed"] = CLAIMED
            row.sources = [s for s in row.sources if not s.startswith("pr:")] + [UNLINKED_SOURCE]
            row.ordinal = ordinals.assign(row)
            rows.append(row)
        if not rows and not skipped:
            report.skipped.append(f"{folder.name}: no ledger rows could be built")
            continue
        report.rows += len(rows)
        batch.append((folder, rows))
    if write and batch:
        added, memos = write_rows([r for _, rows in batch for r in rows], root, stamp)
        report.rows_written, report.memos_written = added, memos
        for folder, _ in batch:  # only after the rows are on disk
            _move(folder, root, UNLINKED)
    report.recorded = [folder.name for folder, _ in batch]
    return report


def run(root: Path, *, write: bool, older_than_hours: float, assume_repo: str | None,
        as_json: bool) -> int:
    report = record_unlinked(root, write=write, older_than_hours=older_than_hours,
                             assume_repo=assume_repo)
    data = asdict(report)
    if as_json:
        print(json.dumps(data, indent=2))
    else:
        verb = "wrote" if write else "would write"
        print(f"{verb} {report.rows} unlinked rows from {len(report.recorded)} runs"
              f" ({report.rows_written} appended); {len(report.too_recent)} too recent, "
              f"{len(report.skipped)} skipped, {len(report.already_backfilled)} already "
              "backfilled")
        for line in report.skipped:
            print("  skipped " + line)
    return 0
