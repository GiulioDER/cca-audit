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

AGENT LAUNCHES. Wired to the `Agent` matcher too, it refuses an L1 auditor launched
before Step 1 built the brief, or with a prompt that does not carry it
(`cca_gate_core.dispatch_reasons`). Every other launch, and every launch outside an
open DIFF or HUNT run, is allowed.

Exit 2 is the Claude Code convention for "block this tool call and show stderr to
the model", so the refusal arrives as a correction rather than as a crash.

WHICH RUN. The run is looked for from the directory the commit runs in, not from
the session's: the payload's `cwd` (else this process's), moved by every leading
`cd <dir>` joined by `&&`, `;`, `||` or a newline, and by `git -C <dir>` on the commit
itself (`commit_directories`). Until 0.11 the guard looked only from its own cwd,
so `cd <worktree> && git commit` was allowed through against a run open in that
worktree. From that directory the walk up stops at the repository root
(`find_root`), so a run in any other checkout, including a checkout that merely
contains this one (the main checkout above `.claude/worktrees/<x>`), stays out of
scope. That is also why the open run registry is not consulted here: a registered
run that the walk does not reach lies in another checkout by construction.
"""

from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import re
import shlex
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


def read_cwd(raw: str) -> str | None:
    """The payload's `cwd`, when the JSON envelope carries one."""
    text = raw.strip()
    if not text.startswith("{"):
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    cwd = payload.get("cwd") if isinstance(payload, dict) else None
    return cwd if isinstance(cwd, str) and cwd else None


#: Joins after which a `cd` is still in effect for what follows. `||` is one: in
#: `cd x || exit 1; git commit` the cd succeeded whenever the commit runs. A pipe or
#: `&` runs the cd in a subshell, so it moves nothing.
_CD_PERSISTS = frozenset({"&&", ";", "\n", "||"})
#: Every operator that ends a simple command.
_ENDS_COMMAND = _CD_PERSISTS | {"|", "&", "|&"}
#: git global options that take their value as the next word.
_GIT_VALUE_OPTIONS = frozenset({
    "-c", "--git-dir", "--work-tree", "--namespace", "--super-prefix", "--config-env",
})
_POSIX_DRIVE = re.compile(r"^/([A-Za-z])(?=/|$)")


_PUNCTUATION = "();<>|&\n"
#: Longest first: shlex returns a run of punctuation as ONE token (`;\n`, `)&&`), and
#: this splits it back into the operators the shell sees.
_OPERATORS = ("&&", "||", "|&", ";;", ">>", "<<", ">&", "<&", ";", "|", "&", "(", ")", "<",
              ">", "\n")


def _split_operators(run: str) -> list[str]:
    out: list[str] = []
    while run:
        op = next((o for o in _OPERATORS if run.startswith(o)), run[0])
        out.append(op)
        run = run[len(op):]
    return out


def _tokens(command: str) -> list[str]:
    lexer = shlex.shlex(command, posix=True, punctuation_chars=_PUNCTUATION)
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    tokens: list[str] = []
    for token in lexer:
        if token and all(ch in _PUNCTUATION for ch in token):
            tokens += _split_operators(token)
        else:
            tokens.append(token)
    return tokens


def _change_dir(current: pathlib.Path, target: str) -> pathlib.Path:
    """`current` after `cd target`; relative targets resolve against `current`."""
    target = os.path.expanduser(target)
    if os.name == "nt":
        # Git Bash spells C:\x as /c/x; pathlib would read it as a path on the current drive.
        match = _POSIX_DRIVE.match(target)
        if match:
            target = f"{match.group(1)}:" + (target[2:] or "/")
    path = pathlib.Path(target)
    return path if path.is_absolute() else current / path


def commit_directories(command: str, base: pathlib.Path) -> list[pathlib.Path]:
    """The directory each `git commit` in `command` runs in.

    Starting from `base`, every `cd <dir>` joined to what follows by `&&`, `;`, `||`
    or a newline moves the directory, and `git -C <dir>` moves it for that git command
    only. A command shlex cannot parse is cut back a line at a time to its longest
    parsable prefix (a heredoc commit message is not shell syntax); when no prefix
    parses the answer is `[base]`, as it also is when no git commit is found.
    """
    tokens = None
    lines = command.split("\n")
    for keep in range(len(lines), 0, -1):
        try:
            tokens = _tokens("\n".join(lines[:keep]))
            break
        except ValueError:
            continue
    if tokens is None:
        return [base]
    found: list[pathlib.Path] = []
    current = base
    segment: list[str] = []
    for token in [*tokens, "\n"]:
        if token not in _ENDS_COMMAND:
            segment.append(token)
            continue
        words = [w for w in segment if w not in ("(", ")")]
        segment = []
        if not words:
            continue
        if words[0] == "cd":
            if token in _CD_PERSISTS and len(words) == 2 and words[1] != "-":
                current = _change_dir(current, words[1])
            elif token in _CD_PERSISTS and len(words) == 1:
                current = pathlib.Path.home()
            continue
        directory = _git_commit_directory(words, current)
        if directory is not None and directory not in found:
            found.append(directory)
    return found or [base]


def _git_commit_directory(words: list[str], current: pathlib.Path) -> pathlib.Path | None:
    """Where this simple command commits, or None when it is not a git commit."""
    for index, word in enumerate(words):
        if os.path.basename(word).lower() not in ("git", "git.exe"):
            continue
        directory = current
        i = index + 1
        while i < len(words) and words[i].startswith("-"):
            if words[i] == "-C" and i + 1 < len(words):
                directory = _change_dir(directory, words[i + 1])
                i += 2
            elif words[i] in _GIT_VALUE_OPTIONS:
                i += 2
            else:
                i += 1
        if i < len(words) and words[i] == "commit":
            return directory
    return None



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

    agent = read_agent(raw)
    if agent is not None:
        return _judge_dispatch(pathlib.Path(read_cwd(raw) or os.getcwd()), *agent)

    command = read_command(raw)
    if not command or not _COMMIT_RE.search(command):
        return 0

    base = pathlib.Path(read_cwd(raw) or os.getcwd())
    try:
        directories = commit_directories(command, base)
    except Exception:  # noqa: BLE001 (an unforeseen parse failure keeps the base directory)
        directories = [base]
    roots: list[pathlib.Path] = []
    for directory in directories:
        try:
            root = find_root(directory.resolve())
        except (OSError, ValueError, RuntimeError):
            root = find_root(base.resolve())
        if root is not None and root not in roots:
            roots.append(root)
    for root in roots:
        refused = _judge(root, command)
        if refused:
            return refused
    return 0


def read_agent(raw: str) -> tuple[str, str] | None:
    """(subagent_type, prompt) when the payload is an Agent launch, else None."""
    text = raw.strip()
    if not text.startswith("{"):
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict) or payload.get("tool_name") != "Agent":
        return None
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    prompt = tool_input.get("prompt")
    return str(tool_input.get("subagent_type") or ""), prompt if isinstance(prompt, str) else ""


def _judge_dispatch(base: pathlib.Path, agent_type: str, prompt: str) -> int:
    """Exit code for an Agent launch: 2 when it is an L1 auditor launched without the brief.

    An unreadable state or a core that will not load allows the launch: the commit gate
    refuses both at the end of the run, and an auditor is cheaper to repeat than a session
    that cannot launch any agent at all.
    """
    try:
        root = find_root(base.resolve())
        if root is None:
            return 0
        core = _load_core()
        state = core.read_state(root)
        if state is None:
            return 0
        reasons = core.dispatch_reasons(root, state, agent_type, prompt)
    except Exception:  # noqa: BLE001 (fail open, see the docstring)
        return 0
    if not reasons:
        return 0
    print(f"CCA pipeline gate: this {agent_type} launch is blocked.\n", file=sys.stderr)
    for reason in reasons:
        print(f"  - {reason}", file=sys.stderr)
    print("\nStep 1 of audit-fix.md: every L1 auditor prompt carries the prior-bugs brief.",
          file=sys.stderr)
    return 2


def _judge(root: pathlib.Path, command: str) -> int:
    """Exit code for a commit against the run open at `root`: 0, or 2 with the reasons."""
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
