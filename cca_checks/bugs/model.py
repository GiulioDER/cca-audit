"""The ledger row, its tier, and the memo generated from it.

The tier is computed, never stored by hand, so a row cannot claim `proved` while
holding a condition that is only `claimed`. The memo is rendered from the row alone:
it adds no text the row does not hold, which is what keeps every memo traceable to
its sources.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

EVIDENCED = "evidenced"
CLAIMED = "claimed"
ABSENT = "absent"
CONDITIONS = ("confirmed_l25", "fixed", "l6_approved", "red_proof")
SHA_LEN = 12
# A full commit sha as git prints it. Lowercase only: GitHub answers in lowercase, and
# a record that resolve accepts must be one that github.lookup accepts too.
SHA40 = re.compile(r"[0-9a-f]{40}")


@dataclass
class BugRow:
    project: str
    repo: str
    finding_id: str
    fix_commit: str
    date: str
    files: list[str]
    summary: str
    severity: str | None = None
    auditor: str | None = None
    category: str | None = None
    run_id: str | None = None
    evidence: dict[str, str] = field(default_factory=dict)
    sources: list[str] = field(default_factory=list)
    supersedes: str | None = None
    # A squash of two CCA commits can carry FIX-1 twice: finding IDs restart in every
    # run. The second one in the same commit is ordinal 2, and its bug_id says so.
    ordinal: int = 1

    @property
    def bug_id(self) -> str:
        base = f"{self.project}:{self.fix_commit[:SHA_LEN]}:{self.finding_id}"
        return base if self.ordinal == 1 else f"{base}~{self.ordinal}"

    @property
    def tier(self) -> str:
        if all(self.evidence.get(c) == EVIDENCED for c in CONDITIONS):
            return "proved"
        return "historical"

    def to_json(self) -> dict:
        return {
            "bug_id": self.bug_id,
            "tier": self.tier,
            "date": self.date,
            "project": self.project,
            "repo": self.repo,
            "files": list(self.files),
            "finding_id": self.finding_id,
            "category": self.category,
            "severity": self.severity,
            "auditor": self.auditor,
            "fix_commit": self.fix_commit,
            "run_id": self.run_id,
            "evidence": {c: self.evidence.get(c, ABSENT) for c in CONDITIONS},
            "sources": list(self.sources),
            "summary": self.summary,
            "supersedes": self.supersedes,
            "ordinal": self.ordinal,
        }


def memo_filename(bug_id: str) -> str:
    # ':' is not a legal filename character on Windows.
    return bug_id.replace(":", "__") + ".md"


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def render_memo(row: BugRow, modified: str) -> str:
    flat = " ".join(row.summary.split())
    description = f"{row.project} {row.finding_id} ({row.tier}): {flat[:120]}"
    lines = [
        "---",
        f"name: {_slug(row.bug_id)}",
        f"description: {json.dumps(description, ensure_ascii=False)}",
        f"valid_from: {row.date}",
        "metadata:",
        "  node_type: memory",
        "  type: project",
        "  originSessionId: cca-bugs",
        f"  modified: {modified}",
        "---",
        "",
        f"# {row.project} {row.finding_id}: a bug CCA fixed",
        "",
        "## The bug",
        "",
        row.summary,
        "",
        "## The fix",
        "",
        f"Commit `{row.fix_commit}` in `{row.repo}`, {row.date}. Files changed by that commit:",
        "",
        *[f"* `{f}`" for f in row.files],
        "",
        "## The evidence",
        "",
        f"Tier: **{row.tier}**.",
        "",
        "| Condition | Value |",
        "|---|---|",
        *[f"| {c} | {row.evidence.get(c, ABSENT)} |" for c in CONDITIONS],
        "",
        "Sources: " + ", ".join(f"`{s}`" for s in row.sources),
        "",
    ]
    return "\n".join(lines)
