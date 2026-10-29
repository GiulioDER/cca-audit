"""Link an unlinked or pending run to the squash merge that carries its commit's whole message.

`python -m cca_checks bugs relink --repo CLONE [--repo CLONE ...] [--assume-repo OWNER/NAME]
[--write] [--json]`

`resolve` asks GitHub which pull request lists a run's commit, and a session that amends,
rebases or carries a fix to another branch leaves no PR listing it; `record-unlinked` then
writes those rows keyed by the run's own commit, `fixed` claimed. The commit is not gone,
though: a squash merge keeps each original commit as a "* <subject>" section followed by that
commit's whole message. Measured 2026-10-29, 38 of 84 stuck runs (28 unlinked, holding 195
rows, and 10 pending) have their run commit's message, trailers aside, equal to exactly one
section of one first-parent commit on the default branch.

That is the link this writes, and only that: one merge whose section equals the whole message,
never a subject or a finding id alone, since generic subjects repeat ("4 audit fixes from CCA
(STANDARD tier)" is two runs). The rows are built as `resolve` builds them, keyed by the merge,
`fixed` evidenced; for an unlinked run each one names the run-commit row it replaces in
`supersedes`, so readers count it once. A finding the run already holds a merge-keyed row for is
skipped. The record then moves to recorded/. Recovered anchors are keyed by row id, so the
archive anchor pass is run again afterwards to carry them to the new rows.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .forward import Merge, rows_and_skips
from .gitread import GitError, default_ref, git, group_clones
from .model import BugRow, render_memo
from .pending import PENDING, RECORDED, TMP_PREFIX
from .resolve import _captured, _check_meta, _meta, _move, _Ordinals
from .store import LEDGER, bug_rows
from .store import write as write_rows
from .unlinked import UNLINKED

_TRAILER = re.compile(r"^(?:Co-Authored-By|Signed-off-by):.*$", re.IGNORECASE | re.MULTILINE)
_PR = re.compile(r"\(#(\d+)\)\s*$")
#: How far before a run's capture its merge may lie: a run commit precedes its capture.
_SLACK = timedelta(days=2)


def normalize(message: str) -> str:
    """A commit message as a squash section holds it: trailers dropped, whitespace collapsed."""
    return " ".join(_TRAILER.sub("", message).split())


@dataclass(frozen=True)
class Section:
    merge: str
    when: datetime
    subject: str


def squash_sections(clone: Path, ref: str, since: str) -> dict[str, list[Section]]:
    """Every first-parent commit on `ref` since `since`, by the normalized text of each section.

    A plain commit is one section, its whole message; a squash also gives one section per
    "* <subject>" line, that line's text plus the lines under it up to the next one.
    """
    log = git(clone, "log", "--first-parent", ref, f"--since={since}",
              "--format=%H%x1f%cI%x1f%B%x1e")
    out: dict[str, list[Section]] = defaultdict(list)
    for record in log.split("\x1e"):
        record = record.strip("\n")
        if not record:
            continue
        sha, when, body = record.split("\x1f", 2)
        lines = body.splitlines()
        meta = Section(sha, datetime.fromisoformat(when), lines[0].strip() if lines else "")
        out[normalize(body)].append(meta)
        current: list[str] | None = None
        for line in lines[1:]:
            if line.startswith("* "):
                if current:
                    out[normalize("\n".join(current))].append(meta)
                current = [line[2:]]
            elif current is not None:
                current.append(line[2:] if line.startswith("  ") else line)
        if current:
            out[normalize("\n".join(current))].append(meta)
    return out


@dataclass
class RelinkReport:
    linked: list[str] = field(default_factory=list)
    unmatched: list[str] = field(default_factory=list)
    ambiguous: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    already: list[str] = field(default_factory=list)
    rows: int = 0
    superseding: int = 0
    rows_written: int = 0
    memos_written: int = 0


def relink(root: Path, clones: list[Path], *, write: bool, assume_repo: str | None = None,
           now: datetime | None = None) -> RelinkReport:
    now = now or datetime.now(timezone.utc)
    report = RelinkReport()
    repos, _ = group_clones(clones)
    by_repo = {name.lower(): path for name, path in repos.items()}
    known = bug_rows(root / LEDGER)
    by_run: dict[str, list[BugRow]] = defaultdict(list)
    for row in known:
        if row.run_id:
            by_run[row.run_id].append(row)
    ordinals = _Ordinals(root / LEDGER)
    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    sections: dict[str, dict[str, list[Section]]] = {}
    batch = []
    for sub in (UNLINKED, PENDING):
        folder_root = root / sub
        if not folder_root.is_dir():
            continue
        for folder in sorted(p for p in folder_root.iterdir() if p.is_dir()):
            if folder.name.startswith(TMP_PREFIX):
                continue
            why = _check_meta(folder)
            if why is not None:
                report.skipped.append(f"{sub}/{folder.name}: {why}")
                continue
            meta = _meta(folder)
            repo = meta.get("repo") or assume_repo
            clone = by_repo.get(str(repo).lower()) if repo else None
            captured = _captured(meta)
            if clone is None or captured is None or not isinstance(repo, str):
                report.skipped.append(
                    f"{sub}/{folder.name}: no clone of {repo!r} or no capture time")
                continue
            key = repo.lower()
            try:
                message = git(clone, "log", "-1", "--format=%B", meta["commit"])
                if key not in sections:
                    ref = default_ref(clone)
                    if ref is None:
                        raise GitError("no default branch")
                    sections[key] = squash_sections(clone, ref, "2026-01-01")
            except GitError as exc:
                report.skipped.append(f"{sub}/{folder.name}: {exc}")
                continue
            hits = {s.merge: s for s in sections[key].get(normalize(message), [])
                    if s.when >= captured - _SLACK}
            if not hits:
                report.unmatched.append(f"{sub}/{folder.name}")
                continue
            if len(hits) > 1:
                report.ambiguous.append(f"{sub}/{folder.name}: {', '.join(sorted(hits))}")
                continue
            hit = next(iter(hits.values()))
            pr = _PR.search(hit.subject)
            merge = Merge(repo, hit.merge, hit.when.strftime("%Y-%m-%d"),
                          int(pr.group(1)) if pr else 0)
            pairs, _skips = rows_and_skips(folder, merge, known)
            run_rows = by_run.get(str(meta.get("run_id")), [])
            linked = {r.finding_id for r in run_rows
                      if not any(s.startswith("unlinked:") for s in r.sources)}
            old = {(r.finding_id, r.ordinal): r for r in run_rows
                   if any(s.startswith("unlinked:") for s in r.sources)}
            rows = []
            for row, _fix in pairs:
                if row.finding_id in linked:
                    report.already.append(f"{folder.name}: {row.finding_id}")
                    continue
                if not merge.pr:
                    row.sources = [s for s in row.sources if not s.startswith("pr:")]
                previous = old.get((row.finding_id, 1)) or next(
                    (r for (fid, _o), r in old.items() if fid == row.finding_id), None)
                if previous is not None:
                    row.supersedes = previous.bug_id
                    report.superseding += 1
                row.ordinal = ordinals.assign(row)
                render_memo(row, stamp)
                rows.append(row)
            report.rows += len(rows)
            report.linked.append(f"{sub}/{folder.name} -> {hit.merge[:12]}")
            batch.append((folder, rows))
    if write and batch:
        added, memos = write_rows([r for _, rows in batch for r in rows], root, stamp)
        report.rows_written, report.memos_written = added, memos
        for folder, _ in batch:  # only after the rows are on disk
            _move(folder, root, RECORDED)
    return report


def run(root: Path, clones: list[Path], *, write: bool, assume_repo: str | None,
        as_json: bool) -> int:
    report = relink(root, clones, write=write, assume_repo=assume_repo)
    if as_json:
        print(json.dumps(asdict(report), indent=2))
    else:
        verb = "wrote" if write else "would write"
        print(f"{verb} {report.rows} rows for {len(report.linked)} runs ({report.superseding} "
              f"superseding an unlinked row, {report.rows_written} appended); "
              f"{len(report.unmatched)} unmatched, {len(report.ambiguous)} ambiguous, "
              f"{len(report.skipped)} skipped")
    return 0
