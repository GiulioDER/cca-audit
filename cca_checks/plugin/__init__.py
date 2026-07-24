"""The CCA-Audit Claude Code plugin: agent + command markdown, and its installer.

This package exists so that `pip install cca-audit` delivers the *product* and
not just its verifier. CCA-Audit is the agent prompts in `agents/` and the
orchestrator in `commands/`; `cca_checks` is the deterministic layer those
prompts shell out to. Shipping only the latter would put a library on PyPI
whose page advertises a tool you cannot install from it.

The markdown lives inside the package rather than at the repo root because a
wheel can only carry package data. `claude-code/install.sh` reads from here
too, so there is one copy on disk and the two install paths cannot drift.

Read through `importlib.resources`, never through `__file__` joins: the latter
works in a source checkout and fails inside a zipimport or a relocated
install, which is exactly the environment `pip install` produces.
"""

from __future__ import annotations

import pathlib
import re
from dataclasses import dataclass, field
from importlib import resources

__all__ = ["InstallResult", "install", "iter_agents", "iter_commands", "iter_tools"]

# Our own files are named `cca-*.md`; the orchestrator commands `audit-fix*.md`
# (canonical plus the DEEP alias). Globs, not hardcoded lists: an auditor added
# tomorrow ships without anyone remembering to update a manifest here.
_AGENT_GLOB = "cca-*.md"
_COMMAND_GLOB = "audit-fix*.md"
# The two checkers the orchestrator shells out to BY PATH (Step 2.6 scorecard,
# Step 5.6 red-state proof). `test_*` is excluded: their tests run in this
# repo's CI and have no business in a user's .claude/.
_TOOL_GLOB = "cca_*.py"

# Agent names CCA-Audit dispatches. Our *files* are cca-*.md but their
# frontmatter `name:` is generic, so a project that already defines one of
# these has a collision no filename check can see -- one agent silently
# shadows the other.
_DISPATCHED_NAMES = re.compile(
    r"^name:[ \t]*("
    r"(?:code|bug|security|perf|doc|numeric|dep|deploy)-auditor"
    r"|env-validator|fp-check|fix-planner|differential-review|architect-reviewer"
    r")[ \t]*$",
    re.MULTILINE,
)


@dataclass
class InstallResult:
    """What an install actually did, so the CLI can report it instead of guessing."""

    installed: int = 0
    backed_up: int = 0
    warnings: list[str] = field(default_factory=list)


def _iter_resources(subdir: str, pattern: str):
    """Yield (filename, text) for packaged markdown matching `pattern`.

    Sorted so install output is deterministic; a run-to-run reordering makes
    diffing two install logs useless.
    """
    root = resources.files(__name__).joinpath(subdir)
    for entry in sorted(root.iterdir(), key=lambda e: e.name):
        if entry.is_file() and pathlib.PurePath(entry.name).match(pattern):
            yield entry.name, entry.read_text(encoding="utf-8")


def iter_agents():
    """Yield (filename, text) for every packaged auditor agent."""
    return _iter_resources("agents", _AGENT_GLOB)


def iter_commands():
    """Yield (filename, text) for every packaged orchestrator command."""
    return _iter_resources("commands", _COMMAND_GLOB)


def iter_tools():
    """Yield (filename, text) for every packaged pipeline checker."""
    return (
        (name, text)
        for name, text in _iter_resources("tools", _TOOL_GLOB)
        if not name.startswith("test_")
    )


def _write(dest: pathlib.Path, text: str, result: InstallResult) -> None:
    """Write one file, preserving a differing existing version as `<name>.bak`.

    Mirrors `claude-code/install.sh`'s `install_file`. The backup is
    conditional on the content actually differing: backing up unconditionally
    would drop a `.bak` beside every file on every upgrade, which trains users
    to ignore them -- and the one time it mattered, they would.
    """
    if dest.exists():
        current = dest.read_text(encoding="utf-8")
        if current != text:
            dest.with_suffix(dest.suffix + ".bak").write_text(current, encoding="utf-8")
            result.backed_up += 1
    dest.write_text(text, encoding="utf-8")
    result.installed += 1


def _warn_on_shadowing_agents(agents_dir: pathlib.Path, result: InstallResult) -> None:
    for existing in sorted(agents_dir.glob("*.md")):
        if existing.name.startswith("cca-"):
            continue  # ours
        try:
            text = existing.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            # An unreadable neighbouring file is not this installer's problem,
            # and must not abort an otherwise good install.
            continue
        if _DISPATCHED_NAMES.search(text):
            result.warnings.append(
                f"{existing.name} declares an agent name CCA-Audit also "
                "dispatches; one will shadow the other."
            )


def install(target: str | pathlib.Path = ".") -> InstallResult:
    """Install the agents, commands and pipeline checkers into `<target>/.claude/`.

    Returns an `InstallResult` rather than printing, so the behaviour is
    testable without capturing stdout and the CLI owns all presentation.
    """
    root = pathlib.Path(target)
    if root.exists() and not root.is_dir():
        raise NotADirectoryError(f"install target is not a directory: {root}")

    agents = list(iter_agents())
    commands = list(iter_commands())
    tools = list(iter_tools())
    # A wheel missing its markdown would install "successfully" into an empty
    # .claude/ -- a total, silent failure. Refuse before creating anything.
    if not agents:
        raise RuntimeError(
            "no agent markdown found in the installed package; the wheel is "
            "incomplete -- reinstall with `pip install --force-reinstall cca-audit`"
        )
    if not commands:
        raise RuntimeError(
            "no command markdown found in the installed package; the wheel is "
            "incomplete -- reinstall with `pip install --force-reinstall cca-audit`"
        )
    # Missing checkers fail the same way but quieter still: the install looks
    # complete, and Steps 2.6 and 5.6 then fail as `command not found` mid-run,
    # so the scorecard and the red-state proof are simply absent from the report
    # with nothing saying why. That was the pre-2026-07-24 behaviour of both
    # shell installers, and it is worth refusing rather than reproducing.
    if not tools:
        raise RuntimeError(
            "no pipeline checkers found in the installed package; the wheel is "
            "incomplete -- reinstall with `pip install --force-reinstall cca-audit`"
        )

    agents_dir = root / ".claude" / "agents"
    commands_dir = root / ".claude" / "commands"
    tools_dir = root / ".claude" / "tools"
    agents_dir.mkdir(parents=True, exist_ok=True)
    commands_dir.mkdir(parents=True, exist_ok=True)
    tools_dir.mkdir(parents=True, exist_ok=True)

    result = InstallResult()
    # Check *before* writing: afterwards our own cca-*.md files are present and
    # a pre-existing collision is harder to attribute.
    _warn_on_shadowing_agents(agents_dir, result)

    for name, text in agents:
        _write(agents_dir / name, text, result)
    for name, text in commands:
        _write(commands_dir / name, text, result)
    for name, text in tools:
        _write(tools_dir / name, text, result)

    return result
