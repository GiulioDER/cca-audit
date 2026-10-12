"""Rebuild the historical tier: landed fix commits, joined to ledger evidence the author binds.

Every condition starts from what the commit alone can show: `fixed` is evidenced (the
commit is on the default branch and was not reverted), the rest are `claimed` because
the commit text says so, and `red_proof` is `absent` unless the bullet even mentions a
test.

Ledger evidence applies only through an explicit binding, `--bind SHA=RUN`. Finding IDs
restart in every run (every run has a FIX-1, and most have a SEC-001), so a run that
merely shares IDs with a commit inside a date window may be about other fixes entirely,
and its artifacts must never make a bug `proved`. Such runs are reported as candidates,
each with the binding that would confirm it. A bound run can upgrade a condition to
`evidenced`, or mark it `absent` where the record contradicts the claim (a FALSE_POSITIVE
verdict, a BLOCKED architect, a TAUTOLOGICAL proof).
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .gitread import default_ref, fix_commits, group_clones, is_undone, revert_map
from .ledgers import (
    LedgerRun,
    LoadReport,
    fix_number,
    ledger_repo,
    load_ledger,
    match_key,
    match_run,
    merge_runs,
)
from .model import ABSENT, CLAIMED, CONDITIONS, EVIDENCED, SHA_LEN, BugRow
from .parse import KNOWN_PREFIXES, parse_message

TEST_HINT = re.compile(r"\+\s*test|\btest_\w+|\bred\b", re.IGNORECASE)


@dataclass
class BackfillResult:
    rows: list[BugRow] = field(default_factory=list)
    unparsed: list[str] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)
    ambiguous: list[str] = field(default_factory=list)
    candidates: list[str] = field(default_factory=list)
    unused_bindings: list[str] = field(default_factory=list)
    unknown_ids: list[str] = field(default_factory=list)
    reverted: list[str] = field(default_factory=list)
    cross_repo: list[str] = field(default_factory=list)
    malformed_ledger_lines: int = 0
    ignored_proofs: list[str] = field(default_factory=list)


def _label(run: LedgerRun) -> str:
    return run.run_id or run.key


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
    """One row per finding and summary within a repository: a squash merge repeats bullets."""
    best: dict[tuple[str, str, str], BugRow] = {}
    for row in rows:
        key = (row.repo.lower(), row.finding_id, _norm(row.summary))
        kept = best.get(key)
        if kept is None or (row.date, row.fix_commit) < (kept.date, kept.fix_commit):
            best[key] = row
    return sorted(best.values(), key=lambda r: (r.date, r.repo, r.fix_commit, r.finding_id))


def _assign_ordinals(rows: list[BugRow]) -> None:
    seen: Counter = Counter()
    for row in rows:
        key = (row.repo.lower(), row.fix_commit, row.finding_id)
        seen[key] += 1
        row.ordinal = seen[key]


def _drop_cross_repo(rows: list[BugRow], result: BackfillResult) -> list[BugRow]:
    """A fork or a lab copy carries the same commits: count each commit's finding once."""
    first: dict[tuple[str, str, int], BugRow] = {}
    kept: list[BugRow] = []
    for row in rows:
        key = (row.fix_commit, row.finding_id, row.ordinal)
        if key in first and first[key].repo.lower() != row.repo.lower():
            result.cross_repo.append(
                f"{row.repo} {row.fix_commit[:SHA_LEN]} {row.finding_id}: "
                f"same commit as {first[key].repo}, counted there"
            )
            continue
        first.setdefault(key, row)
        kept.append(row)
    return kept


def _load_runs(ledgers: list[tuple[Path, str | None]], result: BackfillResult) -> list[LedgerRun]:
    report = LoadReport()
    runs: list[LedgerRun] = []
    for path, repo in ledgers:
        repo = repo or ledger_repo(path)
        if repo is None:
            result.skipped.append((str(path), "ledger: cannot tell which repository it belongs to"))
            continue
        runs.extend(load_ledger(path, repo, report))
    result.malformed_ledger_lines = report.malformed
    result.ignored_proofs = report.ignored_proofs
    return merge_runs(runs)


def _bound_run(runs: list[LedgerRun], repo: str, sha: str, bindings: dict[str, str],
               used: set[str], result: BackfillResult) -> LedgerRun | None:
    for prefix, label in bindings.items():
        if not sha.startswith(prefix.lower()):
            continue
        used.add(prefix)
        named = [r for r in runs
                 if r.repo.lower() == repo.lower() and label in (r.run_id, r.key)]
        if len(named) == 1:
            return named[0]
        result.ambiguous.append(
            f"{repo} {sha[:SHA_LEN]}: binding {prefix}={label} names {len(named)} runs"
        )
    return None


def _row(repo: str, commit, bullet) -> BugRow:
    # Only an auditor-style ID names its auditor: FIX-n does not, and neither does a
    # stand-in ID such as P1-1.
    prefix = bullet.finding_id.rsplit("-", 1)[0]
    auditor = prefix if prefix in KNOWN_PREFIXES and prefix != "FIX" else None
    return BugRow(
        project=repo.split("/", 1)[1], repo=repo, finding_id=bullet.finding_id,
        fix_commit=commit.sha, date=commit.date, files=list(commit.files),
        summary=bullet.summary, severity=bullet.severity,
        auditor=auditor,
        evidence={
            "confirmed_l25": CLAIMED,
            "fixed": EVIDENCED,
            "l6_approved": CLAIMED,
            "red_proof": CLAIMED if TEST_HINT.search(bullet.summary) else ABSENT,
        },
        sources=[f"git:{commit.sha}"],
    )


def backfill(clones: list[Path], ledgers: list[tuple[Path, str | None]],
             bindings: dict[str, str] | None = None) -> BackfillResult:
    result = BackfillResult()
    bindings = bindings or {}
    used: set[str] = set()
    runs = _load_runs(ledgers, result)
    repos, skipped = group_clones(clones)
    result.skipped.extend(skipped)
    rows: list[BugRow] = []
    for repo, clone in repos.items():
        ref = default_ref(clone)
        if ref is None:
            result.skipped.append((str(clone), "no origin default branch"))
            continue
        reverts = revert_map(clone, ref)
        for c in fix_commits(clone, ref):
            if is_undone(c.sha, reverts):
                result.reverted.append(f"{repo} {c.sha[:SHA_LEN]} {c.subject}")
                continue
            rejected: list[str] = []
            bullets = parse_message(c.body, rejected)
            result.unknown_ids.extend(f"{repo} {c.sha[:SHA_LEN]} {i}" for i in rejected)
            if not bullets:
                result.unparsed.append(f"{repo} {c.sha[:SHA_LEN]} {c.subject}")
                continue
            run = _bound_run(runs, repo, c.sha, bindings, used, result)
            if run is None:
                keys = {match_key(b.finding_id) for b in bullets}
                candidate, problem = match_run(runs, repo, date.fromisoformat(c.date), keys)
                if problem:
                    result.ambiguous.append(f"{repo} {c.sha[:SHA_LEN]}: {problem}")
                if candidate is not None:
                    result.candidates.append(
                        f"{repo} {c.sha[:SHA_LEN]}: shares IDs with run {_label(candidate)}; "
                        f"if that run produced this commit, confirm with "
                        f"--bind {c.sha[:SHA_LEN]}={_label(candidate)}"
                    )
            for b in bullets:
                row = _row(repo, c, b)
                if run is not None:
                    _apply_ledger(row, run)
                rows.append(row)
    result.unused_bindings = [f"{p}={lab}" for p, lab in bindings.items() if p not in used]
    rows = _dedupe(rows)
    _assign_ordinals(rows)
    result.rows = _drop_cross_repo(rows, result)
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
        "candidates": list(result.candidates),
        "unused_bindings": list(result.unused_bindings),
        "unknown_ids": list(result.unknown_ids),
        "reverted": list(result.reverted),
        "cross_repo": list(result.cross_repo),
        "malformed_ledger_lines": result.malformed_ledger_lines,
        "ignored_proofs": list(result.ignored_proofs),
    }
