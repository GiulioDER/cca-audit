"""Shared tree-sitter plumbing: grammar loading, parsing, and enclosing spans.

WHY A PARSER AND NOT A COMPILER, FOR THIS LAYER. Python pairs `ast` (spans, and the
syntactic clock-leak check) with pyright (type facts). Rust gets the same pairing:
tree-sitter here, clippy in `clippy_check`. The split matters because the two have
different availability: a grammar wheel is a pip install and always works, while
clippy needs the crate's toolchain AND a crate that builds. Putting spans behind the
compiler would mean an un-buildable crate loses even the checks that never needed it.

WHAT A SYNTACTIC PARSER CANNOT SEE, AND WHY THAT IS DISCLOSED RATHER THAN PATCHED.
tree-sitter reads text. It does not expand macros, resolve glob imports, or know
types. A `macro_rules!`-generated `Utc::now()` is invisible to it. That is a real
blind spot and it is handled the only sound way available: the conditions under
which it could be hiding something BLOCK REFUTATION rather than being ignored (see
`languages/rust.py`). Absence of evidence from a parser that cannot see everything
is not evidence of absence.

MISSING GRAMMAR IS AN EXCEPTION, NEVER AN EMPTY TREE. `parse` raises when the
optional dependency is absent. Every caller already treats an exception from the
parsing layer as "escalate", and returning an empty tree instead would make an
uninstalled extra look exactly like a file with nothing in it -- which is the
"a check that could not run must never be indistinguishable from a check that
passed" rule, applied to the parser itself.
"""

from __future__ import annotations

from functools import cache

#: Grammar module per language name. Kept as a table rather than an import so a
#: missing wheel surfaces as our own message at parse time rather than an
#: ImportError at `cca_checks` import time, which would take the Python layer down
#: with it.
_GRAMMAR_MODULES = {
    "rust": "tree_sitter_rust",
}


class GrammarUnavailable(RuntimeError):
    """The grammar for this language is not installed. Escalate; never refute."""


@cache
def _parser(language: str):
    """A cached parser for `language`.

    Cached because a backend parses the same file once per claim and grammar
    construction is the expensive part; parsers are not shared across threads here
    (the CLI settles one claim per process).
    """
    module_name = _GRAMMAR_MODULES.get(language)
    if module_name is None:
        raise GrammarUnavailable(f"no tree-sitter grammar is registered for {language!r}")
    try:
        import importlib

        from tree_sitter import Language, Parser
        grammar = importlib.import_module(module_name)
    except ImportError as exc:
        raise GrammarUnavailable(
            f"the tree-sitter grammar for {language} is not installed "
            f"(pip install 'cca_checks[{language}]'): {exc}") from exc
    return Parser(Language(grammar.language()))


def parse(path: str, language: str):
    """Parse `path` and return the tree's root node.

    Raises GrammarUnavailable when the grammar is missing and OSError when the file
    cannot be read. Both escalate at the call site.

    NOTE ON ERROR RECOVERY. tree-sitter always returns a tree, inserting ERROR nodes
    where it could not parse. This function does NOT reject a tree containing them,
    because partial parses are still useful for confirming: a clock read the parser
    DID resolve is real regardless of a syntax error elsewhere in the file. Callers
    that want to REFUTE must check `has_error` themselves -- silence from a parser
    that gave up halfway is not absence. `has_error` below is what they use.
    """
    # utf-8 bytes, not str: tree-sitter is byte-oriented and its node offsets are
    # byte offsets. Reading as text and re-encoding would make every offset wrong
    # for any file containing non-ASCII, which is silent and position-dependent.
    with open(path, "rb") as fh:
        source = fh.read()
    # A UTF-8 BOM is not valid Rust and tree-sitter reports it as an ERROR node at
    # offset 0, which would mark every BOM-prefixed file unrefutable. Stripping it
    # mirrors `scope.py`'s use of utf-8-sig for the same reason.
    if source.startswith(b"\xef\xbb\xbf"):
        source = source[3:]
    return _parser(language).parse(source).root_node


def has_error(root) -> bool:
    """True if the parse is incomplete anywhere in the tree.

    `Node.has_error` covers ERROR nodes and MISSING nodes in the subtree, which is
    exactly the question a refutation needs answered: did the parser see all of this
    file, or did it give up somewhere.
    """
    return bool(root.has_error)


def walk(node):
    """Every node in the subtree, root first. Iterative, so deep files cannot recurse.

    A generated or minified source file nests far deeper than Python's default
    recursion limit, and a RecursionError escaping the parsing layer would surface as
    a crash in the middle of an audit rather than an escalation.
    """
    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        stack.extend(reversed(current.children))


def line_span(node) -> tuple[int, int]:
    """1-indexed inclusive (first, last) line the node covers."""
    return node.start_point[0] + 1, node.end_point[0] + 1


def enclosing_span(path: str, line_1based: int, language: str,
                   function_types: frozenset[str]) -> tuple[int, int]:
    """Span of the innermost function-like node containing the line.

    Falls back to the whole file when the line sits outside any of them -- matching
    `scope.python_enclosing_span`, whose module-level fallback this mirrors.

    `function_types` is supplied by the backend rather than hardcoded because the
    node names are grammar-specific, and a language whose closures or methods carry a
    different node type would otherwise silently fall back to the whole file. A
    whole-file span is the WIDE answer, and a wide span makes a refutation rest on
    silence across code the claim never referred to -- so getting this set wrong
    fails toward over-refuting, which is the direction that costs findings.
    """
    root = parse(path, language)
    best: tuple[int, int] | None = None
    for node in walk(root):
        if node.type not in function_types:
            continue
        lo, hi = line_span(node)
        if lo <= line_1based <= hi and (best is None or lo > best[0]):
            best = (lo, hi)
    if best is not None:
        return best
    return line_span(root)
