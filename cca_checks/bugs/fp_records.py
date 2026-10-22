"""False-positive records: written at `pipeline close`, not at merge.

A finding becomes a `false_positive` row only when every one of these holds, and each is a
guard of its own (see `build`):

* fp-check called it FALSE_POSITIVE with `fp_reason: not_real` (criteria a and c of Step 2.5);
* the L6 architect returned ACCEPT for it;
* the run's final L6 verdict is APPROVED;
* every anchor (its own place and each evidence location) resolved to code with a `code_sha`.

A FAST run writes no disposition ledger rows (Step 2.5b is STANDARD and DEEP only), so it has no
rows here and creates no record; its false positives still go to the architect at L6.

The anchors come from `fp_anchors.json`, which `pipeline record L2.5` computed over the code
fp-check judged, so a record pins that code and lapses once it changes. The rows do not wait
for the run's pull request: the hashes already say which code they are about.

Red proof (user rule, 2026-09-08): see the docstrings of `tests/test_fp_records.py`.
"""
from __future__ import annotations

import json
from pathlib import Path

from .anchors import Anchor, well_formed_file, well_formed_symbol
from .records import FP_REASON, FalsePositiveRow, no_nul

APPROVED = "APPROVED"
ACCEPT = "ACCEPT"
SCORECARD = ".claude/audits/AUDITOR_SCORECARD.jsonl"
REPORT = "fp_records.json"


def _text(value: object, default: str = "") -> str:
    return value if isinstance(value, str) else default


def _anchors(facts: object) -> list[Anchor] | None:
    """The record's anchors, or None when any place could not be pinned to its code."""
    if not isinstance(facts, dict) or facts.get("errors"):
        return None
    raw = facts.get("anchors")
    if not isinstance(raw, list) or not raw:
        return None
    out: list[Anchor] = []
    for item in raw:
        if not (isinstance(item, dict) and well_formed_file(item.get("file"))
                and well_formed_symbol(item.get("symbol"))
                and isinstance(item.get("code_sha"), str) and item["code_sha"]):
            return None
        out.append(Anchor(item["file"], item["symbol"], item["code_sha"]))
    return out


def _decisions(acceptance: object) -> dict[str, object]:
    entries = acceptance.get("fp_acceptance") if isinstance(acceptance, dict) else None
    return {e["id"]: e.get("decision") for e in entries or []
            if isinstance(e, dict) and isinstance(e.get("id"), str)}


def build(rows: list[dict], anchors: dict, acceptance: object, verdict: str, *,
          repo: str | None, date: str) -> tuple[list[FalsePositiveRow], list[str]]:
    """The false-positive rows a closed run yields, and "id: why not" for each one it does not.

    `rows` are the run's FALSE_POSITIVE disposition rows, `anchors` its `fp_anchors.json`,
    `acceptance` its stored L6 file and `verdict` its final L6 verdict.
    """
    picked, skipped = eligible(rows, anchors, acceptance, verdict)
    made, more = to_rows(picked, repo=repo, date=date)
    return made, skipped + more


def eligible(rows: list[dict], anchors: dict, acceptance: object,
             verdict: str) -> tuple[list[tuple[dict, list[Anchor]]], list[str]]:
    """The rows that pass every guard, each with its anchors, and "id: why not" for the rest."""
    if not rows:
        return [], []
    if str(verdict).strip().upper() != APPROVED:
        return [], [f"L6 verdict is {str(verdict).strip() or 'missing'}, not {APPROVED}"]
    decided = _decisions(acceptance)
    out: list[tuple[dict, list[Anchor]]] = []
    skipped: list[str] = []
    for row in rows:
        finding = row.get("finding_id")
        if not isinstance(finding, str) or not finding:
            skipped.append("a FALSE_POSITIVE row with no finding_id")
            continue
        if row.get("fp_reason") != FP_REASON:
            skipped.append(f"{finding}: fp_reason {row.get('fp_reason')!r}")
            continue
        if decided.get(finding) != ACCEPT:
            skipped.append(f"{finding}: L6 {decided.get(finding) or 'gave no decision'}")
            continue
        facts = anchors.get(finding)
        where = _anchors(facts)
        if where is None:
            skipped.append(f"{finding}: an anchor did not resolve")
            continue
        # The finding's own fields as L2 recorded them win over the row's (record L2.5).
        recorded = facts.get("finding") if isinstance(facts, dict) else None
        out.append(({**row, **recorded} if isinstance(recorded, dict) else row, where))
    return out, skipped


def to_rows(picked: list[tuple[dict, list[Anchor]]], *, repo: str | None,
            date: str) -> tuple[list[FalsePositiveRow], list[str]]:
    """The records of the eligible rows; none without an owner/name repository."""
    if not picked:
        return [], []
    if not repo or "/" not in repo:
        return [], ["no owner/name repository for this checkout"]
    out: list[FalsePositiveRow] = []
    for row, where in picked:
        refs = row.get("evidence_refs")
        out.append(FalsePositiveRow(
            project=repo.split("/", 1)[1], repo=repo, date=date,
            run_id=_text(row.get("run_id")), finding_id=row["finding_id"],
            category=_text(row.get("category")), severity=_text(row.get("severity")),
            priority=_text(row.get("priority")), high_stakes=row.get("high_stakes") is True,
            auditor=_text(row.get("auditor")), claim=_text(row.get("claim")),
            evidence_refs=[r for r in refs if isinstance(r, str)] if isinstance(refs, list)
            else [],
            verify_source=_text(row.get("verify_source")), anchors=where))
    # Claims and evidence are model output: no U+0000 reaches the ledger or a memo (#88).
    return [no_nul(r) for r in out], []


def _json(path: Path) -> object | None:
    try:
        return json.loads(path.read_bytes())
    except (OSError, ValueError):
        return None


def fp_rows(root: Path, run_id: str) -> list[dict]:
    """The FALSE_POSITIVE disposition rows of `run_id` in the checkout's scorecard."""
    path = Path(root) / SCORECARD
    try:
        lines = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("run_id") == run_id \
                and row.get("verdict") == "FALSE_POSITIVE":
            out.append(row)
    return out


def eligible_for_run(root: Path, run_id: str,
                     verdict: str) -> tuple[list[tuple[dict, list[Anchor]]], list[str]]:
    """`eligible` over the files of `run_id` in the checkout at `root`."""
    run_dir = Path(root) / ".claude" / "audits" / "runs" / run_id
    anchors = _json(run_dir / "fp_anchors.json")
    return eligible(fp_rows(root, run_id), anchors if isinstance(anchors, dict) else {},
                    _json(run_dir / "L6.json"), verdict)
