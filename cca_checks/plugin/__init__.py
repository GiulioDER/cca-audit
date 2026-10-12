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

from .installer import (
    InstallResult,
    default_codex_skills_root,
    hook_snippet,
    install,
    install_codex,
    iter_agents,
    iter_codex_skill,
    iter_commands,
    iter_hooks,
    iter_tools,
    stale_copies,
    user_claude_dir,
)

__all__ = [
    "InstallResult",
    "default_codex_skills_root",
    "hook_snippet",
    "install",
    "install_codex",
    "iter_agents",
    "iter_codex_skill",
    "iter_commands",
    "iter_hooks",
    "iter_tools",
    "stale_copies",
    "user_claude_dir",
]
