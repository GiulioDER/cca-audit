"""Code anchors: the key that joins a ledger record to the code it describes.

An anchor is `(file, symbol, code_sha)`. RE-call's code chunker writes the same triple onto
code chunks and matches records by it, so the two implementations must agree exactly. They
agree only by reproducing the same test vectors
(`tests/fixtures/audit_memory/anchor_vectors.json`, copied from RE-call); the resolver below is
a port of RE-call's reference `recall/anchors.py`, never an import of it, and its rules are the
contract:

- A symbol is every function and class `ast` reports, dotted by nesting: a method is
  `Pool._lease`, a nested function `outer.inner` (never Python's `outer.<locals>.inner`).
- A symbol's span runs from its first decorator to its last line.
- A dotted name seen again in one file (a property and its setter) becomes `name#2`, then
  `#3`, in source order, so two definitions never share an anchor.
- `code_sha` hashes text after CRLF and CR become LF and trailing whitespace is stripped from
  every line, so a Windows checkout and a Linux checkout of one commit give one anchor.

Every anchor is computed during the audit run, where the checkout is: nothing downstream of
the run (resolve, RE-call) has one.
"""
from __future__ import annotations

import ast
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

_DEFINITIONS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
_BOM = "﻿"


@dataclass(frozen=True)
class SymbolSpan:
    """One function or class: its dotted name and 1-based inclusive line range."""

    symbol: str
    start_line: int
    end_line: int


@dataclass(frozen=True)
class Anchor:
    """`file` is repo-relative with forward slashes; `symbol` None means module level.

    `code_sha` is None only on a lesson's anchor, which names a place rather than one version
    of its code.
    """

    file: str
    symbol: str | None
    code_sha: str | None

    def to_json(self) -> dict:
        return {"file": self.file, "symbol": self.symbol, "code_sha": self.code_sha}


def normalize(text: str) -> str:
    """The text every hash is taken over: LF line endings, no trailing whitespace on any line."""
    unified = text.replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.rstrip() for line in unified.split("\n"))


def code_sha(text: str) -> str:
    """SHA-256 hex of `normalize(text)` encoded as UTF-8."""
    return hashlib.sha256(normalize(text).encode("utf-8")).hexdigest()


def resolve_symbols(source: str) -> list[SymbolSpan]:
    """Every function and class in `source`, parents before children, in source order.

    Raises `SyntaxError` when `source` is not parseable Python: the caller decides on a
    file-level anchor, rather than receiving an empty list that reads as "no symbols".
    """
    tree = ast.parse(normalize(source).removeprefix(_BOM))
    spans: list[SymbolSpan] = []
    seen: dict[str, int] = {}

    def visit(body: list[ast.stmt], prefix: str) -> None:
        for node in body:
            if isinstance(node, _DEFINITIONS):
                dotted = f"{prefix}{node.name}"
                seen[dotted] = seen.get(dotted, 0) + 1
                name = dotted if seen[dotted] == 1 else f"{dotted}#{seen[dotted]}"
                start = min([node.lineno, *(d.lineno for d in node.decorator_list)])
                spans.append(SymbolSpan(name, start, node.end_lineno or node.lineno))
                visit(node.body, f"{dotted}.")
            else:
                # A definition under if/try/with/for/while/match belongs to the enclosing scope.
                for child_body in _statement_bodies(node):
                    visit(child_body, prefix)

    visit(tree.body, "")
    return spans


def _statement_bodies(node: ast.stmt) -> list[list[ast.stmt]]:
    bodies = [getattr(node, f) for f in ("body",) if hasattr(node, f)]
    bodies += [h.body for h in getattr(node, "handlers", [])]
    bodies += [getattr(node, f) for f in ("orelse", "finalbody") if hasattr(node, f)]
    bodies += [c.body for c in getattr(node, "cases", [])]
    return [b for b in bodies if isinstance(b, list)]


def enclosing_symbol(source: str, line: int) -> str | None:
    """The innermost symbol whose span contains `line`, or None at module level."""
    containing = [s for s in resolve_symbols(source) if s.start_line <= line <= s.end_line]
    if not containing:
        return None
    return min(containing, key=lambda s: (s.end_line - s.start_line, -s.start_line)).symbol


def symbol_sha(source: str, symbol: str | None) -> str:
    """`code_sha` of `symbol`'s span in `source`, or of the whole file when `symbol` is None.

    Raises `KeyError` when `symbol` is not in `source`.
    """
    if symbol is None:
        return code_sha(source)
    span = {s.symbol: s for s in resolve_symbols(source)}[symbol]
    lines = normalize(source).split("\n")
    return code_sha("\n".join(lines[span.start_line - 1 : span.end_line]))


_DRIVE = re.compile(r"[A-Za-z]:")
_SUFFIX = re.compile(r"#[0-9]+")


def well_formed_file(file: object) -> bool:
    """Whether `file` is a path `_relative` can return: repo-relative POSIX, nothing odd.

    For anchors read back from a file the run did not just compute (a pending record).
    Memos print paths inside backticks, so a backtick is refused with the control characters.
    """
    if not isinstance(file, str) or not file or file.startswith("/") or _DRIVE.match(file):
        return False
    if any(c in file for c in "\\`") or any(ord(c) < 0x20 or ord(c) == 0x7f for c in file):
        return False
    return all(part not in ("", ".", "..") for part in file.split("/"))


def well_formed_symbol(symbol: object) -> bool:
    """Whether `symbol` is None or a name `resolve_symbols` can return."""
    if symbol is None:
        return True
    if not isinstance(symbol, str):
        return False
    name, hash_, n = symbol.partition("#")
    if hash_ and not _SUFFIX.fullmatch(f"#{n}"):
        return False
    return all(part.isidentifier() for part in name.split("."))


def _relative(repo_root: Path, file: str) -> str:
    """`file` as a repo-relative forward-slash path; ValueError when it leaves the repository."""
    root = repo_root.resolve()
    path = Path(file.replace("\\", "/"))
    full = (path if path.is_absolute() else root / path).resolve()
    return full.relative_to(root).as_posix()


def anchor_at(file: str, source: str, line: int) -> Anchor:
    """The anchor of `line` in `source`, the text of repo-relative `file`.

    Python files resolve to the innermost enclosing symbol. Any other file, a Python file that
    does not parse, and line 0 (L2's "file-level") anchor at file level.
    """
    symbol = None
    if file.endswith(".py") and line > 0:
        try:
            symbol = enclosing_symbol(source, line)
        except SyntaxError:
            symbol = None
    return Anchor(file, symbol, symbol_sha(source, symbol))


def read_source(path: Path) -> str:
    # Bytes, not read_text: universal newlines would hide a CR the hash must normalise itself.
    return path.read_bytes().decode("utf-8", errors="replace")


def anchor_for(repo_root: Path, file: str, line: int) -> Anchor:
    """The anchor of `file:line` in the working tree at `repo_root`.

    Raises ValueError for a path outside the repository and OSError for one that cannot be read.
    """
    relative = _relative(Path(repo_root), file)
    return anchor_at(relative, read_source(Path(repo_root) / relative), line)


def fixed_anchor(repo_root: Path, base: str, head: str, file: str, line: int) -> Anchor | None:
    """The anchor of a finding at `file:line` of `base`, hashed over the fixed code at `head`.

    L2 cites base lines, which the fix may shift, so the symbol is resolved in `base` and its
    span hashed in `head`. A symbol the fix removed or renamed anchors at file level; a file the
    fix deleted, or one outside the repository, has no anchor.
    """
    from .gitread import GitError, git

    try:
        relative = _relative(Path(repo_root), file)
        fixed = git(Path(repo_root), "show", f"{head}:{relative}")
    except (ValueError, GitError):
        return None
    if relative.endswith(".py") and line > 0:
        try:
            symbol = enclosing_symbol(git(Path(repo_root), "show", f"{base}:{relative}"), line)
            if symbol is not None:
                return Anchor(relative, symbol, symbol_sha(fixed, symbol))
        except (GitError, SyntaxError, KeyError):
            pass
    return Anchor(relative, None, code_sha(fixed))


def anchors_for_findings(repo_root: Path, findings: list[dict]) -> list[Anchor]:
    """One anchor per finding whose `file` and `line` resolve, in order, without duplicates.

    A finding with no file, an unreadable file or a path outside the repository gets none: no
    record may claim a place nobody could hash.
    """
    out: list[Anchor] = []
    for finding in findings:
        file = finding.get("file")
        try:
            line = int(finding.get("line") or 0)
        except (TypeError, ValueError):
            line = 0
        if not isinstance(file, str) or not file.strip():
            continue
        try:
            anchor = anchor_for(repo_root, file, line)
        except (OSError, ValueError):
            continue
        if anchor not in out:
            out.append(anchor)
    return out
