"""The `cca-audit` console script.

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
            "Install the CCA-Audit auditors and the /audit-fix command into a "
            "project's .claude/ directory."
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
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    if args.version:
        print(_version())
        return 0

    if args.command != "install":
        # Bare invocation. Help on stdout is right for `--help`, but here the
        # user asked for nothing actionable -- exit non-zero so a shell chain
        # (`cca-audit && ...`) does not proceed as though something happened.
        parser.print_help()
        return 2

    try:
        result = install_plugin(args.target)
    except NotADirectoryError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except RuntimeError as exc:
        # An incomplete wheel. Distinct from a bad argument, and worth an exit
        # code the user can distinguish in a script.
        print(f"error: {exc}", file=sys.stderr)
        return 3

    print(f"Installed {result.installed} file(s) into {args.target}/.claude/")
    if result.backed_up:
        print(
            f"  {result.backed_up} customized file(s) were replaced; "
            "the previous versions are kept as *.md.bak"
        )
    for warning in result.warnings:
        print(f"  WARNING: {warning}", file=sys.stderr)

    print("")
    print("Run /audit-fix in Claude Code from this project to audit your changes.")
    print("For the deterministic verification layer, also install:")
    print("    pip install 'cca-audit[verify]'   # hypothesis, pyright, semgrep, mpmath")
    return 0
