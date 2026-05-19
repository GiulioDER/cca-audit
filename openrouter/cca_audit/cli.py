"""CLI entry point for CCA-Audit."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import click
from rich.console import Console

from cca_audit import __version__
from cca_audit.config import Config

console = Console()


@click.command()
@click.version_option(__version__)
@click.option("--model", "-m", default=None, help="LLM model (e.g., anthropic/claude-sonnet-4)")
@click.option("--config", "-c", "config_path", default=None, type=click.Path(), help="Config file")
@click.option("--no-fix", is_flag=True, help="Audit only, no fixes")
@click.option("--p1-only", is_flag=True, help="Fix only P1 Critical findings")
@click.option("--deferred", is_flag=True, help="Second pass: fix P3 items deferred from previous round")
@click.option("--dry-run", is_flag=True, help="Show what would be audited without running")
@click.option("--commit", "-n", default=None, type=int, help="Audit last N commits")
@click.option("--files", "-f", multiple=True, help="Audit specific files")
@click.option(
    "--auditors", "-a", default=None,
    help="Comma-separated auditor names (code,bug,security,perf,doc,env,dep)",
)
@click.option("--format", "output_format", type=click.Choice(["markdown", "json"]), default=None)
def main(
    model: str | None,
    config_path: str | None,
    no_fix: bool,
    p1_only: bool,
    deferred: bool,
    dry_run: bool,
    commit: int | None,
    files: tuple[str, ...],
    auditors: str | None,
    output_format: str | None,
) -> None:
    """CCA-Audit: 6-layer parallel code audit pipeline powered by LLMs."""
    console.print(f"[bold]CCA-Audit v{__version__}[/bold]\n")

    cfg = Config.load(Path(config_path) if config_path else None)

    if model:
        cfg.model = model
    if auditors:
        cfg.auditors = [a.strip() for a in auditors.split(",")]
    if output_format:
        cfg.output_format = output_format

    if not cfg.api_key:
        console.print(
            "[red]Error: No API key. Set OPENROUTER_API_KEY env var or api_key in config.[/red]"
        )
        sys.exit(1)

    if deferred:
        from cca_audit.pipeline import run_deferred_pass

        result = asyncio.run(run_deferred_pass(cfg))
        if result.get("status") == "NO_DEFERRED":
            console.print("[yellow]No deferred items found. Run a full audit first.[/yellow]")
        else:
            fixed = result.get("fixed", 0)
            stale = result.get("stale", 0)
            console.print(
                f"\n[green bold]Second pass complete: {fixed} fixed, {stale} stale[/green bold]"
            )
        return

    from cca_audit.pipeline import run_pipeline

    result = asyncio.run(
        run_pipeline(
            cfg,
            no_fix=no_fix,
            p1_only=p1_only,
            dry_run=dry_run,
            commit=commit,
            files=list(files) if files else None,
        )
    )

    if result.get("verdict") == "APPROVED":
        console.print("\n[green bold]Pipeline complete: APPROVED[/green bold]")
    elif result.get("status") == "DRY_RUN":
        console.print("\n[yellow]Dry run complete.[/yellow]")
    elif result.get("status") == "AUDIT_ONLY":
        console.print("\n[cyan]Audit complete. Review reports in .claude/audits/[/cyan]")
    else:
        console.print(f"\n[yellow]Pipeline complete: {result.get('verdict', 'UNKNOWN')}[/yellow]")
