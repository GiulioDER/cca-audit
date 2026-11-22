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

WHICH COMMAND IS A COMMIT, AND WHERE IT RUNS. The command is parsed the way the shell
that runs it would parse it (bash for the `Bash` tool, PowerShell for the `PowerShell`
tool), and one walk over the parse answers both questions (`commit_directories`): is
there a `git commit` that would run, and in which directory. The walk starts from the
payload's `cwd` (else this process's) and follows `cd`, `pushd`, `popd`, `env -C`,
`git -C`, subshells, `bash -c`, `eval` and the and-or lists that decide whether a `cd`
ran, keeping every directory the shell could be in. A directory it cannot know (a
variable it cannot see, `cd -` before any `cd`) is judged against every open run.

From each directory the run is the nearest one walking up to the repository root
(`find_root`), plus any registered run elsewhere inside that same repository
(`_commit_roots`), because a commit ships the whole index of its repository. A run in
any other checkout, including one that merely contains this one (the main checkout
above `.claude/worktrees/<x>`) or one nested inside it, stays out of scope.
"""

from __future__ import annotations

import base64
import importlib.util
import json
import os
import pathlib
import re
import sys
import threading
import time

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


def _checkout_top(start: pathlib.Path) -> pathlib.Path | None:
    """The nearest ancestor (or `start`) that is a checkout root, i.e. holds `.git`."""
    for candidate in [start, *start.parents]:
        if (candidate / ".git").exists():
            return candidate
    return None


def _registered_roots() -> list[pathlib.Path]:
    """The open run registry; a lookup aid, so any failure to read it is no entries."""
    try:
        return _load_core().registered_roots()
    except Exception:  # noqa: BLE001 (the registry never decides a verdict on its own)
        return []


def _same_path(a: pathlib.Path, b: pathlib.Path) -> bool:
    return os.path.normcase(str(a)) == os.path.normcase(str(b))


def _inside(path: pathlib.Path, top: pathlib.Path) -> bool:
    return _same_path(path, top) or any(_same_path(p, top) for p in path.parents)


class _Maybe:
    """Where text that may run a commit the walk cannot see would run it."""

    def __repr__(self) -> str:
        return "MAYBE"


MAYBE = _Maybe()


def _commit_roots(directory: pathlib.Path | _Maybe | None,
                  base: pathlib.Path) -> list[pathlib.Path]:
    """Every run root a commit made in `directory` must satisfy.

    The walk up from the directory finds a run at or above it. A run opened in a
    subdirectory of the same repository is below it, so the registry supplies that one:
    a registered root counts when its nearest checkout is the commit's checkout, which
    leaves out runs in nested checkouts and in sibling worktrees.

    `None` is a commit whose directory the command does not let us know (`git -C "$X"`,
    `cd "$(...)"`), so every open run is judged. `MAYBE` is text that may run a commit
    the walk cannot see (`trap`, `eval "$X"`, a malformed line naming one); it is judged
    against the run of the session's checkout and every registered run inside that
    checkout, nested worktrees included, because judging every run on the machine for
    every command that merely names git and commit would refuse most of them. A directory
    whose files cannot be read (`exists()` raises on POSIX for EACCES) is judged as
    unknown, since an exception here would exit 1, which Claude Code does not treat as a
    refusal.
    """
    try:
        if directory is None:
            roots = [find_root(base.resolve()), *_registered_roots()]
        elif isinstance(directory, _Maybe):
            start = base.resolve()
            top = _checkout_top(start) or start
            roots = [find_root(start), *(r for r in _registered_roots() if _inside(r, top))]
        else:
            start = directory.resolve()
            roots = [find_root(start)]
            top = _checkout_top(start)
            if top is not None:
                for root in _registered_roots():
                    owner = _checkout_top(root) if _inside(root, top) else None
                    if owner is not None and _same_path(owner, top):
                        roots.append(root)
    except (OSError, ValueError, RuntimeError):
        if directory is None or isinstance(directory, _Maybe):
            return [base]  # judged as the session's own checkout; reading it is _judge's job
        return _commit_roots(None, base)
    unique: list[pathlib.Path] = []
    for root in roots:
        if root is not None and not any(_same_path(root, seen) for seen in unique):
            unique.append(root)
    return unique


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


def read_dialect(raw: str) -> str:
    """`ps` for the PowerShell tool's payload, else `bash` (also for a bare command)."""
    text = raw.strip()
    if not text.startswith("{"):
        return "bash"
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return "bash"
    return "ps" if isinstance(payload, dict) and payload.get("tool_name") == "PowerShell" \
        else "bash"


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


#: git global options that take their value as the next word.
_GIT_VALUE_OPTIONS = frozenset({
    "-c", "--git-dir", "--work-tree", "--namespace", "--super-prefix", "--config-env",
})
_POSIX_DRIVE = re.compile(r"^/([A-Za-z])(?=/|$)")


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


# --- reading a command the way its shell runs it --------------------------------------
#
# WHY A PARSER. Until 0.17 the guard found a commit with a regex and its directory with
# shlex: two models of bash that disagreed with bash and with each other. `git \` +
# newline + `commit`, `g"i"t commit` and `Git commit` were never seen, `cd -P wt` and
# `pushd wt` were not followed, and shlex read the `#` of `a#b` as a comment. Each patch
# on one model opened a bypass in the other (run 20261120T201246Z parked SEC-001 after
# three repair cycles). So there is one lexer per shell, following that shell's rules
# for continuations, comments, quoting, expansions, heredocs and reserved words, and one
# walk (`_Walk`) over what it parses.
#
# A word is kept as its parts before expansion, so `co''mmit`, `"g"it` and `$X` (with X
# assigned earlier in the command, or PWD, OLDPWD or HOME) expand to what the shell would
# pass. A part whose value cannot be known here (a command substitution's output, an
# unknown variable) expands to `_UNKNOWN`, and a commit in such a place is judged as one.

class _Unparsed(Exception):
    """Text the shell would reject as a syntax error here."""


class _ArithIsCommand(Exception):
    """`$((` or `((` that is really two parentheses: bash re-reads it as a subshell."""


_MAX_NESTING = 40     # nested substitutions, shells, evals and calls before giving up
_MAX_STEPS = 50_000   # commands walked before the command counts as too complex to model
#: Seconds the walk may take before the command counts as too complex to model. It is
#: checked between commands, so one command's cost is bounded by `_MAX_EXPANDED` instead.
#: Both keep the guard inside the 60 s hook timeout, which fails open, with room for the
#: 30 s fingerprint.
_DEADLINE_S = 15
_MAX_VALUE = 1_000_000  # characters a variable may hold before its value counts as unknown
#: Characters the walk may expand in all before the command counts as too complex to
#: model: about a second of path and name work, so `cd $X$X...` cannot outrun the hook.
_MAX_EXPANDED = 20_000_000
_MAX_PATH = 32_767    # characters in the longest path Windows or POSIX accepts
_MAX_STATES = 64      # possible shell states kept before collapsing to "unknown directory"
_LOOP_PASSES = 4      # loop bodies walked before a still-moving loop counts as unknown
_UNKNOWN = "\0"       # stands in for text whose value cannot be known here

_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_BRACED_NAME = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\}")
_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\[[^\]]*\])?\+?=")
_DECLARATIONS = ("export", "declare", "typeset", "local", "readonly")  # builtins taking NAME=v
_ARRAY_START = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\+?=")
_TILDE = re.compile(r"~([A-Za-z0-9_.-]*)")
_ANSI_C = {"a": "\a", "b": "\b", "e": "\x1b", "E": "\x1b", "f": "\f", "n": "\n", "r": "\r",
           "t": "\t", "v": "\v", "\\": "\\", "'": "'", '"': '"', "?": "?"}
_ANSI_C_CODE = re.compile(
    r"x([0-9A-Fa-f]{1,2})|u([0-9A-Fa-f]{1,4})|U([0-9A-Fa-f]{1,8})|([0-7]{1,3})")

_GIT_WORD = re.compile(r"\bgit(?:\.exe)?\b")
_COMMIT_WORD = re.compile(r"\bcommit\b")


def _brace_expands(raw: str) -> bool:
    """Whether `raw` holds `{...}` with a `,` or `..` and no brace inside: a brace expansion.

    A loop, not a regex: `\\{[^{}]*(?:,|\\.\\.)[^{}]*\\}` backtracks quadratically on `{,,,,`,
    and a regex runs in C, where the watchdog thread cannot interrupt it.
    """
    opened = separated = False
    for at, c in enumerate(raw):
        if c == "{":
            opened, separated = True, False
        elif c == "}":
            if opened and separated:
                return True
            opened = False
        elif opened and (c == "," or (c == "." and raw.startswith("..", at))):
            separated = True
    return False


def _loosen(text: str) -> str:
    return re.sub(r"[`'\"\\]", "", text.replace("\\\n", "").replace("`\n", "")).lower()


def _names_a_commit(text: str) -> bool:
    """The fail-closed net under the walk: `git` and `commit` both appear as words.

    Quotes, escapes and case are removed first. A command whose walk found no commit, or
    could not be parsed or walked, is still judged as a commit in a directory the guard
    cannot know when this holds, because an executor the walk does not model (`trap`,
    `cmd /c`, a shell reading stdin, an alias, `python -c`) may run that text. The guard
    did the same before it had a parser. The text is read twice: with backslashes deleted,
    so `g\\it` is `git`, and with each taken as `/`, so `C:\\Git\\cmd\\git.exe` keeps `git`
    as a word. Separate searches, not one pattern with `.*` between the words, keep it
    linear in the length of the command.
    """
    readings = (_loosen(text), _loosen(text.replace("\\", "/")))
    return any(_GIT_WORD.search(t) for t in readings) and \
        any(_COMMIT_WORD.search(t) for t in readings)


class _Word:
    """One shell word: its parts before expansion, and the programs nested in it."""

    __slots__ = ("_parts", "_chunks", "_quoted", "subs", "raw", "start")

    def __init__(self):
        self._parts: list[tuple] = []  # ("lit", text, quoted) ("var", name, quoted)
        #                                ("unknown", quoted) ("tilde", user)
        # The literal being built, kept as chunks: one character at a time, joining as it
        # grows would make a long string quadratic to read.
        self._chunks: list[str] = []
        self._quoted = False
        self.subs: list[list] = []    # command substitutions, walked in a subshell
        self.raw = ""
        self.start = -1               # offset of `raw` in the parsed text

    @property
    def parts(self) -> list[tuple]:
        if self._chunks:
            self._parts.append(("lit", "".join(self._chunks), self._quoted))
            self._chunks = []
        return self._parts

    def empty(self) -> bool:
        return not self._parts and not self._chunks

    def lit(self, text: str, quoted: bool) -> None:
        if self._chunks and self._quoted != quoted:
            self.parts  # noqa: B018 (closes the open literal)
        if not self._chunks and self._parts and self._parts[-1][0] == "lit" \
                and self._parts[-1][2] == quoted:
            self._chunks.append(self._parts.pop()[1])
        self._chunks.append(text)
        self._quoted = quoted

    def plain(self) -> str | None:
        """The text of a word that is one unquoted literal, which is how reserved words look."""
        if len(self.parts) == 1 and self.parts[0][0] == "lit" and not self.parts[0][2]:
            return self.parts[0][1]
        return None


def _unknown_word() -> _Word:
    word = _Word()
    word.parts.append(("unknown", False))
    return word


def _heredoc_delimiter(raw: str) -> str:
    """The delimiter bash compares lines with: the word after quote removal."""
    out, i = [], 0
    while i < len(raw):
        if raw[i] == "\\" and i + 1 < len(raw):
            out.append(raw[i + 1])
            i += 2
            continue
        if raw[i] not in "'\"":
            out.append(raw[i])
        i += 1
    return "".join(out)


_BASH_OPS = ("((", "&&", "||", ";;&", ";;", ";&", "|&", "&>>", "&>", "<<<", "<<-", "<<", "<>",
             "<&", ">>", ">&", ">|", ";", "&", "|", "(", ")", "<", ">")
_REDIRECTS = frozenset({"<", ">", ">>", ">|", "<>", "<&", ">&", "&>", "&>>", "<<", "<<-", "<<<"})
#: Text a POSIX shell such as dash reads otherwise than bash: `$[` is text there, and `&>`
#: is `&` then `>`. A nested `sh` script holding one is walked under both readings.
_BASHISMS = ("$[", "&>")
_CASE_ENDS = frozenset({";;", ";&", ";;&"})
_ALWAYS_STOP = frozenset({")"}) | _CASE_ENDS
_WORD_END = " \t\n;&|()<>"
_QUOTING_MODES = ("word", "brace", "bracket", "arith", "group")


class _Bash:
    """A recursive descent parser for the bash grammar, close enough to judge a commit.

    It parses to tuples: a list is `[(and_or, background)]`, an and-or list is
    `("andor", pipeline, [(op, pipeline)])`, a pipeline `("pipe", [command], negated)`,
    and a command one of `simple`, `sub`, `group`, `if`, `loop`, `for`, `case`, `arith`,
    `cond`, `func`, `bg` or `redir`. `complete` holds the top-level items finished by a
    newline, which bash has already run when a later line turns out to be malformed.
    """

    def __init__(self, text: str, nesting: int = 0, bashisms: bool = True):
        if nesting > _MAX_NESTING:
            raise _Unparsed("nested too deeply")
        self.s = text
        self.i = 0
        self.nesting = nesting
        self.bashisms = bashisms  # bash's reading of `_BASHISMS`, else dash's
        self.heredocs: list[tuple] = []
        self.tok: tuple | None = None
        self.complete: list = []
        # Offsets of `$((` already shown to be `$( (`: retrying each one as arithmetic
        # again would re-parse every deeper `$((` twice per level, which is exponential.
        self.not_arith: set[int] = set()
        # Where bash itself reads arithmetic, by this parser's rules (quotes, `$(...)`, `case`
        # patterns): an assignment there is one the walk does not model.
        self.arith_spans: list[tuple[int, int]] = []
        self.sub_arith_names: set[str] = set()  # the same, from backquote and heredoc text
        # Offsets of the assignment words the walk applies: a command's leading `NAME=v`,
        # and `NAME=v` given to a declaration builtin. Any other `NAME=` is not modelled.
        self.assign_at: set[int] = set()

    def arith_names(self) -> set[str]:
        """Every name inside the arithmetic read so far, scanning each character once."""
        names, covered = set(self.sub_arith_names), 0
        for start, end in sorted(self.arith_spans):  # nested spans overlap: skip the overlap
            if end > covered:
                names.update(_NAME.findall(self.s, max(start, covered), end))
                covered = end
        return names

    # -- tokens --------------------------------------------------------------------

    def peek(self) -> tuple:
        if self.tok is None:
            self.tok = self._token()
        return self.tok

    def next(self) -> tuple:
        tok = self.peek()
        self.tok = None
        return tok

    def _token(self) -> tuple:
        s = self.s
        while self.i < len(s):
            c = s[self.i]
            if c in " \t":
                self.i += 1
            elif s.startswith("\\\n", self.i):
                self.i += 2
            elif c == "#":  # a token starts a word, and a `#` starting a word is a comment
                end = s.find("\n", self.i)
                self.i = len(s) if end < 0 else end
            else:
                break
        if self.i >= len(s):
            return ("eof", None)
        if s[self.i] == "\n":
            self.i += 1
            self._heredoc_bodies()
            return ("nl", None)
        if not (s[self.i] in "<>" and s.startswith("(", self.i + 1)):
            for op in _BASH_OPS:
                if s.startswith(op, self.i) and (self.bashisms or op not in ("&>", "&>>")):
                    self.i += len(op)
                    return ("op", op)
        word = self._word()
        text = word.plain()
        if text is not None and text.isdigit() and self.i < len(s) and s[self.i] in "<>":
            return ("io", None)  # the `2` of `2>&1`, not an argument
        return ("word", word)

    def _word(self) -> _Word:
        word = _Word()
        start = self.i
        self._parts(word, "word")
        if self.i == start:
            raise _Unparsed(f"unexpected {self.s[self.i]!r}")
        word.raw = self.s[start:self.i]
        word.start = start
        return word

    def _parts(self, word: _Word, mode: str) -> None:
        """Read the parts of `word` in `mode`: word, dq, brace, arith, group or heredoc."""
        s = self.s
        quoted = mode in ("dq", "heredoc")
        depth = 0
        begin = self.i
        while self.i < len(s):
            c = s[self.i]
            if mode == "word" and c in _WORD_END:
                if c in "<>" and s.startswith("(", self.i + 1) and word.empty():
                    self.i += 2
                    self._substitution(word, quoted)
                    continue
                if c == "(" and self._glob_group(word):
                    continue
                return
            if mode == "dq" and c == '"':
                self.i += 1
                return
            if mode == "brace" and c in "{}":
                if c == "}" and depth == 0:
                    self.i += 1
                    return
                depth += 1 if c == "{" else -1
            if mode == "bracket" and c in "[]":
                if c == "]" and depth == 0:
                    self.i += 1
                    return
                depth += 1 if c == "[" else -1
            if mode in ("arith", "group") and c in "()":
                if c == "(":
                    depth += 1
                elif depth:
                    depth -= 1
                elif mode == "group":
                    self.i += 1
                    return
                elif s.startswith("))", self.i):
                    self.i += 2
                    self.arith_spans.append((begin, self.i))
                    return
                else:
                    raise _ArithIsCommand()
            if c == "\\":
                nxt = s[self.i + 1:self.i + 2]
                if nxt == "\n":
                    self.i += 2
                    continue
                if mode in _QUOTING_MODES or (mode == "dq" and nxt and nxt in '$`"\\') or (
                        mode == "heredoc" and nxt and nxt in "$`\\"):
                    word.lit(nxt, True)
                    self.i += 2
                    continue
                word.lit("\\", quoted)
                self.i += 1
                continue
            if c == "'" and mode in _QUOTING_MODES:
                end = s.find("'", self.i + 1)
                if end < 0:
                    raise _Unparsed("unterminated single quote")
                word.lit(s[self.i + 1:end], True)
                self.i = end + 1
                continue
            if c == '"' and mode in _QUOTING_MODES:
                self.i += 1
                word.lit("", True)
                self._parts(word, "dq")
                continue
            if c == "$":
                self._dollar(word, mode, quoted)
                continue
            if c == "`":
                self._backtick(word, mode, quoted)
                continue
            match = _TILDE.match(s, self.i) if mode == "word" and word.empty() else None
            if match is not None:
                end = match.end()
                if end == len(s) or s[end] in _WORD_END or s[end] == "/":
                    word.parts.append(("tilde", match.group(1)))
                    self.i = end
                    continue
            word.lit(c, quoted)
            self.i += 1
        if mode in ("dq", "brace", "bracket", "arith", "group"):
            raise _Unparsed(f"unterminated {mode}")

    def _glob_group(self, word: _Word) -> bool:
        """`@(a|b)` (extglob) or `x=(a b)` (array): a `(` bash keeps inside the word."""
        text = word.plain()
        array = text is not None and _ARRAY_START.fullmatch(text) is not None
        if not array and not (not word.empty() and self.s[self.i - 1] in "@!?*+"):
            return False
        self.i += 1
        inner = _Word()
        self._parts(inner, "group")
        word.subs += inner.subs
        word.parts.append(("unknown", False))
        return True

    def _dollar(self, word: _Word, mode: str, quoted: bool) -> None:
        s = self.s
        at = self.i + 1
        while s.startswith("\\\n", at):  # `$\<newline>[`: bash joins the lines, then reads `$[`
            at += 2
        self.i = at - 1  # from here on `$` is taken to sit just before `s[at]`
        nxt = s[self.i + 1:self.i + 2]
        if nxt == "'" and mode in _QUOTING_MODES:
            self.i += 2
            word.lit(self._ansi_c(), True)
            return
        if nxt == '"' and mode in _QUOTING_MODES:
            self.i += 2
            word.lit("", True)
            self._parts(word, "dq")
            return
        if nxt == "[" and self.bashisms:  # `$[ ]`, the old form of `$(( ))`
            self.i += 2
            begin = self.i
            inner = _Word()
            self._parts(inner, "bracket")
            self.arith_spans.append((begin, self.i))
            word.subs += inner.subs
            word.parts.append(("unknown", quoted))
            return
        if s.startswith("((", self.i + 1) and self.i not in self.not_arith:
            save = self.i
            self.i += 3
            inner = _Word()
            try:
                self._parts(inner, "arith")
            except _ArithIsCommand:
                self.not_arith.add(save)
                self.i = save + 2
                self._substitution(word, quoted)
                return
            word.subs += inner.subs
            word.parts.append(("unknown", quoted))
            return
        if nxt == "(":
            self.i += 2
            self._substitution(word, quoted)
            return
        if nxt == "{":
            self.i += 2
            match = _BRACED_NAME.match(s, self.i)
            if match:
                word.parts.append(("var", match.group(1), quoted))
                self.i = match.end()
                return
            inner = _Word()
            self._parts(inner, "brace")
            word.subs += inner.subs
            word.parts.append(("unknown", quoted))
            return
        match = _NAME.match(s, self.i + 1)
        if match:
            word.parts.append(("var", match.group(0), quoted))
            self.i = match.end()
            return
        if nxt and nxt in "@*#?$!-0123456789":
            word.parts.append(("unknown", quoted))
            self.i += 2
            return
        word.lit("$", quoted)
        self.i += 1

    def _ansi_c(self) -> str:
        s, out = self.s, []
        while self.i < len(s):
            c = s[self.i]
            if c == "'":
                self.i += 1
                return "".join(out)
            if c == "\\" and self.i + 1 < len(s):
                nxt = s[self.i + 1]
                if nxt in _ANSI_C:
                    out.append(_ANSI_C[nxt])
                    self.i += 2
                    continue
                match = _ANSI_C_CODE.match(s, self.i + 1)
                if match:
                    digits = next(g for g in match.groups() if g)
                    try:
                        out.append(chr(int(digits, 8 if match.group(4) else 16)))
                    except (ValueError, OverflowError):
                        out.append(_UNKNOWN)
                    self.i = match.end()
                    continue
                out.append("\\" + nxt)
                self.i += 2
                continue
            out.append(c)
            self.i += 1
        raise _Unparsed("unterminated $'")

    def _substitution(self, word: _Word, quoted: bool) -> None:
        """The program of `$(...)`, `<(...)` or `>(...)`, after its opening parenthesis."""
        self.nesting += 1
        if self.nesting > _MAX_NESTING:
            raise _Unparsed("nested too deeply")
        body = self._list(frozenset())
        if self.next() != ("op", ")"):
            raise _Unparsed("unterminated substitution")
        self.nesting -= 1
        word.subs.append(body)
        word.parts.append(("unknown", quoted))

    def _backtick(self, word: _Word, mode: str, quoted: bool) -> None:
        s, j, out = self.s, self.i + 1, []
        while j < len(s) and s[j] != "`":
            if s[j] == "\\" and j + 1 < len(s) and (
                    s[j + 1] in "$`\\" or (mode == "dq" and s[j + 1] == '"')):
                out.append(s[j + 1])
                j += 2
                continue
            out.append(s[j])
            j += 1
        if j >= len(s):
            raise _Unparsed("unterminated backquote")
        self.i = j + 1
        inner = _Bash("".join(out), self.nesting + 1, self.bashisms)
        try:
            word.subs.append(inner.program())
        finally:
            self.sub_arith_names |= inner.arith_names()
        word.parts.append(("unknown", quoted))

    def _heredoc_bodies(self) -> None:
        """Read the bodies of the heredocs opened on the line a newline just ended."""
        pending, self.heredocs = self.heredocs, []
        s = self.s
        for delimiter, strip_tabs, expands, holder in pending:
            lines = []
            while self.i < len(s):
                end = s.find("\n", self.i)
                line = s[self.i:] if end < 0 else s[self.i:end]
                self.i = len(s) if end < 0 else end + 1
                if strip_tabs:
                    line = line.lstrip("\t")
                if line == delimiter:
                    break
                lines.append(line)
            if expands:  # an unquoted delimiter: `$(...)` in the body runs
                body = _Bash("\n".join(lines), self.nesting + 1, self.bashisms)
                try:
                    body._parts(holder, "heredoc")
                finally:
                    self.sub_arith_names |= body.arith_names()

    # -- grammar -------------------------------------------------------------------

    def program(self) -> list:
        items = self._list(frozenset(), top=True)
        if self.peek()[0] != "eof":
            raise _Unparsed(f"unexpected {self.peek()[1]!r}")
        self.complete = items
        return items

    def _reserved(self) -> str | None:
        tok = self.peek()
        return tok[1].plain() if tok[0] == "word" else None

    def _stops(self, tok: tuple, stops: frozenset) -> bool:
        if tok[0] == "op":
            return tok[1] in _ALWAYS_STOP
        return tok[0] == "word" and tok[1].plain() in stops

    def _skip_newlines(self) -> None:
        while self.peek()[0] == "nl":
            self.next()

    def _expect_op(self, op: str) -> None:
        if self.next() != ("op", op):
            raise _Unparsed(f"expected {op!r}")

    def _expect_word(self, *words: str) -> str:
        self._skip_newlines()
        tok = self.next()
        text = tok[1].plain() if tok[0] == "word" else None
        if text not in words:
            raise _Unparsed(f"expected {' or '.join(words)}")
        return text

    def _list(self, stops: frozenset, top: bool = False) -> list:
        items: list = []
        while True:
            while self.peek()[0] == "nl":
                self.next()
                if top:
                    self.complete = list(items)
            tok = self.peek()
            if tok[0] == "eof" or self._stops(tok, stops):
                return items
            node = self._and_or()
            tok = self.peek()
            if tok in (("op", "&"), ("op", ";")):
                self.next()
                items.append((node, tok[1] == "&"))
                continue
            items.append((node, False))
            if tok[0] == "nl":
                continue
            if not (tok[0] == "eof" or self._stops(tok, stops)):
                raise _Unparsed(f"unexpected {tok[1]!r}")
            return items

    def _and_or(self) -> tuple:
        first, rest = self._pipeline(), []
        while self.peek() in (("op", "&&"), ("op", "||")):
            op = self.next()[1]
            self._skip_newlines()
            rest.append((op, self._pipeline()))
        return ("andor", first, rest)

    def _pipeline(self) -> tuple:
        negate = False
        while True:
            word = self._reserved()
            if word == "!":
                self.next()
                negate = not negate
            elif word == "time":
                self.next()
                if self._reserved() == "-p":
                    self.next()
            else:
                break
        commands = [self._command()]
        while self.peek() in (("op", "|"), ("op", "|&")):
            self.next()
            self._skip_newlines()
            commands.append(self._command())
        return ("pipe", commands, negate)

    def _command(self) -> tuple:
        tok = self.peek()
        word = self._reserved()
        if tok == ("op", "(("):
            node = self._arith_command()
        elif tok == ("op", "("):
            self.next()
            node = ("sub", self._list(frozenset()))
            self._expect_op(")")
        elif word == "{":
            self.next()
            node = ("group", self._list(frozenset({"}"})))
            self._expect_word("}")
        elif word == "if":
            node = self._if()
        elif word in ("while", "until"):
            self.next()
            cond = self._list(frozenset({"do"}))
            self._expect_word("do")
            body = self._list(frozenset({"done"}))
            self._expect_word("done")
            node = ("loop", cond, body, word == "until")
        elif word in ("for", "select"):
            node = self._for()
        elif word == "case":
            node = self._case()
        elif word == "[[":
            node = self._cond()
        elif word == "function":
            node = self._function()
        elif word == "coproc":
            self.next()
            node = ("bg", self._command())
        else:
            return self._simple()
        extra: list = []
        while self.peek()[0] == "io" or (self.peek()[0] == "op" and self.peek()[1] in _REDIRECTS):
            if self.peek()[0] == "io":
                self.next()
            else:
                self._redirect(extra)
        return ("redir", node, extra) if extra else node

    def _arith_command(self) -> tuple:
        self.next()
        save = self.i
        inner = _Word()
        try:
            self._parts(inner, "arith")
        except _ArithIsCommand:
            # `((cmd) && x)`: a subshell whose first command is a subshell.
            self.i = save - 1
            body = self._list(frozenset())
            self._expect_op(")")
            return ("sub", body)
        return ("arith", [inner])

    def _if(self) -> tuple:
        self.next()
        clauses = []
        while True:
            cond = self._list(frozenset({"then"}))
            self._expect_word("then")
            body = self._list(frozenset({"elif", "else", "fi"}))
            clauses.append((cond, body))
            word = self._expect_word("elif", "else", "fi")
            if word == "elif":
                continue
            otherwise = None
            if word == "else":
                otherwise = self._list(frozenset({"fi"}))
                self._expect_word("fi")
            return ("if", clauses, otherwise)

    def _for(self) -> tuple:
        self.next()
        name, words = None, []
        if self.peek() == ("op", "(("):
            self.next()
            inner = _Word()
            self._parts(inner, "arith")
            words = [inner]
        else:
            tok = self.next()
            if tok[0] != "word":
                raise _Unparsed("for without a name")
            name = tok[1].plain()
            self._skip_newlines()
            if self._reserved() == "in":
                self.next()
                while self.peek()[0] == "word":
                    words.append(self.next()[1])
            else:
                words = [_unknown_word()]  # no `in`: it loops over "$@", unknown here
        if self.peek() == ("op", ";"):
            self.next()
        self._skip_newlines()
        if self._reserved() == "{":
            self.next()
            body = self._list(frozenset({"}"}))
            self._expect_word("}")
        else:
            self._expect_word("do")
            body = self._list(frozenset({"done"}))
            self._expect_word("done")
        return ("for", name, words, body)

    def _case(self) -> tuple:
        self.next()
        tok = self.next()
        if tok[0] != "word":
            raise _Unparsed("case without a word")
        subject = tok[1]
        self._expect_word("in")
        arms = []
        while True:
            self._skip_newlines()
            if self._reserved() == "esac":
                self.next()
                return ("case", subject, arms)
            if self.peek() == ("op", "("):
                self.next()
            patterns = []
            while True:
                tok = self.next()
                if tok[0] != "word":
                    raise _Unparsed("case pattern expected")
                patterns.append(tok[1])
                tok = self.next()
                if tok == ("op", ")"):
                    break
                if tok != ("op", "|"):
                    raise _Unparsed("case pattern without )")
            body = self._list(frozenset({"esac"}))
            end = ";;"
            tok = self.peek()
            if tok[0] == "op" and tok[1] in _CASE_ENDS:
                end = self.next()[1]
            elif self._reserved() != "esac":
                raise _Unparsed("case arm without ;;")
            arms.append((patterns, body, end))

    def _cond(self) -> tuple:
        self.next()
        words = []
        while True:
            tok = self.next()
            if tok[0] == "eof":
                raise _Unparsed("[[ without ]]")
            if tok[0] == "word":
                if tok[1].plain() == "]]":
                    return ("cond", words)
                words.append(tok[1])

    def _function(self) -> tuple:
        self.next()
        tok = self.next()
        if tok[0] != "word":
            raise _Unparsed("function without a name")
        if self.peek() == ("op", "("):
            self.next()
            self._expect_op(")")
        self._skip_newlines()
        return ("func", tok[1].plain() or _UNKNOWN, self._command())

    def _simple(self) -> tuple:
        assigns: list = []
        words: list = []
        extra: list = []
        while True:
            tok = self.peek()
            if tok[0] == "word":
                self.next()
                if not words and _ASSIGNMENT.match(tok[1].raw):
                    assigns.append(tok[1])
                    self.assign_at.add(tok[1].start)
                    continue
                words.append(tok[1])
                if len(words) == 1 and not assigns and self.peek() == ("op", "("):
                    self.next()
                    self._expect_op(")")
                    self._skip_newlines()
                    return ("func", tok[1].plain() or _UNKNOWN, self._command())
                continue
            if tok[0] == "io":
                self.next()
                continue
            if tok[0] == "op" and tok[1] in _REDIRECTS:
                self._redirect(extra)
                continue
            break
        if not (assigns or words or extra):
            raise _Unparsed("unexpected end" if tok[0] == "eof" else f"unexpected {tok[1]!r}")
        if words and words[0].plain() in _DECLARATIONS:
            self.assign_at.update(w.start for w in words[1:] if _ASSIGNMENT.match(w.raw))
        return ("simple", assigns, words, extra)

    def _redirect(self, extra: list) -> None:
        op = self.next()[1]
        tok = self.next()
        if tok[0] != "word":
            raise _Unparsed(f"{op} without a target")
        if op in ("<<", "<<-"):
            holder = _Word()
            expands = not any(ch in tok[1].raw for ch in "'\"\\")
            self.heredocs.append((_heredoc_delimiter(tok[1].raw), op == "<<-", expands, holder))
            extra.append(holder)
        else:
            extra.append(tok[1])


_PS_SINGLE = "'\u2018\u2019\u201a\u201b"
_PS_DOUBLE = '"\u201c\u201d\u201e'
_PS_ESCAPES = {"0": "\0", "a": "\a", "b": "\b", "e": "\x1b", "f": "\f", "n": "\n", "r": "\r",
               "t": "\t", "v": "\v"}
_PS_WORD_END = " \t\r\n;|&(){}<>"
_PS_REDIRECT = re.compile(r"[1-6*]?>>?(?:&[1-6])?|<")
_PS_VAR = re.compile(r"(?:(?:global|local|script|private|using|env):)?[A-Za-z0-9_?]+", re.I)
_PS_HERE_OPEN = re.compile(r"@(['\"])[ \t]*\r?\n")


def _ps_key(name: str) -> str:
    """The walk's key for a PowerShell variable: `$env:X` is the environment's `X`."""
    scope, colon, rest = name.partition(":")
    if colon and scope.lower() == "env":
        return rest
    if colon:
        name = rest
    low = name.lower()
    return {"pwd": "PWD", "home": "HOME"}.get(low, "$" + low)


class _Pwsh:
    """A flat PowerShell reader: statements, `&&`/`||`, pipelines, and the words of commands.

    Parenthesised and `$(...)` groups are walked as run; script blocks `{...}` as maybe run,
    since whether `if`, `foreach` or `&` runs them is not modelled. PowerShell parses a
    whole script before running any of it, so a malformed one runs nothing (`complete` is
    empty).
    """

    def __init__(self, text: str, nesting: int = 0):
        if nesting > _MAX_NESTING:
            raise _Unparsed("nested too deeply")
        # PowerShell ends a statement at a lone CR as well as at LF and CRLF.
        self.s = text.replace("\r\n", "\n").replace("\r", "\n")
        self.i = 0
        self.nesting = nesting
        self.complete: list = []

    def program(self) -> list:
        return self._statements(None)

    def _skip(self, newlines: bool) -> None:
        s = self.s
        while self.i < len(s):
            c = s[self.i]
            if c in " \t\r" or (newlines and c in "\n;"):
                self.i += 1
            elif s.startswith("`\r\n", self.i):
                self.i += 3
            elif s.startswith("`\n", self.i):
                self.i += 2
            elif s.startswith("<#", self.i):
                end = s.find("#>", self.i + 2)
                if end < 0:
                    raise _Unparsed("unterminated block comment")
                self.i = end + 2
            elif c == "#":
                end = s.find("\n", self.i)
                self.i = len(s) if end < 0 else end
            else:
                break

    def _statements(self, close: str | None) -> list:
        items: list = []
        while True:
            self._skip(newlines=True)
            if self.i >= len(self.s):
                if close:
                    raise _Unparsed(f"missing {close!r}")
                return items
            c = self.s[self.i]
            if c == close:
                self.i += 1
                return items
            if c in ")}":
                raise _Unparsed(f"unexpected {c!r}")
            chain = self._chain()
            self._skip(newlines=False)
            background = self.s.startswith("&", self.i) and not self.s.startswith("&&", self.i)
            if background:
                self.i += 1
            items.append((chain, background))

    def _chain(self) -> tuple:
        first, rest = self._pipeline(), []
        while True:
            self._skip(newlines=False)
            op = self.s[self.i:self.i + 2]
            if op not in ("&&", "||"):
                return ("andor", first, rest)
            self.i += 2
            self._skip(newlines=True)
            rest.append((op, self._pipeline()))

    def _pipeline(self) -> tuple:
        commands = [self._command()]
        while True:
            self._skip(newlines=False)
            if self.s[self.i:self.i + 1] != "|" or self.s.startswith("||", self.i):
                break
            self.i += 1
            self._skip(newlines=True)
            commands.append(self._command())
        # A PowerShell pipeline runs in one session, so a Set-Location in it persists.
        return ("pipe", commands if len(commands) == 1 else [("seq", commands)], False)

    def _command(self) -> tuple:
        s = self.s
        words: list = []
        blocks: list = []
        self._skip(newlines=False)
        start = self.i
        called = False
        if s.startswith("&", self.i) and not s.startswith("&&", self.i):
            self.i += 1  # the call operator: `& git commit`
            called = True
        elif s.startswith(". ", self.i):
            self.i += 1  # dot-sourcing
        while True:
            self._skip(newlines=False)
            if self.i >= len(s):
                break
            c = s[self.i]
            if c in ";\n)}|&":
                break
            stop_parsing = s[self.i + 3:self.i + 4] in ("", " ", "\t", "\r", "\n")
            if s.startswith("--%", self.i) and stop_parsing:
                end = s.find("\n", self.i)
                end = len(s) if end < 0 else end
                for piece in s[self.i + 3:end].split():
                    word = _Word()
                    word.lit(piece, True)
                    words.append(word)
                self.i = end
                continue
            if c in "<>*" or (c in "123456" and s[self.i + 1:self.i + 2] == ">"):
                match = _PS_REDIRECT.match(s, self.i)
                if match:
                    self.i = match.end()
                    if "&" not in match.group(0):
                        self._skip(newlines=False)
                        if self.i < len(s) and s[self.i] not in ";\n)}|&":
                            self._word()
                    continue
            if c == "(" or s.startswith(("$(", "@("), self.i):
                self.i += 1 if c == "(" else 2
                blocks.append(("group", self._statements(")")))
                words.append(_unknown_word())
                continue
            if c == "{" or s.startswith("@{", self.i):
                self.i += 1 if c == "{" else 2
                blocks.append(("maybe", self._statements("}")))
                words.append(_unknown_word())
                continue
            words.append(self._word())
        if self.i == start and not words:
            raise _Unparsed(f"unexpected {s[self.i:self.i + 1]!r}")
        return ("ps", words, blocks, called)

    def _word(self) -> _Word:
        s = self.s
        word = _Word()
        start = self.i
        while self.i < len(s):
            c = s[self.i]
            if c in _PS_WORD_END:
                break
            if c == "`":
                nxt = s[self.i + 1:self.i + 2]
                if nxt in ("", "\n", "\r"):
                    break
                word.lit(nxt, True)
                self.i += 2
                continue
            opening = _PS_HERE_OPEN.match(s, self.i) if c == "@" and word.empty() else None
            if opening is not None:
                self._here_string(word, opening)
                continue
            if c in _PS_SINGLE:
                self._single(word)
                continue
            if c in _PS_DOUBLE:
                self.i += 1
                word.lit("", True)
                self._expandable(word, closing=True)
                continue
            if c == "$":
                self._dollar(word, quoted=False)
                continue
            splat = _PS_VAR.match(s, self.i + 1) if c == "@" and word.empty() else None
            if splat is not None:  # `@args` passes the array's elements, unknown here
                word.parts.append(("unknown", False))
                self.i = splat.end()
                continue
            word.lit(c, False)
            self.i += 1
        if self.i == start:
            raise _Unparsed(f"unexpected {s[self.i:self.i + 1]!r}")
        return word

    def _single(self, word: _Word) -> None:
        s, out = self.s, []
        self.i += 1
        while self.i < len(s):
            c = s[self.i]
            if c in _PS_SINGLE:
                if s[self.i + 1:self.i + 2] and s[self.i + 1] in _PS_SINGLE:
                    out.append("'")
                    self.i += 2
                    continue
                self.i += 1
                word.lit("".join(out), True)
                return
            out.append(c)
            self.i += 1
        raise _Unparsed("unterminated single quote")

    def _expandable(self, word: _Word, closing: bool) -> None:
        s = self.s
        while self.i < len(s):
            c = s[self.i]
            if closing and c in _PS_DOUBLE:
                if s[self.i + 1:self.i + 2] and s[self.i + 1] in _PS_DOUBLE:
                    word.lit('"', True)
                    self.i += 2
                    continue
                self.i += 1
                return
            if c == "`" and self.i + 1 < len(s):
                nxt = s[self.i + 1]
                word.lit(_PS_ESCAPES.get(nxt, nxt), True)
                self.i += 2
                continue
            if c == "$":
                self._dollar(word, quoted=True)
                continue
            word.lit(c, True)
            self.i += 1
        if closing:
            raise _Unparsed("unterminated double quote")

    def _here_string(self, word: _Word, opening: re.Match) -> None:
        quote = opening.group(1)
        closing = re.compile(r"^" + re.escape(quote) + "@", re.M).search(self.s, opening.end())
        if closing is None:
            raise _Unparsed("unterminated here-string")
        body = self.s[opening.end():closing.start()].removesuffix("\n").removesuffix("\r")
        self.i = closing.end()
        if quote == "'":
            word.lit(body, True)
        else:
            _Pwsh(body, self.nesting + 1)._expandable(word, closing=False)

    def _dollar(self, word: _Word, quoted: bool) -> None:
        s = self.s
        nxt = s[self.i + 1:self.i + 2]
        if nxt == "(":
            self.i += 2
            word.subs.append(self._statements(")"))
            word.parts.append(("unknown", quoted))
            return
        if nxt == "{":
            end = s.find("}", self.i + 2)
            if end < 0:
                raise _Unparsed("unterminated ${")
            word.parts.append(("var", _ps_key(s[self.i + 2:end]), quoted))
            self.i = end + 1
            return
        match = _PS_VAR.match(s, self.i + 1)
        if match:
            word.parts.append(("var", _ps_key(match.group(0)), quoted))
            self.i = match.end()
            return
        if nxt and nxt in "_$^":
            word.parts.append(("unknown", quoted))
            self.i += 2
            return
        word.lit("$", quoted)
        self.i += 1


def _parser(text: str, dialect: str, nesting: int, bashisms: bool = True):
    return _Bash(text, nesting, bashisms) if dialect == "bash" else _Pwsh(text, nesting)


# --- walking a parsed command ---------------------------------------------------------

_POSIX_SHELLS = frozenset({"bash", "sh", "zsh", "dash", "ksh", "mksh", "ash"})
_SHELL_VALUE_OPTIONS = frozenset({"-o", "+o", "-O", "+O", "--rcfile", "--init-file"})
_PWSH_VALUE_OPTIONS = ("executionpolicy", "configurationname", "outputformat", "inputformat",
                       "windowstyle", "version", "settingsfile", "psconsolefile",
                       "custompipename", "configurationfile")


def _program(field: tuple[str, bool]) -> str | None:
    """A command word's program name as a case-insensitive file system resolves it."""
    text, unknown = field
    if unknown:
        return None
    name = re.split(r"[\\/]", text.rstrip("\r"))[-1].lower()
    return name[:-4] if name.endswith(".exe") else name


def _join(directory: pathlib.Path | None, field: tuple[str, bool]) -> pathlib.Path | None:
    text, unknown = field
    if unknown or len(text) > _MAX_PATH:
        # A path longer than any file system takes cannot be entered; walking its parents
        # (`find_root`) would also be quadratic in it, past the hook timeout.
        return None
    path = _change_dir(directory or pathlib.Path(), text)
    if directory is None and not path.is_absolute():
        return None
    return path


# --- pathname expansion ---------------------------------------------------------------
#
# `cd chil*` moves into `child`, so a target read as the literal `chil*` is judged in a
# path that does not exist (FIX-SEC-005). Expansion runs only where a directory is
# decided (`_Walk._expand`), because elsewhere a pattern only names arguments and reading
# the disk for them costs time the hook does not have: past its 60 s timeout the hook
# lets the commit through, and run 20261120T223924Z measured `ls /*/*/*/*` at 265 s.

_MAX_GLOB_ENTRIES = 4096  # directory entries one command's expansions may read
_MAX_GLOB_PATHS = 64      # paths one pattern may name before it counts as unknown


class _Unknowable(Exception):
    """A pathname expansion this hook cannot know: past a bound, or a rule not modelled."""


class _Field(tuple):
    """A field `(text, unknown)` holding a character bash may expand to pathnames.

    `pattern` is the field's text as `(chunk, unquoted)` pairs, because only an unquoted
    `*`, `?` or `[` is a pattern character: `'a*'/x*` expands the second star alone. It
    compares equal to the plain pair, so code that does not expand reads it as one.
    """

    pattern: tuple = ()


def _ps_wildcard(text: str) -> list:
    """`text` as `(char, active)` pairs under PowerShell's wildcard rules: a backtick escapes."""
    chars, i = [], 0
    while i < len(text):
        if text[i] == "`" and i + 1 < len(text):
            chars.append((text[i + 1], False))
            i += 2
        else:
            chars.append((text[i], True))
            i += 1
    return chars


def _glob_tokens(component: list, ps: bool) -> list | None:
    """One path component as tokens for `_glob_match`, or None when it holds no pattern.

    A token is `"*"`, or `(ranges, negate)` for one character, `ranges` None for any. A
    quoted `]` does not close a bracket expression and a quoted `!` does not negate it
    (BUG-001).
    """
    out, glob, i = [], False, 0
    while i < len(component):
        ch, active = component[i]
        if active and ch == "\\" and not ps:
            raise _Unknowable("a backslash from an expansion escapes the pattern")
        if active and ch in "*?":
            out.append("*" if ch == "*" else (None, False))
            glob = True
            i += 1
            continue
        if not (active and ch == "["):
            out.append((((ch, ch),), False))
            i += 1
            continue
        j = i + 1
        negate = not ps and j < len(component) and component[j][1] and component[j][0] in "!^"
        if negate:
            j += 1
        start = j
        if j < len(component) and component[j][0] == "]":
            j += 1  # a `]` first in the brackets is a member, not the end
        while j < len(component) and component[j] != ("]", True):
            j += 1
        if j >= len(component):  # no closing `]`: the `[` is literal (`[ "$CI" = x ]`)
            out.append(((("[", "["),), False))
            i += 1
            continue
        inner = component[start:j]
        if any(c == "[" for c, _ in inner):
            raise _Unknowable("a character class this hook does not model")
        ranges, k = [], 0
        while k < len(inner):
            low = inner[k][0]
            if k + 2 < len(inner) and inner[k + 1] == ("-", True):
                ranges.append((low, inner[k + 2][0]))
                k += 3
            else:
                ranges.append((low, low))
                k += 1
        out.append((tuple(ranges), negate))
        glob = True
        i = j + 1
    return out if glob else None


def _changes_globs(args: list) -> bool:
    """Whether `set`, `shopt` or a shell's command line given `args` may change expansion:
    `-f`, `noglob`, any `...glob` option, or an argument the walk cannot read."""
    return any(unknown or "glob" in text or (text[:1] in "-+" and text[1:2] != "-" and "f" in text)
               for text, unknown in args)


def _glob_match(tokens: list, name: str, fold: bool) -> bool:
    """`name` against `tokens`, keeping one backtrack point (the last `*`).

    In Python, not `re`: a backtracking regex runs in C holding the GIL, where the watchdog
    thread cannot interrupt it, and `*a*a*a...*b` against a long name ran for minutes
    (SEC-001). Here the cost is at most the product of the two lengths. With `fold`
    (PowerShell) a character also matches as its lower and upper case, the rule of
    re.IGNORECASE, so a range such as `[A-c]` keeps its endpoints.
    """
    t = n = 0
    star, mark = -1, 0
    while n < len(name):
        if t < len(tokens) and tokens[t] == "*":
            star, mark = t, n
            t += 1
            continue
        if t < len(tokens):
            ranges, negate = tokens[t]
            chars = (name[n], name[n].lower(), name[n].upper()) if fold else (name[n],)
            hit = ranges is None or any(low <= c <= high for c in chars for low, high in ranges)
            if hit != negate:
                t += 1
                n += 1
                continue
        if star < 0:
            return False
        t, mark = star + 1, mark + 1
        n = mark
    return all(token == "*" for token in tokens[t:])


def _pathnames(pattern: list, base: pathlib.Path | None, ps: bool, budget: list) -> list[str]:
    """The paths `pattern` names from `base`, spelled as the shell spells them.

    Matched one component at a time against directory entries, as bash does: `*` and `?`
    never cross a separator, a leading `.` must be matched literally (bash only), and
    case counts in bash, Git Bash included, but not in PowerShell. Each component leading
    to the next must be a directory on disk, never by lexical `..`: `'a*'/../x*` names
    nothing without a directory `a*`. `budget[0]` counts down the entries read and the
    paths checked, so no pattern shape escapes the bound (PERF-001).
    """
    seps = "/\\" if ps else "/"
    parts, joins = [[]], []
    for ch, active in pattern:
        if ch in seps:
            joins.append(ch)
            parts.append([])
        else:
            parts[-1].append((ch, active))
    patterns = [_glob_tokens(part, ps) for part in parts]
    if not any(patterns):
        return []
    text = "".join(c for c, _ in pattern)
    if os.name == "nt" and not ps and text.startswith("/") and not _POSIX_DRIVE.match(text):
        raise _Unknowable("Git Bash's `/` is its install directory, not a drive root")

    def charge(count: int) -> None:
        budget[0] -= count
        if budget[0] < 0:
            raise _Unknowable("too many directory entries to expand")

    def place(text: str) -> pathlib.Path:
        path = _join(base, (text or ".", False))
        if path is None:
            raise _Unknowable("a relative pattern in an unknown directory")
        return path

    paths = [""]
    for n, (part, tokens) in enumerate(zip(parts, patterns, strict=True)):
        lead = [""]
        if n:
            charge(len(paths))
            lead = [p + joins[n - 1] for p in paths if place(p + joins[n - 1]).is_dir()]
        if tokens is None:
            paths = [p + "".join(c for c, _ in part) for p in lead]
            continue
        dots = ps or part[:1] and part[0][0] == "."
        paths = []
        for p in lead:
            try:
                with os.scandir(place(p)) as entries:
                    for entry in entries:
                        charge(1)
                        if (not dots and entry.name.startswith(".")) \
                                or not _glob_match(tokens, entry.name, ps):
                            continue
                        if ps and getattr(entry.stat(follow_symlinks=False),
                                          "st_file_attributes", 0) & 2:
                            continue  # PowerShell's wildcard skips a hidden item
                        paths.append(p + entry.name)
            except OSError:
                continue  # bash reads a directory it cannot list as holding no match
            if len(paths) > _MAX_GLOB_PATHS:
                raise _Unknowable("too many paths")
    charge(len(paths))
    return [p for p in paths if os.path.lexists(place(p))]


#: GNU env's long options, for its getopt_long rules: any unambiguous prefix names one.
_ENV_LONG = ("ignore-environment", "null", "unset", "chdir", "split-string", "block-signal",
             "default-signal", "ignore-signal", "list-signal-handling", "debug", "help",
             "version")
_ENV_LONG_VALUE = frozenset({"unset", "chdir", "split-string"})


def _envs_before(names: list) -> list:
    """For each field, the index of the last `env` before it (None if none), in one pass.

    Searching back from each field instead made a command quadratic in its words.
    """
    out, last = [], None
    for index, name in enumerate(names):
        out.append(last)
        if name == "env":
            last = index
    return out


def _env_directory(fields: list, directory: pathlib.Path | None, end: int,
                   envs: list, expand) -> pathlib.Path | None:
    """`directory` for `fields[end]` after the last `env` before it, parsed as GNU env does.

    `envs` is `_envs_before` of the fields. An option env has but this reader does not
    know, or an ambiguous abbreviation, makes the directory unknown (None). `expand` is
    `_Walk._expand`, for a `-C` operand that bash expands as a pathname.
    """
    start = envs[end]
    if start is None:
        return directory
    base = directory  # bash expands every word of the command where it starts
    i = start + 1
    while i < end:
        text, unknown = fields[i]
        if unknown:
            return None
        if text == "--" or not text.startswith("-") or text == "-":
            if "=" in text or text in ("-", "--"):
                i += 1  # an assignment, `-` (same as -i) or the end of the options
                continue
            break  # the command env runs
        if text.startswith("--"):
            name, eq, value = text[2:].partition("=")
            matches = [o for o in _ENV_LONG if o.startswith(name)]
            option = name if name in _ENV_LONG else (matches[0] if len(matches) == 1 else None)
            if option is None:
                return None
            if option in _ENV_LONG_VALUE and not eq:
                if i + 1 >= end:
                    return None
                field = expand(fields[i + 1], base) if option == "chdir" else fields[i + 1]
                value, i = field[0], i + 1
                if field[1]:
                    return None
            if option == "chdir":
                directory = _join(directory, (value, False))
            i += 1
            continue
        letters = text[1:]
        for at, letter in enumerate(letters):
            if letter in "i0v":
                continue
            if letter not in "uCS":
                return None
            value = letters[at + 1:]
            if not value:
                if i + 1 >= end:
                    return None
                field = expand(fields[i + 1], base) if letter == "C" else fields[i + 1]
                if field[1]:
                    return None
                value, i = field[0], i + 1
            if letter == "C":
                directory = _join(directory, (value, False))
            break
        i += 1
    return directory


def _git_commit(fields: list, directory: pathlib.Path | None,
                names: list, expand) -> tuple[bool, pathlib.Path | None]:
    """(True, its directory) when `fields` run `git commit`; the directory is None if unknown.

    Any field naming git counts, not only the first, so `sudo git commit`, `xargs git
    commit` and `time git commit` are seen. A program name that cannot be known followed
    by `commit` counts too, and so does a real git whose options or subcommand cannot be.
    So does a git given `-c alias.<x>=...` or `-c help.autocorrect=...`, since either can
    turn the subcommand it runs into a commit. `names` is `_program` of each field, and
    `expand` is `_Walk._expand`, for a `-C` operand that bash expands as a pathname.
    """
    envs = _envs_before(names)
    for index, field in enumerate(fields):
        real = names[index] == "git"
        if not (real or field[1]):
            continue
        joins = []  # `-C` values

        def here(index=index, joins=joins):
            # Only once a commit is found: parsing env's options for every field instead
            # made a command quadratic in its words.
            place = _env_directory(fields, directory, index, envs, expand)
            for value in joins:
                place = _join(place, expand(value, directory))
            return place

        i = index + 1
        while i < len(fields):
            text, unknown = fields[i]
            if unknown and real:
                return True, None
            if text == "-C" and i + 1 < len(fields):
                joins.append(fields[i + 1])
                i += 2
            elif text == "-c" and real and i + 1 < len(fields) and \
                    fields[i + 1][0].lower().startswith(("alias.", "help.autocorrect")):
                return True, here()
            elif text in _GIT_VALUE_OPTIONS:
                i += 2
            elif text.startswith("-"):
                i += 1
            else:
                break
        if i < len(fields) and not fields[i][1] and fields[i][0].rstrip("\r").lower() == "commit":
            return True, here()
    return False, None


def _shell_script(args: list) -> tuple[str, bool] | None:
    """The script of `bash -c <script>`: the first operand after an option holding `c`."""
    i, command = 0, False
    while i < len(args):
        text, unknown = args[i]
        if unknown:
            return None
        if text in _SHELL_VALUE_OPTIONS:
            i += 2
            continue
        if text == "--":
            i += 1
            break
        if text.startswith("--"):
            i += 1
            continue
        if len(text) > 1 and text[0] in "-+":
            command = command or (text[0] == "-" and "c" in text[1:])
            i += 1
            continue
        break
    return args[i] if command and i < len(args) else None


def _pwsh_script(args: list, legacy: bool) -> tuple[tuple[str, bool] | None, tuple | None]:
    """(script, working directory field) of a `pwsh`/`powershell` command line."""
    workdir = None
    i = 0
    while i < len(args):
        text, unknown = args[i]
        if unknown:
            return None, workdir
        low = text.lower()
        if len(low) > 1 and low[0] in "-/":
            name = low[1:]
            rest = args[i + 1:]
            if name == "c" or (len(name) >= 3 and "command".startswith(name)):
                return (" ".join(t for t, _ in rest), any(u for _, u in rest)), workdir
            if name in ("e", "ec") or (len(name) >= 3 and "encodedcommand".startswith(name)):
                if not rest or rest[0][1]:
                    return None, workdir
                try:
                    script = base64.b64decode(rest[0][0]).decode("utf-16-le")
                except (ValueError, UnicodeDecodeError):
                    return None, workdir
                return (script, False), workdir
            if name in ("f", "file"):
                return None, workdir
            if name == "wd" or (len(name) >= 2 and "workingdirectory".startswith(name)):
                workdir = args[i + 1] if rest else None
                i += 2
                continue
            if any(len(name) >= 2 and full.startswith(name) for full in _PWSH_VALUE_OPTIONS):
                i += 2
                continue
            i += 1
            continue
        if legacy:  # Windows PowerShell reads a bare operand as -Command; pwsh as -File
            rest = args[i:]
            return (" ".join(t for t, _ in rest), any(u for _, u in rest)), workdir
        return None, workdir
    return None, workdir


_PS_ASSIGN_OPS = ("??=", "+=", "-=", "*=", "/=", "%=", "=")  # longest first

#: A mention of a bash name: `$NAME` or `${NAME...` (group 1), else a bare NAME (group 2).
_NAME_MENTION = re.compile(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)|(?<![\w$])([A-Za-z_][A-Za-z0-9_]*)")
_LET_ARGS = re.compile(r"\blet\s+([^;&|\n]*)")
_GLUED_OPTION = re.compile(r"(?<![\w$])-([A-Za-z]+[A-Za-z0-9_]*)")
_FOR_BEFORE = re.compile(r"\bfor\s+$")


def _untrusted_names(text: str) -> tuple[set[str], dict[int, str]]:
    """Names mentioned in `text` in a way the walk does not model, and every `NAME=`.

    Bash writes variables in many places the walk does not follow (`read X`, `getopts o X`,
    `printf -v X`, `${X:=v}`, `((X=1))`, `let X=1`, `declare -n R=X`, `select X`, `wait -p
    X`), and a write it misses leaves a wrong known value. So instead of listing writers, a
    name is trusted only while every mention of it is one the walk models (`$NAME`,
    `${NAME}`, `for NAME`, an assignment the parser produced); any other mention makes it
    unknown, which fails closed. The second result maps the offset of each `NAME=` and
    `NAME+=` to its name: bash also reads those as arithmetic (`[[ 1 -eq D=0 ]]`, `${x:
    D=0}`, `a[ D=0 ]`, `$[ D=0 ]`), so the walk trusts only those at an offset where the
    parser made an assignment word. The walk keeps the union over every text it reads, so a
    mention in eval text counts too. One pass over the mentions, each judged by a few
    characters around it, keeps this linear in the length of the command.
    """
    assigns: dict[int, str] = {}
    odd: set[str] = set()
    at = text.find("((")
    while at >= 0:  # arithmetic, `((...))` and `$((...))`: an assignment there is not modelled
        # A guess at the end, kept as a floor: the walk adds the parser's own spans.
        end = text.find("))", at + 2)
        end = len(text) if end < 0 else end
        odd.update(_NAME.findall(text, at, end))
        at = text.find("((", end + 2)
    for m in _LET_ARGS.finditer(text):
        odd.update(_NAME.findall(m.group(1)))
    for m in _GLUED_OPTION.finditer(text):  # `printf -vX`: X glued to an option letter
        cluster = m.group(1)
        odd.update(c for c in (cluster[i:] for i in range(1, min(len(cluster), 9)))
                   if _NAME.fullmatch(c))
    for m in _NAME_MENTION.finditer(text):
        start, end = m.span()
        if m.group(1) is not None:
            if text[start + 1] == "{" and not text.startswith("}", end):
                odd.add(m.group(1))  # `${NAME:=v}`, `${NAME/x/y}`: not a plain read
            continue
        name = m.group(2)
        if text.startswith(("=", "+="), end):
            assigns[start] = name
        elif not _FOR_BEFORE.search(text[max(0, start - 12):start]):
            odd.add(name)
    return odd, assigns


def _split_ps_operator(words: list) -> list:
    """`words` with an assignment operator glued to the variable or the value split out.

    `$x+='y'` is one word and `$x +='y'` two, yet PowerShell reads both as `$x += 'y'`.
    """
    def split(word: _Word, at: int) -> list | None:
        """[op word, rest word] when `word.parts[at]` starts with an operator, else None."""
        parts = word.parts
        if at >= len(parts) or parts[at][0] != "lit" or parts[at][2]:
            return None
        text = parts[at][1]
        op = next((o for o in _PS_ASSIGN_OPS if text.startswith(o)), None)
        if op is None:
            return None
        op_word, rest = _Word(), _Word()
        op_word.lit(op, False)
        if text[len(op):]:
            rest.lit(text[len(op):], False)
        rest.parts.extend(parts[at + 1:])
        return [op_word] + ([rest] if not rest.empty() else [])

    first = words[0].parts
    if len(first) >= 2:  # `$x+='y'`, `$x+= 'y'`
        pieces = split(words[0], 1)
        if pieces is not None:
            var = _Word()
            var.parts.append(first[0])
            return [var, *pieces, *words[1:]]
    if len(first) == 1 and len(words) >= 2 and words[1].plain() not in _PS_ASSIGN_OPS:
        pieces = split(words[1], 0)  # `$x +='y'`
        if pieces is not None:
            return [words[0], *pieces, *words[2:]]
    return words


def _with(states: set, status: str) -> set:
    return {(cwd, old, stack, status) for cwd, old, stack, _ in states}


_MISSING = object()
#: PowerShell common parameters that take a value, so a Set-Location target never is one.
_PS_VALUE_PARAMS = frozenset({
    "erroraction", "ea", "warningaction", "wa", "informationaction", "infa", "errorvariable",
    "ev", "warningvariable", "wv", "informationvariable", "iv", "outvariable", "ov",
    "outbuffer", "ob", "pipelinevariable", "pv", "stackname"})
_PS_SWITCH_PARAMS = frozenset({"passthru", "verbose", "vb", "debug", "db", "whatif", "wi",
                               "confirm", "cf"})


class _Walk:
    """Walk a parsed command, keeping every state the shell could be in.

    A state is `(cwd, oldpwd, dirstack, status)`: the directory (None when it cannot be
    known), `cd -`'s target, the `pushd` stack, and the last exit status as `S`, `F` or
    `?`. `&&` and `||` split states by status, so `x && cd wt; git commit` commits from
    both directories and `cd wt && git commit` from `wt` alone. A `cd` is taken to
    succeed when its target exists, which is when it would; when the target does not
    exist yet, both outcomes are kept. `commits` collects each directory a `git commit`
    would run in.

    Variables and functions are not part of a state, so two rules keep them honest: a
    subshell (`( )`, a pipeline element, `&`, `$( )`) gets its own copy, and an assignment
    made where the walk cannot tell whether it runs (`&&`, `||`, a branch, a loop body)
    makes a value that differs from the current one unknown, and a function defined there
    is walked both as the function and as the program of the same name.
    """

    def __init__(self, base: pathlib.Path):
        self.base = base
        self.commits: list[pathlib.Path | _Maybe | None] = []
        self.vars: dict[str, str | None] = {}
        self.funcs: dict[str, tuple] = {}  # name: (body, defined only on some paths)
        self.steps = 0
        self.depth = 0
        self.exits: list[tuple[set, set]] = []  # (break, continue) states of each open loop
        self.cond = 0         # > 0 while walking code that may or may not run
        self.locals: list[dict] = []  # per function call: the values `local` shadowed
        self.ifs_changed = False      # IFS assigned: unquoted expansions split unknowably
        self.globs_changed = False    # `set -f` or a glob `shopt`: patterns expand unknowably
        self.glob_budget = [_MAX_GLOB_ENTRIES]
        self.lastpipe = False         # `shopt` seen: the last pipeline element may persist
        self.piped = False            # walking a PowerShell pipeline element after the first
        self.deadline = time.monotonic() + _DEADLINE_S
        self.expanded = 0  # characters `_fields` has read or built, bounded by _MAX_EXPANDED
        self.untrusted: set[str] = set()  # names some mention may write unseen (_untrusted_names)
        self.opaque = False  # a nameref was declared: any assignment may write another name
        self.bashisms = True  # how the shell being walked reads `_BASHISMS`: see `_nested`
        self.exported: set[str] = set()        # names `export` or `declare -x` gave
        self.exported_funcs: set[str] = set()  # functions `export -f` gave

    # -- scripts and lists -----------------------------------------------------------

    def script(self, text: str, dialect: str, states: set) -> set:
        parser = _parser(text, dialect, self.depth, self.bashisms)
        try:
            items, clean = parser.program(), True
        except (_Unparsed, _ArithIsCommand):
            items, clean = parser.complete, False
        if isinstance(parser, _Bash):
            odd, assigns = _untrusted_names(text)
            self.untrusted |= odd | parser.arith_names()
            self.untrusted.update(name for at, name in assigns.items()
                                  if at not in parser.assign_at)
        out = self._list(items, states)
        if not clean and _names_a_commit(text):
            # The complete lines already ran and may have moved, and bash goes on after
            # some errors: a commit in the rest may run where they started or ended.
            self._maybe_commit(*(state[0] for state in states | out))
        return out

    def _scoped(self, run):
        """Run `run()` as a subshell does: nothing it assigns or defines comes back."""
        saved = dict(self.vars), dict(self.funcs), self.ifs_changed, self.globs_changed
        try:
            return run()
        finally:
            self.vars, self.funcs, self.ifs_changed, self.globs_changed = saved

    def _maybe(self, run):
        """Run `run()` as code that may not run at all (a branch, `&&`, a loop body)."""
        self.cond += 1
        try:
            return run()
        finally:
            self.cond -= 1

    def _set(self, name: str, value: str | None) -> None:
        if value is not None and len(value) > _MAX_VALUE:
            value = None  # `X=$X$X` repeated would make every later step slower
        if self.cond and self.vars.get(name, _MISSING) != value:
            value = None  # one path assigns it, another may not: unknown
        if name in ("PWD", "OLDPWD"):
            value = None  # bash rewrites both on every `cd`, so an assigned value goes stale
        self.vars[name] = value

    def _forget(self, permanent: bool = False) -> None:
        """Code the walk cannot see ran in this shell: every variable may have changed."""
        self.vars = dict.fromkeys(self.vars)
        self.opaque = self.opaque or permanent
        self.globs_changed = True  # and any glob option (STAKES-003)

    def _maybe_commit(self, *directories: pathlib.Path | None) -> None:
        """Text that may run a commit the walk cannot see: judge where it starts, and MAYBE."""
        self.commits.extend(directories)
        self.commits.append(MAYBE)

    def _nested(self, script: tuple[str, bool], dialect: str,
                directory: pathlib.Path | None, state: tuple) -> None:
        """A child shell: it starts in `directory`, and nothing it sets comes back.

        `dialect` "sh" is a shell that may not be bash (`sh` is dash on Debian, bash on
        macOS): where the readings differ (`_BASHISMS`) the script is walked under each, and
        what either finds counts. A child keeps an exported value; any other name the parent
        assigned is unknown to it (it may have come from the environment, which exports it),
        never absent, since an absent name would read as `_value`'s default. A function not
        known to be exported may or may not be there (`set -a` exports it).
        """
        if script[1]:
            self._maybe_commit(directory)  # its text cannot be known, so may hold a commit
            return
        text = script[0]
        joined = text.replace("\\\n", "")  # `$\<newline>[` is `$[` to bash
        sh = dialect == "sh" and any(b in joined for b in _BASHISMS)
        readings = [True, False] if sh else [True]
        dialect = "bash" if dialect == "sh" else dialect
        self.depth += 1
        saved = self.bashisms
        try:
            if self.depth > _MAX_NESTING:
                raise RuntimeError("shells nested too deeply to model")
            def child():
                self.vars = {k: v if k in self.exported else None for k, v in self.vars.items()}
                self.funcs = {k: f if k in self.exported_funcs else (f[0], True)
                              for k, f in self.funcs.items()}
                # OLDPWD is exported, so a child shell's `cd -` goes where the parent's would.
                return self.script(text, dialect, {(directory, state[1], (), "S")})

            for bashisms in readings:
                self.bashisms = bashisms
                self._scoped(child)
        finally:
            self.depth -= 1
            self.bashisms = saved

    def _list(self, items: list, states: set) -> set:
        for node, background in items:
            if not states:
                break
            if background:
                self._scoped(lambda node=node, states=states: self._and_or(node, states))
                states = _with(states, "S")
            else:
                states = self._and_or(node, states)
            if len(states) > _MAX_STATES:
                states = {(None, None, (), "?")}
        return states

    def _and_or(self, node: tuple, states: set) -> set:
        _, first, rest = node
        current = self._pipeline(first, states)
        for op, pipeline in rest:
            want = "S" if op == "&&" else "F"
            other = "F" if want == "S" else "S"
            run = {(c, o, k, want) for c, o, k, s in current if s in (want, "?")}
            skip = {(c, o, k, other) for c, o, k, s in current if s in (other, "?")}
            ran = self._maybe(lambda p=pipeline, r=run: self._pipeline(p, r)) if run else set()
            current = ran | skip
        return current

    def _pipeline(self, node: tuple, states: set) -> set:
        _, commands, negate = node
        if len(commands) == 1:
            out = self._command(commands[0], states)
        else:
            for command in commands[:-1]:  # each element of a bash pipeline is a subshell
                self._scoped(lambda c=command: self._command(c, states))
            if self.lastpipe:  # with lastpipe the last element runs in this shell
                out = self._maybe(lambda: self._command(commands[-1], states)) | \
                    _with(states, "?")
            else:
                self._scoped(lambda: self._command(commands[-1], states))
                out = _with(states, "?")
        if negate:
            flip = {"S": "F", "F": "S", "?": "?"}
            out = {(c, o, k, flip[s]) for c, o, k, s in out}
        return out

    def _subs(self, words: list, states: set) -> None:
        for word in words:
            for body in word.subs:
                self._scoped(lambda b=body: self._list(b, states))

    # -- commands --------------------------------------------------------------------

    def _command(self, node: tuple, states: set) -> set:
        self.steps += 1
        if self.steps > _MAX_STEPS or time.monotonic() > self.deadline:
            raise RuntimeError("command too complex to model")
        kind = node[0]
        if kind == "simple":
            return self._simple(node, states)
        if kind == "ps":
            return self._ps(node, states)
        if kind == "sub":
            out = self._scoped(lambda: self._list(node[1], states))
            statuses = {s for *_, s in out}
            return _with(states, statuses.pop() if len(statuses) == 1 else "?")
        if kind == "bg":
            self._scoped(lambda: self._command(node[1], states))
            return _with(states, "S")
        if kind == "group":
            return self._list(node[1], states)
        if kind == "maybe":
            return self._maybe(lambda: self._list(node[1], states)) | _with(states, "?")
        if kind == "seq":
            piped = self.piped
            try:
                for index, command in enumerate(node[1]):
                    self.piped = index > 0
                    states = self._command(command, states)
            finally:
                self.piped = piped
            return _with(states, "?")
        if kind == "redir":
            self._subs(node[2], states)
            return self._command(node[1], states)
        if kind == "if":
            return self._if(node, states)
        if kind == "loop":
            return self._maybe(lambda: self._repeat(node[1], node[2], states, until=node[3]))
        if kind == "for":
            return self._for(node, states)
        if kind == "case":
            return self._case(node, states)
        if kind in ("arith", "cond"):
            self._subs(node[1], states)
            return _with(states, "?")
        if kind == "func":
            known = self.funcs.get(node[1])
            self.funcs[node[1]] = (node[2], bool(self.cond) or (known is not None and known[1]))
            return _with(states, "S")
        raise RuntimeError(f"unknown node {kind}")

    def _if(self, node: tuple, states: set) -> set:
        _, clauses, otherwise = node
        result: set = set()
        flow = states
        for number, (cond, body) in enumerate(clauses):
            if number:
                tested = self._maybe(lambda c=cond, f=flow: self._list(c, f))
            else:
                tested = self._list(cond, flow)
            taken = {(c, o, k, "S") for c, o, k, s in tested if s != "F"}
            flow = {(c, o, k, "F") for c, o, k, s in tested if s != "S"}
            if taken:
                result |= self._maybe(lambda b=body, t=taken: self._list(b, t))
        if otherwise is not None:
            result |= self._maybe(lambda: self._list(otherwise, flow)) if flow else set()
        else:
            result |= _with(flow, "S")
        return result

    def _for(self, node: tuple, states: set) -> set:
        """A `for` over words it can read runs once per word; any other runs any number of times."""
        _, name, words, body = node
        self._subs(words, states)
        lists = [[f for w in words for f in self._fields(w, st)] for st in states]
        known = bool(lists) and all(fields == lists[0] for fields in lists) and not any(
            unknown for _, unknown in lists[0])
        if name is None or not known or len(lists[0]) > _LOOP_PASSES * 4:
            if name is not None:
                self.vars[name] = None
            return self._maybe(lambda: self._repeat(None, body, states))
        self.exits.append((set(), set()))
        try:
            for text, _ in lists[0]:
                self._set(name, text)  # the `cond` and PWD/OLDPWD rules hold here too
                states = self._list(body, states) | self.exits[-1][1]
                self.exits[-1][1].clear()
                if not states:
                    break
        finally:
            breaks = self.exits.pop()[0]
        return _with(states, "S") | _with(breaks, "S")

    def _repeat(self, cond: list | None, body: list, states: set, until: bool = False) -> set:
        """States after a loop whose body runs any number of times, `break` included."""
        go = "F" if until else "S"
        self.exits.append((set(), set()))  # states leaving by `break`, by `continue`
        result: set = set()
        seen: set = set()
        frontier = set(states)
        settled = False
        try:
            for _ in range(_LOOP_PASSES):
                if cond is None:
                    result |= frontier
                    entering = frontier
                else:
                    tested = self._list(cond, frontier)
                    result |= {(c, o, k, "S") for c, o, k, s in tested if s != go}
                    entering = {(c, o, k, go) for c, o, k, s in tested if s in (go, "?")}
                seen |= {state[:3] for state in frontier}
                after = self._list(body, entering) if entering else set()
                after |= self.exits[-1][1]
                self.exits[-1][1].clear()
                frontier = {state for state in after if state[:3] not in seen}
                if not frontier:
                    settled = True
                    break
        finally:
            breaks = self.exits.pop()[0]
        result |= _with(breaks, "S")
        if not settled:  # still moving after every pass: it could end anywhere
            result.add((None, None, (), "?"))
        return result

    def _case(self, node: tuple, states: set) -> set:
        _, subject, arms = node
        self._subs([subject, *(p for patterns, _, _ in arms for p in patterns)], states)
        result = _with(states, "S")
        carry: set = set()
        for _, body, end in arms:
            out = self._maybe(lambda b=body, c=carry: self._list(b, set(states) | c))
            result |= out
            carry = out if end in (";&", ";;&") else set()
        return result

    # -- expansion -------------------------------------------------------------------

    def _value(self, name: str, state: tuple) -> str | None:
        """A variable's value, or None when this hook cannot know it.

        Only what the command itself assigns is known, plus PWD, OLDPWD and HOME. The
        hook's own environment is not the shell's (Claude Code gives hooks variables, such
        as CLAUDE_PROJECT_DIR, that the Bash tool's shell does not have, and the other way
        round), so reading it would turn an unknown directory into a wrong known one.
        """
        if self.opaque or name in self.untrusted:
            return None  # written somewhere the walk does not model: not even its default
        if name in self.vars:
            return self.vars[name]
        if name == "PWD":
            return None if state[0] is None else str(state[0])
        if name == "OLDPWD":
            return None if state[1] is None else str(state[1])
        if name == "HOME":
            return os.environ.get("HOME") or os.path.expanduser("~")
        return None

    def _fields(self, word: _Word, state: tuple, split: bool = True) -> list[tuple[str, bool]]:
        """The fields `word` expands to, each `(text, unknown)`.

        With `split` (bash words), a field holding an unquoted `*`, `?` or `[` is a
        `_Field` that keeps which of its characters were quoted, for `_expand`.
        """
        fields: list[tuple[str, bool]] = []
        text: list[str] = []
        chunks: list[tuple[str, bool]] = []  # (text, unquoted) of the field so far
        unknown = False
        started = False

        def flush():
            nonlocal text, chunks, unknown, started
            if started:
                self.expanded += sum(map(len, text))
                if self.expanded > _MAX_EXPANDED:
                    raise RuntimeError("command too complex to model")
                field = ("".join(text), unknown)
                if split and not unknown and any(
                        active and any(c in chunk for c in "*?[") for chunk, active in chunks):
                    field = _Field(field)
                    field.pattern = tuple(chunks)
                fields.append(field)
            text, chunks, unknown, started = [], [], False, False

        for part in word.parts:
            kind = part[0]
            if kind == "lit":
                text.append(part[1])
                chunks.append((part[1], not part[2]))
                started = True
                continue
            if kind == "tilde":
                value = os.path.expanduser("~" + part[1]) if part[1] else self._value("HOME", state)
            else:
                value = self._value(part[1], state) if kind == "var" else None
            if value is None or (kind == "tilde" and value.startswith("~")):
                text.append(_UNKNOWN)
                unknown = started = True
                continue
            if not split or kind == "tilde" or part[-1]:
                text.append(value)
                chunks.append((value, False))  # quoted, or a tilde, which POSIX never globs
                started = True
                continue
            if self.ifs_changed or self.opaque or "IFS" in self.untrusted:
                # split on an IFS this walk does not track, or one written where it cannot see
                text.append(_UNKNOWN)
                unknown = started = True
                continue
            # Charged before the split, which reads the whole value: a flush comes too late
            # to bound it.
            self.expanded += len(value)
            if self.expanded > _MAX_EXPANDED:
                raise RuntimeError("command too complex to model")
            pieces = value.split()
            if value[:1].isspace():
                flush()
            for n, piece in enumerate(pieces):
                if n:
                    flush()
                text.append(piece)
                chunks.append((piece, True))  # an unquoted expansion is globbed too
                started = True
            if pieces and value[-1:].isspace():
                flush()
        flush()
        if _brace_expands(word.raw):
            fields = [(t, True) for t, _ in fields]
        return fields

    def _home(self, state: tuple) -> pathlib.Path | None:
        home = self._value("HOME", state)
        return None if home is None else _join(state[0], (home, False))

    def _expand(self, field: tuple, base: pathlib.Path | None, ps: bool = False) -> tuple:
        """`field` after pathname expansion in `base`, as one field.

        Called only where a directory or the program is decided. No match leaves the
        word as typed, as bash does without nullglob. A pattern this hook cannot expand,
        or one naming several paths, is unknown: the shell would pass several words, which
        `cd`, `git -C` and Set-Location each refuse or read as the next argument, and that
        is not modelled. `ps` reads the text with PowerShell's wildcard rules. The field
        itself comes back when nothing was expanded.
        """
        if field[1]:
            return field
        if ps:
            pattern = _ps_wildcard(field[0])
        else:
            pattern = [(c, active) for chunk, active in getattr(field, "pattern", ())
                       for c in chunk]
        if not any(active and c in "*?[" for c, active in pattern):
            return field
        try:
            # GLOBIGNORE removes matches and turns on dotglob (SEC-002), and code the walk
            # cannot see may have set any glob option (STAKES-003).
            if not ps and (self.globs_changed or self.opaque or "GLOBIGNORE" in self.vars
                           or "GLOBIGNORE" in self.untrusted):
                raise _Unknowable("glob options this walk does not track")
            paths = _pathnames(pattern, base, ps, self.glob_budget)
        except _Unknowable:
            return (_UNKNOWN, True)
        if not paths:
            return field
        return (paths[0], False) if len(paths) == 1 else (_UNKNOWN, True)

    # -- simple commands -------------------------------------------------------------

    def _per_state(self, states: set, run_one) -> set:
        """`run_one(state)` for each state, each from the variables before the command.

        Variables are one dict, not part of a state, so a command walked once per state
        would otherwise apply its assignments once per state (`X+=a` appended twice). The
        runs are merged afterwards: a name the states leave different becomes unknown.
        """
        listed = list(states)
        if len(listed) == 1:
            return run_one(listed[0])
        before = dict(self.vars)
        out: set = set()
        results = []
        for state in listed:
            if time.monotonic() > self.deadline:
                raise RuntimeError("command too complex to model")
            self.vars = dict(before)
            out |= run_one(state)
            results.append(self.vars)
        merged = dict(before)
        for key in set().union(*results):
            values = {result.get(key, _MISSING) for result in results}
            value = values.pop() if len(values) == 1 else None
            if value is _MISSING:
                merged.pop(key, None)
            else:
                merged[key] = value
        self.vars = merged
        return out

    def _simple(self, node: tuple, states: set) -> set:
        _, assigns, words, extra = node
        self._subs(assigns + words + extra, states)

        def one(state: tuple) -> set:
            fields = [f for w in words for f in self._fields(w, state)]
            if fields:
                # `D=v cmd`: a function or `eval` sees D=v and an external program does not,
                # and bash restores D after. Rather than model which, D is unknown from here.
                self.untrusted.update(self._assignment(w, state)[0] for w in assigns)
                # The program word is expanded too: `gi?` beside a file `git` runs git.
                fields[0] = self._expand(fields[0], state[0])
                return self._run(fields, state)
            for word in assigns:
                name, value = self._assignment(word, state)
                self.ifs_changed = self.ifs_changed or name == "IFS"
                self._set(name, value)
            return {state[:3] + ("S",)}

        return self._per_state(states, one)

    def _assignment(self, word: _Word, state: tuple) -> tuple[str, str | None]:
        head = word.parts[0][1]
        name, _, rest = head.partition("=")
        value = _Word()
        if rest:
            value.lit(rest, False)
        value.parts.extend(word.parts[1:])
        fields = self._fields(value, state, split=False)
        text = fields[0] if fields else ("", False)
        known = None if text[1] else text[0]
        key = name.rstrip("+").split("[")[0]
        if name.endswith("+"):  # `X+=v` appends
            old = self._value(key, state)
            known = None if old is None or known is None else old + known
        return key, known

    def _run(self, fields: list, state: tuple) -> set:
        name = fields[0][0] if not fields[0][1] else None
        bypass = False  # `command` and `builtin` skip shell functions
        while name in ("builtin", "command") and len(fields) > 1:
            if name == "command" and fields[1][0] in ("-v", "-V"):
                return {state[:3] + ("?",)}
            bypass = True
            fields = fields[1:]
            if fields[0] == ("-p", False) and len(fields) > 1:
                fields = fields[1:]
            name = fields[0][0] if not fields[0][1] else None
        if not bypass and name in self.funcs:  # bash looks functions up before builtins
            body, partial = self.funcs[name]
            out = self._call(name, body, state)
            return out | self._builtin(name, fields, state) if partial else out
        return self._builtin(name, fields, state)

    def _call(self, name: str, body: tuple, state: tuple) -> set:
        if self.depth >= _MAX_NESTING:
            raise RuntimeError("functions nested too deeply to model")
        self.depth += 1
        self.locals.append({})
        try:
            return self._command(body, {state})
        finally:
            self.depth -= 1
            for key, old in self.locals.pop().items():
                if old is _MISSING:
                    self.vars.pop(key, None)
                else:
                    self.vars[key] = old

    def _builtin(self, name: str | None, fields: list, state: tuple) -> set:
        args = fields[1:]
        if name == "cd":
            return self._cd(args, state)
        if name == "pushd":
            return self._pushd(args, state)
        if name == "popd":
            return self._popd(args, state)
        if name == "eval":
            return self._eval(args, "bash", state)
        if name == "exit":
            return set()
        if name in ("break", "continue"):
            return self._leave(name, args, state)
        if name in ("true", ":"):
            return {state[:3] + ("S",)}
        if name == "false":
            return {state[:3] + ("F",)}
        if name == "local" and not self.locals:
            return {state[:3] + ("F",)}  # outside a function bash refuses it and assigns nothing
        if name in _DECLARATIONS:
            flags = "".join(t[1:] for t, u in args if u or (t.startswith(("-", "+")) and t != "-"))
            if "n" in flags or any(u and "=" not in t for t, u in args):
                # A nameref (`-n`, or a word that may be it): `R=v` may write any name.
                self._forget(permanent=True)
            if name == "export" or "x" in flags:  # what a child shell will see
                names = {t.partition("=")[0].rstrip("+") for t, u in args
                         if not u and not t.startswith(("-", "+"))}
                (self.exported_funcs if "f" in flags else self.exported).update(names)
            for text, unknown in args:
                key, eq, value = text.partition("=")
                append = key.endswith("+")
                key = key.rstrip("+")
                if not (eq and _NAME.fullmatch(key)):
                    continue
                if name == "local":
                    self.locals[-1].setdefault(key, self.vars.get(key, _MISSING))
                if append:
                    old = self._value(key, state)
                    value = None if old is None or unknown else old + value
                # With an option (-l, -u, -i, -a...) the stored value is transformed.
                self._set(key, None if unknown or flags else value)
            return {state[:3] + ("S",)}
        if name in ("source", "."):
            self._forget()  # a file this guard cannot read runs in this shell
        if name in ("unset", "read", "mapfile", "readarray"):
            for text, unknown in args:
                if not unknown and _NAME.fullmatch(text):
                    self._set(text, "" if name == "unset" else None)
            return {state[:3] + ("?",)}
        if name == "printf":
            for at, (text, _) in enumerate(args[:-1]):
                if text == "-v" and not args[at + 1][1]:
                    self.vars[args[at + 1][0]] = None
        if name == "shopt" and any(u or "lastpipe" in t for t, u in args):
            self.lastpipe = True
        if name is None or (name in ("set", "shopt") and _changes_globs(args)):
            # nocaseglob, nullglob, dotglob, `set -f`, ...; and a program the walk cannot
            # name may be `set` or `shopt` (STAKES-003).
            self.globs_changed = True
        self._programs(fields, state)
        return {state[:3] + ("?",)}

    def _programs(self, fields: list, state: tuple, unknown_program: bool = True) -> None:
        """Record a `git commit` among `fields`, and walk any shell they start with a script."""
        if unknown_program and fields[0][1] and (len(fields) == 1 or fields[1][1]):
            # The program and what it is told to do are both unknown: it may be a commit.
            # The fields after them are still read, for a commit or shell they name.
            self._maybe_commit(state[0])
        names = [_program(field) for field in fields]
        found, directory = _git_commit(fields, state[0], names, self._expand)
        if found:
            self.commits.append(directory)
        envs = _envs_before(names)
        for index, program in enumerate(names):
            if program in _POSIX_SHELLS:
                script = _shell_script(fields[index + 1:])
                if script is not None:
                    start = _env_directory(fields, state[0], index, envs, self._expand)
                    at = next(k for k in range(index + 1, len(fields)) if fields[k] is script)
                    saved = self.globs_changed
                    # `bash -f -c`, `-o noglob`, `-O nocaseglob`: the child expands otherwise
                    self.globs_changed = saved or _changes_globs(fields[index + 1:at])
                    try:
                        self._nested(script, "bash" if program == "bash" else "sh", start,
                                     state)
                    finally:
                        self.globs_changed = saved
            elif program in ("pwsh", "powershell"):
                script, workdir = _pwsh_script(fields[index + 1:], program == "powershell")
                if script is not None:
                    start = _env_directory(fields, state[0], index, envs, self._expand)
                    where = start if workdir is None else _join(
                        start, self._expand(workdir, state[0]))  # bash expands it (BUG-004)
                    self._nested(script, "ps", where, state)

    def _leave(self, name: str, args: list, state: tuple) -> set:
        """`break N` leaves N loops and `continue N` resumes the Nth: the rest is skipped."""
        if not self.exits:
            return {state[:3] + ("?",)}  # outside a loop bash warns and carries on
        slot = 0 if name == "break" else 1
        count = args[0] if args else ("1", False)
        if count[1] or not count[0].isdigit() or int(count[0]) < 1:
            for exits in self.exits:  # a count it cannot read: any enclosing loop
                exits[slot].add(state)
            return set()
        levels = min(int(count[0]), len(self.exits))
        for exits in self.exits[-levels:-1]:
            exits[0].add(state)  # the loops in between are left
        self.exits[-levels][slot].add(state)
        return set()

    def _eval(self, args: list, dialect: str, state: tuple) -> set:
        if any(unknown for _, unknown in args):
            self._maybe_commit(state[0])  # its text cannot be known, so may hold a commit
            self._forget()  # and it runs in this shell, so it may set any variable
            return {state[:3] + ("?",)}
        self.depth += 1
        try:
            if self.depth > _MAX_NESTING:
                raise RuntimeError("evals nested too deeply to model")
            return self.script(" ".join(text for text, _ in args), dialect, {state})
        finally:
            self.depth -= 1

    def _move(self, state: tuple, target: pathlib.Path | None, stack: tuple,
              certain: bool = True) -> set:
        cwd, old, _, _ = state
        moved = (target, cwd, stack, "S")
        if target is not None and certain and target.is_dir():
            return {moved}
        return {moved, state[:3] + ("F",)}  # an unknown place may not exist: `cd` may fail

    def _cd(self, args: list, state: tuple) -> set:
        cwd, old, stack, _ = state
        i = 0
        while i < len(args) and not args[i][1] and args[i][0].startswith("-") and args[i][0] != "-":
            i += 1
            if args[i - 1][0] == "--":
                break
        rest = args[i:]
        certain = len(rest) <= 1
        if not rest:
            target = self._home(state)
        elif rest[0] == ("-", False):
            # bash goes to $OLDPWD: unknown once assigned or written where the walk cannot see
            target = None if self._value("OLDPWD", state) is None else old
        else:
            expanded = self._expand(rest[0], cwd)
            target = self._cdpath(cwd, expanded, state)
            # A match read before the command runs may not be the shell's (SEC-003).
            certain = certain and expanded is rest[0]
        return self._move(state, target, stack, certain=certain)

    def _cdpath(self, cwd: pathlib.Path | None, field: tuple, state: tuple):
        """`cd`'s target: a plain relative name is looked up along CDPATH first, as bash does.

        Only a CDPATH the command assigns is used: the hook's environment is not the
        shell's (`_value`), and the Bash tool's shell rarely sets one.
        """
        text, unknown = field
        target = _join(cwd, field)
        if unknown or cwd is None or text.startswith(("/", "./", "../")) or text in (".", ".."):
            return target
        if self.opaque or "CDPATH" in self.untrusted:
            return None  # written somewhere the walk does not model (see `_value`)
        cdpath = self.vars.get("CDPATH")
        if cdpath is None and "CDPATH" in self.vars:
            return None  # assigned, but to a value this walk cannot know
        for entry in (cdpath or "").split(os.pathsep if os.name == "nt" and ";" in (cdpath or "")
                                          else ":"):
            entry = entry or "."
            candidate = _join(cwd, (os.path.join(entry, text), False))
            if candidate is not None and candidate.is_dir():
                return candidate
        return target

    def _pushd(self, args: list, state: tuple) -> set:
        cwd, old, stack, _ = state
        args = [a for a in args if a != ("--", False)]
        if args and args[0] == ("-n", False):
            pushed = (_join(cwd, self._expand(args[1], cwd)),) if len(args) > 1 else ()
            return {(cwd, old, pushed + stack, "S")}
        if not args:
            if not stack:
                return {state[:3] + ("F",)}
            return self._move(state, stack[0], (cwd,) + stack[1:])
        target = self._expand(args[0], cwd)
        text, unknown = target
        if unknown or (text[:1] in "+-" and text[1:].isdigit()):
            return {(None, cwd, (cwd,) + stack, "?")}
        return self._move(state, _join(cwd, target), (cwd,) + stack,
                          certain=target is args[0])  # SEC-003, as in `_cd`

    def _popd(self, args: list, state: tuple) -> set:
        cwd, old, stack, _ = state
        if any(a != ("+0", False) and a != ("--", False) for a in args):
            return {(cwd, old, stack[1:], "?")}  # `-n`, `+N`: the stack changes, not the cwd
        if not stack:
            return {state[:3] + ("F",)}
        return self._move(state, stack[0], stack[1:])

    # -- PowerShell commands -----------------------------------------------------------

    def _ps(self, node: tuple, states: set) -> set:
        _, words, blocks, called = node
        for block in blocks:
            states = self._command(block, states)
        self._subs(words, states)
        if not words:
            return states

        def one(state: tuple) -> set:
            assignment = self._ps_assignment(words)
            if assignment is not None:
                key, command = assignment
                out = {state[:3] + ("S",)}
                if command:  # `$x = git commit`: the right side is a command, and it runs
                    fields = [f for w in command for f in self._fields(w, state, split=False)]
                    out = self._ps_run(fields, state) if fields else out
                self.vars[key] = None
                return out
            fields = [f for w in words for f in self._fields(w, state, split=False)]
            return self._ps_run(fields, state, called) if fields else {state[:3] + ("S",)}

        return self._per_state(states, one)

    def _ps_assignment(self, words: list) -> tuple[str, list] | None:
        """`$x <op> <value>` or `$env:X <op> <value>`: (key, the command its right side runs).

        The right side is a command when it is more than one word, or one bareword:
        PowerShell runs `$out = git commit -m x` and `$x = child` alike. The value itself is
        never kept: PowerShell's operators, casts, scopes, loops and variable cmdlets are not
        modelled, so a value the walk computed could be a wrong known one.
        """
        first = words[0].parts
        if not first or first[0][0] != "var":
            return None
        words = _split_ps_operator(words)
        first = words[0].parts
        op = words[1].plain() if len(first) == 1 and len(words) >= 2 else None
        if op not in _PS_ASSIGN_OPS:
            return None
        right = words[2:]
        if len(right) != 1:
            return first[0][1], right
        parts = right[0].parts
        bare = bool(parts) and parts[0][0] == "lit" and not parts[0][2] \
            and not re.fullmatch(r"[\d.]+", parts[0][1])
        return first[0][1], right if bare else []

    def _ps_run(self, fields: list, state: tuple, called: bool = False) -> set:
        name = _program(fields[0])
        args = fields[1:]
        if name in ("cd", "chdir", "sl", "set-location"):
            return self._ps_cd(args, state, push=False)
        if name in ("pushd", "push-location"):
            return self._ps_cd(args, state, push=True)
        if name in ("popd", "pop-location"):
            return self._popd([], state)
        if name in ("iex", "invoke-expression"):
            script = [a for a in args if a[1] or not a[0].startswith("-")][:1]
            if not script:  # it reads its script from the pipeline
                self._maybe_commit(state[0])
                return {state[:3] + ("?",)}
            return self._eval(script, "ps", state)
        if name == "exit":
            return set()
        if name in ("break", "continue"):
            return self._leave(name, args, state)
        # Without `&`, a statement that starts with a variable is an expression, not a
        # program: PowerShell runs no command for `$LASTEXITCODE` or `$x.Count`.
        self._programs(fields, state, unknown_program=called)
        out = {state[:3] + ("?",)}
        if (name is None or "." in fields[0][0] or "::" in fields[0][0]) and \
                any("location" in text.lower() for text, _ in fields):
            # A method or a command the walk cannot name that talks about the location
            # (`$ExecutionContext.SessionState.Path.SetLocation(...)`) may move it.
            out.add((None, state[0], state[2], "?"))
        return out

    def _ps_cd(self, args: list, state: tuple, push: bool) -> set:
        cwd, old, stack, _ = state
        target = None
        literal = False  # -LiteralPath (alias -PSPath, -LP) takes no wildcards; -Path does
        i = 0
        while i < len(args):
            text, unknown = args[i]
            if not unknown and text.startswith("-") and len(text) > 1:
                name, colon, value = text[1:].partition(":")
                name = name.lower()
                takes_path = name == "lp" or any(
                    full.startswith(name) for full in ("path", "literalpath", "pspath"))
                if takes_path:
                    literal = not "path".startswith(name)
                    if colon:
                        target = (value, False)
                    elif i + 1 < len(args):
                        target = args[i + 1]
                        i += 1
                elif name in _PS_VALUE_PARAMS:
                    i += 0 if colon else 1
                elif name not in _PS_SWITCH_PARAMS:
                    return {(None, cwd, stack, "?")}  # a parameter it does not know
                i += 1
                continue
            if target is None:
                target = args[i]
            i += 1
        pushed = (cwd,) + stack if push else stack
        if target is None:
            if self.piped:  # `'child' | Set-Location`: the path comes from the pipeline
                return {(None, cwd, pushed, "?")}
            if push:
                return {state[:3] + ("S",)}
            return {(self._home(state), cwd, stack, "S"), state[:3] + ("S",)}
        if target in (("-", False), ("+", False)):
            return {(None, cwd, pushed, "S")}
        expanded = target if literal else self._expand(target, cwd, ps=True)
        return self._move(state, _join(cwd, expanded), pushed,
                          certain=expanded is target)  # SEC-003, as in `_cd`


def commit_directories(command: str, base: pathlib.Path,
                       dialect: str = "bash") -> list[pathlib.Path | _Maybe | None]:
    """The directory each `git commit` in `command` would run in, starting from `base`.

    Empty when no commit would run. None stands for a commit whose directory the command
    does not let us know, MAYBE for text that may run a commit the walk cannot see
    (`_commit_roots` judges the two differently). `dialect` is `bash` or `ps`
    (PowerShell). Fails closed twice over: when the walk raises (a bound, recursion, its
    deadline), the commits it already found are kept and None is added, since the
    directory it had reached is lost; when it finds no commit but the text names one
    (`_names_a_commit`), MAYBE is returned, because something the walk does not model may
    run it.
    """
    walk = _Walk(base)
    try:
        walk.script(command, dialect, {(base, None, (), "S")})
    except Exception:  # noqa: BLE001 (a fault in the model must not hide a commit)
        walk.commits.append(None)  # where the walk had got to is lost: judge every open run
    if not walk.commits and _names_a_commit(command):
        walk.commits.append(MAYBE)
    return list(dict.fromkeys(walk.commits))


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


#: Seconds the guard may take to decide before it stops and fails closed. A hook past its
#: 60 s timeout lets the command through wherever the time went (the parser, the walk, the
#: file system), so the clock bounds the whole decision; the bounds inside the walk only
#: make the common slow cases fast. The fingerprint inside `_judge` stops itself at 30 s.
_WATCHDOG_S = 45.0


def _watchdog(base: pathlib.Path) -> threading.Timer:
    """A timer that ends the process with a decision if the guard has not decided in time.

    It refuses when a run could be open (at the session's checkout or in the registry),
    and allows otherwise: with no run to judge the guard has no opinion.
    """
    def expire() -> None:
        try:
            open_run = find_root(base.resolve()) is not None or bool(_registered_roots())
        except Exception:  # noqa: BLE001 (cannot tell: a run may be open)
            open_run = True
        if open_run:
            print("CCA pipeline gate: the guard could not judge this command in time, and a "
                  "CCA run is open, so the command is refused. Run `git commit` as a plain "
                  "command, apart from the rest.", file=sys.stderr)
        sys.stderr.flush()
        os._exit(2 if open_run else 0)

    timer = threading.Timer(_WATCHDOG_S, expire)
    timer.daemon = True
    timer.start()
    return timer


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
    if not command:
        return 0

    base = pathlib.Path(read_cwd(raw) or os.getcwd())
    timer = _watchdog(base)
    try:
        return _decide(command, base, read_dialect(raw))
    finally:
        timer.cancel()


def _decide(command: str, base: pathlib.Path, dialect: str) -> int:
    """Exit code for a shell command: 2 when a commit in it is refused, else 0."""
    directories = commit_directories(command, base, dialect)
    roots: list[pathlib.Path] = []
    unknown_roots: set[str] = set()
    for directory in directories:
        for root in _commit_roots(directory, base):
            if directory is None or isinstance(directory, _Maybe):
                unknown_roots.add(os.path.normcase(str(root)))
            if not any(_same_path(root, seen) for seen in roots):
                roots.append(root)
    for root in roots:
        refused = _judge(root, command, dialect)
        if refused:
            if os.path.normcase(str(root)) in unknown_roots:
                print("\n(The guard could not tell from the command where a commit in it runs,"
                      "\nor whether it commits at all: it names git and commit, or runs text"
                      "\nthe guard cannot read. Every open run it could affect was judged."
                      "\nRun `git commit` as a plain command with the directory named"
                      "\nliterally.)", file=sys.stderr)
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


def ps_chained_with(command: str) -> str | None:
    """`chained_with` for a PowerShell command, read by the PowerShell reader, not as bash.

    Bash rules misread PowerShell both ways: a backslash is literal there (so `y\\; git
    commit` is two statements) and a backtick escapes. A lone command is one statement
    with no `&&`/`||`, no pipeline, and no `( )`, `$( )` or `{ }` group or `$( )` inside a
    string, any of which can run an edit in the same command. A command the reader cannot
    parse is not shown lone.
    """
    try:
        items = _Pwsh(command).program()
    except _Unparsed:
        return "a command the guard could not parse"
    if not items:
        return None
    if len(items) > 1:
        return "statement separator"
    chain, background = items[0]
    if background:
        return "&"
    _, pipeline, rest = chain
    if rest:
        return rest[0][0]
    commands = pipeline[1]
    if len(commands) != 1 or commands[0][0] != "ps":
        return "|"
    _, words, blocks, _ = commands[0]
    if blocks or any(word.subs for word in words):
        return "a ( ), $( ) or { } group"
    return None


def _judge(root: pathlib.Path, command: str, dialect: str = "bash") -> int:
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
        chained = ps_chained_with if dialect == "ps" else chained_with
        separator = None if legacy else chained(command)
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
