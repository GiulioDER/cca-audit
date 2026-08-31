"""The `cca-audit` console script for Claude Code and Codex.

`pip install cca-audit && cca-audit install` is the primary install path. It
replaces `curl ... | bash` -- not because the shell script was wrong, but
because piping a network fetch into a shell is a install step a large share of
developers will decline outright, and that refusal is invisible: it looks like
disinterest, not like a blocked install.

`main()` returns an exit code instead of calling `sys.exit`, so tests can
assert on it directly. The generated console-script shim propagates whatever
we return.
"""

from __future__ import annotations

import argparse
import sys
from importlib import metadata

from . import default_codex_skills_root, hook_snippet, install_codex
from . import install as install_plugin


def _version() -> str:
    try:
        return metadata.version("cca-audit")
    except metadata.PackageNotFoundError:
        # Running from a source checkout that was never pip-installed. That is
        # a normal developer state, not an error -- do not crash a --version.
        return "unknown (not installed)"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cca-audit",
        description=(
            "Install CCA-Audit for Claude Code or install the native audit+fix "
            "skill for Codex."
        ),
    )
    parser.add_argument("--version", action="store_true", help="print the installed version")

    sub = parser.add_subparsers(dest="command")
    install_cmd = sub.add_parser(
        "install",
        help="copy the agents and commands into <target>/.claude/",
        description=(
            "Copies the auditor agents and the /audit-fix command into "
            "<target>/.claude/. Existing files that you have customized are "
            "preserved as <name>.md.bak before being replaced."
        ),
    )
    install_cmd.add_argument(
        "--target",
        default=".",
        help="project directory to install into (default: the current directory)",
    )
    install_cmd.add_argument(
        "--print-hook",
        action="store_true",
        help=(
            "print the settings.json fragment that arms the commit guard, and "
            "install nothing"
        ),
    )
    codex_cmd = sub.add_parser(
        "install-codex",
        help="install the audit+fix skill into Codex",
        description=(
            "Installs the CCA audit+fix skill, canonical auditor prompts, and "
            "verification helpers into the Codex user skills directory."
        ),
    )
    codex_cmd.add_argument(
        "--target",
        default=None,
        help=(
            "Codex skills directory (default: $CODEX_HOME/skills, or "
            "~/.codex/skills when CODEX_HOME is unset)"
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    if args.version:
        print(_version())
        return 0

    if args.command not in {"install", "install-codex"}:
        # Bare invocation. Help on stdout is right for `--help`, but here the
        # user asked for nothing actionable -- exit non-zero so a shell chain
        # (`cca-audit && ...`) does not proceed as though something happened.
        parser.print_help()
        return 2

    if args.command == "install" and args.print_hook:
        # Deliberately the only thing this path does. Printing a config fragment
        # and silently editing the user's settings.json are different acts, and a
        # tool that arms a commit-blocking hook without being asked is one nobody
        # should install twice.
        print(hook_snippet(args.target))
        return 0

    is_codex = args.command == "install-codex"
    try:
        result = (
            install_codex(args.target)
            if is_codex
            else install_plugin(args.target)
        )
    except NotADirectoryError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except RuntimeError as exc:
        # An incomplete wheel. Distinct from a bad argument, and worth an exit
        # code the user can distinguish in a script.
        print(f"error: {exc}", file=sys.stderr)
        return 3
    except OSError as exc:
        if not is_codex:
            raise
        print(f"error: Codex skill installation failed: {exc}", file=sys.stderr)
        return 1

    if is_codex:
        skills_root = (
            default_codex_skills_root()
            if args.target is None
            else args.target
        )
        print(f"Installed {result.installed} file(s) into {skills_root}/cca-audit/")
    else:
        print(f"Installed {result.installed} file(s) into {args.target}/.claude/")
    if result.backed_up:
        print(
            f"  {result.backed_up} customized file(s) were replaced; "
            "the previous versions are kept as *.bak"
        )
    for warning in result.warnings:
        print(f"  WARNING: {warning}", file=sys.stderr)

    print("")
    if is_codex:
        print("Start a new Codex task, then say `audit+fix` to audit your changes.")
    else:
        print("Run /audit-fix in Claude Code from this project to audit your changes.")
    print("For the deterministic verification layer, also install:")
    print("    pip install 'cca-audit[verify]'   # hypothesis, pyright, semgrep, mpmath")
    return 0
