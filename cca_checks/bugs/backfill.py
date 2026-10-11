"""Rebuild the historical tier: landed fix commits, joined to whatever ledger evidence exists.

Every condition starts from what the commit alone can show: `fixed` is evidenced (the
commit is on the default branch), the rest are `claimed` because the commit text says
so, and `red_proof` is `absent` unless the bullet even mentions a test. A matched
ledger run can then upgrade a condition to `evidenced`, or mark it `absent` where the
record contradicts the claim (a FALSE_POSITIVE verdict, a BLOCKED architect, a
TAUTOLOGICAL proof).
"""
from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .gitread import default_ref, fix_commits, group_clones
from .ledgers import (
    LedgerRun,
    LoadReport,
    fix_number,
    ledger_repo,
    load_ledger,
    match_key,
    match_run,
)
from .model import ABSENT, CLAIMED, CONDITIONS, EVIDENCED, BugRow
from .parse import parse_message

TEST_HINT = re.compile(r"\+\s*test|\btest_\w+|\bred\b", re.IGNORECASE)


@dataclass
class BackfillResult:
    rows: list[BugRow] = field(default_factory=list)
    unparsed: list[str] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)
    ambiguous: list[str] = field(default_factory=list)
    malformed_ledger_lines: int = 0
    ignored_proofs: list[str] = field(default_factory=list)


def _apply_ledger(row: BugRow, run: LedgerRun) -> None:
    n = fix_number(row.finding_id)
    disp = run.dispositions.get(n) if n is not None else run.findings.get(row.finding_id)
    if disp is not None:
        confirmed = str(disp.get("verdict", "")).upper() == "CONFIRMED"
        row.evidence["confirmed_l25"] = EVIDENCED if confirmed else ABSENT
        row.sources.append(f"ledger:{run.source}#L{disp['_line']}")
        if disp.get("priority") in ("P1", "P2", "P3"):
            row.severity = disp["priority"]
        row.category = disp.get("category") or row.category
        row.auditor = disp.get("auditor") or row.auditor
        if n is None:
            n = fix_number(disp.get("fix_id"))
    if n is not None:
        out = run.outcomes.get(n)
        if out is not None:
            verdict = str(out.get("architect_verdict", "")).upper()
            if verdict.startswith("APPROVED"):
                row.evidence["l6_approved"] = EVIDENCED
            elif verdict in ("BLOCKED", "REVISE"):
                row.evidence["l6_approved"] = ABSENT
            row.sources.append(f"ledger:{run.source}#L{out['_line']}")
        proof = run.redproof.get(n)
        if proof is not None:
            row.evidence["red_proof"] = EVIDENCED if proof == "RED" else ABSENT
            row.sources.append(f"redproof:{run.proof_source}")
    if run.run_id:
        row.run_id = run.run_id


def _norm(text: str) -> str:
    return " ".join(text.lower().split())


def _dedupe(rows: list[BugRow]) -> list[BugRow]:
    best: dict[tuple[str, str, str], BugRow] = {}
    for row in rows:
        key = (row.repo.lower(), row.finding_id, _norm(row.summary))
        kept = best.get(key)
        if kept is None or (row.date, row.fix_commit) < (kept.date, kept.fix_commit):
            best[key] = row
    return sorted(best.values(), key=lambda r: (r.date, r.repo, r.fix_commit, r.finding_id))


def _load_runs(ledgers: list[tuple[Path, str | None]], result: BackfillResult) -> list[LedgerRun]:
    report = LoadReport()
    runs: list[LedgerRun] = []
    seen: set[str] = set()
    for path, repo in ledgers:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest in seen:  # the same ledger copied into a worktree
            continue
        seen.add(digest)
        repo = repo or ledger_repo(path)
        if repo is None:
            result.skipped.append((str(path), "ledger: cannot tell which repository it belongs to"))
            continue
        runs.extend(load_ledger(path, repo, report))
    result.malformed_ledger_lines = report.malformed
    result.ignored_proofs = report.ignored_proofs
    return runs


def backfill(clones: list[Path], ledgers: list[tuple[Path, str | None]]) -> BackfillResult:
    result = BackfillResult()
    runs = _load_runs(ledgers, result)
    repos, skipped = group_clones(clones)
    result.skipped.extend(skipped)
    rows: list[BugRow] = []
    for repo, clone in repos.items():
        ref = default_ref(clone)
        if ref is None:
            result.skipped.append((str(clone), "no origin default branch"))
            continue
        project = repo.split("/", 1)[1]
        for c in fix_commits(clone, ref):
            bullets = parse_message(c.body)
            if not bullets:
                result.unparsed.append(f"{repo} {c.sha[:12]} {c.subject}")
                continue
            keys = {match_key(b.finding_id) for b in bullets}
            run, problem = match_run(runs, repo, date.fromisoformat(c.date), keys)
            if problem:
                result.ambiguous.append(f"{repo} {c.sha[:12]}: {problem}")
            for b in bullets:
                is_fix_id = fix_number(b.finding_id) is not None
                row = BugRow(
                    project=project, repo=repo, finding_id=b.finding_id, fix_commit=c.sha,
                    date=c.date, files=list(c.files), summary=b.summary, severity=b.severity,
                    auditor=None if is_fix_id else b.finding_id.rsplit("-", 1)[0],
                    evidence={
                        "confirmed_l25": CLAIMED,
                        "fixed": EVIDENCED,
                        "l6_approved": CLAIMED,
                        "red_proof": CLAIMED if TEST_HINT.search(b.summary) else ABSENT,
                    },
                    sources=[f"git:{c.sha}"],
                )
                if run is not None:
                    _apply_ledger(row, run)
                rows.append(row)
    result.rows = _dedupe(rows)
    return result


def summarize(result: BackfillResult) -> dict:
    rows = result.rows
    return {
        "rows": len(rows),
        "by_repo": dict(Counter(r.repo for r in rows)),
        "tiers": dict(Counter(r.tier for r in rows)),
        "evidence": {c: dict(Counter(r.evidence[c] for r in rows)) for c in CONDITIONS},
        "unparsed": len(result.unparsed),
        "unparsed_commits": list(result.unparsed),
        "skipped": [f"{p}: {why}" for p, why in result.skipped],
        "ambiguous": list(result.ambiguous),
        "malformed_ledger_lines": result.malformed_ledger_lines,
        "ignored_proofs": list(result.ignored_proofs),
    }
