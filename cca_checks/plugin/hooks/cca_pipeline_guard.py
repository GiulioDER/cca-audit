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


#: A commit that names another index or repository ships something the staged check
#: never read (it reads the default index of this checkout).
_REDIRECTED_GIT = re.compile(
    r"\bGIT_(?:INDEX_FILE|DIR|WORK_TREE|OBJECT_DIRECTORY|COMMON_DIR)\s*=|--(?:git-dir|work-tree)\b"
)


#: What a comment must not contain: if bash reads its `#` literally, any of these runs.
_HIDES_A_COMMAND = (";", "&", "|", "`", "$(", "<(", ">(", "${")


def chained_with(command: str) -> str | None:
    """The first shell separator outside quotes in `command`, or None when it is lone.

    The guard runs BEFORE the command, so it judges the tree as it is now. An edit
    chained ahead of the commit (`<edit> && git add f && git commit`) would change the
    tree after that judgement. The scanner models enough of bash to fail closed on such
    a chain, not all of it: single quotes, double quotes, and an unquoted `#` at a word
    start opening a comment to the end of the line; `#` inside unquoted `${...}` and
    `((...))` stays literal, and a comment that holds a separator or a substitution is
    refused, because bash keeps `#` literal in contexts not modelled here (extglob
    `@(...)`, quotes nested in `${...}`). A redirection (`2>&1`, `&>`) is not a
    separator and a backslash-newline is a line continuation. A scan that ends inside a
    quote is refused: the quoting was not understood, so the command cannot be shown
    lone. Parked to a human by run 20261011T121347Z: command substitution (`$(...)`,
    backticks) is not inspected (finding V11), and `$'...'` is read as a plain single
    quote (the ANSI-C part of V03; bash lexes `$$'` differently from `$'`).
    """
    text = command.strip()
    quote = None
    word_start = True
    nest: list[str] = []  # open `${` and `((`, inside which bash reads `#` literally
    i = 0
    while i < len(text):
        ch = text[i]
        if quote == "'":
            if ch == "'":
                quote = None
        elif ch == "\\":
            i += 2
            word_start = False
            continue
        elif ch == "$" and text[i + 1:i + 2] == "{":
            if quote is None:
                nest.append("}")
            i += 2
            word_start = False
            continue
        elif quote == '"':
            if ch == '"':
                quote = None
        elif ch in "'\"":
            quote = ch
        elif ch == "(" and text[i + 1:i + 2] == "(":
            nest.append("))")
            i += 2
            word_start = True
            continue
        elif nest and text.startswith(nest[-1], i):
            i += len(nest.pop())
            word_start = False
            continue
        elif ch == "#" and word_start and not nest:
            newline = text.find("\n", i)
            comment = text[i:] if newline == -1 else text[i:newline]
            # Bash keeps `#` literal in contexts this scanner does not model (extglob
            # `@(#a|b)`, quotes nested in `${...}`). A comment can hide a command only on
            # its own line, so refuse one that holds a separator or a substitution.
            if any(token in comment for token in _HIDES_A_COMMAND):
                return "comment holding a command separator"
            if newline == -1:
                break
            i = newline  # the newline that ends the comment is still a separator
            continue
        elif ch == "&" and (text[i - 1:i] in ("<", ">") or text[i + 1:i + 2] == ">"):
            pass
        elif ch in ";&|\n":
            return "newline" if ch == "\n" else ch
        word_start = quote is None and ch in " \t()"
        i += 1
    return "unterminated quote" if quote else None



def refuse(root: pathlib.Path, reasons: list[str]) -> int:
    print("CCA pipeline gate: this commit is blocked.\n", file=sys.stderr)
    for reason in reasons:
        print(f"  - {reason}", file=sys.stderr)
    print(
        "\nThe CCA audit+fix pipeline is open in this checkout and has not finished."
        "\nRun each layer named above, re-reading its section of audit-fix.md; the"
        "\nrecord command for each layer is in the Contract table at its top (L2.6 and"
        "\nL5.6 are recorded by their tools). A paused run must be resumed first, and a"
        "\nno-fix run never commits. The commit proceeds once nothing is listed here."
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
    except Exception as exc:  # noqa: BLE001
        # A run is open, so failing open here would ship unverified fixes. Any
        # exception, not only ImportError: a core that raises RuntimeError at import
        # would otherwise exit 1, which Claude Code treats as non blocking.
        return refuse(root, [
            f"cca_gate_core.py could not be loaded next to this guard ({exc}); reinstall "
            "with `cca-audit install --target .`"
        ])
    try:
        try:
            state = core.read_state(root)
        except ValueError as exc:
            return refuse(
                root, [f"{exc}; a run was started and cannot be shown to have finished"]
            )
        if state is None:
            return 0
        legacy = core.is_legacy(state)
        if not legacy and _REDIRECTED_GIT.search(command):
            return refuse(root, [
                "this commit names another git index or repository (GIT_INDEX_FILE, "
                "GIT_DIR, --git-dir or similar); the gate judges this checkout's own index, "
                "so it cannot vouch for what such a commit ships. Commit without it"
            ])
        separator = None if legacy else chained_with(command)
        if separator:
            return refuse(root, [
                f"this commit is chained with other commands (`{separator}`); the gate "
                "judges the tree before the command runs, so an edit in the same command "
                "would be committed unjudged. Run the edits, `git add` and `git commit` "
                "as separate commands"
            ])
        # One deadline for the fingerprint and the staged check: two would let the guard
        # outrun its hook timeout, and a hook that times out lets the commit through.
        tree, staged_ok = (None, True) if legacy else core.commit_view(root)
        reasons = core.commit_reasons(state, tree)
        if not reasons and staged_ok is not True:
            reasons = [
                "a staged file differs from its working tree content (or the two could "
                "not be compared), so this commit would ship content no verdict judged; "
                "re-stage it (`git add <file>`) or restore it, then commit"
            ]
    except Exception as exc:  # noqa: BLE001
        # An uncaught exception exits 1, which Claude Code treats as non blocking, so a
        # run that cannot be evaluated would let the commit through. Refuse instead.
        return refuse(root, [
            f"the CCA gate could not evaluate this run ({exc.__class__.__name__}: {exc}); "
            "a run was started and cannot be shown to have finished"
        ])
    if reasons:
        return refuse(root, reasons)
    return 0


if __name__ == "__main__":
    sys.exit(main())
