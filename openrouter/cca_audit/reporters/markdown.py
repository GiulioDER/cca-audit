"""Markdown report generator."""

from __future__ import annotations

from cca_audit.consolidator import ConsolidatedFinding


def generate_fixes_md(
    findings: list[ConsolidatedFinding],
    audit_results: list[dict],
    total_raw: int,
) -> str:
    p1 = [f for f in findings if f.priority == "P1"]
    p2 = [f for f in findings if f.priority == "P2"]
    p3 = [f for f in findings if f.priority == "P3"]

    lines = [
        "# Consolidated Fix Plan\n",
        "## Summary",
        f"| Priority | Count |",
        f"|----------|-------|",
        f"| P1 (Critical) | {len(p1)} |",
        f"| P2 (High) | {len(p2)} |",
        f"| P3 (Deferred) | {len(p3)} |",
        "",
        f"**Total unique findings:** {len(findings)} (from {total_raw} raw across {len(audit_results)} auditors)",
        f"**Duplicates removed:** {total_raw - len(findings)}",
        "",
        "## Sources Consulted",
        "| Auditor | Status | Findings |",
        "|---------|--------|----------|",
    ]

    for r in audit_results:
        lines.append(f"| {r['auditor']} | {r['status']} | {r.get('finding_count', '?')} |")

    if p1:
        lines.extend(["", "---", "", "## P1 -- Critical (Fix Immediately)", ""])
        for f in p1:
            lines.extend(_format_finding(f))

    if p2:
        lines.extend(["", "---", "", "## P2 -- High (Fix Now)", ""])
        for f in p2:
            lines.extend(_format_finding(f))

    if p3:
        lines.extend(["", "---", "", "## P3 -- Deferred", ""])
        for f in p3:
            lines.append(f"- {f.id}: {f.title} ({f.file_location})")

    return "\n".join(lines) + "\n"


def _format_finding(f: ConsolidatedFinding) -> list[str]:
    return [
        f"### [ ] {f.id}: {f.title}",
        f"**Priority:** {f.priority} ({f.severity})",
        f"**Source:** {', '.join(f.sources)}",
        f"**File:** `{f.file_location}`" if f.file_location else "",
        f"**Issue:** {f.description[:300]}",
        "",
    ]
