"""The ledger row, its tier, and the memo generated from it.

The tier is computed, never stored by hand, so a row cannot claim `proved` while
holding a condition that is only `claimed`. The memo is rendered from the row alone:
it adds no text the row does not hold, which is what keeps every memo traceable to
its sources.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import ClassVar

from .anchors import Anchor

EVIDENCED = "evidenced"
CLAIMED = "claimed"
ABSENT = "absent"
# A red proof the pipeline never asks for: audit-fix requires one only for a CONFIRMED
# P1 fix, so a P2 or P3 fix with no proof entry at all is not missing anything. Only
# forward rows set it, and only for red_proof.
NOT_REQUIRED = "not_required"
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
    # Finding-level places, computed during the run over the fixed code (anchors.py).
    anchors: list[Anchor] = field(default_factory=list)
    # bug_ids of earlier CCA fixes whose lines this fix modified or removed (caused_by.py).
    caused_by: list[str] = field(default_factory=list)
    kind: ClassVar[str] = "bug"

    @property
    def bug_id(self) -> str:
        base = f"{self.project}:{self.fix_commit[:SHA_LEN]}:{self.finding_id}"
        return base if self.ordinal == 1 else f"{base}~{self.ordinal}"

    @property
    def id(self) -> str:
        return self.bug_id

    @property
    def tier(self) -> str:
        if all(self.evidence.get(c) == EVIDENCED
               or (c == "red_proof" and self.evidence.get(c) == NOT_REQUIRED)
               for c in CONDITIONS):
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
            "anchors": [a.to_json() for a in self.anchors],
            "caused_by": list(self.caused_by),
            "kind": self.kind,
        }

    @classmethod
    def from_json(cls, row: dict) -> BugRow:
        """A ledger line read back; one written before anchors existed reads with none.

        `bug_id` and `tier` are derived, so they are recomputed rather than read.
        """
        return cls(
            project=row["project"], repo=row["repo"], finding_id=row["finding_id"],
            fix_commit=row["fix_commit"], date=row["date"], files=list(row["files"]),
            summary=row["summary"], severity=row.get("severity"), auditor=row.get("auditor"),
            category=row.get("category"), run_id=row.get("run_id"),
            evidence=dict(row.get("evidence") or {}), sources=list(row.get("sources") or []),
            supersedes=row.get("supersedes"), ordinal=int(row.get("ordinal", 1)),
            anchors=[Anchor(a["file"], a["symbol"], a["code_sha"])
                     for a in row.get("anchors") or []],
            caused_by=list(row.get("caused_by") or []),
        )


_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def _printable(text: str) -> str:
    # Commit subjects come from repositories the author may not control. A raw ESC or
    # BEL in one could rewrite or hide what the terminal shows, so control characters
    # are replaced before printing. --json is already safe: json.dumps escapes them.
    return _CONTROL.sub("�", text)


def memo_filename(bug_id: str) -> str:
    # ':' is not a legal filename character on Windows.
    return bug_id.replace(":", "__") + ".md"


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def render_memo(row: BugRow, modified: str) -> str:
    """The memo for `row`; the template lives in `records.render_memo`, which renders every kind."""
    from .records import render_memo as render

    return render(row, modified=modified)
