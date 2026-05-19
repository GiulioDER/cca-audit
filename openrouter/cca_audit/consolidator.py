"""Deduplication and prioritization of audit findings."""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class Finding:
    id: str
    title: str
    severity: str
    file_location: str
    description: str
    source_auditor: str
    fix: str = ""

    @property
    def severity_rank(self) -> int:
        return {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}.get(self.severity, 4)

    @property
    def priority(self) -> str:
        if self.severity in ("Critical",) or (
            self.severity == "High" and self.source_auditor == "security"
        ):
            return "P1"
        if self.severity == "High":
            return "P2"
        return "P3"


@dataclass
class ConsolidatedFinding:
    id: str
    title: str
    severity: str
    priority: str
    file_location: str
    description: str
    sources: list[str] = field(default_factory=list)
    fix: str = ""


def parse_findings(content: str, auditor_name: str) -> list[Finding]:
    """Extract findings from an auditor's markdown output."""
    findings: list[Finding] = []
    pattern = re.compile(
        r"###\s+([\w-]+):\s+(.+?)$",
        re.MULTILINE,
    )

    for match in pattern.finditer(content):
        finding_id = match.group(1)
        title = match.group(2).strip()
        start = match.end()
        next_match = pattern.search(content, start)
        block = content[start : next_match.start() if next_match else len(content)]

        severity = "Medium"
        for sev in ("Critical", "High", "Medium", "Low"):
            if sev in block[:200]:
                severity = sev
                break

        file_loc = ""
        loc_match = re.search(r"`([^`]+:\d+)`|File:\s*`([^`]+)`", block)
        if loc_match:
            file_loc = loc_match.group(1) or loc_match.group(2) or ""

        findings.append(
            Finding(
                id=finding_id,
                title=title,
                severity=severity,
                file_location=file_loc,
                description=block.strip()[:500],
                source_auditor=auditor_name,
            )
        )

    return findings


def deduplicate(all_findings: list[Finding]) -> list[ConsolidatedFinding]:
    """Merge findings that reference the same file:line or same issue type on same file."""
    by_location: dict[str, list[Finding]] = {}

    for f in all_findings:
        key = f.file_location if f.file_location else f"{f.source_auditor}:{f.id}"
        by_location.setdefault(key, []).append(f)

    consolidated: list[ConsolidatedFinding] = []
    idx = 1
    for _loc, group in by_location.items():
        best = min(group, key=lambda f: f.severity_rank)
        cf = ConsolidatedFinding(
            id=f"FIX-{idx:03d}",
            title=best.title,
            severity=best.severity,
            priority=best.priority,
            file_location=best.file_location,
            description=best.description,
            sources=[f"{f.source_auditor} ({f.id})" for f in group],
            fix=best.fix,
        )
        consolidated.append(cf)
        idx += 1

    consolidated.sort(key=lambda f: ({"P1": 0, "P2": 1, "P3": 2}.get(f.priority, 3)))
    return consolidated
