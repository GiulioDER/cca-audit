"""Tests for finding deduplication and prioritization."""

from cca_audit.consolidator import ConsolidatedFinding, Finding, deduplicate, parse_findings


def test_parse_findings_basic() -> None:
    content = """
### SEC-001: SQL Injection in User Query
**CVSS Score:** 9.8 (Critical)
**Location:** `src/api/users.py:47`
Some description here.

### SEC-002: Missing Auth
**Severity:** High
**Location:** `src/api/admin.py:12`
Another description.
"""
    findings = parse_findings(content, "security")
    assert len(findings) == 2
    assert findings[0].id == "SEC-001"
    assert findings[0].severity == "Critical"
    assert findings[0].file_location == "src/api/users.py:47"
    assert findings[1].severity == "High"


def test_dedup_same_location() -> None:
    findings = [
        Finding("SEC-001", "SQL Injection", "Critical", "src/db.py:10", "desc", "security"),
        Finding("BUG-003", "Unvalidated input", "High", "src/db.py:10", "desc", "bug"),
    ]
    consolidated = deduplicate(findings)
    assert len(consolidated) == 1
    assert consolidated[0].severity == "Critical"
    assert len(consolidated[0].sources) == 2


def test_dedup_different_locations() -> None:
    findings = [
        Finding("SEC-001", "Injection", "Critical", "src/a.py:10", "desc", "security"),
        Finding("BUG-001", "Null ref", "High", "src/b.py:20", "desc", "bug"),
    ]
    consolidated = deduplicate(findings)
    assert len(consolidated) == 2


def test_priority_assignment() -> None:
    f1 = Finding("SEC-001", "Injection", "Critical", "a.py:1", "desc", "security")
    assert f1.priority == "P1"

    f2 = Finding("SEC-002", "Missing rate limit", "High", "b.py:2", "desc", "security")
    assert f2.priority == "P1"

    f3 = Finding("CODE-001", "DRY violation", "High", "c.py:3", "desc", "code")
    assert f3.priority == "P2"

    f4 = Finding("DOC-001", "Missing docstring", "Low", "d.py:4", "desc", "doc")
    assert f4.priority == "P3"


def test_sorted_by_priority() -> None:
    findings = [
        Finding("DOC-001", "Missing docs", "Low", "d.py:4", "desc", "doc"),
        Finding("SEC-001", "Injection", "Critical", "a.py:1", "desc", "security"),
        Finding("CODE-001", "DRY", "High", "c.py:3", "desc", "code"),
    ]
    consolidated = deduplicate(findings)
    assert consolidated[0].priority == "P1"
    assert consolidated[-1].priority == "P3"
