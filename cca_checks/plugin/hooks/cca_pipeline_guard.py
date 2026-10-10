#!/usr/bin/env python
"""PreToolUse hook: refuse a commit while a CCA run has unsatisfied gates.

This is the enforcement half of `cca_checks/pipeline_state.py`. The pipeline's
layers used to be enforced by prose alone, which meant an orchestrator could skip
L2.5 (anti-hallucination) or L6 (architect gate) and produce a commit that looked
exactly like a verified one. This hook makes that particular commit impossible
rather than merely discouraged.

WHY IT IMPORTS NOTHING FROM `cca_checks`. It runs from the user's global hooks
directory, against every repository they touch, including ones where
`cca_checks` was never installed. An `ImportError` in a PreToolUse hook is not a
safe failure: it either blocks every commit on the machine or, worse, is
swallowed and silently stops guarding. The rules live in `cca_gate_core.py`,
installed beside this file and loaded BY PATH, so they exist once and use only
the stdlib. If that file is missing while a run is open, the guard refuses
rather than guessing, because failing open there would ship unverified fixes.

BEHAVIOUR
  no state file            -> exit 0 (no audit in flight; this hook has no opinion)
  command is not a commit  -> exit 0
  state file unparseable   -> exit 2 (a run started; assuming it finished is the
                              optimistic guess the gate exists to remove)
  gates unsatisfied        -> exit 2, with the missing layers named on stderr
  gates satisfied          -> exit 0

Exit 2 is the Claude Code convention for "block this tool call and show stderr to
the model", so the refusal arrives as a correction rather than as a crash.
"""

from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import re
import sys

# Deliberately loose. A false positive costs one state-file read; a false
# negative lets an unverified fix through, which is the failure this exists to
# stop. `git -C x commit`, `git commit -am`, and a commit inside a compound
# command all have to match.
_COMMIT_RE = re.compile(r"\bgit\b[^\n;|&]*\bcommit\b")

_HERE = pathlib.Path(__file__).resolve().parent


def _load_core():
    path = _HERE / "cca_gate_core.py"
    spec = importlib.util.spec_from_file_location("cca_gate_core", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def find_root(start: pathlib.Path) -> pathlib.Path | None:
    """Nearest ancestor holding a CCA run state, bounded by the repo root."""
    for candidate in [start, *start.parents]:
        if (candidate / ".claude" / "audits" / "run-state.json").exists():
            return candidate
        if (candidate / ".git").exists():
            # Reached the checkout root without finding a run. Do not keep
            # walking into the parent of the repository: a run open in a sibling
            # checkout must never block a commit in this one.
            return None
    return None


def read_command(raw: str) -> str:
    """Extract the shell command from a hook payload.

    Accepts both the JSON envelope Claude Code sends and a bare command piped in
    by a `case` wrapper, because this repository's other hooks are invoked both
    ways and a guard that silently sees an empty command guards nothing.
    """
    text = raw.strip()
    if not text:
        return ""
    if text.startswith("{"):
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return text
        if isinstance(payload, dict):
            tool_input = payload.get("tool_input")
            if isinstance(tool_input, dict):
                return str(tool_input.get("command", "") or "")
            return str(payload.get("command", "") or "")
    return text


def evaluate(state: dict, tree: str | None = None) -> list[str]:
    """Reasons this run may not commit. Empty list means allowed."""
    return _load_core().commit_reasons(state, tree)


def refuse(root: pathlib.Path, reasons: list[str]) -> int:
    print("CCA pipeline gate: this commit is blocked.\n", file=sys.stderr)
    for reason in reasons:
        print(f"  - {reason}", file=sys.stderr)
    print(
        "\nThe CCA audit+fix pipeline is open in this checkout and has not finished."
        "\nRun the missing layers and record each one:"
        "\n  python -m cca_checks pipeline record <LAYER> --detail <verdict>"
        "\nWhen the architect gate returns APPROVED the commit proceeds."
        "\n"
        "\nIf this commit is unrelated to the audit, close the run explicitly:"
        f"\n  python -m cca_checks pipeline abort --reason \"<why>\"   (in {root})"
        "\nAborting is recorded in .claude/audits/EXECUTION_LOG.md. Deleting the"
        "\nstate file by hand is not an alternative: it leaves no trail, which is"
        "\nthe exact failure this gate replaces.",
        file=sys.stderr,
    )
    return 2


def main(argv: list[str] | None = None) -> int:
    try:
        raw = sys.stdin.read()
    except (OSError, UnicodeDecodeError):
        # No readable stdin means no command to judge. Allowing is correct: the
        # guard only ever constrains a commit it can actually see.
        return 0

    command = read_command(raw)
    if not command or not _COMMIT_RE.search(command):
        return 0

    root = find_root(pathlib.Path(os.getcwd()).resolve())
    if root is None:
        return 0

    try:
        core = _load_core()
    except (ImportError, OSError, SyntaxError) as exc:
        # A run is open, so failing open here would ship unverified fixes.
        return refuse(root, [
            f"cca_gate_core.py could not be loaded next to this guard ({exc}); reinstall "
            "with `python -m cca_checks.plugin.cli install --target .`"
        ])
    try:
        state = core.read_state(root)
    except ValueError as exc:
        return refuse(root, [f"{exc}; a run was started and cannot be shown to have finished"])
    if state is None:
        return 0
    tree = None if core.is_legacy(state) else core.fingerprint(root)
    reasons = core.commit_reasons(state, tree)
    if reasons:
        return refuse(root, reasons)
    return 0


if __name__ == "__main__":
    sys.exit(main())
