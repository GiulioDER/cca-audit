#!/usr/bin/env python
"""PreToolUse hook: refuse a commit while a CCA run has unsatisfied gates.

This is the enforcement half of `cca_checks/pipeline_state.py`. The pipeline's
layers used to be enforced by prose alone, which meant an orchestrator could skip
L2.5 (anti-hallucination) or L6 (architect gate) and produce a commit that looked
exactly like a verified one. This hook makes that particular commit impossible
rather than merely discouraged.

WHY IT IS STANDALONE. It runs from the user's global hooks directory, against
every repository they touch, including ones where `cca_checks` was never
installed. An `ImportError` in a PreToolUse hook is not a safe failure: it either
blocks every commit on the machine or, worse, is swallowed and silently stops
guarding. So the required-layer table is embedded here, and
`tests/test_pipeline_guard.py` asserts it agrees with `pipeline_state` across a
matrix of run states. Duplication that is pinned by a test is cheaper than an
import that may not resolve.

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

import json
import os
import pathlib
import re
import sys

# Mirrors _CORE_REQUIRED / _FIX_REQUIRED in cca_checks/pipeline_state.py.
# tests/test_pipeline_guard.py fails if these drift apart.
CORE_REQUIRED = ("L1", "L2", "L2.5", "L6")
FIX_REQUIRED = {
    "FAST": ("L5",),
    "STANDARD": ("L5", "L5.5", "L5.6"),
    "DEEP": ("L5", "L5.5", "L5.6"),
}
APPROVING_VERDICT = "APPROVED"
BAD_STATUS = frozenset({"skipped", "failed"})

# Deliberately loose. A false positive costs one state-file read; a false
# negative lets an unverified fix through, which is the failure this exists to
# stop. `git -C x commit`, `git commit -am`, and a commit inside a compound
# command all have to match.
_COMMIT_RE = re.compile(r"\bgit\b[^\n;|&]*\bcommit\b")


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


def evaluate(state: dict) -> list[str]:
    """Reasons this run may not commit. Empty list means allowed."""
    reasons: list[str] = []

    tier = str(state.get("tier", "")).upper()
    if tier not in FIX_REQUIRED:
        return [f"run state names an unknown tier {tier!r}"]

    layers = state.get("layers")
    if not isinstance(layers, dict):
        return ["run state has no 'layers' object"]

    if state.get("no_fix"):
        reasons.append(
            "this run was started with `no-fix`, which forbids editing and committing; "
            "abort the run first if this commit is unrelated"
        )

    required = list(CORE_REQUIRED)
    try:
        fixes = int(state.get("fixes_applied", 0) or 0)
    except (TypeError, ValueError):
        fixes = 0
    if fixes > 0:
        required.extend(FIX_REQUIRED[tier])

    for layer in required:
        entry = layers.get(layer)
        if not entry:
            reasons.append(f"{layer} has no recorded verdict (tier {tier})")
        elif str(entry.get("status", "")).lower() in BAD_STATUS:
            reasons.append(f"{layer} is recorded as {entry.get('status')}")

    gate = layers.get("L6")
    if isinstance(gate, dict):
        verdict = str(gate.get("detail", "")).strip().upper()
        if not verdict:
            reasons.append(
                "L6 was recorded without a verdict; record it as "
                "--detail APPROVED, REVISE or BLOCKED"
            )
        elif verdict != APPROVING_VERDICT:
            reasons.append(
                f"the L6 architect gate returned {verdict}, not {APPROVING_VERDICT}"
            )

    return reasons


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

    path = root / ".claude" / "audits" / "run-state.json"
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return refuse(
            root,
            [
                f"the run state at {path} is unreadable ({exc.__class__.__name__}); "
                "a run was started and cannot be shown to have finished"
            ],
        )
    if not isinstance(state, dict):
        return refuse(root, [f"the run state at {path} is not a JSON object"])

    reasons = evaluate(state)
    if reasons:
        return refuse(root, reasons)
    return 0


if __name__ == "__main__":
    sys.exit(main())
