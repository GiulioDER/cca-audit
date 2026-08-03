"""The CCA-Audit agent resources and installers for Claude Code and Codex.

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

import os
import pathlib
import re
import shutil
import stat
import tempfile
from dataclasses import dataclass, field
from importlib import resources

__all__ = [
    "InstallResult",
    "default_codex_skills_root",
    "install",
    "install_codex",
    "iter_agents",
    "iter_codex_skill",
    "iter_commands",
    "iter_tools",
]

# Our own files are named `cca-*.md`; the orchestrator commands `audit-fix*.md`
# (canonical plus the DEEP alias). Globs, not hardcoded lists: an auditor added
# tomorrow ships without anyone remembering to update a manifest here.
_AGENT_GLOB = "cca-*.md"
_COMMAND_GLOB = "audit-fix*.md"
# The two checkers the orchestrator shells out to BY PATH (Step 2.6 scorecard,
# Step 5.6 red-state proof). `test_*` is excluded: their tests run in this
# repo's CI and have no business in a user's .claude/.
_TOOL_GLOB = "cca_*.py"
_CODEX_SKILL_NAME = "cca-audit"
_REQUIRED_CODEX_AGENT_FILES = frozenset(
    {
        "cca-architect-reviewer.md",
        "cca-bug-auditor.md",
        "cca-code-auditor.md",
        "cca-dep-auditor.md",
        "cca-deploy-auditor.md",
        "cca-differential-review.md",
        "cca-doc-auditor.md",
        "cca-env-validator.md",
        "cca-fix-planner.md",
        "cca-fp-check.md",
        "cca-numeric-auditor.md",
        "cca-perf-auditor.md",
        "cca-security-auditor.md",
    }
)

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


def _iter_tree(subdir: str):
    """Yield relative paths and text for every packaged file below `subdir`."""
    root = resources.files(__name__)
    for part in pathlib.PurePosixPath(subdir).parts:
        root = root.joinpath(part)

    def walk(directory, prefix: pathlib.PurePosixPath):
        for entry in sorted(directory.iterdir(), key=lambda item: item.name):
            relative = prefix / entry.name
            if entry.is_dir():
                yield from walk(entry, relative)
            elif entry.is_file():
                yield relative.as_posix(), entry.read_text(encoding="utf-8")

    return walk(root, pathlib.PurePosixPath())


def iter_codex_skill():
    """Yield the complete Codex skill, reusing canonical pipeline resources.

    The adapter itself is Codex specific. The pipeline, role prompts, and checker
    scripts are projected from their canonical package resources at install time,
    so the Claude and Codex surfaces cannot acquire independent audit contracts.
    """
    yield from _iter_tree(f"codex/{_CODEX_SKILL_NAME}")

    commands = dict(iter_commands())
    pipeline = commands.get("audit-fix.md")
    if pipeline is not None:
        yield "references/pipeline.md", pipeline
    for name, text in iter_agents():
        yield f"references/agents/{name}", text
    for name, text in iter_tools():
        yield f"scripts/{name}", text


def default_codex_skills_root() -> pathlib.Path:
    """Return the user skill root Codex auto-discovers."""
    configured = os.environ.get("CODEX_HOME")
    codex_home = pathlib.Path(configured) if configured else pathlib.Path.home() / ".codex"
    return codex_home / "skills"


def _is_link_or_reparse(path: pathlib.Path) -> bool:
    """Return whether `path` redirects filesystem traversal.

    `Path.is_symlink()` does not detect Windows directory junctions on every
    supported Python version. Junctions carry the reparse-point attribute, so
    inspect `lstat` as well without following the path.
    """
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    attributes = getattr(info, "st_file_attributes", 0)
    return stat.S_ISLNK(info.st_mode) or bool(attributes & reparse_flag)


def _validate_codex_skill_tree(
    skill_dir: pathlib.Path, relative_paths: list[str]
) -> None:
    """Reject redirects and destination conflicts before the first write."""
    if not skill_dir.exists():
        return
    if _is_link_or_reparse(skill_dir):
        raise RuntimeError(f"Codex skill path is a link or reparse point: {skill_dir}")
    if not skill_dir.is_dir():
        raise NotADirectoryError(f"Codex skill path is not a directory: {skill_dir}")

    for current, directories, filenames in os.walk(skill_dir, followlinks=False):
        current_path = pathlib.Path(current)
        for name in [*directories, *filenames]:
            candidate = current_path / name
            if _is_link_or_reparse(candidate):
                raise RuntimeError(
                    f"Codex skill contains a link or reparse point: {candidate}"
                )

    for relative in relative_paths:
        destination = skill_dir.joinpath(*pathlib.PurePosixPath(relative).parts)
        parent = destination.parent
        while parent != skill_dir:
            if parent.exists() and not parent.is_dir():
                raise NotADirectoryError(
                    f"Codex skill destination parent is not a directory: {parent}"
                )
            parent = parent.parent
        if destination.exists() and destination.is_dir():
            raise IsADirectoryError(
                f"Codex skill destination is a directory, expected a file: {destination}"
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


def install_codex(target: str | pathlib.Path | None = None) -> InstallResult:
    """Install the CCA skill below a Codex skills root.

    `target` names the skills directory, not the final skill directory. When it
    is omitted, use `$CODEX_HOME/skills` or `~/.codex/skills`, matching Codex's
    discovery contract.
    """
    skills_root = pathlib.Path(target) if target is not None else default_codex_skills_root()
    if skills_root.exists() and not skills_root.is_dir():
        raise NotADirectoryError(f"Codex skills target is not a directory: {skills_root}")
    skills_root.mkdir(parents=True, exist_ok=True)
    skills_root = skills_root.resolve(strict=True)

    files = list(iter_codex_skill())
    paths = [name for name, _ in files]
    required = {
        "SKILL.md",
        "agents/openai.yaml",
        "references/pipeline.md",
        "scripts/cca_scorecard.py",
        "scripts/cca_tautology_check.py",
        *(f"references/agents/{name}" for name in _REQUIRED_CODEX_AGENT_FILES),
    }
    missing = sorted(required.difference(paths))
    if missing:
        raise RuntimeError(
            "Codex skill resources are incomplete; missing "
            + ", ".join(missing)
            + ". Reinstall with `pip install --force-reinstall cca-audit`."
        )
    if len(paths) != len(set(paths)):
        raise RuntimeError("Codex skill resources contain duplicate destination paths")

    skill_dir = skills_root / _CODEX_SKILL_NAME
    _validate_codex_skill_tree(skill_dir, paths)

    stage_dir = pathlib.Path(
        tempfile.mkdtemp(prefix=f".{_CODEX_SKILL_NAME}-stage-", dir=skills_root)
    )
    old_dir: pathlib.Path | None = None
    committed = False
    result = InstallResult()
    try:
        if skill_dir.exists():
            shutil.copytree(skill_dir, stage_dir, dirs_exist_ok=True)
        for relative, text in files:
            destination = stage_dir.joinpath(*pathlib.PurePosixPath(relative).parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            _write(destination, text, result)

        if skill_dir.exists():
            old_dir = pathlib.Path(
                tempfile.mkdtemp(prefix=f".{_CODEX_SKILL_NAME}-old-", dir=skills_root)
            )
            old_dir.rmdir()
            os.replace(skill_dir, old_dir)
        try:
            os.replace(stage_dir, skill_dir)
            committed = True
        except OSError:
            if old_dir is not None and old_dir.exists() and not skill_dir.exists():
                try:
                    os.replace(old_dir, skill_dir)
                except OSError as rollback_error:
                    raise RuntimeError(
                        "Codex skill update failed and automatic rollback also failed; "
                        f"the previous skill is preserved at {old_dir}"
                    ) from rollback_error
                else:
                    old_dir = None
            raise
    finally:
        if stage_dir.exists():
            shutil.rmtree(stage_dir, ignore_errors=True)
        if committed and old_dir is not None and old_dir.exists():
            shutil.rmtree(old_dir, ignore_errors=True)
    return result
