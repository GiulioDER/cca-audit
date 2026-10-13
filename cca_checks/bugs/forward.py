"""Ledger rows for one captured run whose pull request merged.

Built only from the run's own files and the merge GitHub reported, never from a commit
message, so a squash merge (whose message does not carry CCA's fix bullets) loses nothing.
The row is keyed by the merge commit, the same `bug_id` a backfill of that commit makes.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .ledgers import fix_number
from .model import ABSENT, CLAIMED, EVIDENCED, BugRow


_SAFE_ID = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,63}")


@dataclass(frozen=True)
class Merge:
    repo: str
    sha: str
    date: str
    pr: int


def _load(record: Path, name: str, default):
    path = record / name
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _scorecard(record: Path) -> list[dict]:
    path = record / "scorecard.jsonl"
    rows = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def _same(a: str, b: str) -> bool:
    na, nb = fix_number(a), fix_number(b)
    return a == b or (na is not None and na == nb)


def _architect(rows: list[dict], fix_id: str) -> str:
    for row in reversed(rows):  # the last verdict decides, as in backfill
        if _same(str(row.get("fix_id") or ""), fix_id) and "architect_verdict" in row:
            verdict = str(row["architect_verdict"]).upper()
            if verdict.startswith("APPROVED"):
                return EVIDENCED
            if verdict in ("BLOCKED", "REVISE"):
                return ABSENT
    return CLAIMED


def _proof(proofs: list[dict], finding: str, fix_id: str) -> str:
    verdicts = [str(p.get("verdict", "")).upper() for p in proofs
                if _same(str(p.get("finding", "")), fix_id) or p.get("finding") == finding]
    return EVIDENCED if verdicts and all(v == "RED" for v in verdicts) else ABSENT


def rows_for(record: Path, merge: Merge) -> list[BugRow]:
    meta = _load(record, "meta.json", {})
    l2 = {f.get("id"): f for f in _load(record, "L2.json", []) if isinstance(f, dict)}
    l25 = {v.get("id"): v.get("verdict") for v in _load(record, "L2.5.json", [])
           if isinstance(v, dict)}
    proofs = [p for p in _load(record, "L5.6.json", []) if isinstance(p, dict)]
    card = _scorecard(record)
    run_id = meta.get("run_id")
    out = []
    for entry in _load(record, "L4.json", []):
        if not isinstance(entry, dict):
            continue
        fix_id = str(entry.get("fix_id") or "")
        finding = entry.get("finding") or entry.get("finding_id") or fix_id
        # The id becomes a memo filename, and L4 comes from untrusted places.
        if (not isinstance(finding, str) or not _SAFE_ID.fullmatch(finding)
                or not finding.strip(".")):
            continue
        found = l2.get(finding, {})
        out.append(BugRow(
            project=merge.repo.split("/", 1)[1], repo=merge.repo, finding_id=finding,
            fix_commit=merge.sha, date=merge.date, files=list(entry.get("files") or []),
            summary=found.get("claim") or entry.get("summary") or "(no description recorded)",
            severity=found.get("priority"), category=found.get("category"),
            auditor=found.get("auditor"), run_id=run_id,
            evidence={
                "confirmed_l25": EVIDENCED if l25.get(finding) == "CONFIRMED" else ABSENT,
                "fixed": EVIDENCED,
                "l6_approved": _architect(card, fix_id),
                "red_proof": _proof(proofs, finding, fix_id),
            },
            sources=[f"run:{merge.repo}:{run_id}", f"pr:{merge.repo}#{merge.pr}",
                     f"git:{merge.sha}"],
        ))
    return out
