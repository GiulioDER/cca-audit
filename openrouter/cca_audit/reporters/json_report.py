"""JSON report generator."""

from __future__ import annotations

import json
from typing import Any

from cca_audit.consolidator import ConsolidatedFinding


def generate_json_report(
    findings: list[ConsolidatedFinding],
    audit_results: list[dict],
    total_raw: int,
) -> str:
    report: dict[str, Any] = {
        "summary": {
            "total_raw": total_raw,
            "total_unique": len(findings),
            "duplicates_removed": total_raw - len(findings),
            "p1_count": sum(1 for f in findings if f.priority == "P1"),
            "p2_count": sum(1 for f in findings if f.priority == "P2"),
            "p3_count": sum(1 for f in findings if f.priority == "P3"),
        },
        "sources": [
            {"auditor": r["auditor"], "status": r["status"]}
            for r in audit_results
        ],
        "findings": [
            {
                "id": f.id,
                "title": f.title,
                "severity": f.severity,
                "priority": f.priority,
                "file_location": f.file_location,
                "description": f.description[:500],
                "sources": f.sources,
            }
            for f in findings
        ],
    }
    return json.dumps(report, indent=2) + "\n"
