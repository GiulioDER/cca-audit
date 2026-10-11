"""Read the per-project scorecard ledgers and saved proof files, and match a run to a commit.

Ledger rows name findings by a run-local `fix_id` (FIX-1, FIX-01, FIX-001), which is
not the auditor-style ID a commit bullet carries (STAKES-001). So a run is matched to
a commit only on keys both sides actually share: FIX ids named in the commit's bullets,
and `finding` values where a ledger has them. Anything else stays unlinked, and the
bug's conditions stay `claimed`: nothing on record says which FIX it was.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from .gitread import remote_repo

WINDOW_BEFORE = timedelta(days=3)
WINDOW_AFTER = timedelta(days=1)  # timestamps mix time zones
_FIX = re.compile(r"FIX-0*(\d+)$", re.IGNORECASE)


def fix_number(fix_id: object) -> int | None:
    if not isinstance(fix_id, str):
        return None
    m = _FIX.match(fix_id.strip())
    return int(m.group(1)) if m else None


def match_key(finding_id: str) -> str:
    n = fix_number(finding_id)
    return f"FIX-{n}" if n is not None else finding_id


@dataclass
class LedgerRun:
    key: str
    repo: str
    source: str
    run_id: str | None
    day: date | None = None
    dispositions: dict[int, dict] = field(default_factory=dict)
    outcomes: dict[int, dict] = field(default_factory=dict)
    findings: dict[str, dict] = field(default_factory=dict)
    redproof: dict[int, str] = field(default_factory=dict)
    proof_source: str | None = None

    def keys(self) -> set[str]:
        fixes = set(self.dispositions) | set(self.outcomes)
        return {f"FIX-{n}" for n in fixes} | set(self.findings)


@dataclass
class LoadReport:
    malformed: int = 0
    ignored_proofs: list[str] = field(default_factory=list)


def ledger_repo(path: Path) -> str | None:
    # <project>/.claude/audits/AUDITOR_SCORECARD.jsonl
    return remote_repo(path.resolve().parents[2])


def _row_day(row: dict) -> date | None:
    ts = row.get("ts")
    if not isinstance(ts, str) or len(ts) < 10:
        return None
    try:
        return date.fromisoformat(ts[:10])
    except ValueError:
        return None


def load_ledger(path: Path, repo: str, report: LoadReport) -> list[LedgerRun]:
    runs: dict[str, LedgerRun] = {}
    text = path.read_text(encoding="utf-8", errors="replace")
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            report.malformed += 1
            continue
        if not isinstance(row, dict):
            report.malformed += 1
            continue
        run_id = row.get("run_id") if isinstance(row.get("run_id"), str) else None
        key = f"{repo}:{run_id}" if run_id else f"{repo}:{path}#norun"
        run = runs.setdefault(key, LedgerRun(key=key, repo=repo, source=str(path), run_id=run_id))
        day = _row_day(row)
        if day and (run.day is None or day < run.day):
            run.day = day
        stored = {**row, "_line": number}
        num = fix_number(row.get("fix_id"))
        if "verdict" in row:
            if num is not None:
                run.dispositions[num] = stored
            if isinstance(row.get("finding"), str):
                run.findings[row["finding"]] = stored
        elif num is not None and any(k in row for k in ("result", "outcome", "architect_verdict")):
            run.outcomes[num] = stored
    _attach_proofs(path.parent, runs, report)
    return list(runs.values())


def _attach_proofs(folder: Path, runs: dict[str, LedgerRun], report: LoadReport) -> None:
    for proof in sorted(folder.glob("redproof-*.json")):
        stem = proof.stem[len("redproof-"):]
        target = next((r for r in runs.values() if r.run_id == stem), None)
        if target is None:
            if len(runs) != 1:
                report.ignored_proofs.append(f"{proof}: cannot tell which run it belongs to")
                continue
            target = next(iter(runs.values()))
        try:
            entries = json.loads(proof.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            report.ignored_proofs.append(f"{proof}: unreadable")
            continue
        target.proof_source = str(proof)
        for entry in entries if isinstance(entries, list) else []:
            num = fix_number(entry.get("finding")) if isinstance(entry, dict) else None
            if num is None:
                continue
            verdict = str(entry.get("verdict", "")).upper()
            if verdict == "RED":
                target.redproof.setdefault(num, "RED")
            else:  # one non-RED proof makes the finding unproved, whatever else ran
                target.redproof[num] = verdict or "UNKNOWN"


def match_run(runs: list[LedgerRun], repo: str, day: date,
              keys: set[str]) -> tuple[LedgerRun | None, str | None]:
    if not keys:
        return None, None
    need = math.ceil(len(keys) / 2)
    hits = [
        r for r in runs
        if r.repo.lower() == repo.lower() and r.day is not None
        and day - WINDOW_BEFORE <= r.day <= day + WINDOW_AFTER
        and len(keys & r.keys()) >= need
    ]
    if len(hits) == 1:
        return hits[0], None
    if len(hits) > 1:
        return None, "ambiguous: " + ", ".join(sorted(r.key for r in hits))
    return None, None


def merge_runs(runs: list[LedgerRun]) -> list[LedgerRun]:
    """One run per key, however many ledger files held a copy of it.

    A worktree's copy of a ledger diverges once it gains a later run, so a run is merged
    by key rather than by file: otherwise the same run loads twice and every lookup of
    it is ambiguous. A non-RED proof stays sticky across copies, as within one file.
    """
    merged: dict[str, LedgerRun] = {}
    for run in runs:
        kept = merged.get(run.key)
        if kept is None:
            merged[run.key] = run
            continue
        if run.day and (kept.day is None or run.day < kept.day):
            kept.day = run.day
        for n, row in run.dispositions.items():
            kept.dispositions.setdefault(n, row)
        for n, row in run.outcomes.items():
            kept.outcomes.setdefault(n, row)
        for fid, row in run.findings.items():
            kept.findings.setdefault(fid, row)
        for n, verdict in run.redproof.items():
            if verdict != "RED":
                kept.redproof[n] = verdict
            else:
                kept.redproof.setdefault(n, "RED")
        kept.proof_source = kept.proof_source or run.proof_source
    return list(merged.values())
