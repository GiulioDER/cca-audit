"""Async 7-step audit pipeline orchestrator."""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from typing import Any

import httpx
from rich.console import Console
from rich.table import Table

from cca_audit.auditors import AUDITOR_REGISTRY
from cca_audit.config import Config
from cca_audit.consolidator import ConsolidatedFinding, deduplicate, parse_findings
from cca_audit.detector import ProjectInfo, detect_project
from cca_audit.reporters.json_report import generate_json_report
from cca_audit.reporters.markdown import generate_fixes_md
from cca_audit.reviewer import run_review

console = Console()


async def run_pipeline(
    config: Config,
    no_fix: bool = False,
    p1_only: bool = False,
    dry_run: bool = False,
    commit: int | None = None,
    files: list[str] | None = None,
) -> dict[str, Any]:
    root = Path.cwd()
    output_dir = root / config.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    # Step 0: Detect changed files
    console.print("\n[bold]=== Step 0: Detecting changed files ===[/bold]")
    changed_files, diff_cmd = _detect_files(commit, files)
    if not changed_files:
        console.print("[yellow]No changed files to audit.[/yellow]")
        return {"status": "SKIPPED", "reason": "no files"}

    console.print(f"Auditing {len(changed_files)} files:")
    for f in changed_files[:20]:
        console.print(f"  {f}")
    if len(changed_files) > 20:
        console.print(f"  ... and {len(changed_files) - 20} more")

    # Step 0.5: Language detection
    console.print("\n[bold]=== Step 0.5: Language detection ===[/bold]")
    project = detect_project(root, changed_files)
    console.print(f"Languages: {project.languages_str}")
    if project.test_cmd:
        console.print(f"Test command: {project.test_cmd}")
    if project.lint_cmd:
        console.print(f"Lint command: {project.lint_cmd}")

    diff_content = _get_diff(diff_cmd)

    if dry_run:
        console.print("\n[yellow]Dry run — would launch auditors for these files.[/yellow]")
        return {"status": "DRY_RUN", "files": changed_files, "project": project}

    # Step 1: Parallel auditors
    console.print("\n[bold]=== Step 1: Launching parallel auditors ===[/bold]")
    audit_results = await _run_auditors(config, project, changed_files, diff_cmd, diff_content)

    for r in audit_results:
        status_icon = "[green]OK[/green]" if r["status"] == "COMPLETE" else "[red]ERR[/red]"
        console.print(f"  {r['auditor']}: {status_icon} ({r['duration']}s)")

    # Save individual reports
    for r in audit_results:
        (output_dir / r["output_file"]).write_text(r["content"], encoding="utf-8")

    # Step 2: Consolidate
    console.print("\n[bold]=== Step 2: Consolidating findings ===[/bold]")
    all_findings = []
    for r in audit_results:
        parsed = parse_findings(r["content"], r["auditor"])
        r["finding_count"] = len(parsed)
        all_findings.extend(parsed)

    consolidated = deduplicate(all_findings)
    total_raw = len(all_findings)
    console.print(f"  {total_raw} raw findings → {len(consolidated)} unique after dedup")

    # Generate report
    if config.output_format == "json":
        report = generate_json_report(consolidated, audit_results, total_raw)
        (output_dir / "FIXES.json").write_text(report, encoding="utf-8")
    else:
        report = generate_fixes_md(consolidated, audit_results, total_raw)
        (output_dir / "FIXES.md").write_text(report, encoding="utf-8")

    _print_summary_table(consolidated)

    if no_fix:
        console.print("\n[yellow]=== Audit complete (no-fix mode) ===[/yellow]")
        return _build_result(audit_results, consolidated, total_raw, "AUDIT_ONLY")

    # Steps 3-4: Fix plan + implementation (report only — fixes require tool access)
    console.print("\n[bold]=== Steps 3-4: Fix plan ===[/bold]")
    p1 = [f for f in consolidated if f.priority == "P1"]
    p2 = [f for f in consolidated if f.priority == "P2"]
    p3 = [f for f in consolidated if f.priority == "P3"]
    to_fix = p1 + ([] if p1_only else p2)
    console.print(
        f"  P1: {len(p1)} | P2: {len(p2)} | P3 deferred: {len(p3)} | To fix: {len(to_fix)}"
    )
    console.print(
        "[dim]Note: The OpenRouter variant reports findings but cannot auto-fix. "
        "Use the Claude Code or Codex variant for auto-fix.[/dim]"
    )

    # Step 5: Verification info
    console.print("\n[bold]=== Step 5: Verification ===[/bold]")
    if project.test_cmd:
        console.print(f"  Run: {project.test_cmd}")
    if project.lint_cmd:
        console.print(f"  Run: {project.lint_cmd}")

    # Step 6: Architect review
    console.print("\n[bold]=== Step 6: Architect review ===[/bold]")
    async with httpx.AsyncClient() as client:
        review = await run_review(client, config, report, diff_content)
    console.print(f"  Verdict: [bold]{review['verdict']}[/bold]")
    (output_dir / "REVIEW.md").write_text(review["content"], encoding="utf-8")

    return _build_result(audit_results, consolidated, total_raw, review["verdict"])


async def _run_auditors(
    config: Config,
    project: ProjectInfo,
    files: list[str],
    diff_cmd: str,
    diff_content: str,
) -> list[dict[str, Any]]:
    async with httpx.AsyncClient() as client:
        tasks = []
        for name in config.auditors:
            auditor_cls = AUDITOR_REGISTRY.get(name)
            if not auditor_cls:
                continue
            auditor = auditor_cls(config, project)
            tasks.append(auditor.run(client, files, diff_cmd, diff_content))
        return list(await asyncio.gather(*tasks))


def _detect_files(
    commit: int | None, explicit_files: list[str] | None
) -> tuple[list[str], str]:
    if explicit_files:
        return explicit_files, "git diff HEAD -- " + " ".join(explicit_files)

    if commit:
        cmd = f"git diff HEAD~{commit} --name-only --diff-filter=ACMR"
        diff_cmd = f"git diff HEAD~{commit}"
    else:
        cmd = "git diff --name-only --diff-filter=ACMR HEAD"
        diff_cmd = "git diff HEAD"

    try:
        result = subprocess.run(cmd.split(), capture_output=True, text=True, timeout=30)
        files = [f.strip() for f in result.stdout.strip().split("\n") if f.strip()]
    except (subprocess.TimeoutExpired, FileNotFoundError):
        files = []

    if not files and not commit:
        try:
            result = subprocess.run(
                "git diff --name-only HEAD~1".split(), capture_output=True, text=True, timeout=30
            )
            files = [f.strip() for f in result.stdout.strip().split("\n") if f.strip()]
            diff_cmd = "git diff HEAD~1"
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

    return files, diff_cmd


def _get_diff(diff_cmd: str) -> str:
    try:
        result = subprocess.run(diff_cmd.split(), capture_output=True, text=True, timeout=60)
        return result.stdout[:16000]
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""


def _print_summary_table(findings: list[ConsolidatedFinding]) -> None:
    table = Table(title="Consolidated Findings")
    table.add_column("ID", style="cyan")
    table.add_column("Priority", style="bold")
    table.add_column("Finding")
    table.add_column("Severity")
    table.add_column("File")
    table.add_column("Sources")

    for f in findings:
        style = {"P1": "red", "P2": "yellow", "P3": "dim"}.get(f.priority, "")
        table.add_row(
            f.id, f.priority, f.title[:50], f.severity, f.file_location[:40],
            ", ".join(s.split(" (")[0] for s in f.sources),
            style=style,
        )

    console.print(table)


def _build_result(
    audit_results: list[dict],
    findings: list[ConsolidatedFinding],
    total_raw: int,
    verdict: str,
) -> dict[str, Any]:
    return {
        "status": "COMPLETE",
        "total_raw": total_raw,
        "total_unique": len(findings),
        "p1": sum(1 for f in findings if f.priority == "P1"),
        "p2": sum(1 for f in findings if f.priority == "P2"),
        "p3": sum(1 for f in findings if f.priority == "P3"),
        "verdict": verdict,
        "auditors": len(audit_results),
    }
